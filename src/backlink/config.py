"""Application-wide configuration helpers.

This module centralises filesystem paths used by the bot engine, CLI and
administration panel. Paths are created on import which keeps higher level
modules focused on domain logic rather than housekeeping.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parent
PROJECT_ROOT: Final[Path] = PACKAGE_ROOT.parent
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
RECIPES_DIR: Final[Path] = DATA_DIR / "recipes"
LOG_DIR: Final[Path] = DATA_DIR / "logs"
EXECUTION_DIR: Final[Path] = DATA_DIR / "executions"
VARIABLE_DIR: Final[Path] = DATA_DIR / "variables"
EXPORT_DIR: Final[Path] = DATA_DIR / "exports"
TARGET_DIR: Final[Path] = DATA_DIR / "targets"
ASSET_DIR: Final[Path] = DATA_DIR / "assets"
SCREENSHOT_DIR: Final[Path] = DATA_DIR / "screenshots"
DB_PATH: Final[Path] = DATA_DIR / "backlink.db"

for path in (
    RECIPES_DIR,
    LOG_DIR,
    EXECUTION_DIR,
    VARIABLE_DIR,
    EXPORT_DIR,
    TARGET_DIR,
    ASSET_DIR,
    SCREENSHOT_DIR,
):
    path.mkdir(parents=True, exist_ok=True)

ENVIRONMENT: Final[str] = os.getenv("BACKLINK_ENV", "development")
HEADLESS: Final[bool] = os.getenv("BACKLINK_HEADLESS", "true").lower() == "true"
DEFAULT_TIMEOUT: Final[int] = int(os.getenv("BACKLINK_TIMEOUT", "15"))
DEFAULT_TIMEOUT_MS: Final[int] = int(os.getenv("BACKLINK_TIMEOUT_MS", str(DEFAULT_TIMEOUT * 1000)))
CONTENT_CACHE_DAYS: Final[int] = int(os.getenv("BACKLINK_CONTENT_CACHE_DAYS", "7"))
TROUBLESHOOT_RETRIES: Final[int] = int(os.getenv("BACKLINK_TROUBLESHOOT_RETRIES", "2"))

__all__ = [
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "DATA_DIR",
    "RECIPES_DIR",
    "LOG_DIR",
    "EXECUTION_DIR",
    "VARIABLE_DIR",
    "EXPORT_DIR",
    "TARGET_DIR",
    "ASSET_DIR",
    "SCREENSHOT_DIR",
    "DB_PATH",
    "ENVIRONMENT",
    "HEADLESS",
    "DEFAULT_TIMEOUT",
    "DEFAULT_TIMEOUT_MS",
    "CONTENT_CACHE_DAYS",
    "TROUBLESHOOT_RETRIES",
]
