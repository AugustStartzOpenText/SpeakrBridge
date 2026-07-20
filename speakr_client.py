from __future__ import annotations

import asyncio
from typing import Any

import httpx

from config import SpeakrConfig
from models import RecordingMetadata, RecordingRef, SpeakrRecordingBundle


class SpeakrClient:
    def __init__(self, config: SpeakrConfig) -> None:
        self._config = config

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._config.api_token}"}

    async def list_recordings(
        self,
        *,
        status: str = "completed",
        limit: int = 10,
        query: str | None = None,
    ) -> list[RecordingRef]:
        params: dict[str, str | int] = {
            "page": 1,
            "per_page": limit,
            "status": status,
            "sort_by": "created_at",
            "sort_order": "desc",
        }
        if query:
            params["q"] = query

        async with httpx.AsyncClient(
            base_url=self._config.base_url,
            headers=self._headers(),
            timeout=30.0,
        ) as client:
            payload = await self._get_json(client, "/api/v1/recordings", params=params)

        raw_recordings = payload.get("recordings")
        if not isinstance(raw_recordings, list):
            raise ValueError("Expected recordings array from /api/v1/recordings")
        return [RecordingRef.model_validate(item) for item in raw_recordings if isinstance(item, dict)]

    async def fetch_recording_bundle(self, recording_id: int) -> SpeakrRecordingBundle:
        async with httpx.AsyncClient(
            base_url=self._config.base_url,
            headers=self._headers(),
            timeout=30.0,
        ) as client:
            metadata_task = self._get_json(client, f"/api/v1/recordings/{recording_id}")
            transcript_task = self._get_text(
                client,
                f"/api/v1/recordings/{recording_id}/transcript",
                params={"format": "text"},
            )
            summary_task = self._get_summary(client, f"/api/v1/recordings/{recording_id}/summary")
            notes_task = self._get_notes(client, f"/api/v1/recordings/{recording_id}/notes")

            metadata_raw, transcript, summary_markdown, notes = await asyncio.gather(
                metadata_task,
                transcript_task,
                summary_task,
                notes_task,
            )

        metadata = RecordingMetadata(
            id=recording_id,
            title=metadata_raw.get("title") or f"Recording {recording_id}",
            participants=self._extract_participants(metadata_raw),
            meeting_date=metadata_raw.get("meeting_date"),
            audio_duration=metadata_raw.get("audio_duration"),
            folder=metadata_raw.get("folder"),
            tags=metadata_raw.get("tags") or [],
            link=metadata_raw.get("url") or metadata_raw.get("link"),
            raw=metadata_raw,
        )
        return SpeakrRecordingBundle(
            metadata=metadata,
            transcript=transcript.strip(),
            summary_markdown=summary_markdown.strip(),
            notes=notes.strip() if notes else None,
        )

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        response = await client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object from {path}")
        return payload

    async def _get_text(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, str] | None = None,
    ) -> str:
        response = await client.get(path, params=params)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = response.json()
            if isinstance(payload, dict):
                return str(payload.get("content") or payload.get("transcript") or "")
        return response.text

    async def _get_summary(self, client: httpx.AsyncClient, path: str) -> str:
        response = await client.get(path)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("summary") or "")
        return ""

    async def _get_notes(self, client: httpx.AsyncClient, path: str) -> str | None:
        response = await client.get(path)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            notes = payload.get("notes")
            return str(notes) if notes else None
        return None

    def _extract_participants(self, payload: dict[str, Any]) -> list[str]:
        raw = payload.get("participants") or []
        participants: list[str] = []
        for item in raw:
            if isinstance(item, str):
                participants.append(item)
                continue
            if isinstance(item, dict):
                name = item.get("name") or item.get("display_name") or item.get("email")
                if name:
                    participants.append(str(name))
        return participants

