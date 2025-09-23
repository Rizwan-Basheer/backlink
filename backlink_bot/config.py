"""Central configuration and path utilities for the Backlink bot."""
from __future__ import annotations

from pathlib import Path
import os

BASE_DIR = Path(os.getenv("BACKLINK_HOME", Path(__file__).resolve().parent.parent))
DATA_DIR = BASE_DIR / "data"
RECIPES_DIR = Path(os.getenv("BACKLINK_RECIPES_DIR", DATA_DIR / "recipes"))
LOG_DIR = Path(os.getenv("BACKLINK_LOG_DIR", DATA_DIR / "logs"))
CSV_DIR = Path(os.getenv("BACKLINK_CSV_DIR", DATA_DIR / "csv"))
VERSION_DIR = Path(os.getenv("BACKLINK_VERSION_DIR", DATA_DIR / "versions"))
SCREENSHOT_DIR = Path(os.getenv("BACKLINK_SCREENSHOT_DIR", DATA_DIR / "screenshots"))
DB_PATH = Path(os.getenv("BACKLINK_DB", DATA_DIR / "backlink.db"))

for directory in (DATA_DIR, RECIPES_DIR, LOG_DIR, CSV_DIR, VERSION_DIR, SCREENSHOT_DIR):
    directory.mkdir(parents=True, exist_ok=True)

DEFAULT_ACCOUNTS_FILE = CSV_DIR / "accounts.csv"
DEFAULT_POSTS_FILE = CSV_DIR / "posts.csv"

MAX_LOG_BYTES = int(os.getenv("BACKLINK_MAX_LOG_BYTES", 5 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.getenv("BACKLINK_LOG_BACKUP_COUNT", 3))

RUN_HEADLESS = os.getenv("BACKLINK_HEADLESS", "true").lower() != "false"
