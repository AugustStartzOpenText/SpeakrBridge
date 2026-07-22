from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scoping.extraction import ExtractedAnswer, ScopingExtractionResult
from scoping.jobs import InvalidScopingJobTransition, ScopingJobStore


def extraction_result() -> ScopingExtractionResult:
    return ScopingExtractionResult(
        template_id="template-1",
        template_version="v1",
        mode="upgrade",
        model="test-model",
        answers=[
            ExtractedAnswer(
                answer_id="company",
                status="unknown",
                value=None,
                confidence=0,
            )
        ],
    )


class ScopingJobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "jobs.db"
        self.store = ScopingJobStore(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_jobs_persist_and_revisions_increment_per_recording_template_and_mode(self) -> None:
        first = self.store.create_job(
            recording_id=10,
            template_id="template-1",
            template_version="v1",
            mode="upgrade",
        )
        second = self.store.create_job(
            recording_id=10,
            template_id="template-1",
            template_version="v1",
            mode="upgrade",
        )
        install = self.store.create_job(
            recording_id=10,
            template_id="template-1",
            template_version="v1",
            mode="install",
        )

        reopened = ScopingJobStore(self.database_path)
        self.assertEqual(reopened.get_job(first.job_id).revision, 1)
        self.assertEqual(second.revision, 2)
        self.assertEqual(install.revision, 1)

    def test_inbox_registration_is_idempotent_and_tracks_selected_job(self) -> None:
        first = self.store.enqueue_recording(
            recording_id=17,
            recording_title="RightFax Upgrade",
            onenote_page_id="page-1",
            onenote_link="onenote:https://example.test/page-1",
        )
        repeated = self.store.enqueue_recording(
            recording_id=17,
            recording_title="RightFax Upgrade - Updated",
            onenote_page_id="page-2",
        )

        self.assertEqual(first.status, "pending")
        self.assertEqual(repeated.recording_title, "RightFax Upgrade - Updated")
        self.assertEqual(repeated.onenote_page_id, "page-2")
        self.assertEqual(repeated.onenote_link, "onenote:https://example.test/page-1")
        self.assertEqual(len(self.store.list_inbox()), 1)

        job = self.store.create_job(
            recording_id=17,
            template_id="template-1",
            template_version="v1",
            mode="upgrade",
        )
        started = self.store.mark_inbox_started(17, job.job_id)
        self.assertIsNotNone(started)
        self.assertEqual(started.status, "started")
        self.assertEqual(started.job_id, job.job_id)

        dismissed = self.store.set_inbox_status(17, "dismissed")
        self.assertEqual(dismissed.status, "dismissed")
        self.assertEqual(self.store.list_inbox(status="dismissed"), [dismissed])

    def test_extraction_and_generation_follow_persisted_state_machine(self) -> None:
        job = self.store.create_job(
            recording_id=10,
            template_id="template-1",
            template_version="v1",
            mode="upgrade",
        )
        self.assertEqual(self.store.claim_extraction(job.job_id).status, "extracting")
        review = self.store.complete_extraction(
            job.job_id,
            extraction=extraction_result(),
            recording_title="Example Upgrade",
        )
        self.assertEqual(review.status, "review")
        self.assertEqual(review.recording_title, "Example Upgrade")
        self.assertIsNotNone(review.extraction)

        generating = self.store.claim_generation(job.job_id, include_inferred=False)
        self.assertEqual(generating.status, "generating")
        completed = self.store.complete_generation(
            job.job_id,
            output_path="/tmp/example.docx",
            generation_warnings=["Truncated field 'company' from 300 to 255 characters for legacy Word form compatibility"],
        )
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(completed.completed_at)
        self.assertEqual(len(completed.generation_warnings), 1)

        with self.assertRaises(InvalidScopingJobTransition):
            self.store.claim_generation(job.job_id, include_inferred=False)

    def test_restart_recovery_marks_in_progress_jobs_retryable(self) -> None:
        extraction_job = self.store.create_job(
            recording_id=11,
            template_id="template-1",
            template_version="v1",
            mode="upgrade",
        )
        self.store.claim_extraction(extraction_job.job_id)

        generation_job = self.store.create_job(
            recording_id=12,
            template_id="template-1",
            template_version="v1",
            mode="upgrade",
        )
        self.store.claim_extraction(generation_job.job_id)
        self.store.complete_extraction(
            generation_job.job_id,
            extraction=extraction_result(),
            recording_title="Generation Job",
        )
        self.store.claim_generation(generation_job.job_id, include_inferred=True)

        self.assertEqual(self.store.recover_interrupted(), 2)
        recovered_extraction = self.store.get_job(extraction_job.job_id)
        recovered_generation = self.store.get_job(generation_job.job_id)
        self.assertEqual(recovered_extraction.failed_operation, "extraction")
        self.assertEqual(recovered_generation.failed_operation, "generation")
        self.assertEqual(
            self.store.claim_generation(generation_job.job_id, include_inferred=False).status,
            "generating",
        )


if __name__ == "__main__":
    unittest.main()
