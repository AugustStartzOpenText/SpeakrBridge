from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


BASE_DIR = Path(__file__).resolve().parent.parent


class ScopingCliImportTests(unittest.TestCase):
    def test_cli_startup_does_not_import_speakr_or_extraction(self) -> None:
        script = """
import importlib.abc
import sys

class RejectUnusedDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in {"scoping.extraction", "speakr_client"}:
            raise ImportError(f"unexpected eager import: {fullname}")
        return None

sys.meta_path.insert(0, RejectUnusedDependencies())
import scoping_cli
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=BASE_DIR,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
