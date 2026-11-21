"""Recall cost must not grow with re-reading the whole store on every query.

1. The inverted ``LexicalIndex`` scores exactly like ``compute_bm25_scores``.
2. The store's write generation changes on every write path, including another
   process writing the same store, and lexical recall sees new memories at once.
3. The project view is reused until the store changes.
4. Recall telemetry separates cache rebuilds and embedding; ``doctor`` warns
   only if slowness repeats within a category.
"""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from memhub import MemoryClient
from memhub.cli import hub as cli_hub
from memhub.cli import main as cli_main
from memhub.config import Settings
from memhub.hub import HubClient
from memhub.hub.server import HUB_USER_ID, ProjectViewCache, create_hub_app
from memhub.retrieval.bm25 import LexicalIndex, compute_bm25_scores
from memhub.storage.vector_store import ChromaVectorStore
from memhub.types import Layer, MemoryNode

runner = CliRunner()


def _node(i: int, text: str, dim: int = 8) -> MemoryNode:
    return MemoryNode(
        node_id=f"n{i}", content=text, layer=Layer.L1_FACT, app_id="a", user_id="u",
        embedding=[1.0] + [0.0] * (dim - 1),
    )


def test_lexical_index_scores_match_the_full_scan() -> None:
    rng = random.Random(7)
    vocab = ["uv", "pip", "部署", "端口", "8080", "redis", "缓存", "超时", "重试", "日志"]
    docs = [" ".join(rng.choices(vocab, k=rng.randint(1, 12))) for _ in range(300)]
    index = LexicalIndex(docs)
    for query in (["uv", "部署"], ["redis", "超时", "absent"], ["8080"]):
        full = compute_bm25_scores(query, docs)
        sparse = index.scores(query)
        assert {i for i, s in enumerate(full) if s > 0} == set(sparse)
        assert all(math.isclose(full[i], s, rel_tol=1e-12) for i, s in sparse.items())


def test_generation_changes_on_every_write_and_across_processes(tmp_path: Path) -> None:
    store = ChromaVectorStore(str(tmp_path))
    seen = [store.generation]
    store.upsert([_node(1, "端口 8080")])
    seen.append(store.generation)
    store.update_payload("n1", {"tags": "x"})
    seen.append(store.generation)
    # A second process opening the same directory writes; this one must notice.
    other = ChromaVectorStore(str(tmp_path))
    other.upsert([_node(2, "端口 9090")])
    seen.append(store.generation)
    store.delete(["n1"])
    seen.append(store.generation)
    assert seen == sorted(set(seen)), seen
    other.close()
    store.close()


async def test_lexical_recall_sees_a_new_memory_right_after_the_write(
    tmp_path: Path, fake_embed, fake_llm, fake_jev
) -> None:
    core = MemoryClient(
        settings=Settings(storage_dir=str(tmp_path / "store"), mode="system1"),
        embed=fake_embed, llm=fake_llm, jev=fake_jev,
    )
    await core.add(content="部署脚本用 uv 运行", user_id="u", request_id="r1")
    first = await core.search(query="qx9zz 错误码", user_id="u", limit=5)
    assert not any("qx9zz" in m.content for m in first.memories.normal)
    await core.add(content="错误码前缀是 qx9zz", user_id="u", request_id="r2")
    second = await core.search(query="qx9zz 错误码", user_id="u", limit=5)
    assert any("qx9zz" in m.content for m in second.memories.normal)
    await core.aclose()


def test_project_view_is_reused_until_the_store_changes(monkeypatch) -> None:
    import memhub.hub.server as server

    calls: list[str] = []

    def build(_client, workspace):
        calls.append(workspace)
        return [f"s{len(calls)}"], None

    class Store:
        generation = 1

    class Client:
        factory = type("F", (), {"vector": Store})

    monkeypatch.setattr(server, "project_sessions_and_view", build)
    cache = ProjectViewCache(max_age=60)
    assert cache.get(Client, "/w") == (["s1"], None)
    assert cache.get(Client, "/w") == (["s1"], None)
    Store.generation = 2
    assert cache.get(Client, "/w") == (["s2"], None)
    assert cache.get(Client, "/other") == (["s3"], None)
    assert calls == ["/w", "/w", "/other"]
    expired = ProjectViewCache(max_age=0)
    expired.get(Client, "/w")
    expired.get(Client, "/w")
    assert calls[-2:] == ["/w", "/w"]


