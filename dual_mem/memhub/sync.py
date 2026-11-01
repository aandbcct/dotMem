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
  ``log/``, ``snapshot/`` and ``manifest.json``. Scripts, README and other files
  in that repository are never read into the store and are never included in a
  sync commit. A rebase does not conflict, because each device writes only its
  own origin directory.
- ``http``: another MemHub Core (``/v1/hub/log/*``), which relays every origin it
  has received.
- ``ssh``: the same Core API, each call one ``ssh <target> dual-mem hub relay``
  command. Nothing stays connected between syncs.

``manifest.json`` (dir / git) and ``/v1/hub/log/heads`` (http / ssh) carry the
embedding model and dimension: replicas share vectors, so a mismatch is refused.

A log only grows, so the device that digests periodically folds it into a
snapshot: ``snapshot/state.json`` holds every live node, the tombstones and the
evidence edges, standing in for every entry up to ``base``. The log prefix is
then dropped from the target (for git, by publishing a new root commit, so the
old objects leave the clone), and a device that has not seen the snapshot
rebuilds itself from it. The target's size therefore follows the live memory,
not the number of writes ever made.
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
# Applied log entries this store lets stand on the target before folding them
# into a snapshot: roughly the write volume of a few weeks of use.
COMPACT_AFTER = 400


class SyncError(RuntimeError):
    """The sync target is unreachable, incompatible or rejected the change."""


class SyncBackend(Protocol):
    def open(self, embed: dict[str, Any], origin: str) -> None:
        """Check compatibility (and fetch remote state) before a cycle."""

    def heads(self) -> dict[str, int]:
        """Per origin, the contiguous sequence the target holds."""

    def push(self, entries: list[dict]) -> None: ...

    def fetch(self, after: dict[str, int], limit: int) -> list[dict]: ...

    def snapshot(self) -> dict | None:
        """The snapshot the target's log starts after, or None before any compaction."""
        ...

    def publish_snapshot(self, state: dict) -> bool:
        """Make ``state`` the target's snapshot and drop the log it covers.

        False when the target changed meanwhile; the next cycle tries again.
        """
        ...

    def close(self) -> None:
        """Publish what was pushed (a git commit) after a cycle."""

    def digester(self) -> str:
        """Origin of the one store that runs System 2 digest; "" = the remote Core."""


# Version of the on-target layout: vectors packed as base64 float16, and a
# snapshot that replaces the log prefix. A target in another format is refused.
FORMAT = 2


def _check_embed(theirs: dict[str, Any], ours: dict[str, Any], where: str) -> None:
    if theirs != ours:
        raise SyncError(
            f"{where} holds vectors from embedding {theirs}, this device uses {ours}; "
            "replicas must use the same embed_model and embed_dim"
        )


def _check_format(manifest: dict[str, Any], where: str) -> None:
    if manifest["format"] != FORMAT:
        raise SyncError(
            f"{where} is sync format {manifest['format']}, this dual-mem writes format "
            f"{FORMAT}; start a new target (or empty this one) and connect again"
        )


class DirBackend:
    """A shared folder; each origin owns ``log/<origin>/``, the digester owns
    ``snapshot/``."""

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
            manifest = {
                "format": FORMAT,
                "embed": embed,
                "digester": origin,
                "snapshot": None,
            }
            _write_json(self.manifest_path(), manifest)
        _check_format(manifest, str(self.root))
        _check_embed(manifest["embed"], embed, str(self.root))
        self._digester = manifest["digester"]
        self._manifest = manifest

    def digester(self) -> str:
        return self._digester

    def _seqs(self, origin_dir: Path) -> list[int]:
        return sorted(
            int(path.stem) for path in origin_dir.glob("*.json") if path.stem.isdigit()
        )

    def heads(self) -> dict[str, int]:
        """Per origin, the contiguous sequence held, counting from the snapshot."""
        snap = self._manifest["snapshot"]
        heads: dict[str, int] = dict(snap["base"]) if snap else {}
        log = self.root / "log"
        if not log.is_dir():
            return heads
        for origin_dir in log.iterdir():
            if not origin_dir.is_dir():
                continue
            head = heads.get(origin_dir.name, 0)
            for seq in self._seqs(origin_dir):
                if seq <= head:
                    continue
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

    def snapshot(self) -> dict | None:
        snap = self._manifest["snapshot"]
        if snap is None:
            return None
        state = json.loads((self.root / snap["path"]).read_text(encoding="utf-8"))
        return {"epoch": snap["epoch"], "base": snap["base"], "state": state}

    def publish_snapshot(self, state: dict) -> bool:
        """Write the snapshot, then point the manifest at it, then drop old files.

        A reader that sees the new manifest finds a complete snapshot; one that
        still sees the old manifest finds every entry it may ask for (removal
        only touches entries the new snapshot covers, after the switch).
        """
        path = f"snapshot/state-{state['epoch']:06d}.json"
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_json(target, state)
        self._manifest = {
            **self._manifest,
            "snapshot": {"epoch": state["epoch"], "base": state["base"], "path": path},
        }
        _write_json(self.manifest_path(), self._manifest)
        for old in target.parent.glob("state-*.json"):
            if old.name != target.name:
                old.unlink()
        self._drop_covered(state["base"])
        return True

    def _drop_covered(self, base: dict[str, int]) -> None:
        for name, seq in base.items():
            origin_dir = self.root / "log" / name
            if not origin_dir.is_dir():
                continue
            for old in self._seqs(origin_dir):
                if old <= seq:
                    (origin_dir / f"{old:0{_SEQ_WIDTH}d}.json").unlink()

    def close(self) -> None:
        return None


def _write_json(path: Path, data: Any) -> None:
    # A synced folder must never upload a half-written file.
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class GitBackend(DirBackend):
    """``DirBackend`` in a local clone; ``open`` pulls, ``close`` commits and pushes.

    Publishing a snapshot starts a new root commit holding only the files of the
    current one (with the covered log removed) and replaces the branch with
    ``--force-with-lease``: the old history stops being reachable, so neither
    the remote nor a fresh clone carries it. The lease fails if another device
    pushed meanwhile, and the snapshot waits for the next cycle. A device whose
    clone still has the old history adopts the new root on its next pull.
    """

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
        if not self._remote_has_branch():
            return
        self._git("fetch", "--quiet", "origin", self.BRANCH)
        remote = f"origin/{self.BRANCH}"
        born = self._git("rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0
        if not born:
            self._git("reset", "--quiet", "--hard", remote)
            return
        if self._snapshot_epoch(remote) > self._snapshot_epoch("HEAD"):
            # The remote was compacted onto a new root. Everything this clone
            # committed but did not push is in the local store's log and is
            # pushed again from there, so the clone simply follows the remote
            # and lets go of the old history.
            self._git("reset", "--quiet", "--hard", remote)
            self._forget_history()
            return
        # Log files never collide (one directory per origin). Only manifest.json
        # can, when two devices start on an empty repository: the one pushed
        # first wins (``-X ours`` is the upstream side during a rebase).
        # --autostash keeps a local edit to a script or README from blocking
        # the memory pull. close() never stages those files.
        self._git("rebase", "--autostash", "-X", "ours", "--quiet", remote)

    def _forget_history(self) -> None:
        self._git("reflog", "expire", "--expire=now", "--all", check=False)
        self._git("gc", "--quiet", "--prune=now", check=False)

    def _snapshot_epoch(self, rev: str) -> int:
        shown = self._git("show", f"{rev}:manifest.json", check=False)
        if shown.returncode:
            return 0
        snap = json.loads(shown.stdout).get("snapshot")
        return snap["epoch"] if snap else 0

    def open(self, embed: dict[str, Any], origin: str) -> None:
        if not (self.root / ".git").is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
            self._git("init", "--quiet")
            self._git("remote", "add", "origin", self.url)
            self._git("checkout", "--quiet", "-B", self.BRANCH)
        self._pull()
        super().open(embed, origin)

    _OURS = ("log", "snapshot", "manifest.json")

    def close(self) -> None:
        # Only the replication log is ours. A word-cloud script, README or
        # image living in the same repository must not be committed or imported.
        if not self._stage():
            return
        self._commit("dual-mem sync")
        if self._git("push", "--quiet", "origin", f"HEAD:{self.BRANCH}", check=False).returncode:
            # Another device pushed first; its files are in other directories.
            self._pull()
            self._git("push", "--quiet", "origin", f"HEAD:{self.BRANCH}")

    def _stage(self) -> bool:
        """Stage our paths, deletions included; whether anything changed."""
        paths = [
            name
            for name in self._OURS
            if (self.root / name).exists()
            or self._git("ls-files", "--", name).stdout.strip()
        ]
        if paths:
            self._git("add", "--all", "--", *paths)
        return self._git("diff", "--cached", "--quiet", check=False).returncode != 0

    def _commit(self, message: str) -> None:
        self._git(
            "-c", "user.name=dual-mem", "-c", "user.email=dual-mem@localhost",
            "commit", "--quiet", "-m", message,
        )

    def publish_snapshot(self, state: dict) -> bool:
        # Pushes of this cycle land first, so the snapshot covers what the remote has.
        self.close()
        leased = self._git("rev-parse", "HEAD").stdout.strip()
        super().publish_snapshot(state)
        self._stage()
        tree = self._git("write-tree").stdout.strip()
        root = self._git(
            "-c", "user.name=dual-mem", "-c", "user.email=dual-mem@localhost",
            "commit-tree", tree, "-m", f"dual-mem snapshot {state['epoch']}",
        ).stdout.strip()
        pushed = self._git(
            "push", "--quiet", f"--force-with-lease={self.BRANCH}:{leased}",
            "origin", f"{root}:{self.BRANCH}", check=False,
        )
        if pushed.returncode:
            # Someone pushed after our lease: undo locally, retry next cycle.
            self._git("reset", "--quiet", "--hard", leased)
            self._manifest = self.manifest()
            return False
        self._git("reset", "--quiet", "--hard", root)
        self._forget_history()
        return True


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

    def snapshot(self) -> dict | None:
        # A Core relays its whole log from its own database; it has no snapshot.
        return None

    def publish_snapshot(self, state: dict) -> bool:
        return False

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
    store: ChromaVectorStore,
    backend: SyncBackend,
    *,
    embed: dict[str, Any],
    compact: bool | None = None,
) -> dict[str, Any]:
    """Adopt a newer snapshot, push this store's own entries, pull every other
    origin's, and fold the log into a snapshot when it has grown.

    ``compact``: None folds once the target holds ``COMPACT_AFTER`` log entries
    and this store is the digester; True folds now (still digester only);
    False never folds.
    """
    backend.open(embed, store.origin)
    scopes: set[tuple[str, str, str]] = set()
    adopted = None
    compacted = None
    try:
        snap = backend.snapshot()
        if snap is not None and snap["epoch"] > store.snapshot_epoch():
            scopes.update(store.adopt_snapshot(snap["state"]))
            adopted = snap["epoch"]
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
        if digester == store.origin and compact is not False:
            compacted = _maybe_compact(store, backend, force=bool(compact))
    finally:
        backend.close()
    return {
        "pushed": pushed,
        "received": received,
        "applied": applied,
        "pending": result["pending"],
        "scopes": sorted(scopes),
        "digester": digester,
        "snapshot_adopted": adopted,
        "compacted": compacted,
    }


def _maybe_compact(
    store: ChromaVectorStore, backend: SyncBackend, *, force: bool
) -> dict[str, Any] | None:
    """Fold every entry the target and this store both have into a snapshot.

    Only entries this store has applied are folded, and only those the target
    holds: a device that later asks for an entry past the base still finds it.
    """
    heads = backend.heads()
    covered = store.applied_heads()
    base = {
        origin: min(seq, heads.get(origin, 0))
        for origin, seq in covered.items()
        if min(seq, heads.get(origin, 0)) > 0
    }
    previous = store.log_base()
    folded = sum(seq - previous.get(origin, 0) for origin, seq in base.items())
    if folded <= 0 or (not force and folded < COMPACT_AFTER):
        return None
    if base != covered:
        # The target lacks something this store applied (it was pushed by a
        # device that has not finished). Wait until the two agree.
        return None
    epoch = max(store.snapshot_epoch(), (backend.snapshot() or {"epoch": 0})["epoch"]) + 1
    state = store.snapshot_state(epoch)
    if not backend.publish_snapshot(state):
        return None
    store.mark_snapshot(epoch, state["base"])
    return {"epoch": epoch, "folded": folded, "nodes": len(state["nodes"])}


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

    async def run_once(self, *, compact: bool | None = None) -> dict[str, Any]:
        """One cycle; ``compact=True`` folds the log into a snapshot now
        (on the digesting device only), None lets the threshold decide."""
        async with self._lock:
            try:
                result = await asyncio.to_thread(
                    sync_once,
                    self.client.factory.vector,
                    self.backend,
                    embed=self.embed,
                    compact=compact,
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
