"""Request-local observations shared by the Hub and the retrieval paths it calls."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

RecallBucket = Literal["warm", "after_write", "cold"]


@dataclass
class RecallTiming:
    embedding_seconds: float = 0.0
    # to_thread copies the context, so all routes update the same request's set.
    rebuilds: set[RecallBucket] = field(default_factory=set)

    @property
    def bucket(self) -> RecallBucket:
        if "after_write" in self.rebuilds:
            return "after_write"
        return "cold" if self.rebuilds else "warm"


_active_recall: ContextVar[RecallTiming | None] = ContextVar("recall_timing", default=None)


@contextmanager
def capture_recall_timing() -> Iterator[RecallTiming]:
    timing = RecallTiming()
    token = _active_recall.set(timing)
    try:
        yield timing
    finally:
        _active_recall.reset(token)


def record_cache_rebuild(*, after_write: bool) -> None:
    timing = _active_recall.get()
    if timing is not None:
        timing.rebuilds.add("after_write" if after_write else "cold")


@contextmanager
def measure_recall_embedding() -> Iterator[None]:
    timing = _active_recall.get()
    if timing is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        timing.embedding_seconds += time.perf_counter() - start