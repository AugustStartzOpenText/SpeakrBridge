from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from models import RecordingMetadata, SpeakrRecordingBundle
from scoping.catalog import ScopingTemplateCatalog
from scoping.extraction import ExtractedAnswer, ExtractionEvidence, ScopingExtractionResult
from scoping.jobs import ScopingJobStore
from scoping.service import ScopingService

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_ID = "open_text_fax_install_upgrade_2025_08_20"


class FakeRecordingSource:
    async def fetch_recording_bundle(self, recording_id: int) -> SpeakrRecordingBundle:
        return SpeakrRecordingBundle(
            metadata=RecordingMetadata(
                id=recording_id,
                title="Example Hospital Upgrade",
                meeting_date=datetime(2026, 7, 18, 9, 0),
            ),
            transcript="We currently run RightFax 16.6.",
        )


class FakeExtractor:
    async def extract(self, *, bundle, template, mode) -> ScopingExtractionResult:
        return ScopingExtractionResult(
            template_id=template.id,
            template_version=template.version,
            mode=mode,
            model="test-model",
            answers=[
                ExtractedAnswer(
                    answer_id="current_open_text_fax_version",
                    status="found",
                    value="16.6",
                    confidence=1,
                    evidence=[
                        ExtractionEvidence(source="transcript", quote="currently run RightFax 16.6")
                    ],
                )
            ],
        )


class FakeWriter:
    def generate(self, *, template, mode, values, output_path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-docx")
        return path


class ScopingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        temp_path = Path(self.temporary_directory.name)
        self.service = ScopingService(
            catalog=ScopingTemplateCatalog(base_dir=BASE_DIR),
            store=ScopingJobStore(temp_path / "jobs.db"),
            recording_source=FakeRecordingSource(),
            extractor=FakeExtractor(),
            writer=FakeWriter(),
            output_directory=temp_path / "outputs",
        )

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_service_runs_extraction_and_generation_from_persisted_jobs(self) -> None:
        job = self.service.create_job(
            recording_id=99,
            template_id=TEMPLATE_ID,
            mode="upgrade",
        )
        self.service.claim_extraction(job.job_id)
        await self.service.run_extraction(job.job_id)
        review = self.service.store.get_job(job.job_id)
        self.assertEqual(review.status, "review")
        self.assertEqual(review.recording_title, "Example Hospital Upgrade")

        self.service.claim_generation(job.job_id, include_inferred=False)
        await self.service.run_generation(job.job_id)
        completed = self.service.store.get_job(job.job_id)
        self.assertEqual(completed.status, "completed")
        self.assertTrue(self.service.completed_document(job.job_id).is_file())
        self.assertTrue(Path(completed.output_path).is_relative_to(self.service.output_directory))


if __name__ == "__main__":
    unittest.main()
