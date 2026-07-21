from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from config import AppConfig
from models import OneNotePageContent, SpeakrRecordingBundle, StructuredSummary


def build_page(
    bundle: SpeakrRecordingBundle,
    structured_summary: StructuredSummary | None,
    config: AppConfig,
) -> OneNotePageContent:
    meeting_date = _format_datetime(bundle.metadata.meeting_date)
    title = f"{bundle.metadata.title} - {_safe_date(bundle.metadata.meeting_date)}"
    warning_banner = None
    if structured_summary is None:
        warning_banner = "Ollama summarization unavailable - structured summary not included."

    outlines: list[str] = []
    y = 140

    if warning_banner:
        outlines.append(_build_outline([warning_banner], y))
        y += 80

    outlines.append(
        _build_outline(
            [
                "Meeting Header",
                f"Title: {bundle.metadata.title}",
                f"Date: {meeting_date}",
                f"Duration: {_format_duration(bundle.metadata.audio_duration)}",
                f"Participants: {', '.join(bundle.metadata.participants) or 'Unknown'}",
                f"Tags: {_format_tags(bundle.metadata.tags)}",
                f"Recording: {bundle.metadata.link or 'Unavailable'}",
            ],
            y,
        )
    )
    y += 210
    outlines.append(
        _build_outline(
            [
                "Ollama Executive Summary",
                structured_summary.executive_summary if structured_summary else "Unavailable",
            ],
            y,
        )
    )
    y += 100
    outlines.append(_build_outline(["Speakr AI Summary"] + _markdownish_lines(bundle.summary_markdown), y))
    y += _outline_height(["Speakr AI Summary"] + _markdownish_lines(bundle.summary_markdown)) + 30
    outlines.append(_build_outline(["Key Decisions"] + _bullet_lines(structured_summary.key_decisions if structured_summary else []), y))
    y += _outline_height(["Key Decisions"] + _bullet_lines(structured_summary.key_decisions if structured_summary else [])) + 30
    outlines.append(
        _build_outline(
            ["Action Items"] + _action_item_lines(structured_summary.action_items if structured_summary else []),
            y,
        )
    )
    y += _outline_height(["Action Items"] + _action_item_lines(structured_summary.action_items if structured_summary else [])) + 30
    outlines.append(
        _build_outline(
            ["Follow-up Questions"] + _bullet_lines(structured_summary.follow_up_questions if structured_summary else []),
            y,
        )
    )
    y += _outline_height(["Follow-up Questions"] + _bullet_lines(structured_summary.follow_up_questions if structured_summary else [])) + 30
    if bundle.notes:
        outlines.append(_build_outline(["User Notes"] + _plain_lines(bundle.notes), y))
        y += _outline_height(["User Notes"] + _plain_lines(bundle.notes)) + 30
    outlines.append(_build_outline(["Full Transcript"] + _plain_lines(bundle.transcript or "Transcript unavailable."), y))
    y += _outline_height(["Full Transcript"] + _plain_lines(bundle.transcript or "Transcript unavailable.")) + 30
    outlines.append(_build_outline(_footer_lines(bundle, config), y))
    return OneNotePageContent(title=title, page_xml_body="".join(outlines), warning_banner=warning_banner)


def _footer_lines(bundle: SpeakrRecordingBundle, config: AppConfig) -> list[str]:
    processed_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return [
        "Metadata Footer",
        "Source: Speakr",
        f"Ollama model: {config.ollama.model}",
        f"Processed at: {processed_at}",
        f"Speakr recording ID: {bundle.metadata.id}",
    ]


def _bullet_lines(items: list[str]) -> list[str]:
    if not items:
        return ["None"]
    return [f"- {item}" for item in items]


def _format_tags(tags: list[Any]) -> str:
    normalized = [str(tag).strip() for tag in tags if str(tag).strip()]
    return ", ".join(normalized) if normalized else "None"


def _action_item_lines(items: list) -> list[str]:
    if not items:
        return ["None"]
    return [f"- Owner: {item.owner} | Task: {item.task} | Due: {item.due}" for item in items]


def _markdownish_lines(text: str) -> list[str]:
    if not text.strip():
        return ["No summary provided by Speakr."]
    return _plain_lines(text)


def _plain_lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines()]
    normalized = [line for line in lines if line.strip()]
    return normalized or ["None"]


def _build_outline(lines: list[str], y: int) -> str:
    items = "".join(
        f'<one:OE alignment="left"><one:T><![CDATA[{_cdata_text(line)}]]></one:T></one:OE>'
        for line in lines
    )
    return (
        "<one:Outline>"
        f'<one:Position x="36" y="{y}"/>'
        f'<one:Size width="560" height="{_outline_height(lines)}"/>'
        f"<one:OEChildren>{items}</one:OEChildren>"
        "</one:Outline>"
    )


def _outline_height(lines: list[str]) -> int:
    return max(30, 24 * len(lines))


def _cdata_text(value: str) -> str:
    return escape(value).replace("]]>", "]]]]><![CDATA[>")


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "Unknown"
    return value.strftime("%Y-%m-%d %H:%M")


def _safe_date(value: datetime | None) -> str:
    if value is None:
        return "unknown-date"
    return value.strftime("%Y-%m-%d")


def _format_duration(seconds: float | None) -> str:
    if not seconds:
        return "Unknown"
    rounded_seconds = int(round(seconds))
    minutes, remainder = divmod(rounded_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {remainder}s"
    return f"{minutes}m {remainder}s"
