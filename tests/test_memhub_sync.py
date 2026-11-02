# -*- coding: utf-8 -*-
"""Replication of the store's change log between devices (MemHub sync)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from memhub import MemoryClient
from memhub.cli import main as cli_main
from memhub.config import Settings
from memhub.hub import HubClient
from memhub.hub.config import SyncConfig, load_sync_config
from memhub.hub.server import create_hub_app
from memhub.hub.sync import (
    CoreBackend,
    DirBackend,
    GitBackend,
    SyncError,
    ssh_call,
    sync_once,
)
from memhub.storage.history_store import HistoryStore
from memhub.storage.vector_store import (
    ChromaVectorStore,
    merge_metadata,
    pack_vector,
    unpack_vector,
)
from memhub.types import Layer, MemoryNode

EMBED = {"model": "m", "dim": 2}
SCOPE = ("app", "u", "")
runner = CliRunner()


def node(nid: str, **kw) -> MemoryNode:
    kw.setdefault("layer", Layer.L1_FACT)
    return MemoryNode(
        node_id=nid, content=nid, app_id="app", user_id="u", embedding=[1.0, 0.0], **kw
    )


def store(path: Path) -> ChromaVectorStore:
    path.mkdir(parents=True, exist_ok=True)
    return ChromaVectorStore(str(path), history=HistoryStore(str(path)))


def plan(s: ChromaVectorStore, nodes, expected=None, archives=None, **kw) -> bool:
    return s.apply_version_plan(
        nodes=nodes, expected=expected or {}, archives=archives or [], scope=SCOPE, **kw
    )


def state(s: ChromaVectorStore) -> dict:
    """Content and version structure of every node; revision is a local CAS token."""
    got = s.collection.get(include=["metadatas", "documents"])
    return {
        nid: (doc, {k: v for k, v in meta.items() if k != "revision"})
        for nid, doc, meta in zip(got["ids"], got["documents"], got["metadatas"])
    }


def sync(s: ChromaVectorStore, backend) -> dict:
    return sync_once(s, backend, embed=EMBED)


# --- store level ------------------------------------------------------------------


def test_replica_receives_raw_versions_and_vectors_without_any_model(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    a.upsert([node("raw", layer=Layer.L0_RAW)])
    a.upsert([node("x")])
    assert plan(a, [node("y", supersedes=["x"])], {"x": 0}, request_key="k")
    folder = DirBackend(tmp_path / "cloud")
    assert sync(a, folder)["pushed"] == 3
    result = sync(b, folder)
    assert (result["received"], result["applied"], result["pending"]) == (3, 3, 0)
    assert state(b) == state(a)
    assert b.get("y").embedding == pytest.approx([1.0, 0.0])
    assert b.get("x").superseded_by == ["y"] and not b.get("x").is_latest
    # The received plan is a committed request on the replica too.
    assert b.completed_request("k")["extra_node_ids"] == ["y"]


def test_a_store_written_before_the_log_publishes_a_baseline(tmp_path):
    a = store(tmp_path / "a")
    a.upsert([node("old1"), node("old2")])
    a._journal.execute("DELETE FROM log")
    a._journal.execute("DELETE FROM meta")
    a._journal.commit()
    a.close()
    a = store(tmp_path / "a")
    b = store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    sync(a, folder)
    sync(b, folder)
    assert set(state(b)) == {"old1", "old2"}


def test_concurrent_supersede_of_one_head_converges_with_both_branches(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("x")])
    sync(a, folder)
    sync(b, folder)
    # Offline on both devices: each replaces x with its own version.
    assert plan(a, [node("ya", supersedes=["x"])], {"x": 0})
    assert plan(b, [node("yb", supersedes=["x"])], {"x": 0})
    for _ in range(2):
        sync(a, folder)
        sync(b, folder)
    assert state(a) == state(b)
    x = a.get("x")
    assert not x.is_latest and x.superseded_by == ["ya", "yb"]
    assert a.get("ya").is_latest and a.get("yb").is_latest


def test_retirement_is_final_whatever_order_patches_arrive(tmp_path):
    current = node("x").to_metadata()
    retired = {**current, "is_latest": False, "superseded_by": "y", "revision": 1}
    stale = {**current, "custom": json.dumps({"behavior_text": "calm"}), "revision": 1}
    one = merge_metadata(merge_metadata(current, retired), stale)
    two = merge_metadata(merge_metadata(current, stale), retired)
    assert one["is_latest"] is False and two["is_latest"] is False
    assert {k: v for k, v in one.items() if k != "revision"} == {
        k: v for k, v in two.items() if k != "revision"
    }
    assert json.loads(one["custom"]) == {"behavior_text": "calm"}


def test_entry_waits_for_a_node_from_another_origin(tmp_path):
    """C supersedes a node it got from A; B hears from C before A."""
    a, b, c = store(tmp_path / "a"), store(tmp_path / "b"), store(tmp_path / "c")
    a.upsert([node("x")])
    c.import_log(a.export_log(origin=a.origin))
    assert plan(c, [node("y", supersedes=["x"])], {"x": 0})
    first = b.import_log(c.export_log(origin=c.origin))
    assert first["applied"] == 0 and first["pending"] == 1 and b.get("y") is None
    # A heard nothing from C, so B must ask C's entries again once x arrives.
    assert b.log_heads()[c.origin] == 1
    second = b.import_log(a.export_log(origin=a.origin))
    assert second["applied"] == 2 and second["pending"] == 0
    assert state(b) == state(c)


def test_entries_apply_in_order_and_a_gap_is_asked_for_again(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    a.upsert([node("one")])
    a.upsert([node("two")])
    first, second = a.export_log(origin=a.origin)
    assert b.import_log([second])["applied"] == 0
    assert b.log_heads().get(a.origin, 0) == 0 and b.get("two") is None
    assert b.import_log([first, second])["applied"] == 2
    assert b.log_heads()[a.origin] == 2


def test_deleted_node_stays_deleted_on_every_replica(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("x")])
    a.update_payload_many({"x": {"tags": "t"}})
    a.delete(["x"])
    sync(a, folder)
    result = sync(b, folder)
    # The patch of a deleted node is not exported, so nothing waits for it.
    assert result["pending"] == 0 and b.get("x") is None
    b.import_log([{"origin": "late", "seq": 1, "kind": "nodes",
                   "data": a._node_rows([]) + [{"id": "x", "content": "x",
                                               "embedding": pack_vector([1.0, 0.0]),
                                               "meta": node("x").to_metadata()}]}])
    assert b.get("x") is None


def test_a_plan_retiring_a_since_deleted_node_does_not_stall_its_origin(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    a.upsert([node("x")])
    assert plan(a, [node("y", supersedes=["x"])], {"x": 0})
    a.delete(["x"])
    a.upsert([node("z")])
    result = b.import_log(a.export_log(origin=a.origin))
    assert result["pending"] == 0
    assert set(state(b)) == {"y", "z"}


def test_received_evidence_edges_reach_the_replica_graph(tmp_path, fake_embed):
    from memhub.hub.sync import project_received
    from memhub.registry import ComponentFactory

    def factory(path):
        return ComponentFactory(settings=Settings(mode="dual", storage_dir=str(path)),
                                embed=fake_embed)

    fa, fb = factory(tmp_path / "a"), factory(tmp_path / "b")
    fact = MemoryNode(content="f", layer=Layer.L1_FACT, app_id="app", user_id="u",
                      embedding=fake_embed.embed_sync("f"), node_id="f")
    schema = MemoryNode(content="s", layer=Layer.L3_DERIVED, kind="schema", app_id="app",
                        user_id="u", embedding=fake_embed.embed_sync("s"), node_id="s",
                        custom={"source_ids": ["f"]})
    fa.vector.upsert([fact])
    assert fa.vector.apply_version_plan(nodes=[schema], expected={}, archives=[], scope=SCOPE)
    other = MemoryNode(content="g", layer=Layer.L1_FACT, app_id="app", user_id="u",
                       embedding=fake_embed.embed_sync("g"), node_id="g")
    fa.vector.upsert([other])
    assert fa.vector.apply_version_plan(nodes=[], expected={"g": 0}, archives=[], scope=SCOPE,
                                        processed_evidence=["g"], edges=[("s", "g")])
    fb.vector.import_log(fa.vector.export_log())
    asyncio.run(project_received(SimpleNamespace(factory=fb)))
    # The schema's own evidence and the edge added later by another plan.
    assert set(fb.graph.evidence_of("s")) == {"f", "g"}
    assert fb.vector.projection_scopes() == []
    fa.close()
    fb.close()


# --- backends ---------------------------------------------------------------------


def test_folder_refuses_vectors_of_another_embedding(tmp_path):
    a = store(tmp_path / "a")
    folder = DirBackend(tmp_path / "cloud")
    sync(a, folder)
    with pytest.raises(SyncError, match="embed"):
        sync_once(a, folder, embed={"model": "other", "dim": 2})
    assert json.loads((tmp_path / "cloud" / "manifest.json").read_text())["digester"] == a.origin


def test_folder_never_has_two_devices_write_one_file(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("x")])
    b.upsert([node("y")])
    sync(a, folder)
    sync(b, folder)
    owners = {p.parent.name for p in (tmp_path / "cloud" / "log").rglob("*.json")}
    assert owners == {a.origin, b.origin}
    assert not list((tmp_path / "cloud").rglob(".*.tmp"))


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_git_backend_syncs_two_devices_that_push_concurrently(tmp_path):
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "--quiet", "-b", "main", str(remote))
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    ga = GitBackend(str(remote), tmp_path / "ga")
    gb = GitBackend(str(remote), tmp_path / "gb")
    a.upsert([node("x")])
    sync(a, ga)
    b.upsert([node("y")])
    # b's clone predates a's next push: its push is rejected, then rebased.
    sync(b, gb)
    a.upsert([node("z")])
    sync(a, ga)
    b.upsert([node("w")])
    sync(b, gb)
    sync(a, ga)
    assert set(state(a)) == set(state(b)) == {"x", "y", "z", "w"}


def test_git_sync_does_not_import_or_commit_files_outside_the_log(tmp_path):
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "--quiet", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    _git("init", "--quiet", "-b", "main", str(seed))
    _git("remote", "add", "origin", str(remote), cwd=seed)
    script = seed / "scripts" / "memory_cloud.py"
    script.parent.mkdir()
    script.write_text("print('not-a-memory')\n")
    _git("add", "--", "scripts/memory_cloud.py", cwd=seed)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "script", cwd=seed)
    _git("push", "--quiet", "origin", "main", cwd=seed)

    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    ga = GitBackend(str(remote), tmp_path / "ga")
    a.upsert([node("kept")])
    sync(a, ga)
    checkout_script = tmp_path / "ga" / "scripts" / "memory_cloud.py"
    assert checkout_script.read_text() == "print('not-a-memory')\n"
    checkout_script.write_text("print('local-edit-must-stay-uncommitted')\n")
    (tmp_path / "ga" / "notes.md").write_text("not-a-memory-either\n")
    a.upsert([node("also-kept")])
    sync(a, ga)

    view = tmp_path / "view"
    _git("clone", "--quiet", str(remote), str(view))
    files = {
        p.relative_to(view).as_posix()
        for p in view.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    assert "scripts/memory_cloud.py" in files
    assert (view / "scripts" / "memory_cloud.py").read_text() == "print('not-a-memory')\n"
    assert "notes.md" not in files
    assert any(name.startswith("log/") and name.endswith(".json") for name in files)

    gb = GitBackend(str(remote), tmp_path / "gb")
    sync(b, gb)
    blob = " ".join(doc or "" for doc, _meta in state(b).values())
    assert "kept" in blob and "also-kept" in blob
    assert "not-a-memory" not in blob
    assert checkout_script.read_text() == "print('local-edit-must-stay-uncommitted')\n"


def test_git_push_rejected_by_a_newer_remote_rebases_and_retries(tmp_path):
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "--quiet", "-b", "main", str(remote))
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    ga = GitBackend(str(remote), tmp_path / "ga")
    gb = GitBackend(str(remote), tmp_path / "gb")
    gb.open(EMBED, b.origin)  # b fetched before a pushed
    a.upsert([node("x")])
    sync(a, ga)
    b.upsert([node("y")])
    gb.push(b.export_log(origin=b.origin))
    gb.close()
    sync(a, ga)
    assert set(state(a)) == {"x", "y"}
    # Both started on an empty repository; the manifest pushed first is kept.
    assert sync(b, gb)["digester"] == a.origin


def test_ssh_backend_sends_one_command_per_call():
    calls = []

    def run(argv, *, input, **kw):
        calls.append((argv, input))
        return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    call = ssh_call("me@box", ssh_args=["-p", "2222"], remote_command="memhub", run=run)
    assert call("POST", "/v1/hub/log/export", {"after": {}}) == {"ok": True}
    (argv, body), = calls
    assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-p", "2222"] and argv[5] == "me@box"
    assert argv[6] == "memhub relay POST /v1/hub/log/export"
    assert json.loads(body) == {"after": {}}

    failed = ssh_call("box", ssh_args=[], remote_command="memhub",
                      run=lambda *a, **k: SimpleNamespace(returncode=255, stdout="",
                                                         stderr="Connection refused"))
    with pytest.raises(SyncError, match="Connection refused"):
        failed("GET", "/v1/hub/log/heads", None)


# --- Cores ------------------------------------------------------------------------


def _client(root: Path, fake_embed, fake_llm, fake_jev, mode="system1") -> MemoryClient:
    return MemoryClient(
        settings=Settings(storage_dir=str(root / "store"), mode=mode),
        embed=fake_embed, llm=fake_llm, jev=fake_jev,
    )


def _core_backend(app) -> CoreBackend:
    """A CoreBackend that calls ``app`` in-process (the async app on its own loop)."""
    loop = asyncio.get_running_loop()

    def call(method, path, body):
        async def go():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://core") as http:
                response = await http.request(
                    method, path, json=body, headers={"Authorization": "Bearer server"}
                )
                response.raise_for_status()
                return response.json()

        return asyncio.run_coroutine_threadsafe(go(), loop).result()

    return CoreBackend(call)


async def _drain(app) -> None:
    while app.state.queue.counts().get("pending", 0) or app.state.queue.counts().get("running", 0):
        await asyncio.sleep(0.01)


async def test_laptop_recalls_locally_what_the_desktop_wrote_through_a_server(
    tmp_path, fake_embed, fake_llm, fake_jev
):
    """Desktop and laptop sync through a server Core; recall never leaves the laptop."""
    server_client = _client(tmp_path / "server", fake_embed, fake_llm, fake_jev)
    server = create_hub_app(api_key="server", hub_dir=tmp_path / "server", client=server_client)
    async with server.router.lifespan_context(server):
        devices = []
        for name in ("desktop", "laptop"):
            client = _client(tmp_path / name, fake_embed, fake_llm, fake_jev)
            app = create_hub_app(
                api_key="secret", hub_dir=tmp_path / name, client=client,
                sync=SyncConfig(kind="http", target="https://server"),
                sync_backend=_core_backend(server), sync_interval_seconds=3600,
            )
            devices.append((app, client))
        (desktop, _), (laptop, laptop_client) = devices
        async with desktop.router.lifespan_context(desktop), \
                laptop.router.lifespan_context(laptop):
            dt = HubClient(base_url="http://d", api_key="secret",
                           transport=httpx.ASGITransport(app=desktop))
            lt = HubClient(base_url="http://l", api_key="secret",
                           transport=httpx.ASGITransport(app=laptop))
            job = await dt.write(
                messages=[{"role": "user", "content": "我偏好 PyTorch"},
                          {"role": "assistant", "content": "记住了"}],
                session_id="codex:s1", request_id="r1", wait=True,
            )
            assert job["status"] == "done"
            pushed = await dt.sync()
            assert pushed["pushed"] >= 2
            pulled = await lt.sync()
            assert pulled["pending"] == 0 and pulled["applied"] == pushed["pushed"]
            assert (await lt.status())["sync"]["digests_here"] is False

            # The server is gone: recall is answered from the laptop's own store.
            async def unreachable():
                raise AssertionError("recall must not wait for the network")

            laptop.state.syncer.run_once = unreachable
            recall = await lt.recall(query="PyTorch")
            assert any("PyTorch" in m["content"] for g in recall["memories"].values() for m in g)
            assert await asyncio.to_thread(laptop_client.factory.vector.log_heads)
            await dt.aclose()
            await lt.aclose()
    await server_client.aclose()


async def test_only_the_digester_queues_received_facts(tmp_path, fake_embed, fake_llm, fake_jev):
    folder = tmp_path / "cloud"
    cores = []
    for name in ("first", "second"):
        client = _client(tmp_path / name, fake_embed, fake_llm, fake_jev, mode="dual")
        app = create_hub_app(api_key="k", hub_dir=tmp_path / name, client=client,
                             settings=client.settings, digest_min_hours=0,
                             sync=SyncConfig(kind="dir", target=str(folder)),
                             sync_interval_seconds=3600)
        cores.append((app, client))
    (first, fc), (second, sc) = cores
    async with first.router.lifespan_context(first), second.router.lifespan_context(second):
        await first.state.syncer.run_once()
        await second.state.syncer.run_once()
        assert first.state.syncer.digests_here() and not second.state.syncer.digests_here()
        sc.factory.vector.upsert([MemoryNode(
            content="fact", layer=Layer.L1_FACT, app_id="default", user_id="default",
            embedding=fake_embed.embed_sync("fact"), node_id="n")])
        await second.state.syncer.run_once()
        await first.state.syncer.run_once()
        assert fc.factory.vector.get("n") is not None
        assert fc.factory.cache.list_pending_s2_scopes()
        assert sc.factory.cache.list_pending_s2_scopes() == []
    await fc.aclose()
    await sc.aclose()


def test_connect_disconnect_and_status_without_a_running_core(tmp_path):
    hub = tmp_path / "hub"
    sync_dir = tmp_path / "iCloud" / "memhub"
    result = runner.invoke(cli_main.app, ["connect", "dir", str(sync_dir),
                                          "--hub-dir", str(hub)])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["sync"]["kind"] == "dir" and sync_dir.is_dir()
    assert "not running" in out["first_sync"]
    assert load_sync_config(hub) == SyncConfig(kind="dir", target=str(sync_dir.resolve()))
    assert oct((hub / "hub.json").stat().st_mode & 0o777) == "0o600"

    missing = runner.invoke(cli_main.app, ["connect", "http", "https://x",
                                           "--hub-dir", str(hub)])
    assert missing.exit_code != 0 and "connect http needs" in missing.output
    assert load_sync_config(hub).kind == "dir"

    done = runner.invoke(cli_main.app, ["disconnect", "--hub-dir", str(hub)])
    done_out = json.loads(done.output)
    assert done_out["sync_enabled"] is False
    assert done_out["removed_sync"]["kind"] == "dir"
    assert done_out["message"] == "sync disconnected"
    assert load_sync_config(hub) is None

    again = runner.invoke(cli_main.app, ["disconnect", "--hub-dir", str(hub)])
    again_out = json.loads(again.output)
    assert again_out["sync_enabled"] is False
    assert again_out["removed_sync"] is None
    assert "already disconnected" in again_out["message"]


def test_relay_only_forwards_the_log_api(tmp_path):
    result = runner.invoke(cli_main.app, ["relay", "POST", "/v1/hub/writes",
                                          "--hub-dir", str(tmp_path)], input="{}")
    assert result.exit_code != 0 and "/v1/hub/log/" in result.output


async def test_auto_digest_runs_only_on_the_digesting_device(
    tmp_path, fake_embed, fake_llm, fake_jev
):
    folder = tmp_path / "cloud"
    cores = []
    for name in ("first", "second"):
        client = _client(tmp_path / name, fake_embed, fake_llm, fake_jev, mode="dual")
        calls: list[int] = []

        async def digest(calls=calls):
            calls.append(1)
            return SimpleNamespace(processed=0)

        client.digest = digest
        app = create_hub_app(api_key="k", hub_dir=tmp_path / name, client=client,
                             settings=client.settings,
                             sync=SyncConfig(kind="dir", target=str(folder)),
                             sync_interval_seconds=3600)
        cores.append((app, client, calls))
    (first, fc, first_calls), (second, sc, second_calls) = cores
    async with first.router.lifespan_context(first), second.router.lifespan_context(second):
        await first.state.syncer.run_once()
        await second.state.syncer.run_once()
        for app, client in ((first, fc), (second, sc)):
            client.factory.cache.enqueue_s2_task(user_id="default", app_id="default", agent_id="")
            app.state.queue._idle_seconds = 0.01
            app.state.queue.wake()
        for _ in range(200):
            if first_calls:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert first_calls and not second_calls
    await fc.aclose()
    await sc.aclose()


# --- size control: packed vectors and snapshots -----------------------------------


def test_vectors_travel_as_float16_and_keep_their_direction(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    vec = [0.123456789, -0.987654321]
    a.upsert([MemoryNode(node_id="v", content="v", app_id="app", user_id="u",
                         layer=Layer.L1_FACT, embedding=vec)])
    (entry,) = a.export_log(origin=a.origin)
    packed = entry["data"][0]["embedding"]
    assert isinstance(packed, str) and len(packed) == 8  # 2 dims x 2 bytes, base64
    assert unpack_vector(packed) == pytest.approx(vec, abs=1e-3)
    b.import_log([entry])
    assert b.get("v").embedding == pytest.approx(vec, abs=1e-3)
    # Decimal text vectors are no longer accepted on the wire.
    with pytest.raises(ValueError, match="o/1 is not in the current sync format"):
        store(tmp_path / "c").import_log([{**entry, "seq": 1, "origin": "o",
                                           "data": [{**entry["data"][0], "embedding": vec}]}])


def test_folder_in_the_old_format_is_refused(tmp_path):
    target = tmp_path / "cloud"
    target.mkdir()
    (target / "manifest.json").write_text(json.dumps(
        {"format": 1, "embed": EMBED, "digester": "x"}))
    with pytest.raises(SyncError, match="format 1"):
        sync(store(tmp_path / "a"), DirBackend(target))


def _log_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in (root / "log").rglob("*.json")}


def test_compaction_replaces_the_log_and_a_new_device_starts_from_the_snapshot(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("x")])
    assert plan(a, [node("y", supersedes=["x"])], {"x": 0})
    a.upsert([node("gone")])
    a.delete(["gone"])
    sync(a, folder)
    b.upsert([node("from-b")])
    sync(b, folder)
    sync(a, folder)
    assert len(_log_files(tmp_path / "cloud")) == 5

    result = sync_once(a, folder, embed=EMBED, compact=True)
    assert result["compacted"]["epoch"] == 1 and result["compacted"]["folded"] == 5
    # Every entry both stores had is now inside the snapshot.
    assert _log_files(tmp_path / "cloud") == set()
    manifest = json.loads((tmp_path / "cloud" / "manifest.json").read_text())
    assert manifest["snapshot"]["base"] == {a.origin: 4, b.origin: 1}

    c = store(tmp_path / "c")
    pulled = sync(c, folder)
    assert pulled["snapshot_adopted"] == 1 and pulled["pending"] == 0
    assert state(c) == state(a)
    assert c.get("gone") is None and not c.get("x").is_latest
    # c never held "gone": only the snapshot's tombstone keeps a stale copy
    # of it (from a device that has not caught up) from bringing it back.
    c.import_log([{"origin": "stale", "seq": 1, "kind": "nodes",
                   "data": [{"id": "gone", "content": "gone",
                             "embedding": pack_vector([1.0, 0.0]),
                             "meta": node("gone").to_metadata()}]}])
    assert c.get("gone") is None
    # Writes after the snapshot travel as log entries from the base on.
    a.upsert([node("later")])
    sync(a, folder)
    assert _log_files(tmp_path / "cloud") == {f"log/{a.origin}/{5:012d}.json"}
    sync(c, folder)
    assert c.get("later") is not None and c.log_heads()[a.origin] == 5


def test_only_the_digester_compacts(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("x")])
    sync(a, folder)
    b.upsert([node("y")])
    assert sync_once(b, folder, embed=EMBED, compact=True)["compacted"] is None
    assert sync_once(a, folder, embed=EMBED, compact=True)["compacted"] is not None


def test_compaction_waits_for_the_threshold(tmp_path, monkeypatch):
    from memhub.hub import sync as sync_mod

    monkeypatch.setattr(sync_mod, "COMPACT_AFTER", 3)
    a = store(tmp_path / "a")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("one")])
    a.upsert([node("two")])
    assert sync(a, folder)["compacted"] is None
    a.upsert([node("three")])
    assert sync(a, folder)["compacted"]["folded"] == 3


def test_a_device_behind_the_snapshot_keeps_its_own_unpushed_writes(tmp_path):
    """b was offline across a compaction that deleted x; its own write survives,
    the deletion reaches it, and its unpushed entry is published afterwards."""
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("x")])
    a.upsert([node("kept")])
    sync(a, folder)
    sync(b, folder)
    b.upsert([node("offline")])  # not pushed yet
    b.update_payload_many({"kept": {"tags": "from-b"}})  # newer than the snapshot
    a.delete(["x"])
    sync_once(a, folder, embed=EMBED, compact=True)

    result = sync(b, folder)
    assert result["snapshot_adopted"] == 1
    assert b.get("x") is None
    assert b.get("offline") is not None
    assert b.get("kept").tags == ["from-b"]
    assert result["pushed"] == 2
    sync(a, folder)
    assert a.get("offline") is not None and a.get("kept").tags == ["from-b"]
    assert state(a) == state(b)


def test_a_device_that_already_has_everything_is_unchanged_by_the_snapshot(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("x")])
    assert plan(a, [node("y", supersedes=["x"])], {"x": 0})
    sync(a, folder)
    sync(b, folder)
    before = state(b)
    sync_once(a, folder, embed=EMBED, compact=True)
    assert sync(b, folder)["snapshot_adopted"] == 1
    assert state(b) == before


def test_snapshot_carries_schemas_and_edges_to_a_new_replica(tmp_path, fake_embed):
    from memhub.hub.sync import project_received
    from memhub.registry import ComponentFactory

    def factory(path):
        return ComponentFactory(settings=Settings(mode="dual", storage_dir=str(path)),
                                embed=fake_embed)

    fa, fb = factory(tmp_path / "a"), factory(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    fact = MemoryNode(content="f", layer=Layer.L1_FACT, app_id="app", user_id="u",
                      embedding=fake_embed.embed_sync("f"), node_id="f")
    other = MemoryNode(content="g", layer=Layer.L1_FACT, app_id="app", user_id="u",
                       embedding=fake_embed.embed_sync("g"), node_id="g")
    schema = MemoryNode(content="s", layer=Layer.L3_DERIVED, kind="schema", app_id="app",
                        user_id="u", embedding=fake_embed.embed_sync("s"), node_id="s",
                        custom={"source_ids": ["f"]})
    fa.vector.upsert([fact, other])
    assert fa.vector.apply_version_plan(nodes=[schema], expected={}, archives=[], scope=SCOPE)
    assert fa.vector.apply_version_plan(nodes=[], expected={"g": 0}, archives=[], scope=SCOPE,
                                        processed_evidence=["g"], edges=[("s", "g")])
    sync_once(fa.vector, folder, embed=EMBED, compact=True)
    assert _log_files(tmp_path / "cloud") == set()
    sync_once(fb.vector, folder, embed=EMBED)
    asyncio.run(project_received(SimpleNamespace(factory=fb)))
    assert set(fb.graph.evidence_of("s")) == {"f", "g"}
    fa.close()
    fb.close()


def _remote_objects(remote: Path) -> int:
    out = subprocess.run(["git", "count-objects", "-v"], cwd=remote, capture_output=True,
                         text=True, check=True).stdout
    _git("gc", "--quiet", "--prune=now", cwd=remote)
    out = subprocess.run(["git", "rev-list", "--all", "--objects"], cwd=remote,
                         capture_output=True, text=True, check=True).stdout
    return len(out.splitlines())


def test_git_compaction_drops_the_old_history_and_other_clones_follow(tmp_path):
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "--quiet", "-b", "main", str(remote))
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    ga = GitBackend(str(remote), tmp_path / "ga")
    gb = GitBackend(str(remote), tmp_path / "gb")
    for i in range(6):
        a.upsert([node(f"n{i}")])
        sync(a, ga)
    sync(b, gb)
    b.upsert([node("from-b-offline")])  # b committed nothing for it yet

    sync_once(a, ga, embed=EMBED, compact=True)
    log = subprocess.run(["git", "log", "--format=%s", "main"], cwd=remote,
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert log == ["memhub snapshot 1"]
    # The old commits and their log blobs are gone from the remote.
    objects = subprocess.run(["git", "rev-list", "--all", "--objects"], cwd=remote,
                             capture_output=True, text=True, check=True).stdout
    assert "log/" not in objects

    # b's clone has the old history; it follows the new root, keeps its own write.
    result = sync(b, gb)
    assert result["snapshot_adopted"] == 1 and result["pushed"] == 1
    # Following the new root means the old history is not pushed back.
    tree = subprocess.run(["git", "ls-tree", "-r", "--name-only", "main"], cwd=remote,
                          capture_output=True, text=True, check=True).stdout.splitlines()
    assert [p for p in tree if p.startswith("log/")] == [f"log/{b.origin}/{1:012d}.json"]
    commits = subprocess.run(["git", "rev-list", "--count", "main"], cwd=remote,
                             capture_output=True, text=True, check=True).stdout.strip()
    assert commits == "2"
    sync(a, ga)
    assert set(state(a)) == set(state(b)) == {*(f"n{i}" for i in range(6)), "from-b-offline"}
    history = subprocess.run(["git", "log", "--format=%s", "main"], cwd=tmp_path / "gb",
                             capture_output=True, text=True, check=True).stdout.splitlines()
    assert history[-1] == "memhub snapshot 1"


def test_git_compaction_loses_its_lease_to_a_concurrent_push(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "--quiet", "-b", "main", str(remote))
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    ga = GitBackend(str(remote), tmp_path / "ga")
    gb = GitBackend(str(remote), tmp_path / "gb")
    a.upsert([node("x")])
    sync(a, ga)
    sync(b, gb)

    original = GitBackend.publish_snapshot

    def racing(self, state):
        # b pushes between a's last pull and a's snapshot push.
        b.upsert([node("racer")])
        sync(b, gb)
        return original(self, state)

    monkeypatch.setattr(GitBackend, "publish_snapshot", racing)
    assert sync_once(a, ga, embed=EMBED, compact=True)["compacted"] is None
    monkeypatch.setattr(GitBackend, "publish_snapshot", original)
    # Nothing was lost: the racer is on the remote, and a retry compacts.
    sync(a, ga)
    assert a.get("racer") is not None
    assert sync_once(a, ga, embed=EMBED, compact=True)["compacted"]["epoch"] == 1
    sync(b, gb)
    assert set(state(a)) == set(state(b)) == {"x", "racer"}


def test_a_node_written_again_after_its_deletion_reaches_every_replica(tmp_path):
    a, b = store(tmp_path / "a"), store(tmp_path / "b")
    folder = DirBackend(tmp_path / "cloud")
    a.upsert([node("x")])
    sync(a, folder)
    sync(b, folder)
    a.delete(["x"])
    sync(a, folder)
    sync(b, folder)
    assert b.get("x") is None

    # Node ids are derived from content, so a retried write reuses the id.
    a.upsert([node("x")])
    plan(a, [node("y")])
    a.delete(["y"])
    # A later operation (another id) writing the same content gets the same node id.
    plan(a, [node("y"), node("y2")])
    a.update_payload_many({"x": {"tags": "t"}})
    sync(a, folder)
    sync(b, folder)
    assert b.get("x") is not None and b.get("x").tags == ["t"]
    assert b.get("y") is not None and state(b) == state(a)

    # A device replaying the whole log, and one built from the snapshot, agree.
    replay = store(tmp_path / "replay")
    assert sync(replay, folder)["pending"] == 0
    assert state(replay) == state(a)
    assert sync_once(a, folder, embed=EMBED, compact=True)["compacted"]
    built = store(tmp_path / "built")
    assert sync(built, folder)["snapshot_adopted"] == 1
    assert state(built) == state(a)
    manifest = json.loads((tmp_path / "cloud" / "manifest.json").read_text())
    snapshot = json.loads((tmp_path / "cloud" / manifest["snapshot"]["path"]).read_text())
    assert not {"x", "y"} & set(snapshot["tombstones"])


def test_a_stale_copy_does_not_undo_a_deletion_after_a_revival_elsewhere(tmp_path):
    a = store(tmp_path / "a")
    a.upsert([node("x")])
    a.delete(["x"])
    a.upsert([node("z")])
    a.delete(["z"])
    a.upsert([node("x")])
    stale = {"id": "z", "content": "z", "embedding": pack_vector([1.0, 0.0]),
             "meta": node("z").to_metadata()}
    a.import_log([{"origin": "stale", "seq": 1, "kind": "nodes", "data": [stale]}])
    assert a.get("x") is not None and a.get("z") is None
