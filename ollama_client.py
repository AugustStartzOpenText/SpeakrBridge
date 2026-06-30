from __future__ import annotations

import json
import logging

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
            parsed = json.loads(raw_text)
            return StructuredSummary.model_validate(parsed)
        except Exception:
            LOGGER.warning("Ollama returned non-JSON summary", extra={"response": raw_text[:500]})
            return None
