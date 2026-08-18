"""Small collection helpers shared across workflow layers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")

__all__ = ["dedupe_preserve_order"]


def dedupe_preserve_order(values: Iterable[T]) -> list[T]:
    """Return the first occurrence of each hashable value in input order."""

    seen: set[T] = set()
    result: list[T] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
