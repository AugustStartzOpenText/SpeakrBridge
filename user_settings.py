from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError


class UserDestination(BaseModel):
    notebook_id: str
    notebook_name: str
    section_id: str
    section_name: str
    path: str


class UserSettings(BaseModel):
    destination: UserDestination | None = None


class UserSettingsStore:
    def __init__(self, path: str | Path = "user_settings.json") -> None:
        self._path = Path(path)

    def load(self) -> UserSettings:
        if not self._path.exists():
            return UserSettings()

        raw = json.loads(self._path.read_text(encoding="utf-8"))
        try:
            return UserSettings.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"Invalid user settings in {self._path}: {exc}") from exc

    def save(self, settings: UserSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = settings.model_dump(mode="json")
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self._path.parent,
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")

        temp_path.replace(self._path)

    def save_destination(self, destination: UserDestination) -> None:
        self.save(UserSettings(destination=destination))
