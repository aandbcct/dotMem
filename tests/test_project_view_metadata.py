# -*- coding: utf-8 -*-
"""项目视图只读取来源元数据，不把原始对话正文拉进内存。"""

import json

from memhub import MemoryClient
from memhub.config import Settings
from memhub.hub.server import project_sessions_and_view
from memhub.storage.vector_store import ChromaVectorStore
from memhub.types import Layer, MemoryNode


def _l0(content: str, session_id: str, workspace: str, *, app_id: str) -> MemoryNode:
    return MemoryNode(
        content=content,
        layer=Layer.L0_RAW,
        app_id=app_id,
        user_id="default",
        agent_id="",
        session_id=session_id,
        kind="raw",
        custom={"workspace_id": workspace},
        is_latest=True,
        embedding=[0.0] * 1536,
    )


def test_metadata_nodes_keep_provenance_without_documents(tmp_path) -> None:
    store = ChromaVectorStore(str(tmp_path))
    node = _l0("x" * 50_000, "s-1", "/w/proj", app_id="memhub")
    store.upsert([node])
    rows = store.get_metadata_nodes({"layer": "L0_RAW"})
    assert len(rows) == 1
    assert rows[0].content == ""
    assert rows[0].session_id == "s-1"
    assert rows[0].custom == {"workspace_id": "/w/proj"}
    assert store.get(node.node_id).content == "x" * 50_000
    store.close()


async def test_project_view_never_reads_l0_documents(
    tmp_path, fake_embed, fake_llm, fake_jev, monkeypatch
) -> None:
    core = MemoryClient(
        settings=Settings(storage_dir=str(tmp_path / "store"), mode="system1"),
        embed=fake_embed,
        llm=fake_llm,
        jev=fake_jev,
    )
    vector = core.factory.vector
    app_id = core.settings.default_app_id
    raw = _l0("对话正文" * 20_000, "s-1", "/w/proj", app_id=app_id)
    raw.embedding = await fake_embed.embed(raw.content)
    fact = MemoryNode(
        "项目约定：金额一律用整数分",
        Layer.L1_FACT,
        app_id,
        "default",
        agent_id="",
        session_id="s-1",
        kind="fact",
        owner="user",
        custom={"workspace_id": "/w/proj", "source_ids": [raw.node_id]},
        embedding=await fake_embed.embed("项目约定：金额一律用整数分"),
    )
    vector.upsert([raw, fact])

    original = vector.get_many
    seen: list[dict] = []

    def spy(where, limit=1000):
        seen.append(where)
        return original(where, limit)

    monkeypatch.setattr(vector, "get_many", spy)
    sessions, view = project_sessions_and_view(core, "/w/proj")
    assert sessions == ["s-1"]
    assert view is not None
    assert any("金额一律用整数分" in item["content"] for item in view["conventions"])
    assert not any("L0_RAW" in json.dumps(where, default=str) for where in seen)
    await core.aclose()
