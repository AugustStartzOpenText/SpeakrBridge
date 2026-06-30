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
from user_settings import UserDestination, UserSettingsStore


@dataclass(frozen=True)
class OneNoteSection:
    notebook_id: str
    notebook_name: str
    section_id: str
    section_name: str
    path: str


@dataclass(frozen=True)
class OneNoteWriteResult:
    page_id: str
    section: OneNoteSection


class OneNoteWriter:
    def __init__(self, config: OneNoteConfig) -> None:
        self._config = config
        self._script_path = Path(__file__).resolve().parent / "scripts" / "onenote_bridge.ps1"
        self._settings = UserSettingsStore()

    def write_page(self, page: OneNotePageContent) -> OneNoteWriteResult:
        section = self._resolve_section()
        return self.write_page_to_section(page, section)

    def write_page_to_section(self, page: OneNotePageContent, section: OneNoteSection) -> OneNoteWriteResult:
        payload = self._run_bridge(
            "create_page",
            {
                "sectionId": section.section_id,
                "title": page.title,
                "pageXmlBody": page.page_xml_body,
            },
        )
        return OneNoteWriteResult(page_id=str(payload.get("pageId", "")), section=section)

    def write_fallback_file(self, page: OneNotePageContent) -> Path:
        path = Path(tempfile.gettempdir()) / f"{self._safe_file_name(page.title)}.xml"
        path.write_text(page.page_xml_body, encoding="utf-8")
        return path

    def _resolve_section(self) -> OneNoteSection:
        sections = self.list_sections()
        saved = self._resolve_saved_section(sections)
        if saved is not None:
            return saved

        for section in sections:
            if (
                section.notebook_name == self._config.notebook
                and section.section_name == self._config.section
            ):
                self._save_destination(section)
                return section

        notebook_id = self._resolve_notebook_id(sections)
        created = self._run_bridge(
            "create_section",
            {
                "notebookId": notebook_id,
                "sectionName": self._config.section,
            },
        )
        section = OneNoteSection(
            notebook_id=str(created.get("notebookId", notebook_id)),
            notebook_name=str(created.get("notebookName", self._config.notebook)),
            section_id=str(created.get("sectionId", "")),
            section_name=str(created.get("sectionName", self._config.section)),
            path=str(created.get("path", f"{self._config.notebook} / {self._config.section}")),
        )
        self._save_destination(section)
        return section

    def get_saved_destination(self) -> OneNoteSection | None:
        destination = self._settings.load().destination
        if destination is None:
            return None
        return OneNoteSection(
            notebook_id=destination.notebook_id,
            notebook_name=destination.notebook_name,
            section_id=destination.section_id,
            section_name=destination.section_name,
            path=destination.path,
        )

    def set_destination(self, section_id: str) -> OneNoteSection:
        for section in self.list_sections():
            if section.section_id == section_id:
                self._save_destination(section)
                return section
        raise RuntimeError(f"Section not found: {section_id}")

    def _resolve_saved_section(self, sections: list[OneNoteSection]) -> OneNoteSection | None:
        saved = self.get_saved_destination()
        if saved is None:
            return None
        for section in sections:
            if section.section_id == saved.section_id:
                self._save_destination(section)
                return section
        return None

    def _save_destination(self, section: OneNoteSection) -> None:
        self._settings.save_destination(
            UserDestination(
                notebook_id=section.notebook_id,
                notebook_name=section.notebook_name,
                section_id=section.section_id,
                section_name=section.section_name,
                path=section.path,
            )
        )

    @staticmethod
    def format_section_choice(section: OneNoteSection) -> str:
        return f"{section.path} [{section.section_id}]"

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
