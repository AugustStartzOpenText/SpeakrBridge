from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from config import SpeakrConfig
from speakr_client import SpeakrClient


class SpeakrClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_recordings_requests_recent_completed_recordings(self) -> None:
        client = SpeakrClient(SpeakrConfig(base_url="https://speakr.example.com", api_token="token"))
        payload = {
            "recordings": [
                {
                    "id": 42,
                    "title": "RightFax upgrade discovery",
                    "status": "COMPLETED",
                    "meeting_date": "2026-07-20T10:00:00Z",
                }
            ]
        }

        with patch.object(client, "_get_json", AsyncMock(return_value=payload)) as get_json:
            recordings = await client.list_recordings(limit=15, query="RightFax")

        self.assertEqual(recordings[0].id, 42)
        get_json.assert_awaited_once()
        _, path = get_json.await_args.args
        self.assertEqual(path, "/api/v1/recordings")
        self.assertEqual(
            get_json.await_args.kwargs["params"],
            {
                "page": 1,
                "per_page": 15,
                "status": "completed",
                "sort_by": "created_at",
                "sort_order": "desc",
                "q": "RightFax",
            },
        )

    async def test_list_recordings_rejects_an_invalid_response_shape(self) -> None:
        client = SpeakrClient(SpeakrConfig(base_url="https://speakr.example.com", api_token="token"))

        with patch.object(client, "_get_json", AsyncMock(return_value={})):
            with self.assertRaisesRegex(ValueError, "Expected recordings array"):
                await client.list_recordings()


if __name__ == "__main__":
    unittest.main()
