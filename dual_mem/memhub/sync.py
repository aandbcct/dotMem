# -*- coding: utf-8 -*-
"""Replicate one personal memory across devices through the store's change log.

Every device keeps a full local store and answers recall from it. What travels
is the store's replication log (``ChromaVectorStore.export_log``): committed
version plans, raw evidence and patches, with their embeddings, each tagged
``(origin, seq)``. A receiver applies them without any model call.

Backends only move log entries:

- ``dir``: a folder (iCloud Drive, Dropbox, Syncthing, a NAS mount). Each store
  writes ``log/<origin>/<seq>.json`` under its own origin only, so two devices
  never write the same file.
- ``git``: the same layout in a git repository; every device commits only
  ``log/`` and ``manifest.json``. Scripts, README and other files in that
  repository are never read into the store and are never included in a sync
  commit. A rebase does not conflict, because each device writes only its own
  origin directory.
- ``http``: another MemHub Core (``/v1/hub/log/*``), which relays every origin it
  has received.
- ``ssh``: the same Core API, each call one ``ssh <target> dual-mem hub relay``
  command. Nothing stays connected between syncs.

``manifest.json`` (dir / git) and ``/v1/hub/log/heads`` (http / ssh) carry the
embedding model and dimension: replicas share vectors, so a mismatch is refused.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from dual_mem.memhub.config import SyncConfig, hub_root
from dual_mem.storage.vector_store import ChromaVectorStore

PAGE = 200
_SEQ_WIDTH = 12


class SyncError(RuntimeError):
    """The sync target is unreachable, incompatible or rejected the change."""


class SyncBackend(Protocol):
    def open(self, embed: dict[str, Any], origin: str) -> None:
        """Check compatibility (and fetch remote state) before a cycle."""

    def heads(self) -> dict[str, int]:
        """Per origin, the contiguous sequence the target holds."""

    def push(self, entries: list[dict]) -> None: ...

    def fetch(self, after: dict[str, int], limit: int) -> list[dict]: ...

    def close(self) -> None:
        """Publish what was pushed (a git commit) after a cycle."""

    def digester(self) -> str:
        """Origin of the one store that runs System 2 digest; "" = the remote Core."""


def _check_embed(theirs: dict[str, Any], ours: dict[str, Any], where: str) -> None:
    if theirs != ours:
        raise SyncError(
            f"{where} holds vectors from embedding {theirs}, this device uses {ours}; "
            "replicas must use the same embed_model and embed_dim"
        )


class DirBackend:
    """A shared folder; each origin owns ``log/<origin>/``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def manifest(self) -> dict[str, Any] | None:
        path = self.manifest_path()
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def open(self, embed: dict[str, Any], origin: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        manifest = self.manifest()
        if manifest is None:
            # The first device to sync runs digest for everyone: two digesters
            # would each induce their own schemas from the same facts.
            manifest = {"format": 1, "embed": embed, "digester": origin}
            _write_json(self.manifest_path(), manifest)
        _check_embed(manifest["embed"], embed, str(self.root))
        self._digester = manifest["digester"]

    def digester(self) -> str:
        return self._digester

    def _seqs(self, origin_dir: Path) -> list[int]:
        return sorted(
            int(path.stem) for path in origin_dir.glob("*.json") if path.stem.isdigit()
        )

    def heads(self) -> dict[str, int]:
        heads: dict[str, int] = {}
        log = self.root / "log"
        if not log.is_dir():
            return heads
        for origin_dir in log.iterdir():
            if not origin_dir.is_dir():
                continue
            head = 0
            for seq in self._seqs(origin_dir):
                if seq != head + 1:
                    break
                head = seq
            heads[origin_dir.name] = head
        return heads

    def push(self, entries: list[dict]) -> None:
        for entry in entries:
            path = self.root / "log" / entry["origin"] / f"{entry['seq']:0{_SEQ_WIDTH}d}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(path, entry)

    def fetch(self, after: dict[str, int], limit: int) -> list[dict]:
        out: list[dict] = []
        log = self.root / "log"
        if not log.is_dir():
            return out
        for origin_dir in sorted(log.iterdir()):
            if not origin_dir.is_dir():
                continue
            start = after.get(origin_dir.name, 0)
            for seq in self._seqs(origin_dir):
                if seq <= start:
                    continue
                path = origin_dir / f"{seq:0{_SEQ_WIDTH}d}.json"
                out.append(json.loads(path.read_text(encoding="utf-8")))
                if len(out) >= limit:
                    return out
        return out

    def close(self) -> None:
        return None


def _write_json(path: Path, data: Any) -> None:
    # A synced folder must never upload a half-written file.
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class GitBackend(DirBackend):
    """``DirBackend`` in a local clone; ``open`` pulls, ``close`` commits and pushes."""

    BRANCH = "main"

    def __init__(self, url: str, checkout: Path) -> None:
        super().__init__(checkout)
        self.url = url

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=300
        )
        if check and result.returncode != 0:
            raise SyncError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result

    def _remote_has_branch(self) -> bool:
        return bool(self._git("ls-remote", "--heads", "origin", self.BRANCH).stdout.strip())

    def _pull(self) -> None:
        if self._remote_has_branch():
            # Log files never collide (one directory per origin). Only manifest.json
            # can, when two devices start on an empty repository: the one pushed
            # first wins (``-X ours`` is the upstream side during a rebase).
            # --autostash keeps a local edit to a script or README from blocking
            # the memory pull. close() never stages those files.
            self._git(
                "pull", "--rebase", "--autostash", "-X", "ours", "--quiet", "origin", self.BRANCH
            )

    def open(self, embed: dict[str, Any], origin: str) -> None:
        if not (self.root / ".git").is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
            self._git("init", "--quiet")
            self._git("remote", "add", "origin", self.url)
            self._git("checkout", "--quiet", "-B", self.BRANCH)
        self._pull()
        super().open(embed, origin)

    def close(self) -> None:
        # Only the replication log is ours. A word-cloud script, README or
        # image living in the same repository must not be committed or imported.
        paths = [name for name in ("log", "manifest.json") if (self.root / name).exists()]
        if not paths:
            return
        self._git("add", "--", *paths)
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return
        self._git(
            "-c", "user.name=dual-mem", "-c", "user.email=dual-mem@localhost",
            "commit", "--quiet", "-m", "dual-mem sync",
        )
        if self._git("push", "--quiet", "origin", f"HEAD:{self.BRANCH}", check=False).returncode:
            # Another device pushed first; its files are in other directories.
            self._pull()
            self._git("push", "--quiet", "origin", f"HEAD:{self.BRANCH}")


