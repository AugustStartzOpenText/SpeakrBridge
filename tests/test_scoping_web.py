from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scoping.web import router


class ScopingWebTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def test_workflow_page_and_assets_are_served(self) -> None:
        page = self.client.get("/scoping")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Scoping Desk", page.text)
        self.assertIn("/scoping/app.js", page.text)

        script = self.client.get("/scoping/app.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("/api/scoping/inbox", script.text)

        styles = self.client.get("/scoping/styles.css")
        self.assertEqual(styles.status_code, 200)
        self.assertIn(".inbox-grid", styles.text)


if __name__ == "__main__":
    unittest.main()
