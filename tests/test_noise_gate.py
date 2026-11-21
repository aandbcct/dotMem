"""Retention decisions come from structured extraction, not sentence shape."""

import httpx
import pytest

from memhub import MemoryClient
from memhub.agent.mem_agent import MemAgent
from memhub.cli.hub import build_hub_prompt_context
from memhub.config import Settings
from memhub.hub.server import create_hub_app
from memhub.providers.llm import merge_extract_results
from memhub.types import Layer, MemoryNode


def test_collect_new_memories_drops_narration_before_jev() -> None:
    extracted = {
        "facts": [
            {
                "content": "The assistant confirmed the fix.",
                "speaker": "assistant",
                "durable": False,
            },
            {"content": "项目超时统一设为 7.3 秒", "speaker": "user"},
            {"content": "The assistant reported progress on a passport form.",
             "speaker": "assistant", "durable": False},
        ],
        "identity": [
            {"content": "用户偏好简体中文", "speaker": "user"},
            {"content": "长期偏好使用 ~/notes/index.md 管理项目", "speaker": "user"},
        ],
        "intentions": [],
        "basic_info": {},
    }
    memories, meta = MemAgent._collect_new_memories(extracted, ["src-1"])
    assert memories == [
        "项目超时统一设为 7.3 秒", "用户偏好简体中文", "长期偏好使用 ~/notes/index.md 管理项目",
    ]
    assert len(memories) == len(meta)
    assert all(entry["source_ids"] == ["src-1"] for entry in meta)
    assert all("durable" not in entry for entry in meta)


def test_structured_identity_uses_user_language() -> None:
    memories, meta = MemAgent._collect_new_memories(
        {
            "facts": [],
            "identity": [{"attribute": "偏好", "value": "简体中文", "speaker": "user"}],
            "intentions": [],
            "basic_info": {"常用语言": "中文"},
        },
        ["src-2"],
    )
    assert "用户的偏好是简体中文。" in memories
    assert "用户的常用语言是中文。" in memories
    assert all(entry["kind"] == "profile" for entry in meta)


@pytest.mark.parametrize("labels, retained", [
    ((False, True), True), ((True, False), True),
    ((False, None), True), ((None, False), True), ((False, False), False),
])
def test_chunk_durability_conflicts_conservatively_keep_fact(labels, retained) -> None:
    text = "The assistant recommended Parkside Hotel (+81-3-5555-0199)."
    parts = []
    for label in labels:
        candidate = {"content": text, "speaker": "assistant"}
        if label is not None:
            candidate["durable"] = label
        parts.append({"facts": [candidate]})
    memories, meta = MemAgent._collect_new_memories(merge_extract_results(parts), ["evidence"])
    assert memories == ([text] if retained else [])
    if retained:
        assert meta[0]["owner"] == "agent"


def test_injection_keeps_assistant_concrete_fact() -> None:
    text = "The assistant recommended Parkside Hotel (+81-3-5555-0199)."
    item = {"memory_id": "hotel", "category": "fact", "kind": "fact", "content": text}
    prompt = build_hub_prompt_context({"memories": {"normal": [item]}}, "What is the hotel number?")
    assert text in prompt


async def test_retired_bulk_noise_route_cannot_delete_records(
    tmp_path, fake_embed, fake_llm, fake_jev,
) -> None:
    hub = tmp_path / "hub"
    client = MemoryClient(
        settings=Settings(storage_dir=str(hub / "store"), hub_capture_dsh=False),
        embed=fake_embed, llm=fake_llm, jev=fake_jev,
    )
    app = create_hub_app(api_key="test-token", hub_dir=hub, client=client)
    try:
        text = "The assistant recommended Parkside Hotel (+81-3-5555-0199)."
        node = MemoryNode(
            text, Layer.L1_FACT, client.settings.default_app_id, "default",
            kind="fact", embedding=await fake_embed.embed(text),
        )
        client.factory.vector.upsert([node])
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as http:
            response = await http.post(
                "/v1/hub/memories/purge", json={"dry_run": False, "confirm": True},
            )
        assert response.status_code in {404, 405}
        assert client.factory.vector.get(node.node_id).content == text
    finally:
        await client.aclose()
