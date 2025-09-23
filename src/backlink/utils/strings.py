"""String helpers."""

from __future__ import annotations

import re
from typing import Iterable

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, delimiter: str = "-") -> str:
    """Return a filesystem-friendly slug."""

    value = value.lower().strip()
    value = _slug_re.sub(delimiter, value)
    value = re.sub(rf"{delimiter}+", delimiter, value)
    return value.strip(delimiter)


def join_non_empty(values: Iterable[str], *, sep: str = " ") -> str:
    """Join iterable of strings skipping empty values."""

    return sep.join([v for v in values if v])

__all__ = ["slugify", "join_non_empty"]
