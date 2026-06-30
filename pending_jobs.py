from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ValidationError

from models import OneNotePageContent


class PendingJob(BaseModel):
    job_id: str
    recording_id: int
    delivery_id: str | None = None
    event: str
    meeting_title: str
    created_at: datetime
    page: OneNotePageContent
    last_error: str | None = None
    last_attempted_at: datetime | None = None


class PendingJobStore:
    def __init__(self, directory: str | Path = "pending_jobs") -> None:
        self._directory = Path(directory)

    def create_job(
        self,
        *,
        recording_id: int,
        delivery_id: str | None,
        event: str,
        meeting_title: str,
        page: OneNotePageContent,
    ) -> PendingJob:
        return PendingJob(
            job_id=uuid.uuid4().hex,
            recording_id=recording_id,
            delivery_id=delivery_id,
            event=event,
            meeting_title=meeting_title,
            created_at=datetime.now(timezone.utc),
            page=page,
        )

    def list_jobs(self) -> list[PendingJob]:
        if not self._directory.exists():
            return []

        jobs: list[PendingJob] = []
        for path in sorted(self._directory.glob("*.json")):
            jobs.append(self._load_path(path))
        jobs.sort(key=lambda job: (job.created_at, job.job_id))
        return jobs

    def get_job(self, job_id: str) -> PendingJob | None:
        path = self._path_for(job_id)
        if not path.exists():
            return None
        return self._load_path(path)

    def save_job(self, job: PendingJob) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._write_path(self._path_for(job.job_id), job)

    def delete_job(self, job_id: str) -> None:
        self._path_for(job_id).unlink(missing_ok=True)

    def mark_failed(self, job: PendingJob, error: str) -> None:
        updated = job.model_copy(
            update={
                "last_error": error,
                "last_attempted_at": datetime.now(timezone.utc),
            }
        )
        self.save_job(updated)

    def find_duplicate(
        self,
        *,
        recording_id: int,
        delivery_id: str | None,
        event: str,
    ) -> PendingJob | None:
        for job in self.list_jobs():
            if delivery_id and job.delivery_id and job.delivery_id == delivery_id:
                return job
            if job.recording_id == recording_id and job.event == event:
                return job
        return None

    def _path_for(self, job_id: str) -> Path:
        return self._directory / f"{job_id}.json"

    def _load_path(self, path: Path) -> PendingJob:
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            return PendingJob.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"Invalid pending job in {path}: {exc}") from exc

    def _write_path(self, path: Path, job: PendingJob) -> None:
        payload = job.model_dump(mode="json")
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")

        temp_path.replace(path)
