"""Settings persistence helpers."""

import json
import os
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field, ConfigDict

from ..config import (
    DEFAULT_TIMEOUT_MS,
    HEADLESS_DEFAULT,
    LOG_DIR,
    RECIPES_DIR,
    SCREENSHOT_DIR,
    SETTINGS_FILE,
    SNAPSHOT_DIR,
    VERSIONS_DIR,
)


class SettingsData(BaseModel):
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    headless_default: bool = Field(default=HEADLESS_DEFAULT)
    playwright_timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS)
    recipes_path: str = Field(default_factory=lambda: str(RECIPES_DIR))
    versions_path: str = Field(default_factory=lambda: str(VERSIONS_DIR))
    log_path: str = Field(default_factory=lambda: str(LOG_DIR))
    screenshots_path: str = Field(default_factory=lambda: str(SCREENSHOT_DIR))
    snapshots_path: str = Field(default_factory=lambda: str(SNAPSHOT_DIR))
    rate_limit_per_minute: int = Field(default=60)

    model_config = ConfigDict(populate_by_name=True)


class SettingsService:
    """Load and persist settings to disk."""

    def __init__(self, settings_path: Path | None = None) -> None:
        self.settings_path = settings_path or SETTINGS_FILE

    def load(self) -> SettingsData:
        if self.settings_path.exists():
            data = json.loads(self.settings_path.read_text("utf-8"))
            return SettingsData.model_validate(data)
        return SettingsData()

    def save(self, settings: SettingsData) -> SettingsData:
        payload = settings.model_dump(by_alias=True)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if settings.openai_api_key:
            os.environ["OPENAI_API_KEY"] = settings.openai_api_key
        return settings

    def update(self, updates: Dict[str, Any]) -> SettingsData:
        current = self.load()
        merged = current.model_copy(update=updates)
        return self.save(merged)


__all__ = ["SettingsData", "SettingsService"]
