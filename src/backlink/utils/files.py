"""File utilities for working with recipe files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

import yaml


def read_yaml(path: Union[Path, str]) -> dict[str, Any]:
    if isinstance(path, Path):
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return yaml.safe_load(path) or {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(content)

__all__ = ["read_yaml", "write_yaml", "write_text"]
