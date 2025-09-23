"""Utility helpers for working with variable placeholders."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

import pandas as pd

from ..config import DATA_DIR, VARIABLE_DIR

_PLACEHOLDER_RE = re.compile(r"{{\s*(?P<key>[a-zA-Z0-9_\.]+)\s*}}")
_STATE_FILE = DATA_DIR / "rotation_state.json"


@dataclass
class VariableSource:
    name: str
    path: Path
    description: str | None = None


class VariablesManager:
    """Load CSV files and provide round-robin access to records."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or VARIABLE_DIR
        self._state: Dict[str, int] = self._load_state()

    # CSV management -----------------------------------------------------
    def list_sources(self) -> list[VariableSource]:
        sources: list[VariableSource] = []
        for path in sorted(self.base_dir.glob("*.csv")):
            sources.append(VariableSource(name=path.stem, path=path))
        return sources

    def load_table(self, name: str) -> pd.DataFrame:
        path = self._resolve_path(name)
        if not path.exists():
            raise FileNotFoundError(f"variable file '{name}' not found")
        return pd.read_csv(path)

    def get_next_record(
        self,
        name: str,
        *,
        filter_by: Mapping[str, Any] | None = None,
        rotation_key: str | None = None,
    ) -> dict[str, Any]:
        table = self.load_table(name)
        df = table
        if filter_by:
            for key, value in filter_by.items():
                df = df[df[key] == value]
        if df.empty:
            raise ValueError(f"no rows available in '{name}' after applying filters")

        key = rotation_key or name
        index = self._next_index(key, len(df))
        row = df.iloc[index]
        return row.to_dict()

    # Placeholder helpers ------------------------------------------------
    @staticmethod
    def substitute_placeholders(text: str, variables: Mapping[str, Any]) -> str:
        def replacer(match: re.Match[str]) -> str:
            key = match.group("key")
            value = VariablesManager._lookup_variable(key, variables)
            return str(value) if value is not None else match.group(0)

        return _PLACEHOLDER_RE.sub(replacer, text)

    def apply_to_payload(self, payload: Mapping[str, Any], variables: Mapping[str, Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str):
                resolved[key] = self.substitute_placeholders(value, variables)
            else:
                resolved[key] = value
        return resolved

    @staticmethod
    def _lookup_variable(key: str, variables: Mapping[str, Any]) -> Any:
        if key.startswith("env."):
            env_key = key.split(".", 1)[1]
            return os.getenv(env_key)
        parts = key.split(".")
        current: Any = variables
        for part in parts:
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                return variables.get(key)
        return current

    # Internal -----------------------------------------------------------
    def _resolve_path(self, name: str) -> Path:
        path = Path(name)
        if not path.suffix:
            path = self.base_dir / f"{name}.csv"
        elif not path.is_absolute():
            path = self.base_dir / path
        return path

    def _next_index(self, key: str, length: int) -> int:
        index = self._state.get(key, 0)
        self._state[key] = (index + 1) % length
        self._save_state()
        return index

    def _load_state(self) -> Dict[str, int]:
        if _STATE_FILE.exists():
            with _STATE_FILE.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        return {}

    def _save_state(self) -> None:
        with _STATE_FILE.open("w", encoding="utf-8") as fh:
            json.dump(self._state, fh, indent=2)


__all__ = ["VariablesManager", "VariableSource"]
