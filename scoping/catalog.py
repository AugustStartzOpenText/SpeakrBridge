from __future__ import annotations

import json
from pathlib import Path

from scoping.models import ScopingTemplate


class ScopingTemplateCatalog:
    def __init__(
        self,
        *,
        base_dir: str | Path,
        manifests_dir: str | Path | None = None,
    ) -> None:
        self._base_dir = Path(base_dir).resolve()
        self._manifests_dir = (
            Path(manifests_dir).resolve()
            if manifests_dir is not None
            else Path(__file__).resolve().parent / "templates"
        )

    def list_templates(self) -> list[ScopingTemplate]:
        if not self._manifests_dir.exists():
            return []

        templates = [self._load_path(path) for path in sorted(self._manifests_dir.glob("*.json"))]
        ids = [template.id for template in templates]
        if len(ids) != len(set(ids)):
            raise ValueError("Scoping template catalog contains duplicate template ids")
        return templates

    def get(self, template_id: str) -> ScopingTemplate:
        for template in self.list_templates():
            if template.id == template_id:
                return template
        raise KeyError(f"Unknown scoping template: {template_id}")

    def _load_path(self, path: Path) -> ScopingTemplate:
        raw = json.loads(path.read_text(encoding="utf-8"))
        template = ScopingTemplate.model_validate(raw)
        template.manifest_path = path.resolve()
        template.base_dir = self._base_dir
        return template
