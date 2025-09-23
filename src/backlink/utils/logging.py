"""Logging helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..config import LOG_DIR

_LOGGER: Optional[logging.Logger] = None


def setup_logging(name: str = "backlink") -> logging.Logger:
    global _LOGGER
    if _LOGGER:
        return _LOGGER

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(LOG_DIR / "backlink.log")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _LOGGER = logger
    return logger


def create_execution_logger(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"execution.{path.stem}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    handler = logging.FileHandler(path)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

__all__ = ["setup_logging", "create_execution_logger"]
