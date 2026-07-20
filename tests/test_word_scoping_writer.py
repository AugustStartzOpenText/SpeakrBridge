from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scoping.catalog import ScopingTemplateCatalog
from scoping.word_writer import WordScopingWriter

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_ID = "open_text_fax_install_upgrade_2025_08_20"


class WordScopingWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = ScopingTemplateCatalog(base_dir=BASE_DIR).get(TEMPLATE_ID)

    def test_generate_merges_project_mode_preset_and_validates_types(self) -> None:
        writer = WordScopingWriter()
        captured: dict = {}

        def fake_run_bridge(command_name: str, payload: dict) -> dict:
            captured["command_name"] = command_name
            captured["payload"] = payload
            Path(payload["outputPath"]).touch()
            return {"outputPath": payload["outputPath"]}

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "draft.docx"
            with patch.object(writer, "_run_bridge", side_effect=fake_run_bridge):
                writer.generate(
                    template=self.template,
                    mode="upgrade",
                    values={"end_user_company_name": "Example Hospital"},
                    output_path=output_path,
                )

        self.assertEqual(captured["command_name"], "fill_template")
        mapped_values = {item["id"]: item["value"] for item in captured["payload"]["values"]}
        self.assertEqual(mapped_values["end_user_company_name"], "Example Hospital")
        self.assertFalse(mapped_values["project_type_install"])
        self.assertTrue(mapped_values["project_type_upgrade"])

    def test_generate_rejects_non_boolean_checkbox_value(self) -> None:
        writer = WordScopingWriter()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TypeError):
                writer.generate(
                    template=self.template,
                    mode="install",
                    values={"onsite_services_yes": "yes"},
                    output_path=Path(directory) / "draft.docx",
                )

    def test_generate_refuses_to_overwrite_output(self) -> None:
        writer = WordScopingWriter()
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "draft.docx"
            output_path.touch()
            with self.assertRaises(FileExistsError):
                writer.generate(
                    template=self.template,
                    mode="install",
                    values={},
                    output_path=output_path,
                )

    def test_bridge_uses_word_legacy_form_field_type_values(self) -> None:
        bridge_source = (BASE_DIR / "scripts" / "word_scoping_bridge.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('70 { return "text" }', bridge_source)
        self.assertIn('71 { return "checkbox" }', bridge_source)
        self.assertIn('83 { return "dropdown" }', bridge_source)
        self.assertNotIn('72 { return "dropdown" }', bridge_source)

    def test_bridge_avoids_generic_list_array_conversion_on_windows_powershell(self) -> None:
        bridge_source = (BASE_DIR / "scripts" / "word_scoping_bridge.ps1").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("System.Collections.Generic.List", bridge_source)

    def test_bridge_reports_manifest_compatible_collection_positions(self) -> None:
        bridge_source = (BASE_DIR / "scripts" / "word_scoping_bridge.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("index   = $Position", bridge_source)
        self.assertNotIn("index   = [int]$FormField.Index", bridge_source)


if __name__ == "__main__":
    unittest.main()
