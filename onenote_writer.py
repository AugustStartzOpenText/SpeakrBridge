from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from config import OneNoteConfig
from models import OneNotePageContent


@dataclass(frozen=True)
class OneNoteSection:
    notebook_id: str
    notebook_name: str
    section_id: str
    section_name: str
    path: str


class OneNoteWriter:
    def __init__(self, config: OneNoteConfig) -> None:
        self._config = config
        self._script_path = Path(__file__).resolve().parent / "scripts" / "onenote_bridge.ps1"

    def write_page(self, page: OneNotePageContent) -> str:
        section = self._resolve_section()
        payload = self._run_bridge(
            "create_page",
            {
                "sectionId": section.section_id,
                "title": page.title,
                "pageXmlBody": page.page_xml_body,
            },
        )
        return str(payload.get("pageId", ""))

    def write_fallback_file(self, page: OneNotePageContent) -> Path:
        path = Path(tempfile.gettempdir()) / f"{self._safe_file_name(page.title)}.xml"
        path.write_text(page.page_xml_body, encoding="utf-8")
        return path

    def _resolve_section(self) -> OneNoteSection:
        sections = self.list_sections()
        for section in sections:
            if (
                section.notebook_name == self._config.notebook
                and section.section_name == self._config.section
            ):
                return section

        notebook_id = self._resolve_notebook_id(sections)
        created = self._run_bridge(
            "create_section",
            {
                "notebookId": notebook_id,
                "sectionName": self._config.section,
            },
        )
        return OneNoteSection(
            notebook_id=str(created.get("notebookId", notebook_id)),
            notebook_name=str(created.get("notebookName", self._config.notebook)),
            section_id=str(created.get("sectionId", "")),
            section_name=str(created.get("sectionName", self._config.section)),
            path=str(created.get("path", f"{self._config.notebook} / {self._config.section}")),
        )

    def list_sections(self) -> list[OneNoteSection]:
        payload = self._run_bridge("list_sections")
        sections_raw = payload.get("sections", [])
        if not isinstance(sections_raw, list):
            return []
        sections: list[OneNoteSection] = []
        for item in sections_raw:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("sectionId", ""))
            section_name = str(item.get("sectionName", ""))
            if not section_id or not section_name:
                continue
            sections.append(
                OneNoteSection(
                    notebook_id=str(item.get("notebookId", "")),
                    notebook_name=str(item.get("notebookName", "")),
                    section_id=section_id,
                    section_name=section_name,
                    path=str(item.get("path", "")),
                )
            )
        return sections

    def _resolve_notebook_id(self, sections: list[OneNoteSection]) -> str:
        for section in sections:
            if section.notebook_name == self._config.notebook and section.notebook_id:
                return section.notebook_id
        raise RuntimeError(f"Notebook not found: {self._config.notebook}")

    def _run_bridge(self, command_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("OneNote PowerShell bridge is only available on Windows.")
        if not self._script_path.exists():
            raise RuntimeError(f"Missing PowerShell bridge script: {self._script_path}")

        payload_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
                payload_path = Path(handle.name)
                json.dump(payload or {}, handle)

            command = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self._script_path),
                "-CommandName",
                command_name,
                "-PayloadPath",
                str(payload_path),
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or f"Bridge command failed: {command_name}"
                raise RuntimeError(message)
            stdout = result.stdout.strip()
            if not stdout:
                return {}
            parsed = json.loads(stdout)
            if not isinstance(parsed, dict):
                raise RuntimeError(f"Unexpected bridge response for {command_name}: {parsed!r}")
            return parsed
        finally:
            if payload_path and payload_path.exists():
                payload_path.unlink(missing_ok=True)

    @staticmethod
    def _safe_file_name(value: str) -> str:
        return "".join(char if char.isalnum() or char in {" ", "-", "_"} else "_" for char in value).strip() or "page"
