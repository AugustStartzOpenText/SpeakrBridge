from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from scoping.catalog import ScopingTemplateCatalog

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_ID = "open_text_fax_install_upgrade_2025_08_20"


class ScopingTemplateCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ScopingTemplateCatalog(base_dir=BASE_DIR)
        self.template = self.catalog.get(TEMPLATE_ID)

    def test_open_text_fax_manifest_maps_all_legacy_fields(self) -> None:
        self.assertEqual(self.template.expected_field_count, 67)
        self.assertEqual(len(self.template.fields), 67)
        self.assertEqual(
            self.template.expected_type_counts,
            {"text": 25, "checkbox": 41, "dropdown": 1},
        )
        self.assertEqual(
            [field.word_index for field in self.template.fields],
            list(range(1, 68)),
        )

    def test_install_and_upgrade_modes_set_mutually_exclusive_project_type(self) -> None:
        self.assertEqual(
            self.template.mode("install").preset_values,
            {"project_type_install": True, "project_type_upgrade": False},
        )
        self.assertEqual(
            self.template.mode("upgrade").preset_values,
            {"project_type_install": False, "project_type_upgrade": True},
        )

    def test_source_document_matches_versioned_manifest_hash(self) -> None:
        self.assertEqual(
            self.template.validate_source(),
            BASE_DIR / "working" / "ScopingForms" / "OpenText_Fax_Quoting_Worksheet.doc",
        )

    def test_manifest_defines_business_answers_for_both_modes(self) -> None:
        self.assertEqual(len(self.template.answers), 36)
        self.assertEqual(len(self.template.extractable_answers("install")), 32)
        self.assertEqual(len(self.template.extractable_answers("upgrade")), 35)
        self.assertEqual(len(self.template.derivation_rules), 3)
        self.assertEqual(
            self.template.derivation_rules[1].match_any,
            ["Office 365", "Microsoft 365", "O365", "M365"],
        )

    def test_catalog_rejects_incomplete_field_mapping(self) -> None:
        manifest = json.loads(self.template.manifest_path.read_text(encoding="utf-8"))
        manifest["fields"].pop()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            broken_catalog = ScopingTemplateCatalog(base_dir=BASE_DIR, manifests_dir=directory)
            with self.assertRaises(ValidationError):
                broken_catalog.list_templates()


if __name__ == "__main__":
    unittest.main()
