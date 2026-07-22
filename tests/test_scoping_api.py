from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scoping.api import router
from scoping.catalog import ScopingTemplateCatalog
from scoping.jobs import ScopingJobStore
from scoping.service import ScopingService
from tests.test_scoping_service import FakeExtractor, FakeRecordingSource, FakeWriter

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_ID = "open_text_fax_install_upgrade_2025_08_20"


class ScopingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        temp_path = Path(self.temporary_directory.name)
        service = ScopingService(
            catalog=ScopingTemplateCatalog(base_dir=BASE_DIR),
            store=ScopingJobStore(temp_path / "jobs.db"),
            recording_source=FakeRecordingSource(),
            extractor=FakeExtractor(),
            writer=FakeWriter(),
            output_directory=temp_path / "outputs",
            api_token="test-token",
        )
        app = FastAPI()
        app.state.services = SimpleNamespace(scoping=service)
        app.include_router(router)
        self.client = TestClient(app, headers={"Authorization": "Bearer test-token"})
        self.service = service

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_template_job_extraction_generation_and_download_workflow(self) -> None:
        templates_response = self.client.get("/api/scoping/templates")
        self.assertEqual(templates_response.status_code, 200)
        self.assertEqual(templates_response.json()[0]["id"], TEMPLATE_ID)
        self.assertEqual(templates_response.json()[0]["answer_count_by_mode"]["upgrade"], 35)
        template_detail = self.client.get(f"/api/scoping/templates/{TEMPLATE_ID}")
        self.assertEqual(template_detail.status_code, 200)
        self.assertEqual(len(template_detail.json()["answers"]), 35)

        create_response = self.client.post(
            "/api/scoping/jobs",
            json={
                "recording_id": 123,
                "template_id": TEMPLATE_ID,
                "mode": "upgrade",
                "start_extraction": False,
            },
        )
        self.assertEqual(create_response.status_code, 201)
        job_id = create_response.json()["job_id"]
        self.assertEqual(create_response.json()["status"], "ready")

        extract_response = self.client.post(f"/api/scoping/jobs/{job_id}/extract")
        self.assertEqual(extract_response.status_code, 202)
        self.assertEqual(self.client.get(f"/api/scoping/jobs/{job_id}").json()["status"], "review")

        generate_response = self.client.post(
            f"/api/scoping/jobs/{job_id}/generate",
            json={"include_inferred": False},
        )
        self.assertEqual(generate_response.status_code, 202)
        completed = self.client.get(f"/api/scoping/jobs/{job_id}").json()
        self.assertEqual(completed["status"], "completed")

        jobs_response = self.client.get("/api/scoping/jobs", params={"status": "completed"})
        self.assertEqual(jobs_response.status_code, 200)
        self.assertEqual(jobs_response.json()[0]["found_count"], 1)
        self.assertEqual(jobs_response.json()[0]["generation_warnings"], [])
        self.assertNotIn("extraction", jobs_response.json()[0])

        document_response = self.client.get(f"/api/scoping/jobs/{job_id}/document")
        self.assertEqual(document_response.status_code, 200)
        self.assertEqual(document_response.content, b"fake-docx")

    def test_invalid_state_returns_conflict(self) -> None:
        create_response = self.client.post(
            "/api/scoping/jobs",
            json={
                "recording_id": 124,
                "template_id": TEMPLATE_ID,
                "mode": "install",
                "start_extraction": False,
            },
        )
        job_id = create_response.json()["job_id"]
        response = self.client.post(
            f"/api/scoping/jobs/{job_id}/generate",
            json={"include_inferred": False},
        )
        self.assertEqual(response.status_code, 409)

    def test_inbox_can_be_listed_started_and_dismissed(self) -> None:
        self.service.store.enqueue_recording(
            recording_id=125,
            recording_title="Hospital Fax Upgrade",
            onenote_page_id="page-125",
        )

        inbox_response = self.client.get("/api/scoping/inbox")
        self.assertEqual(inbox_response.status_code, 200)
        self.assertEqual(inbox_response.json()[0]["status"], "pending")

        create_response = self.client.post(
            "/api/scoping/jobs",
            json={
                "recording_id": 125,
                "template_id": TEMPLATE_ID,
                "mode": "upgrade",
                "start_extraction": False,
            },
        )
        self.assertEqual(create_response.status_code, 201)
        started = self.client.get("/api/scoping/inbox").json()[0]
        self.assertEqual(started["status"], "started")
        self.assertEqual(started["job_id"], create_response.json()["job_id"])

        dismiss_response = self.client.patch(
            "/api/scoping/inbox/125",
            json={"status": "dismissed"},
        )
        self.assertEqual(dismiss_response.status_code, 200)
        self.assertEqual(dismiss_response.json()["status"], "dismissed")

        dismissed = self.client.get("/api/scoping/inbox", params={"status": "dismissed"})
        self.assertEqual(len(dismissed.json()), 1)

    def test_missing_api_token_is_rejected(self) -> None:
        response = self.client.get(
            "/api/scoping/templates",
            headers={"Authorization": ""},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
