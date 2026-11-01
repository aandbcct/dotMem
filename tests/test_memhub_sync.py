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

from dual_mem import MemoryClient
from dual_mem.cli import main as cli_main
from dual_mem.config import Settings
from dual_mem.memhub import HubClient
from dual_mem.memhub.config import SyncConfig, load_sync_config
from dual_mem.memhub.server import create_hub_app
from dual_mem.memhub.sync import (
    CoreBackend,
    DirBackend,
    GitBackend,
    SyncError,
    ssh_call,
    sync_once,
)
from dual_mem.storage.history_store import HistoryStore
from dual_mem.storage.vector_store import ChromaVectorStore, merge_metadata
from dual_mem.types import Layer, MemoryNode

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
                                               "embedding": [1.0, 0.0],
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
    from dual_mem.memhub.sync import project_received
    from dual_mem.registry import ComponentFactory

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

    call = ssh_call("me@box", ssh_args=["-p", "2222"], remote_command="dual-mem", run=run)
    assert call("POST", "/v1/hub/log/export", {"after": {}}) == {"ok": True}
    (argv, body), = calls
    assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-p", "2222"] and argv[5] == "me@box"
    assert argv[6] == "dual-mem hub relay POST /v1/hub/log/export"
    assert json.loads(body) == {"after": {}}

    failed = ssh_call("box", ssh_args=[], remote_command="dual-mem",
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
    sync_dir = tmp_path / "iCloud" / "dual-mem"
    result = runner.invoke(cli_main.app, ["hub", "connect", "dir", str(sync_dir),
                                          "--hub-dir", str(hub)])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["sync"]["kind"] == "dir" and sync_dir.is_dir()
    assert "not running" in out["first_sync"]
    assert load_sync_config(hub) == SyncConfig(kind="dir", target=str(sync_dir.resolve()))
    assert oct((hub / "hub.json").stat().st_mode & 0o777) == "0o600"

    missing = runner.invoke(cli_main.app, ["hub", "connect", "http", "https://x",
                                           "--hub-dir", str(hub)])
    assert missing.exit_code != 0 and "connect http needs" in missing.output
    assert load_sync_config(hub).kind == "dir"

    done = runner.invoke(cli_main.app, ["hub", "disconnect", "--hub-dir", str(hub)])
    assert json.loads(done.output)["disconnected"] is True
    assert load_sync_config(hub) is None


def test_relay_only_forwards_the_log_api(tmp_path):
    result = runner.invoke(cli_main.app, ["hub", "relay", "POST", "/v1/hub/writes",
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