async def test_status_separates_recall_paths_and_embedding_time(
    tmp_path: Path, fake_embed, fake_llm, fake_jev, monkeypatch
) -> None:
    core = MemoryClient(
        settings=Settings(storage_dir=str(tmp_path / "store"), mode="system1"),
        embed=fake_embed, llm=fake_llm, jev=fake_jev,
    )
    workspace = str(tmp_path)
    vector = core.factory.vector
    vector.upsert([MemoryNode(
        content="[user]: 部署端口", layer=Layer.L0_RAW,
        app_id=core.settings.default_app_id, user_id=HUB_USER_ID, session_id="src",
        custom={"workspace_id": workspace}, embedding=fake_embed.embed_sync("端口"),
    )])
    perf_counter = time.perf_counter
    embed_time = 0.0

    async def embed(text: str) -> list[float]:
        nonlocal embed_time
        embed_time += 0.25
        return fake_embed.embed_sync(text)

    monkeypatch.setattr(fake_embed, "embed", embed)
    monkeypatch.setattr(time, "perf_counter", lambda: perf_counter() + embed_time)
    app = create_hub_app(api_key="secret", hub_dir=tmp_path / "hub", client=core)
    async with app.router.lifespan_context(app), HubClient(
        base_url="http://hub", api_key="secret", transport=httpx.ASGITransport(app=app)
    ) as remote:
        assert (await remote.status())["recall"] is None
        await remote.recall(query="端口", workspace=workspace)
        await remote.recall(query="端口", workspace=workspace)
        vector.upsert([_node(9, "external write", dim=fake_embed.dim)])
        await remote.recall(query="端口", workspace=workspace)
        last = (await remote.status())["recall"]
        await remote.recall(query="", limit=0, raw_limit=0, procedure_limit=0)
        assert (await remote.status())["recall"] == last

        assert last["at"] > 0
        samples = last["sample_seconds"]
        # Scoped, wider, and raw lookup each goes through Reader.embed.
        assert samples["embedding"] == pytest.approx([0.75] * 3, abs=0.02)
        assert len(samples["cold"]) == len(samples["warm"]) == len(samples["after_write"]) == 1
        assert samples["after_write"] == [last["seconds"]]
        assert all(samples[k][0] >= 0.75 for k in ("warm", "after_write", "cold"))

        monotonic = time.monotonic
        with monkeypatch.context() as clock:
            clock.setattr(time, "monotonic", lambda: monotonic() + last["window_seconds"] + 1)
            expired = (await remote.status())["recall"]
            assert expired["at"] == last["at"]
            assert not any(expired["sample_seconds"].values())
    await core.aclose()


