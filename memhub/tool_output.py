# -*- coding: utf-8 -*-
"""Size bounds for what a memory tool hands back to an agent.

A tool result lands in the host agent's context window. Stored content has no
upper bound (one raw dialogue can be 80K chars), so every tool caps both the row
count and each row's text; the worst case is then fixed by construction instead
of by whatever the store happens to hold.
"""

from __future__ import annotations

# Rows per call.
SEARCH_MAX_ROWS = 20
LIST_MAX_ROWS = 50
CONVERSATION_MAX_ROWS = 10
SCOPE_MAX_ROWS = 200
CONTEXT_MAX_TURNS = 3

# Characters per text field.
SEARCH_ROW_CHARS = 600
LIST_ROW_CHARS = 200
CONVERSATION_ROW_CHARS = 1_500
ANCHOR_CHARS = 8_000
GET_CHARS = 8_000


def clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` chars, marked with its original length when cut."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[已截断，原文共 {len(text)} 字]"


def cap(limit: int, maximum: int) -> int:
    """A caller's row ``limit`` held within ``1..maximum``."""
    return max(1, min(int(limit), maximum))


_VERSION_LISTS = ("evolution_chain", "previous", "conflicts")


def clip_item(item: dict, limit: int) -> dict:
    """A ``MemoryItem.to_dict()`` with its content, its versions' content and a
    procedure's solution each clipped to ``limit``."""
    out = {**item, "content": clip(item["content"], limit)}
    for key in _VERSION_LISTS:
        if key in out:
            out[key] = [
                {**version, "content": clip(version["content"], limit)}
                for version in out[key]
            ]
    if "procedure" in out:
        procedure = out["procedure"]
        out["procedure"] = {**procedure, "solution": clip(procedure["solution"], limit)}
    return out