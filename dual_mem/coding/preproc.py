# -*- coding: utf-8 -*-
"""Coding-session preprocessing — structural message routing, zero LLM.

Messages are OpenAI-style dicts: role/content, optional assistant ``tool_calls``
(flat ``name``/``arguments`` or nested under ``function``), and ``role="tool"``
results.

What a coding session is remembered by is what the user asked and what the
assistant finally told them. The tool trajectory in between (reads, greps, test
runs and their output) is how the answer was reached, not what is worth
recalling, and it is most of the bytes. So the trajectory contributes only two
structured facts — that this was tool work, and which files it touched — and
the memory pipeline sees the dialogue.
"""
import json
from typing import Any

PATH_KEYS: tuple[str, ...] = (
    "path",
    "file_path",
    "filePath",
    "filename",
    "file",
    "filepath",
    "target_path",
    "notebook_path",
    "src_path",
    "dst_path",
)
# Space a clipped message's elision marker takes; ``fit_dialogue`` budgets for it.
_MARKER_RESERVE = 48


def has_any_tool_message(messages: list[dict]) -> bool:
    """Check if messages contain tool messages or assistant tool_calls."""
    return any(
        m.get("role") == "tool" or (m.get("role") == "assistant" and m.get("tool_calls"))
        for m in messages
    )


def extract_files(messages: list[dict]) -> list[str]:
    """File paths named by the well-known path arguments of tool calls."""
    paths: set[str] = set()
    for m in messages:
        for tc in m.get("tool_calls") or []:
            args = tc.get("arguments", tc.get("function", {}).get("arguments"))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            if not isinstance(args, dict):
                continue
            for k in PATH_KEYS:
                v = args.get(k)
                if isinstance(v, str) and v:
                    paths.add(v)
    return sorted(paths)


def _text(content: Any) -> str:
    """Text of a message content: strings as-is, structured parts as JSON."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def clip_text(text: str, max_chars: int) -> str:
    """Keep head and tail of an oversized text; the middle is elided."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n[… {len(text) - 2 * half} chars elided …]\n{text[-half:]}"


def coding_dialogue(messages: list[dict]) -> list[dict]:
    """Every user message, and the assistant's last text before the next one.

    Tool results, tool-call messages and the narration between calls are
    dropped; what is left is the request and the answer the user read.
    """
    out: list[dict] = []
    answer: dict | None = None
    for m in messages:
        role = m.get("role")
        text = _text(m.get("content")).strip()
        if role == "user":
            if answer is not None:
                out.append(answer)
                answer = None
            if text:
                out.append({"role": "user", "content": text})
        elif role == "assistant" and text:
            answer = {"role": "assistant", "content": text}
    if answer is not None:
        out.append(answer)
    return out


def _caps(lengths: list[int], budget: int) -> list[int]:
    """Water-filling: short items stay whole, long ones share what is left equally."""
    caps = list(lengths)
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    left, remaining = budget, len(lengths)
    for i in order:
        share = max(left // remaining, 0) if remaining else 0
        caps[i] = min(lengths[i], share)
        left -= caps[i]
        remaining -= 1
    return caps


def fit_dialogue(messages: list[dict], max_chars: int) -> list[dict]:
    """Bound a dialogue to ``max_chars`` of content, keeping every message.

    The user's words carry the memory, so they keep up to three quarters of the
    budget (all of it they need, if less); the answers share the rest. An
    oversized message keeps its head and tail.
    """
    if max_chars <= 0:
        return messages
    lengths = [len(m["content"]) for m in messages]
    if sum(lengths) <= max_chars:
        return messages
    budget = max(max_chars - _MARKER_RESERVE * len(messages), 0)
    users = [i for i, m in enumerate(messages) if m["role"] == "user"]
    others = [i for i, m in enumerate(messages) if m["role"] != "user"]
    user_caps = _caps([lengths[i] for i in users], min(sum(lengths[i] for i in users), budget * 3 // 4))
    other_caps = _caps([lengths[i] for i in others], budget - sum(user_caps))
    caps = dict(zip(users, user_caps)) | dict(zip(others, other_caps))
    return [
        dict(m, content=clip_text(m["content"], caps[i])) for i, m in enumerate(messages)
    ]
