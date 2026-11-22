"""Native notifications wake the local DSH scanner; its cursor owns progress."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from watchfiles import Change, awatch

from memhub.errors import IdempotencyConflictError
from memhub.hub.config import CursorStore, format_time
from memhub.hub.sources import (
    DSH_TRANSCRIPT_NAME,
    TranscriptError,
    default_source_roots,
    scan_file,
)


class DshCapture:
    def __init__(
        self, hub_dir: Path, submit: Callable[..., Awaitable[dict[str, Any]]],
        *, excluded_projects: str = "",
    ) -> None:
        self._hub_dir = hub_dir
        self._source_root = default_source_roots()["dsh"].resolve()
        self._submit = submit
        self._excluded_projects = excluded_projects
        self._stop = asyncio.Event()
        self._state = "starting"
        self._last_at: float | None = None
        self._watch_error: str | None = None
        self._file_errors: dict[Path, str] = {}
        self._task = asyncio.create_task(self._run(), name="memhub-dsh-capture")

    def status(self) -> dict[str, Any]:
        return {
            "mode": "watch",
            "state": self._state,
            "last_at": format_time(self._last_at),
            "last_error": self._watch_error or next(iter(self._file_errors.values()), None),
        }

    async def close(self) -> None:
        self._stop.set()
        await self._task

    async def _run(self) -> None:
        if not self._source_root.is_dir():
            self._state = "missing"
            self._watch_error = "DSH sessions directory does not exist"
            return
        core_loop = asyncio.get_running_loop()
        first = True
        try:
            # The first batch (even a timeout) proves the native watch is installed.
            # Do the one-time catch-up then, so a write during startup is not lost.
            async for changes in awatch(
                self._source_root, watch_filter=None, force_polling=False,
                debounce=350, step=80, rust_timeout=500, yield_on_timeout=True,
                stop_event=self._stop,
            ):
                if first or changes:
                    written, errors = await asyncio.to_thread(
                        self._scan, core_loop, changes, first,
                    )
                    for path, error in errors.items():
                        if error is None:
                            self._file_errors.pop(path, None)
                        else:
                            self._file_errors[path] = error
                    if written:
                        self._last_at = time.time()
                    self._state = "watching"
                    first = False
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            self._state = "failed"
            self._watch_error = f"DSH file watch failed: {type(exc).__name__}"

    def _scan(
        self, core_loop: asyncio.AbstractEventLoop, changes: set[tuple[Change, str]],
        catch_up: bool,
    ) -> tuple[int, dict[Path, str | None]]:
        # Decompression, cursor I/O and the interprocess flock stay off the Core
        # loop. A CLI scan may hold that lock while waiting for this Core to reply.
        root = self._source_root
        paths = set(root.rglob(DSH_TRANSCRIPT_NAME)) if catch_up else set()
        for change, name in changes:
            path = Path(name)
            if not path.resolve().is_relative_to(root):
                continue
            if path.name == DSH_TRANSCRIPT_NAME:
                paths.add(path)
            elif change == Change.added and path.is_dir():
                paths.update(path.rglob(DSH_TRANSCRIPT_NAME))

        submit = self._submit

        class Writer:
            async def write(self, **payload: Any) -> dict[str, Any]:
                result = asyncio.run_coroutine_threadsafe(submit(**payload), core_loop)
                return await asyncio.wrap_future(result)

        async def run() -> tuple[int, dict[Path, str | None]]:
            written = 0
            errors: dict[Path, str | None] = {}
            writer = Writer()
            with CursorStore(self._hub_dir) as cursors:
                for path in sorted(paths):
                    if not path.is_file() or not path.resolve().is_relative_to(root):
                        errors[path] = None
                        continue
                    try:
                        written += await scan_file(
                            writer, source="dsh", path=path, cursors=cursors,
                            excluded_projects=self._excluded_projects,
                        )
                    except (TranscriptError, OSError, sqlite3.Error, IdempotencyConflictError) as exc:
                        # Isolate one bad transcript and don't expose its payload in status.
                        errors[path] = f"{path.parent.name}: {type(exc).__name__}"
                    else:
                        errors[path] = None
            return written, errors

        return asyncio.run(run())