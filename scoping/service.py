from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import NamedTuple, Protocol

from models import SpeakrRecordingBundle
from scoping.catalog import ScopingTemplateCatalog
from scoping.extraction import ScopingExtractionResult, extraction_to_word_values
from scoping.jobs import ScopingJob, ScopingJobStore
from scoping.models import ProjectMode, ScopingTemplate

LOGGER = logging.getLogger(__name__)


class GeneratedDocument(NamedTuple):
    output_path: Path
    warnings: list[str]


class RecordingSource(Protocol):
    async def fetch_recording_bundle(self, recording_id: int) -> SpeakrRecordingBundle: ...


class ExtractionEngine(Protocol):
    async def extract(
        self,
        *,
        bundle: SpeakrRecordingBundle,
        template: ScopingTemplate,
        mode: ProjectMode,
    ) -> ScopingExtractionResult: ...


class DocumentWriter(Protocol):
    def generate(
        self,
        *,
        template: ScopingTemplate,
        mode: ProjectMode,
        values: dict[str, str | bool],
        output_path: str | Path,
    ) -> GeneratedDocument: ...


class ScopingService:
    def __init__(
        self,
        *,
        catalog: ScopingTemplateCatalog,
        store: ScopingJobStore,
        recording_source: RecordingSource,
        extractor: ExtractionEngine,
        writer: DocumentWriter,
        output_directory: str | Path,
        api_token: str | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self._recording_source = recording_source
        self._extractor = extractor
        self._writer = writer
        self.api_token = api_token.strip() if api_token and api_token.strip() else None
        self.output_directory = Path(output_directory).resolve()
        self.output_directory.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        *,
        recording_id: int,
        template_id: str,
        mode: ProjectMode,
    ) -> ScopingJob:
        template = self.catalog.get(template_id)
        template.mode(mode)
        job = self.store.create_job(
            recording_id=recording_id,
            template_id=template.id,
            template_version=template.version,
            mode=mode,
        )
        self.store.mark_inbox_started(recording_id, job.job_id)
        return job

    def claim_extraction(self, job_id: str) -> ScopingJob:
        return self.store.claim_extraction(job_id)

    async def run_extraction(self, job_id: str) -> None:
        try:
            job = self.store.get_job(job_id)
            template = self._job_template(job)
            bundle = await self._recording_source.fetch_recording_bundle(job.recording_id)
            extraction = await self._extractor.extract(
                bundle=bundle,
                template=template,
                mode=job.mode,
            )
            self.store.complete_extraction(
                job_id,
                extraction=extraction,
                recording_title=bundle.metadata.title,
            )
            LOGGER.info("Scoping extraction completed", extra={"job_id": job_id})
        except Exception as exc:
            LOGGER.exception("Scoping extraction failed", extra={"job_id": job_id})
            try:
                self.store.mark_failed(job_id, operation="extraction", error=str(exc))
            except Exception:
                LOGGER.exception("Failed to persist scoping extraction failure", extra={"job_id": job_id})

    def claim_generation(self, job_id: str, *, include_inferred: bool) -> ScopingJob:
        return self.store.claim_generation(job_id, include_inferred=include_inferred)

    async def run_generation(self, job_id: str) -> None:
        try:
            job = self.store.get_job(job_id)
            if job.extraction is None:
                raise RuntimeError("Scoping job does not contain an extraction result")
            template = self._job_template(job)
            values = extraction_to_word_values(
                result=job.extraction,
                template=template,
                include_inferred=job.include_inferred,
            )
            output_path = self._next_output_path(job)
            generation_result = await asyncio.to_thread(
                self._writer.generate,
                template=template,
                mode=job.mode,
                values=values,
                output_path=output_path,
            )
            generated_path = generation_result.output_path.resolve()
            self._assert_output_path(generated_path)
            self.store.complete_generation(
                job_id,
                output_path=str(generated_path),
                generation_warnings=generation_result.warnings,
            )
            LOGGER.info("Scoping document generated", extra={"job_id": job_id})
        except Exception as exc:
            LOGGER.exception("Scoping document generation failed", extra={"job_id": job_id})
            try:
                self.store.mark_failed(job_id, operation="generation", error=str(exc))
            except Exception:
                LOGGER.exception("Failed to persist scoping generation failure", extra={"job_id": job_id})

    def completed_document(self, job_id: str) -> Path:
        job = self.store.get_job(job_id)
        if job.status != "completed" or not job.output_path:
            raise RuntimeError(f"Scoping job {job_id} does not have a completed document")
        path = Path(job.output_path).resolve()
        self._assert_output_path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Generated scoping document is missing: {path}")
        return path

    def recover_interrupted(self) -> int:
        return self.store.recover_interrupted()

    def _job_template(self, job: ScopingJob) -> ScopingTemplate:
        template = self.catalog.get(job.template_id)
        if template.version != job.template_version:
            raise RuntimeError(
                f"Job template version {job.template_version!r} is no longer available; "
                f"catalog has {template.version!r}"
            )
        return template

    def _next_output_path(self, job: ScopingJob) -> Path:
        title = job.recording_title or f"recording-{job.recording_id}"
        safe_title = re.sub(r"[^A-Za-z0-9._ -]+", "", title).strip().replace(" ", "_")
        safe_title = safe_title or f"recording-{job.recording_id}"
        base_name = f"{safe_title}_{job.mode}_r{job.revision}_{job.job_id[:8]}"
        candidate = self.output_directory / f"{base_name}.docx"
        if candidate.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            candidate = self.output_directory / f"{base_name}_{stamp}.docx"
        return candidate

    def _assert_output_path(self, path: Path) -> None:
        if not path.is_relative_to(self.output_directory):
            raise RuntimeError(f"Generated document escaped the configured output directory: {path}")
