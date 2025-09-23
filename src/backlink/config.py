"""Application-wide configuration helpers.

This module centralises filesystem paths used by the bot engine, CLI and
administration panel. Paths are created on import which keeps higher level
modules focused on domain logic rather than housekeeping.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

load_dotenv(os.getenv("BACKLINK_ENV_FILE") or None)

PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parent
PROJECT_ROOT: Final[Path] = PACKAGE_ROOT.parent
DATA_DIR: Final[Path] = Path(os.getenv("BACKLINK_DATA_DIR", PROJECT_ROOT / "data"))
RECIPES_DIR: Final[Path] = Path(os.getenv("RECIPES_DIR", DATA_DIR / "recipes"))
VERSIONS_DIR: Final[Path] = Path(os.getenv("VERSIONS_DIR", DATA_DIR / "versions"))
LOG_DIR: Final[Path] = Path(os.getenv("LOG_DIR", DATA_DIR / "logs"))
EXECUTION_DIR: Final[Path] = LOG_DIR / "executions"
VARIABLE_DIR: Final[Path] = DATA_DIR / "variables"
EXPORT_DIR: Final[Path] = DATA_DIR / "exports"
TARGET_DIR: Final[Path] = DATA_DIR / "targets"
SNAPSHOT_DIR: Final[Path] = Path(os.getenv("SNAPSHOTS_DIR", DATA_DIR / "snapshots"))
ASSET_DIR: Final[Path] = DATA_DIR / "assets"
SCREENSHOT_DIR: Final[Path] = Path(os.getenv("SCREENSHOTS_DIR", DATA_DIR / "screenshots"))
SETTINGS_FILE: Final[Path] = Path(os.getenv("BACKLINK_SETTINGS_FILE", DATA_DIR / "settings.json"))
DB_PATH: Final[Path] = DATA_DIR / "backlink.db"

for path in (
    RECIPES_DIR,
    VERSIONS_DIR,
    LOG_DIR,
    EXECUTION_DIR,
    VARIABLE_DIR,
    EXPORT_DIR,
    TARGET_DIR,
    SNAPSHOT_DIR,
    ASSET_DIR,
    SCREENSHOT_DIR,
):
    path.mkdir(parents=True, exist_ok=True)

ENVIRONMENT: Final[str] = os.getenv("APP_ENV", os.getenv("BACKLINK_ENV", "development"))
HEADLESS_DEFAULT: Final[bool] = (
    os.getenv("HEADLESS_DEFAULT")
    or os.getenv("BACKLINK_HEADLESS")
    or "true"
).lower() in {"1", "true", "yes", "on"}
DEFAULT_TIMEOUT: Final[int] = int(os.getenv("BACKLINK_TIMEOUT", "15"))
DEFAULT_TIMEOUT_MS: Final[int] = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", os.getenv("BACKLINK_TIMEOUT_MS", str(DEFAULT_TIMEOUT * 1000))))
CONTENT_CACHE_DAYS: Final[int] = int(os.getenv("BACKLINK_CONTENT_CACHE_DAYS", "7"))
TROUBLESHOOT_RETRIES: Final[int] = int(os.getenv("BACKLINK_TROUBLESHOOT_RETRIES", "2"))
DEFAULT_SECRET_KEY = "insecure-development-secret"
SECRET_KEY: Final[str] = os.getenv("SECRET_KEY", DEFAULT_SECRET_KEY)

__all__ = [
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "DATA_DIR",
    "RECIPES_DIR",
    "VERSIONS_DIR",
    "LOG_DIR",
    "EXECUTION_DIR",
    "VARIABLE_DIR",
    "EXPORT_DIR",
    "TARGET_DIR",
    "SNAPSHOT_DIR",
    "ASSET_DIR",
    "SCREENSHOT_DIR",
    "SETTINGS_FILE",
    "DB_PATH",
    "ENVIRONMENT",
    "HEADLESS_DEFAULT",
    "DEFAULT_TIMEOUT",
    "DEFAULT_TIMEOUT_MS",
    "CONTENT_CACHE_DAYS",
    "TROUBLESHOOT_RETRIES",
    "SECRET_KEY",
]
