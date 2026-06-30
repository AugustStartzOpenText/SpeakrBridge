from __future__ import annotations

import json
import logging
import re

import httpx

from config import OllamaConfig
from models import StructuredSummary

LOGGER = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a professional meeting summarizer for a B2B sales and solutions
consulting team. Analyze the transcript below and respond ONLY with a
valid JSON object. No explanation, no markdown fences.

Return exactly this structure:
{{
  "executive_summary": "2-3 sentence overview",
  "key_decisions": ["decision 1", "decision 2"],
  "action_items": [
    {{"owner": "Name or Unknown", "task": "...", "due": "date or None"}}
  ],
  "follow_up_questions": ["question 1"],
  "sentiment": "positive|neutral|mixed|negative",
  "topics": ["topic 1", "topic 2"]
}}

TRANSCRIPT:
{transcript_text}
"""


class OllamaClient:
    def __init__(self, config: OllamaConfig) -> None:
        self._config = config

    async def summarize(self, transcript_text: str) -> StructuredSummary | None:
        if not transcript_text.strip():
            return None

        payload = {
            "model": self._config.model,
            "prompt": PROMPT_TEMPLATE.format(transcript_text=transcript_text),
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                response = await client.post(f"{self._config.host}/api/generate", json=payload)
                response.raise_for_status()
        except Exception:
            LOGGER.exception("Ollama request failed")
            return None

        data = response.json()
        raw_text = data.get("response", "") if isinstance(data, dict) else ""
        if not raw_text:
            LOGGER.warning("Ollama response did not include response text")
            return None

        try:
            parsed = _parse_summary_response(raw_text)
            return StructuredSummary.model_validate(parsed)
        except Exception:
            LOGGER.warning("Ollama returned unparseable summary", extra={"response": raw_text[:1000]})
            return None


def _parse_summary_response(raw_text: str) -> dict:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = _strip_code_fences(cleaned)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    candidate = _extract_first_json_object(cleaned)
    if candidate:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Unable to parse Ollama summary as JSON object")


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None
