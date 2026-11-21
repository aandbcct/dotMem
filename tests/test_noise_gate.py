# -*- coding: utf-8 -*-
"""抽取闸门与批量精简规则的行为测试。"""

from memhub.agent.mem_agent import MemAgent
from memhub.agent.quality import (
    injection_noise,
    is_low_value_candidate,
    is_one_off_profile,
)
from memhub.hub.noise import normalized, purge_reason

NOW = 1_800_000_000.0


def test_low_value_candidate_drops_only_non_durable_items() -> None:
    assert is_low_value_candidate("The assistant confirmed the fix.", "fact", False)
    assert not is_low_value_candidate(
        "The assistant recommended Parkside Hotel (+81-3-5555-0199).", "fact"
    )
    assert not is_low_value_candidate("The assistant noted passport EQ9821460.", "fact")
    assert not is_low_value_candidate("项目超时统一设为 7.3 秒", "fact")
    assert is_low_value_candidate("用户有日志 ~/.x.log", "profile")


def test_profile_whitelist_keeps_stable_attributes() -> None:
    assert is_one_off_profile("用户有日志 ~/.agentica/logs/x.log")
    assert not is_one_off_profile("用户偏好简体中文")
    assert not is_one_off_profile("高级研发工程师")


def test_collect_new_memories_drops_narration_before_jev() -> None:
    extracted = {
        "facts": [
            {
                "content": "The assistant confirmed the fix.",
                "speaker": "assistant",
                "durable": False,
            },
            {"content": "项目超时统一设为 7.3 秒", "speaker": "user"},
        ],
        "identity": [
            {"content": "用户偏好简体中文", "speaker": "user"},
            {"content": "用户有日志 ~/.agentica/logs/x.log", "speaker": "user"},
        ],
        "intentions": [],
        "basic_info": {},
    }
    memories, meta = MemAgent._collect_new_memories(extracted, ["src-1"])
    assert memories == ["项目超时统一设为 7.3 秒", "用户偏好简体中文"]
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


def test_purge_reason_keeps_raw_and_personal_identifiers() -> None:
    assert purge_reason(
        {"kind": "raw", "layer": "L0_RAW", "content": "The assistant said hi", "at": 0},
        NOW,
    ) is None
    assert purge_reason(
        {
            "kind": "fact",
            "layer": "L1_FACT",
            "content": "The assistant noted passport EQ9821460",
            "at": NOW,
        },
        NOW,
    ) is None
    assert purge_reason(
        {
            "kind": "fact",
            "layer": "L1_FACT",
            "content": "The assistant installed pytest.",
            "at": NOW,
        },
        NOW,
    ) == "assistant_narration"
    assert purge_reason(
        {
            "kind": "fact",
            "layer": "L1_FACT",
            "content": "The user owns /Users/xuming/Documents/Codes/agentica",
            "at": NOW,
        },
        NOW,
    ) == "path_snapshot"
    assert purge_reason(
        {
            "kind": "intention",
            "layer": "L1_FACT",
            "content": "用户计划发布 v0.2.4",
            "at": NOW - 20 * 86400,
        },
        NOW,
    ) == "stale_intention"
    assert purge_reason(
        {
            "kind": "intention",
            "layer": "L1_FACT",
            "content": "用户计划今晚复核结果",
            "at": NOW,
        },
        NOW,
    ) is None
    assert purge_reason(
        {
            "kind": "profile",
            "layer": "L2_PROFILE",
            "content": "用户有日志 ~/.x.log",
            "at": NOW,
        },
        NOW,
    ) == "one_off_profile"


def test_normalized_and_injection_noise() -> None:
    assert normalized("A b, c!") == "abc"
    assert injection_noise("The assistant fixed the build.")
    assert not injection_noise("项目超时统一设为 7.3 秒")
