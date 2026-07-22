from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch

from config import OllamaConfig
from models import RecordingMetadata, SpeakrRecordingBundle
from scoping.catalog import ScopingTemplateCatalog
from scoping.extraction import (
    build_extraction_prompt,
    build_sources,
    extraction_to_word_values,
    ScopingExtractor,
    extract_scoping_focus,
    validate_extraction_payload,
)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_ID = "open_text_fax_install_upgrade_2025_08_20"


class ScopingExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = ScopingTemplateCatalog(base_dir=BASE_DIR).get(TEMPLATE_ID)
        self.bundle = SpeakrRecordingBundle(
            metadata=RecordingMetadata(
                id=42,
                title="Example Hospital RightFax Upgrade",
                participants=["Casey Customer", "Sam Consultant"],
                meeting_date=datetime(2026, 7, 18, 9, 0),
                tags=["rightfax", "upgrade"],
            ),
            notes="Customer wants no onsite services. Target completion is October 15.",
            summary_markdown="Upgrade the existing RightFax 16.6 environment with 24 FoIP channels.",
            transcript=(
                "Casey Customer: We are Example Hospital and currently run RightFax 16.6. "
                "We have 24 FoIP channels and need SMTP with OAuth for Microsoft 365."
            ),
        )
        self.sources = build_sources(self.bundle)

    def test_upgrade_prompt_is_derived_from_applicable_answers_and_all_sources(self) -> None:
        prompt = build_extraction_prompt(
            template=self.template,
            mode="upgrade",
            sources=self.sources,
        )
        self.assertIn('"answer_id": "current_open_text_fax_version"', prompt)
        self.assertNotIn('"answer_id": "project_type"', prompt)
        self.assertIn("Customer wants no onsite services", prompt)
        self.assertIn("currently run RightFax 16.6", prompt)

    def test_focus_phrase_extracts_priority_scoping_source_from_transcript(self) -> None:
        bundle = SpeakrRecordingBundle(
            metadata=self.bundle.metadata,
            transcript=(
                "Earlier discussion about discovery. "
                "Let me summarize this project from 50,000 feet. "
                "This is a new install of RightFax 25.4 into a two-server collective. "
                "There is no suid yet as the software has not been purchased. "
                "The integrations are going to be Epic. That is going to use the REST API. "
                "HP MFP devices with 500 of them across all the sites. "
                "Office 365 will be using OAuth and there's a local SMTP. "
                "Also some users would like some admin training so that should be quoted out as well."
            ),
        )

        self.assertTrue(
            extract_scoping_focus(bundle).startswith("Let me summarize this project from 50,000 feet.")
        )
        sources = build_sources(bundle)
        self.assertIn("scoping_focus", sources)
        self.assertIn("HP MFP devices", sources["scoping_focus"])
        self.assertIn("Office 365 will be using OAuth", sources["scoping_focus"])

    def test_prompt_marks_scoping_focus_as_high_priority_when_present(self) -> None:
        bundle = SpeakrRecordingBundle(
            metadata=self.bundle.metadata,
            transcript=(
                "Let me summarize this project from 50,000 feet. "
                "HP MFP devices with 500 of them across all the sites."
            ),
        )
        prompt = build_extraction_prompt(
            template=self.template,
            mode="install",
            sources=build_sources(bundle),
        )

        self.assertIn('A source named "scoping_focus" contains the speaker\'s end-of-call scoping recap.', prompt)
        self.assertIn('<source name="scoping_focus">', prompt)
        self.assertIn("HP MFP devices with 500 of them across all the sites.", prompt)

    def test_found_answer_requires_verifiable_exact_evidence(self) -> None:
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "current_open_text_fax_version",
                        "status": "found",
                        "value": "16.6",
                        "confidence": 0.98,
                        "evidence": [
                            {
                                "source": "transcript",
                                "quote": "currently run RightFax 16.6",
                            }
                        ],
                    }
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=self.sources,
        )
        answer = result.answer("current_open_text_fax_version")
        self.assertEqual(answer.status, "found")
        self.assertEqual(answer.value, "16.6")
        self.assertGreater(len(result.warnings), 0)  # Omitted answers are explicitly tracked.

    def test_unsupported_found_answer_is_downgraded_to_unknown(self) -> None:
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "upgrade_suids",
                        "status": "found",
                        "value": "9999999",
                        "confidence": 0.99,
                        "evidence": [{"source": "transcript", "quote": "SUID 9999999"}],
                    }
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=self.sources,
        )
        answer = result.answer("upgrade_suids")
        self.assertEqual(answer.status, "unknown")
        self.assertIsNone(answer.value)
        self.assertTrue(any("no verifiable evidence" in warning for warning in result.warnings))

    def test_found_answers_translate_to_individual_word_controls(self) -> None:
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "onsite_services_requested",
                        "status": "found",
                        "value": "no",
                        "confidence": 1,
                        "evidence": [{"source": "notes", "quote": "no onsite services"}],
                    },
                    {
                        "answer_id": "email_integrations",
                        "status": "found",
                        "value": ["smtp_pop3_oauth"],
                        "confidence": 0.95,
                        "evidence": [
                            {"source": "transcript", "quote": "SMTP with OAuth for Microsoft 365"}
                        ],
                    },
                    {
                        "answer_id": "channel_count",
                        "status": "found",
                        "value": 24,
                        "confidence": 0.9,
                        "evidence": [{"source": "transcript", "quote": "24 FoIP channels"}],
                    },
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=self.sources,
        )
        values = extraction_to_word_values(result=result, template=self.template)
        self.assertFalse(values["onsite_services_yes"])
        self.assertTrue(values["onsite_services_no"])
        self.assertTrue(values["email_smtp_pop3_oauth"])
        self.assertFalse(values["email_none"])
        self.assertEqual(values["channel_count"], "24")

    def test_inferred_answers_are_not_written_by_default(self) -> None:
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "managed_services_interest",
                        "status": "inferred",
                        "value": "no",
                        "confidence": 0.5,
                        "evidence": [],
                    }
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=self.sources,
        )
        self.assertNotIn(
            "managed_services_no",
            extraction_to_word_values(result=result, template=self.template),
        )
        self.assertTrue(
            extraction_to_word_values(
                result=result,
                template=self.template,
                include_inferred=True,
            )["managed_services_no"]
        )

    def test_grounded_microsoft_365_comment_derives_oauth_checkbox(self) -> None:
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "email_comments",
                        "status": "found",
                        "value": "Customer uses Microsoft 365 for email.",
                        "confidence": 0.9,
                        "evidence": [
                            {"source": "transcript", "quote": "OAuth for Microsoft 365"}
                        ],
                    }
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=self.sources,
        )

        self.assertEqual(result.answer("email_integrations").value, ["smtp_pop3_oauth"])
        self.assertTrue(extraction_to_word_values(result=result, template=self.template)["email_smtp_pop3_oauth"])
        self.assertTrue(any("microsoft_365_oauth_email_integration" in item for item in result.warnings))

    def test_grounded_microsoft_365_ews_comment_derives_ews_checkbox(self) -> None:
        sources = self.sources | {"notes": "The Office 365 integration uses EWS."}
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "email_comments",
                        "status": "found",
                        "value": "Office 365 integration uses EWS.",
                        "confidence": 0.95,
                        "evidence": [
                            {"source": "notes", "quote": "Office 365 integration uses EWS"}
                        ],
                    }
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=sources,
        )

        values = extraction_to_word_values(result=result, template=self.template)
        self.assertTrue(values["email_exchange_ews"])
        self.assertFalse(values["email_smtp_pop3_oauth"])

    def test_explicit_email_integration_selection_wins_over_derivation(self) -> None:
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "email_integrations",
                        "status": "found",
                        "value": ["exchange_connector"],
                        "confidence": 0.9,
                        "evidence": [
                            {"source": "transcript", "quote": "SMTP with OAuth for Microsoft 365"}
                        ],
                    },
                    {
                        "answer_id": "email_comments",
                        "status": "found",
                        "value": "Microsoft 365 is also mentioned.",
                        "confidence": 0.9,
                        "evidence": [
                            {"source": "transcript", "quote": "Microsoft 365"}
                        ],
                    },
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=self.sources,
        )

        self.assertEqual(result.answer("email_integrations").value, ["exchange_connector"])

    def test_grounded_derivation_supersedes_ungrounded_inference(self) -> None:
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "email_integrations",
                        "status": "inferred",
                        "value": ["exchange_connector"],
                        "confidence": 0.4,
                        "evidence": [],
                    },
                    {
                        "answer_id": "email_comments",
                        "status": "found",
                        "value": "Microsoft 365 is used for email.",
                        "confidence": 0.9,
                        "evidence": [
                            {"source": "transcript", "quote": "Microsoft 365"}
                        ],
                    },
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=self.sources,
        )

        self.assertEqual(result.answer("email_integrations").status, "found")
        self.assertEqual(result.answer("email_integrations").value, ["smtp_pop3_oauth"])

    def test_epic_appends_integration_module_and_review_warning(self) -> None:
        sources = self.sources | {"notes": "Epic is an integration application for this project."}
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "modules",
                        "status": "found",
                        "value": ["pdf_module"],
                        "confidence": 0.8,
                        "evidence": [
                            {"source": "speakr_summary", "quote": "Upgrade the existing RightFax"}
                        ],
                    },
                    {
                        "answer_id": "integration_applications",
                        "status": "found",
                        "value": "Epic",
                        "confidence": 0.95,
                        "evidence": [
                            {"source": "notes", "quote": "Epic is an integration application"}
                        ],
                    },
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=sources,
        )

        self.assertEqual(result.answer("modules").value, ["pdf_module", "integration_module"])
        values = extraction_to_word_values(result=result, template=self.template)
        self.assertTrue(values["module_pdf"])
        self.assertTrue(values["module_integration"])
        self.assertTrue(any("confirm which method" in item for item in result.warnings))

    def test_mfp_brands_append_mfp_module_and_populate_brands(self) -> None:
        sources = self.sources | {"notes": "The MFP devices are Xerox and Ricoh."}
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "mfp_brands",
                        "status": "found",
                        "value": "Xerox and Ricoh",
                        "confidence": 0.98,
                        "evidence": [
                            {"source": "notes", "quote": "MFP devices are Xerox and Ricoh"}
                        ],
                    }
                ]
            },
            template=self.template,
            mode="upgrade",
            model="test-model",
            sources=sources,
        )

        values = extraction_to_word_values(result=result, template=self.template)
        self.assertTrue(values["module_mfp"])
        self.assertEqual(values["mfp_brands"], "Xerox and Ricoh")
        self.assertTrue(any("mfp_devices_require_mfp_module" in item for item in result.warnings))

    def test_focus_source_evidence_can_drive_mfp_and_training_fields(self) -> None:
        sources = build_sources(
            SpeakrRecordingBundle(
                metadata=self.bundle.metadata,
                transcript=(
                    "Let me summarize this project from 50,000 feet. "
                    "HP MFP devices with 500 of them across all the sites. "
                    "Also some users would like some admin training so that should be quoted out as well."
                ),
            )
        )
        result = validate_extraction_payload(
            payload={
                "answers": [
                    {
                        "answer_id": "mfp_brands",
                        "status": "found",
                        "value": "HP",
                        "confidence": 0.97,
                        "evidence": [
                            {"source": "scoping_focus", "quote": "HP MFP devices"}
                        ],
                    },
                    {
                        "answer_id": "training_types",
                        "status": "found",
                        "value": ["admin_intro"],
                        "confidence": 0.85,
                        "evidence": [
                            {"source": "scoping_focus", "quote": "some users would like some admin training"}
                        ],
                    },
                ]
            },
            template=self.template,
            mode="install",
            model="test-model",
            sources=sources,
        )

        values = extraction_to_word_values(result=result, template=self.template)
        self.assertEqual(values["mfp_brands"], "HP")
        self.assertTrue(values["module_mfp"])
        self.assertTrue(values["training_admin_intro"])


class ScopingExtractorHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_extractor_requests_json_and_validates_response(self) -> None:
        template = ScopingTemplateCatalog(base_dir=BASE_DIR).get(TEMPLATE_ID)
        bundle = SpeakrRecordingBundle(
            metadata=RecordingMetadata(id=7, title="Example Upgrade"),
            transcript="Customer: We currently run RightFax 16.6.",
        )
        captured: dict = {"payloads": []}

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "response": (
                        '{"answers":[{"answer_id":"current_open_text_fax_version",'
                        '"status":"found","value":"16.6","confidence":0.99,'
                        '"evidence":[{"source":"transcript",'
                        '"quote":"currently run RightFax 16.6"}]}]}'
                    ),
                    "done_reason": "stop",
                    "eval_count": 60,
                }

        class FakeClient:
            def __init__(self, *, timeout: int) -> None:
                captured["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

            async def post(self, url: str, *, json: dict) -> FakeResponse:
                captured["url"] = url
                captured["payloads"].append(json)
                return FakeResponse()

        config = OllamaConfig(host="http://ollama.test:11434", model="test-model")
        with patch("scoping.extraction.httpx.AsyncClient", FakeClient):
            result = await ScopingExtractor(config).extract(
                bundle=bundle,
                template=template,
                mode="upgrade",
            )

        self.assertEqual(result.answer("current_open_text_fax_version").value, "16.6")
        self.assertEqual(captured["timeout"], 180)
        self.assertEqual(captured["url"], "http://ollama.test:11434/api/generate")
        self.assertEqual(len(captured["payloads"]), 5)
        self.assertEqual(captured["payloads"][0]["format"]["type"], "object")
        self.assertEqual(captured["payloads"][0]["options"]["temperature"], 0)
        self.assertEqual(captured["payloads"][0]["options"]["num_ctx"], 32768)

    async def test_extractor_retries_invalid_batch_with_diagnostic_prompt(self) -> None:
        template = ScopingTemplateCatalog(base_dir=BASE_DIR).get(TEMPLATE_ID)
        bundle = SpeakrRecordingBundle(
            metadata=RecordingMetadata(id=8, title="Example Upgrade"),
            transcript="Customer: We currently run RightFax 16.6.",
        )
        prompts: list[str] = []

        class FakeResponse:
            def __init__(self, response_text: str) -> None:
                self._response_text = response_text

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"response": self._response_text, "done_reason": "stop", "eval_count": 20}

        class FakeClient:
            def __init__(self, *, timeout: int) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

            async def post(self, url: str, *, json: dict) -> FakeResponse:
                prompts.append(json["prompt"])
                if len(prompts) == 1:
                    return FakeResponse("incomplete response")
                return FakeResponse('{"answers":[]}')

        config = OllamaConfig(
            host="http://ollama.test:11434",
            model="test-model",
            scoping_batch_size=100,
        )
        with patch("scoping.extraction.httpx.AsyncClient", FakeClient):
            result = await ScopingExtractor(config).extract(
                bundle=bundle,
                template=template,
                mode="upgrade",
            )

        self.assertEqual(len(prompts), 2)
        self.assertIn("previous response was not complete valid JSON", prompts[1])
        self.assertTrue(all(answer.status == "unknown" for answer in result.answers))


if __name__ == "__main__":
    unittest.main()
