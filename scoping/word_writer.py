from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, NamedTuple

from scoping.models import ProjectMode, ScopingTemplate

LOGGER = logging.getLogger(__name__)


class WordScopingGenerationResult(NamedTuple):
    output_path: Path
    warnings: list[str]


class WordScopingWriter:
    """Validate and populate legacy Word form fields through Microsoft Word COM."""

    COMMAND_TIMEOUT_SECONDS = 120
    LEGACY_TEXT_FIELD_MAX_CHARS = 255

    def __init__(self, script_path: str | Path | None = None) -> None:
        self._script_path = (
            Path(script_path).resolve()
            if script_path is not None
            else Path(__file__).resolve().parent.parent / "scripts" / "word_scoping_bridge.ps1"
        )

    def inspect(self, template: ScopingTemplate) -> dict[str, Any]:
        source_path = template.validate_source()
        return self._run_bridge(
            "inspect_template",
            {
                "templatePath": str(source_path),
                "expectedFieldCount": template.expected_field_count,
                "expectedTypeCounts": template.expected_type_counts,
            },
        )

    def generate(
        self,
        *,
        template: ScopingTemplate,
        mode: ProjectMode,
        values: dict[str, str | bool],
        output_path: str | Path,
    ) -> WordScopingGenerationResult:
        source_path = template.validate_source()

        destination = Path(output_path).resolve()
        if destination.suffix.lower() != ".docx":
            raise ValueError("Generated scoping documents must use the .docx extension")
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite generated document: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)

        merged_values = template.mode(mode).preset_values | values
        field_values: list[dict[str, Any]] = []
        warnings: list[str] = []
        for field_id, value in merged_values.items():
            field = template.field(field_id)
            if field.type == "checkbox" and not isinstance(value, bool):
                raise TypeError(f"Checkbox field {field_id!r} requires a boolean value")
            if field.type != "checkbox" and not isinstance(value, str):
                raise TypeError(f"{field.type.title()} field {field_id!r} requires a string value")
            if mode not in field.applies_to and value not in (False, ""):
                raise ValueError(f"Field {field_id!r} does not apply to {mode} projects")
            if field.type == "text":
                value, warning = self._truncate_legacy_text_value(field.id, value)
                if warning:
                    warnings.append(warning)
            field_values.append(
                {
                    "id": field.id,
                    "index": field.word_index,
                    "type": field.type,
                    "value": value,
                }
            )

        result = self._run_bridge(
            "fill_template",
            {
                "templatePath": str(source_path),
                "outputPath": str(destination),
                "expectedFieldCount": template.expected_field_count,
                "expectedTypeCounts": template.expected_type_counts,
                "values": field_values,
            },
        )
        generated_path = Path(str(result.get("outputPath", destination))).resolve()
        if not generated_path.is_file():
            raise RuntimeError(f"Word bridge did not create the expected document: {generated_path}")
        return WordScopingGenerationResult(output_path=generated_path, warnings=warnings)

    def _truncate_legacy_text_value(self, field_id: str, value: str) -> tuple[str, str | None]:
        if len(value) <= self.LEGACY_TEXT_FIELD_MAX_CHARS:
            return value, None

        limit = self.LEGACY_TEXT_FIELD_MAX_CHARS
        truncated = value[: limit - 3].rstrip() + "..."
        warning = (
            f"Truncated field {field_id!r} from {len(value)} to {len(truncated)} characters "
            "for legacy Word form compatibility"
        )
        LOGGER.warning(
            "Truncated scoping field %s from %d to %d characters for legacy Word form compatibility",
            field_id,
            len(value),
            len(truncated),
        )
        return truncated, warning

    def _run_bridge(self, command_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("Scoping document generation requires Microsoft Word on Windows.")
        if not self._script_path.is_file():
            raise RuntimeError(f"Missing Word scoping bridge script: {self._script_path}")

        payload_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
                payload_path = Path(handle.name)
                json.dump(payload, handle)

            result = subprocess.run(
                [
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
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or command_name
                raise RuntimeError(f"Word scoping bridge failed: {detail}")
            if not result.stdout.strip():
                return {}
            parsed = json.loads(result.stdout)
            if not isinstance(parsed, dict):
                raise RuntimeError(f"Unexpected Word bridge response: {parsed!r}")
            return parsed
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Word scoping bridge timed out after {self.COMMAND_TIMEOUT_SECONDS} seconds. "
                "Word may be waiting on a dialog."
            ) from exc
        finally:
            if payload_path is not None:
                payload_path.unlink(missing_ok=True)
