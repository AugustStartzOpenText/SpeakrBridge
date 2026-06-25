from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RecordingRef(BaseModel):
    id: int
    title: str | None = None
    status: str | None = None
    meeting_date: datetime | None = None
    completed_at: datetime | None = None
    has_summary: bool | None = None
    has_transcription: bool | None = None


class WebhookEnvelope(BaseModel):
    event: str
    recording: RecordingRef


class RecordingMetadata(BaseModel):
    id: int
    title: str
    participants: list[str] = Field(default_factory=list)
    meeting_date: datetime | None = None
    audio_duration: int | None = None
    folder: str | None = None
    tags: list[str] = Field(default_factory=list)
    link: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SpeakrRecordingBundle(BaseModel):
    metadata: RecordingMetadata
    transcript: str = ""
    summary_markdown: str = ""
    notes: str | None = None


class ActionItem(BaseModel):
    owner: str
    task: str
    due: str


class StructuredSummary(BaseModel):
    executive_summary: str
    key_decisions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    topics: list[str] = Field(default_factory=list)


class OneNotePageContent(BaseModel):
    title: str
    page_xml_body: str
    warning_banner: str | None = None