class CoreBackend:
    """Another MemHub Core reached through ``call(method, path, body)``."""

    def __init__(self, call) -> None:
        self._call = call

    def open(self, embed: dict[str, Any], origin: str) -> None:
        info = self._call("GET", "/v1/hub/log/heads", None)
        _check_embed(info["embed"], embed, "the remote Core")
        self._heads = info["heads"]

    def heads(self) -> dict[str, int]:
        return self._heads

    def push(self, entries: list[dict]) -> None:
        self._call("POST", "/v1/hub/log/import", {"entries": entries})

    def fetch(self, after: dict[str, int], limit: int) -> list[dict]:
        return self._call("POST", "/v1/hub/log/export", {"after": after, "limit": limit})[
            "entries"
        ]

    def close(self) -> None:
        return None

    def digester(self) -> str:
        return ""


def http_call(base_url: str, api_key: str, *, client: httpx.Client | None = None):
    http = client or httpx.Client(base_url=base_url, timeout=120)
    headers = {"Authorization": f"Bearer {api_key}"}

    def call(method: str, path: str, body: Any) -> Any:
        try:
            response = http.request(method, path, json=body, headers=headers)
        except httpx.TransportError as exc:
            raise SyncError(f"{base_url} unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise SyncError(f"{base_url}{path}: HTTP {response.status_code} {response.text}")
        return response.json()

    return call


def ssh_call(target: str, *, ssh_args: list[str], remote_command: str, run=subprocess.run):
    """Each call is one ``ssh`` command; the server's CLI relays it to its own Core."""

    def call(method: str, path: str, body: Any) -> Any:
        remote = f"{remote_command} hub relay {shlex.quote(method)} {shlex.quote(path)}"
        result = run(
            ["ssh", "-o", "BatchMode=yes", *ssh_args, target, remote],
            input=json.dumps(body, ensure_ascii=False) if body is not None else "",
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise SyncError(f"ssh {target}: {result.stderr.strip()}")
        return json.loads(result.stdout)

    return call


def make_backend(cfg: SyncConfig, hub_dir: str | Path | None = None) -> SyncBackend:
    if cfg.kind == "dir":
        return DirBackend(Path(cfg.target).expanduser())
    if cfg.kind == "git":
        return GitBackend(cfg.target, hub_root(hub_dir) / "sync-git")
    if cfg.kind == "http":
        return CoreBackend(http_call(cfg.target, cfg.api_key))
    if cfg.kind == "ssh":
        return CoreBackend(
            ssh_call(cfg.target, ssh_args=cfg.ssh_args, remote_command=cfg.remote_command)
        )
    raise SyncError(f"unknown sync kind {cfg.kind!r}")


def sync_once(
    store: ChromaVectorStore, backend: SyncBackend, *, embed: dict[str, Any]
) -> dict[str, Any]:
    """Push this store's own entries, then pull every other origin's."""
    backend.open(embed, store.origin)
    try:
        pushed = 0
        remote = backend.heads().get(store.origin, 0)
        while True:
            entries = store.export_log({store.origin: remote}, origin=store.origin, limit=PAGE)
            if not entries:
                break
            backend.push(entries)
            pushed += len(entries)
            remote = entries[-1]["seq"]
        received = applied = 0
        result: dict[str, Any] = {"pending": 0}
        scopes: set[tuple[str, str, str]] = set()
        while True:
            entries = [
                e for e in backend.fetch(store.log_heads(), PAGE) if e["origin"] != store.origin
            ]
            if not entries:
                break
            result = store.import_log(entries)
            received += result["received"]
            applied += result["applied"]
            scopes.update(tuple(scope) for scope in result["scopes"])
            if not result["received"]:
                # Only entries behind a gap are left; they wait for the missing one.
                break
        digester = backend.digester()
    finally:
        backend.close()
    return {
        "pushed": pushed,
        "received": received,
        "applied": applied,
        "pending": result["pending"],
        "scopes": sorted(scopes),
        "digester": digester,
    }


class HubSyncer:
    """The Core's replication loop: one cycle at a time, off the event loop.

    A cycle runs after local writes (debounced), on a timer, and when recall finds
    the last pull older than ``pull_seconds``; recall itself never waits for it.
    Received facts are queued for System 2 only on the device that digests, and
    received schemas and evidence edges are projected into the local graph.
    """

    def __init__(
        self,
        client: Any,
        cfg: SyncConfig,
        *,
        hub_dir: str | Path | None = None,
        backend: SyncBackend | None = None,
        interval_seconds: float = 300.0,
        pull_seconds: float = 60.0,
        debounce_seconds: float = 2.0,
    ) -> None:
        self.client = client
        self.cfg = cfg
        self.backend = backend or make_backend(cfg, hub_dir)
        self.interval = interval_seconds
        self.pull_seconds = pull_seconds
        self.debounce = debounce_seconds
        self.digester: str | None = None
        self.last_at: float | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def embed(self) -> dict[str, Any]:
        settings = self.client.settings
        return {"model": settings.embed_model, "dim": settings.embed_dim}

    def digests_here(self) -> bool:
        """Whether System 2 runs on this device (unknown before the first sync)."""
        return self.digester == self.client.factory.vector.origin

    async def run_once(self) -> dict[str, Any]:
        async with self._lock:
            try:
                result = await asyncio.to_thread(
                    sync_once, self.client.factory.vector, self.backend, embed=self.embed
                )
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                raise
            self.digester = result["digester"]
            self.last_at = time.time()
            self.last_error = None
            self.last_result = {k: v for k, v in result.items() if k != "scopes"}
        await self.after_import([tuple(scope) for scope in result["scopes"]])
        return self.last_result

    async def after_import(self, scopes: list[tuple[str, str, str]]) -> None:
        await project_received(self.client)
        if self.client.settings.mode == "dual" and self.digests_here():
            for app_id, user_id, agent_id in scopes:
                self.client.factory.cache.enqueue_s2_task(
                    user_id=user_id, app_id=app_id, agent_id=agent_id
                )

    def kick(self) -> None:
        """Ask for a cycle soon (after a local write)."""
        self._wake.set()

    def pull_if_stale(self) -> None:
        if self.last_at is None or time.time() - self.last_at >= self.pull_seconds:
            self._wake.set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="dual-mem-hub-sync")
        self._wake.set()

    async def _loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.wait_for(self._wake.wait(), self.interval)
                # Let a burst of writes land in one cycle.
                await asyncio.sleep(self.debounce)
            except TimeoutError:
                pass
            if self._closed:
                return
            self._wake.clear()
            try:
                await self.run_once()
            except Exception:
                # Recorded in last_error; the next trigger retries. Offline is normal.
                continue

    async def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def status(self) -> dict[str, Any]:
        return {
            **self.cfg.public_dict(),
            "origin": self.client.factory.vector.origin,
            "digester": self.digester,
            "digests_here": self.digests_here(),
            "last_at": self.last_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }


async def project_received(client: Any) -> None:
    """Project committed schemas and evidence edges not yet in the local graph."""
    if client.factory.graph is None:
        return
    from dual_mem.system2.system2_agent import System2Agent

    agent = System2Agent(factory=client.factory)
    for scope in client.factory.vector.projection_scopes():
        await agent.project_committed(scope)


__all__ = [
    "CoreBackend",
    "DirBackend",
    "GitBackend",
    "HubSyncer",
    "SyncBackend",
    "SyncError",
    "http_call",
    "make_backend",
    "project_received",
    "ssh_call",
    "sync_once",
]
