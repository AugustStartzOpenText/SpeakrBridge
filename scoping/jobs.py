from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Literal
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from scoping.extraction import ScopingExtractionResult
from scoping.models import ProjectMode

JobStatus = Literal["ready", "extracting", "review", "generating", "completed", "failed"]
JobOperation = Literal["extraction", "generation"]


class ScopingJob(BaseModel):
    job_id: str
    recording_id: int = Field(ge=1)
    recording_title: str | None = None
    template_id: str
    template_version: str
    mode: ProjectMode
    revision: int = Field(ge=1)
    status: JobStatus
    extraction: ScopingExtractionResult | None = None
    output_path: str | None = None
    include_inferred: bool = False
    error: str | None = None
    failed_operation: JobOperation | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ScopingJobNotFound(KeyError):
    pass


class InvalidScopingJobTransition(RuntimeError):
    pass


class ScopingJobStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_job(
        self,
        *,
        recording_id: int,
        template_id: str,
        template_version: str,
        mode: ProjectMode,
    ) -> ScopingJob:
        if recording_id < 1:
            raise ValueError("recording_id must be positive")
        now = _utc_now()
        job_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(revision), 0) + 1 AS revision
                FROM scoping_jobs
                WHERE recording_id = ? AND template_id = ? AND mode = ?
                """,
                (recording_id, template_id, mode),
            ).fetchone()
            revision = int(row["revision"])
            connection.execute(
                """
                INSERT INTO scoping_jobs (
                    job_id, recording_id, template_id, template_version, mode, revision,
                    status, include_inferred, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ready', 0, ?, ?)
                """,
                (
                    job_id,
                    recording_id,
                    template_id,
                    template_version,
                    mode,
                    revision,
                    now,
                    now,
                ),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> ScopingJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scoping_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise ScopingJobNotFound(job_id)
        return self._row_to_job(row)

    def list_jobs(
        self,
        *,
        recording_id: int | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
    ) -> list[ScopingJob]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        clauses: list[str] = []
        parameters: list[object] = []
        if recording_id is not None:
            clauses.append("recording_id = ?")
            parameters.append(recording_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM scoping_jobs {where} ORDER BY created_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def claim_extraction(self, job_id: str) -> ScopingJob:
        now = _utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE scoping_jobs
                SET status = 'extracting', extraction_json = NULL, output_path = NULL,
                    include_inferred = 0, error = NULL, failed_operation = NULL,
                    started_at = ?, completed_at = NULL, updated_at = ?
                WHERE job_id = ? AND status IN ('ready', 'review', 'failed')
                """,
                (now, now, job_id),
            )
            if result.rowcount != 1:
                self._raise_transition(connection, job_id, "extracting")
        return self.get_job(job_id)

    def complete_extraction(
        self,
        job_id: str,
        *,
        extraction: ScopingExtractionResult,
        recording_title: str,
    ) -> ScopingJob:
        now = _utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE scoping_jobs
                SET status = 'review', extraction_json = ?, recording_title = ?,
                    error = NULL, failed_operation = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'extracting'
                """,
                (extraction.model_dump_json(), recording_title, now, job_id),
            )
            if result.rowcount != 1:
                self._raise_transition(connection, job_id, "review")
        return self.get_job(job_id)

    def claim_generation(self, job_id: str, *, include_inferred: bool) -> ScopingJob:
        now = _utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE scoping_jobs
                SET status = 'generating', include_inferred = ?, output_path = NULL,
                    error = NULL, failed_operation = NULL, started_at = ?,
                    completed_at = NULL, updated_at = ?
                WHERE job_id = ?
                  AND extraction_json IS NOT NULL
                  AND (status = 'review' OR (status = 'failed' AND failed_operation = 'generation'))
                """,
                (int(include_inferred), now, now, job_id),
            )
            if result.rowcount != 1:
                self._raise_transition(connection, job_id, "generating")
        return self.get_job(job_id)

    def complete_generation(self, job_id: str, *, output_path: str) -> ScopingJob:
        now = _utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE scoping_jobs
                SET status = 'completed', output_path = ?, error = NULL,
                    failed_operation = NULL, completed_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'generating'
                """,
                (output_path, now, now, job_id),
            )
            if result.rowcount != 1:
                self._raise_transition(connection, job_id, "completed")
        return self.get_job(job_id)

    def mark_failed(
        self,
        job_id: str,
        *,
        operation: JobOperation,
        error: str,
    ) -> ScopingJob:
        expected_status = "extracting" if operation == "extraction" else "generating"
        now = _utc_now()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE scoping_jobs
                SET status = 'failed', error = ?, failed_operation = ?, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (error[:4000], operation, now, job_id, expected_status),
            )
            if result.rowcount != 1:
                self._raise_transition(connection, job_id, "failed")
        return self.get_job(job_id)

    def recover_interrupted(self) -> int:
        now = _utc_now()
        with self._connect() as connection:
            extraction = connection.execute(
                """
                UPDATE scoping_jobs
                SET status = 'failed', failed_operation = 'extraction',
                    error = 'Extraction interrupted by service restart', updated_at = ?
                WHERE status = 'extracting'
                """,
                (now,),
            ).rowcount
            generation = connection.execute(
                """
                UPDATE scoping_jobs
                SET status = 'failed', failed_operation = 'generation',
                    error = 'Generation interrupted by service restart', updated_at = ?
                WHERE status = 'generating'
                """,
                (now,),
            ).rowcount
        return extraction + generation

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scoping_jobs (
                    job_id TEXT PRIMARY KEY,
                    recording_id INTEGER NOT NULL CHECK (recording_id > 0),
                    recording_title TEXT,
                    template_id TEXT NOT NULL,
                    template_version TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode IN ('install', 'upgrade')),
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    status TEXT NOT NULL CHECK (
                        status IN ('ready', 'extracting', 'review', 'generating', 'completed', 'failed')
                    ),
                    extraction_json TEXT,
                    output_path TEXT,
                    include_inferred INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    failed_operation TEXT CHECK (
                        failed_operation IS NULL OR failed_operation IN ('extraction', 'generation')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE (recording_id, template_id, mode, revision)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS scoping_jobs_recording ON scoping_jobs(recording_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS scoping_jobs_status ON scoping_jobs(status)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _raise_transition(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        target_status: JobStatus,
    ) -> None:
        row = connection.execute(
            "SELECT status FROM scoping_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise ScopingJobNotFound(job_id)
        raise InvalidScopingJobTransition(
            f"Cannot transition scoping job {job_id} from {row['status']} to {target_status}"
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ScopingJob:
        extraction = (
            ScopingExtractionResult.model_validate_json(row["extraction_json"])
            if row["extraction_json"]
            else None
        )
        return ScopingJob(
            job_id=row["job_id"],
            recording_id=row["recording_id"],
            recording_title=row["recording_title"],
            template_id=row["template_id"],
            template_version=row["template_version"],
            mode=row["mode"],
            revision=row["revision"],
            status=row["status"],
            extraction=extraction,
            output_path=row["output_path"],
            include_inferred=bool(row["include_inferred"]),
            error=row["error"],
            failed_operation=row["failed_operation"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
