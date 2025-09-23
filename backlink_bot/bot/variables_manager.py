"""Utilities for loading variables from CSV and performing substitutions."""
from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

import pandas as pd

from .. import config
from ..utils.logging import get_logger

logger = get_logger("backlink.variables")

_PLACEHOLDER_RE = re.compile(r"{{\s*(?P<name>[a-zA-Z0-9_\.]+)\s*}}")


class VariablesManager:
    """Load CSV data sources and substitute placeholders."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or config.CSV_DIR
        self._cache: Dict[str, List[Dict[str, str]]] = {}
        self._iterators: Dict[str, Iterator[Dict[str, str]]] = {}

    def _load_csv(self, filename: str) -> List[Dict[str, str]]:
        path = self.base_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")
        if path.suffix.lower() not in {".csv", ".tsv"}:
            raise ValueError("Only CSV/TSV files are supported")
        delimiter = "," if path.suffix.lower() == ".csv" else "\t"
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, delimiter=delimiter)
        records = frame.to_dict(orient="records")
        logger.info("Loaded %s records from %s", len(records), path)
        return records

    def get_dataset(self, name: str) -> List[Dict[str, str]]:
        if name not in self._cache:
            self._cache[name] = self._load_csv(name)
        return self._cache[name]

    def _get_iterator(self, name: str) -> Iterator[Dict[str, str]]:
        if name not in self._iterators:
            dataset = self.get_dataset(name)
            if not dataset:
                raise ValueError(f"Dataset {name} is empty")
            self._iterators[name] = itertools.cycle(dataset)
        return self._iterators[name]

    def next_record(self, name: str) -> Dict[str, str]:
        iterator = self._get_iterator(name)
        record = next(iterator)
        logger.debug("Rotated %s -> %s", name, record)
        return record

    def substitute(self, payload: Dict[str, str], runtime: Optional[Dict[str, object]] = None) -> Dict[str, str]:
        runtime = runtime or {}
        substituted = {}
        for key, value in payload.items():
            substituted[key] = self._replace(value, runtime)
        return substituted

    def substitute_in_actions(
        self,
        actions: Iterable[Dict[str, str]],
        datasets: Optional[Dict[str, str]] = None,
        runtime: Optional[Dict[str, object]] = None,
    ) -> List[Dict[str, str]]:
        runtime = runtime or {}
        datasets = datasets or {}
        dataset_values: Dict[str, Dict[str, str]] = {
            name: self.next_record(filename) for name, filename in datasets.items()
        }
        context = {**runtime, **dataset_values}

        substituted: List[Dict[str, str]] = []
        for action in actions:
            substituted_action = {}
            for key, value in action.items():
                if isinstance(value, str):
                    substituted_action[key] = self._replace(value, context)
                else:
                    substituted_action[key] = value
            substituted.append(substituted_action)
        return substituted

    def _replace(self, value: str, runtime: Dict[str, object]) -> str:
        def _replace_match(match: re.Match[str]) -> str:
            name = match.group("name")
            if "." in name:
                dataset, field = name.split(".", 1)
                record = runtime.get(dataset)
                if isinstance(record, dict) and field in record:
                    return str(record[field])
            if name in runtime:
                return str(runtime[name])
            return match.group(0)

        return _PLACEHOLDER_RE.sub(_replace_match, value)


__all__ = ["VariablesManager"]