async def test_raw_recall_reuses_retired_claims_until_a_write(
    tmp_path: Path, fake_embed, fake_llm, fake_jev, monkeypatch
) -> None:
    core = MemoryClient(
        settings=Settings(storage_dir=str(tmp_path / "store"), mode="system1"),
        embed=fake_embed, llm=fake_llm, jev=fake_jev,
    )
    vector = core.factory.vector
    query = "部署端口"
    scope = {"app_id": core.settings.default_app_id, "user_id": HUB_USER_ID}
    embedding = fake_embed.embed_sync(query)
    raw = MemoryNode(
        **scope, layer=Layer.L0_RAW, session_id="source", embedding=embedding,
        content="[user]: 部署端口曾用 8080，现用 9090",
    )
    # The provenance link, not the claim's own session, determines which raw
    # dialogue carries the retired wording.
    old = MemoryNode(
        **scope, layer=Layer.L1_FACT, session_id="different-session",
        content="部署端口用 8080", embedding=embedding, is_latest=False,
        custom={"source_ids": [raw.node_id]},
    )
    live = MemoryNode(
        **scope, layer=Layer.L2_PROFILE, content="部署端口用 9090", embedding=embedding,
        custom={"source_node_id": raw.node_id, "source_ids": [raw.node_id]},
    )
    other_agent = MemoryNode(
        **scope, layer=Layer.L1_FACT, agent_id="other", content="其他 agent 的历史",
        embedding=embedding, is_latest=False, custom={"source_ids": [raw.node_id]},
    )
    vector.upsert([raw, old, live, other_agent])
    claims_reads = []
    get_many = vector.get_many

    def observe_claim_reads(where: dict, limit: int | None = 1000) -> list[MemoryNode]:
        nodes = get_many(where, limit)
        if where.get("layer") == {"$in": [Layer.L1_FACT.value, Layer.L2_PROFILE.value]}:
            claims_reads.append({n.node_id for n in nodes})
        return nodes

    monkeypatch.setattr(vector, "get_many", observe_claim_reads)
    app = create_hub_app(api_key="secret", hub_dir=tmp_path / "hub", client=core)
    async with app.router.lifespan_context(app), HubClient(
        base_url="http://hub", api_key="secret", transport=httpx.ASGITransport(app=app)
    ) as remote:
        async def claims() -> list[str]:
            out = await remote.recall(query=query, limit=0, procedure_limit=0, raw_limit=1)
            (row,) = out["conversations"]
            assert row["memory_id"] == raw.node_id
            return row["retired_claims"]

        assert await claims() == [old.content]
        assert await claims() == [old.content]
        assert claims_reads == [{old.node_id}]

        await remote.delete_memory(live.node_id)
        assert sorted(await claims()) == sorted([old.content, live.content])
        assert claims_reads == [{old.node_id}, {old.node_id, live.node_id}]
    await core.aclose()


@pytest.mark.parametrize(
    ("slow_bucket", "slow_history", "expected_warning"),
    [
        ("cold", [4.9], None),
        ("warm", [4.9, 4.9, 4.9, 0.6, 0.6, 3.0], None),
        ("warm", [3.1, 0.6, 3.2, 3.0, 4.0], "Recall warm"),
        ("after_write", [3.1, 3.2, 3.3], "Recall after write"),
        ("cold", [3.1, 3.2, 3.3], "Recall cold"),
        ("embedding", [3.1, 3.2, 3.3], "Recall embedding"),
    ],
)
def test_doctor_warns_only_on_repeated_slowness(
    tmp_path: Path, monkeypatch, slow_bucket: str, slow_history: list[float],
    expected_warning: str | None,
) -> None:
    samples = {"warm": [0.6], "after_write": [2.5], "cold": [], "embedding": [0.7]}
    samples[slow_bucket] = slow_history
    status = {
        "memories": {"current": 41_000},
        # Older Core versions may still return this field to a newer CLI.
        "noise": {"current": 41_000, "template_current": 20_000},
        "writes": {"done": 1},
        "recall": {"at": 1, "seconds": 4.9, "window_seconds": 900, "sample_seconds": samples},
    }
    monkeypatch.setattr(cli_hub, "daemon_status", lambda _root: status)
    empty = tmp_path / "bin"
    empty.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(empty))
    for key in ("JEV", "LLM", "EMBED"):
        monkeypatch.setenv(f"MEMHUB_{key}_API_KEY", "k")
    out = runner.invoke(cli_main.app, ["doctor", "--json", "--hub-dir", str(tmp_path / "hub")])
    labels = {
        "warm": "Recall warm", "after_write": "Recall after write",
        "cold": "Recall cold", "embedding": "Recall embedding",
    }
    checks = {
        c["name"]: c for c in json.loads(out.output)["checks"] if c["name"].startswith("Recall")
    }
    assert set(checks) == set(labels.values())
    assert {name for name, check in checks.items() if check["status"] == "warn"} == (
        {expected_warning} if expected_warning else set()
    )
    assert "outgrown" not in out.output
    assert "41000 current memories" in out.output
    assert "purge-noise" not in out.output
    assert "Memory hygiene" not in out.output
    for key, name in labels.items():
        if samples[key]:
            assert f"last {samples[key][-1]:.2f}s" in checks[name]["detail"]
