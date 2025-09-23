"""Logging helpers for the Backlink bot."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from .. import config


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"


def get_logger(name: str, log_file: Optional[Path] = None) -> logging.Logger:
    """Return a configured logger.

    Parameters
    ----------
    name:
        Name of the logger.
    log_file:
        Optional path for a file handler. If not provided the default
        log directory is used and a log file is created with the logger name.
    """

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    log_file = log_file or (config.LOG_DIR / f"{name.replace('.', '_')}.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=config.MAX_LOG_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
