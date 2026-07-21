from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from models import OneNotePageContent
from onenote_writer import OneNoteSection, OneNoteWriteResult
from pending_jobs import PendingJobStore
from queue_web import api_router, router


class FakeNotifier:
    def __init__(self) -> None:
        self.successes: list[tuple[str, str]] = []

    def notify_success(self, meeting_title: str, section_name: str) -> None:
        self.successes.append((meeting_title, section_name))


class FakeOneNoteWriter:
    def __init__(self) -> None:
        self.sections = [
            OneNoteSection(
                notebook_id="notebook-1",
                notebook_name="Notebook",
                section_id="section-1",
                section_name="Default",
                path="Notebook / Default",
            ),
            OneNoteSection(
                notebook_id="notebook-1",
                notebook_name="Notebook",
                section_id="section-2",
                section_name="Projects",
                path="Notebook / Projects",
            ),
        ]
        self.saved = self.sections[0]
        self.written: list[OneNoteSection] = []

    def get_saved_destination(self) -> OneNoteSection:
        return self.saved

    def set_destination(self, section_id: str) -> OneNoteSection:
        self.saved = next(section for section in self.sections if section.section_id == section_id)
        return self.saved

    def list_sections(self) -> list[OneNoteSection]:
        return self.sections

    def write_page(self, page: OneNotePageContent) -> OneNoteWriteResult:
        return self.write_page_to_section(page, self.saved)

    def write_page_to_section(self, page: OneNotePageContent, section: OneNoteSection) -> OneNoteWriteResult:
        self.written.append(section)
        return OneNoteWriteResult(page_id="page-1", page_link="onenote://page-1", section=section)


class QueueWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = PendingJobStore(Path(self.temporary_directory.name) / "pending")
        self.onenote = FakeOneNoteWriter()
        self.notifier = FakeNotifier()

        app = FastAPI()
        app.state.services = SimpleNamespace(
            pending_jobs=self.store,
            onenote=self.onenote,
            notifier=self.notifier,
            scoping=None,
        )
        app.include_router(api_router)
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_queue_page_and_assets_are_served(self) -> None:
        page = self.client.get("/queue")
        self.assertEqual(page.status_code, 200)
        self.assertIn("SpeakrBridge Queue", page.text)
        self.assertIn("/queue/app.js", page.text)

        script = self.client.get("/queue/app.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("/api/queue/jobs", script.text)

        styles = self.client.get("/queue/styles.css")
        self.assertEqual(styles.status_code, 200)
        self.assertIn(".table-wrap", styles.text)

    def test_jobs_can_be_listed_and_deleted(self) -> None:
        job = self._save_job()

        response = self.client.get("/api/queue/jobs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["job_id"], job.job_id)

        delete_response = self.client.delete(f"/api/queue/jobs/{job.job_id}")
        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(self.client.get("/api/queue/jobs").json(), [])

    def test_job_can_be_routed_to_selected_section_and_removed(self) -> None:
        job = self._save_job()

        response = self.client.post(
            f"/api/queue/jobs/{job.job_id}/route",
            json={"section_id": "section-2", "save_as_default": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["section"]["section_id"], "section-2")
        self.assertEqual(self.onenote.written[0].section_id, "section-2")
        self.assertEqual(self.onenote.saved.section_id, "section-2")
        self.assertIsNone(self.store.get_job(job.job_id))
        self.assertEqual(self.notifier.successes, [("Queued Meeting", "Notebook / Projects")])

    def _save_job(self):
        job = self.store.create_job(
            recording_id=123,
            delivery_id="delivery-1",
            event="recording.completed",
            meeting_title="Queued Meeting",
            page=OneNotePageContent(title="Queued Meeting", page_xml_body="<one:Outline />"),
        )
        self.store.save_job(job)
        return job


if __name__ == "__main__":
    unittest.main()
