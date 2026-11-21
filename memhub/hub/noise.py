# -*- coding: utf-8 -*-
"""Hub 侧批量精简规则：L0_RAW 与含个人标识的条目一律保留。"""

from __future__ import annotations

import re
from typing import Any

from memhub.agent.quality import (
    PATH_MARKERS,
    TEMPLATE_PREFIX,
    USER_TEMPLATE_PREFIX,
    has_personal_identifier,
    injection_noise,
    is_one_off_profile,
)
from memhub.types import Layer, MemoryNode

INTENTION_TTL_DAYS = 14

__all__ = [
    "INTENTION_TTL_DAYS",
    "injection_noise",
    "normalized",
    "purge_reason",
    "template_current",
]


def normalized(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text).lower()


def purge_reason(row: dict[str, Any], now: float) -> str | None:
    """返回清理原因码，None 表示必须保留。"""
    if row.get("kind") == "raw" or row.get("layer") == "L0_RAW":
        return None
    content = str(row.get("content") or "")
    if has_personal_identifier(content):
        return None
    kind = row.get("kind")
    if kind == "fact" and content.startswith(TEMPLATE_PREFIX):
        return "assistant_narration"
    if (
        kind == "fact"
        and content.startswith(USER_TEMPLATE_PREFIX)
        and any(marker in content for marker in PATH_MARKERS)
    ):
        return "path_snapshot"
    if kind == "intention":
        at = row.get("at") or 0
        if at and now - at > INTENTION_TTL_DAYS * 86400:
            return "stale_intention"
    if kind == "profile" and is_one_off_profile(content):
        return "one_off_profile"
    return None


def template_current(nodes: list[MemoryNode]) -> int:
    """当前生效的非 raw 节点里，助手播报模板的条数。"""
    return sum(
        1
        for node in nodes
        if node.layer != Layer.L0_RAW and node.content.startswith(TEMPLATE_PREFIX)
    )
