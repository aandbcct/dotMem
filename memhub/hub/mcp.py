# -*- coding: utf-8 -*-
"""Local stdio MCP proxy for one bearer-authenticated MemHub Core."""

import argparse
import re
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

import httpx

from memhub.hub.browse import current_rows, is_excluded_workspace
from memhub.hub.client import HubClient
from memhub.hub.config import hub_settings
from memhub.hub.redact import sanitize_text
from memhub.tool_output import (
    ANCHOR_CHARS,
    CONTEXT_MAX_TURNS,
    CONVERSATION_MAX_ROWS,
    CONVERSATION_ROW_CHARS,
    GET_CHARS,
    LIST_MAX_ROWS,
    LIST_ROW_CHARS,
    SEARCH_MAX_ROWS,
    SEARCH_ROW_CHARS,
    cap,
    clip,
)

_SCOPE_NOTE = "已绑定本机个人记忆，不需要也不能传 user_id / app_id / agent_id。"
_MEMORY_GROUPS = (
    "profile",
    "proactive",
    "normal",
    "raw",
    "schema",
    "catalog",
    "procedures",
)


async def _call(awaitable: Awaitable[Any]) -> Any:
    """Turn a Core HTTP failure into a readable tool result instead of a traceback."""
    try:
        return await awaitable
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "error": f"Hub Core 返回 {exc.response.status_code}: {exc.response.text[:200]}",
            "hint": "按错误信息修正参数后只重试一次。",
        }


_L0_ID_RE = re.compile(
    r"^(?:L0\s+)?([0-9a-f]{8,36}(?:-[0-9a-f-]+)*)$", re.IGNORECASE
)


def _looks_like_l0_id(query: str) -> str:
    """The L0 id inside ``query``, or ``""``.

    Needs 8+ hex chars, at least one letter and at least one digit. With a bare hex
    class, an English word such as ``decade`` matched and the tool answered a
    search with a 404; without the digit rule, a purely numeric query like
    ``1234567890`` would too.
    """
    match = _L0_ID_RE.match(query.strip())
    if match is None:
        return ""
    value = match.group(1)
    has_letter = any(ch in "abcdefABCDEF" for ch in value)
    has_digit = any(ch.isdigit() for ch in value)
    return value if has_letter and has_digit else ""


def _source_ref(item: dict[str, Any]) -> str:
    """The 8-char L0 ref for a hit: ``source_node_id``, else its ``source_ids`` head.

    Facts get ``custom.source_node_id`` from the reconciler, but a procedure keeps
    only ``custom.source_ids``, so keying on ``source_node_id`` alone left every
    engineering memory without a ref to open.
    """
    candidates = [item.get("source_node_id"), *(item.get("source_ids") or [])]
    for candidate in candidates:
        if candidate:
            return str(candidate)[:8]
    return ""


def _search_rows(memories: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Flatten grouped recall results into one agent-readable list with ids.

    Each content is clipped; ``memory_get`` returns a row in full.
    """
    rows = [
        {
            "id": item.get("memory_id", ""),
            "content": clip(item.get("content", ""), SEARCH_ROW_CHARS),
            "category": item.get("category", group),
            "layer": item.get("layer", ""),
            "score": item.get("score", 0.0),
            "group": group,
            **({"source_id": ref} if (ref := _source_ref(item)) else {}),
        }
        for group in _MEMORY_GROUPS
        for item in (memories.get(group) or [])
    ]
    rows.sort(key=lambda row: row["score"], reverse=True)
    return rows[:limit]


def _list_rows(rows: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "content": clip(" ".join(str(row["content"]).split()), LIST_ROW_CHARS),
            "layer": row["layer"],
            "kind": row["kind"],
            "at": row["at"],
        }
        for row in current_rows(rows, query=query, limit=limit)
    ]


def _memory_view(item: dict[str, Any]) -> dict[str, Any]:
    """One memory for ``memory_get``: identity, state and its text once.

    A procedure's ``content`` is its task and solution again, so only the
    structured ``procedure`` is returned for it.
    """
    view: dict[str, Any] = {
        "memory_id": item["memory_id"],
        "kind": item.get("kind", ""),
        "layer": item.get("layer", ""),
        "is_latest": item.get("is_latest", True),
        **({"archived": True} if item.get("archived") else {}),
        **({"memory_at": item["memory_at"]} if item.get("memory_at") else {}),
        **({"update_type": item["update_type"]} if item.get("update_type") else {}),
    }
    procedure = item.get("procedure")
    if procedure:
        view["procedure"] = {
            "task": procedure.get("task", ""),
            "solution": clip(procedure.get("solution", ""), GET_CHARS),
            "boundary_scope": procedure.get("boundary_scope", ""),
            "boundary_envs": procedure.get("boundary_envs", ""),
            "files": procedure.get("files", []),
            **({"workspace_id": procedure["workspace_id"]} if procedure.get("workspace_id") else {}),
            **({"branch": procedure["branch"]} if procedure.get("branch") else {}),
        }
    else:
        view["content"] = clip(item.get("content", ""), GET_CHARS)
    ref = _source_ref(item)
    if ref:
        view["source_id"] = ref
    return view


def _conversation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Raw dialogue rows, each clipped; the anchor of a source lookup gets more room."""
    out = []
    for row in rows:
        anchor = bool(row.get("is_anchor"))
        memory_id = str(row.get("memory_id", ""))
        out.append(
            {
                "source_id": row.get("source_id") or memory_id[:8],
                "session_id": row.get("session_id", ""),
                **({"memory_at": row["memory_at"]} if row.get("memory_at") else {}),
                **({"workspace_id": row["workspace_id"]} if row.get("workspace_id") else {}),
                **({"score": row["score"]} if "score" in row else {}),
                **({"is_anchor": True} if anchor else {}),
                "content": clip(
                    str(row.get("content", "")),
                    ANCHOR_CHARS if anchor else CONVERSATION_ROW_CHARS,
                ),
                **(
                    {
                        "retired_claims": [
                            clip(str(c), LIST_ROW_CHARS)
                            for c in row["retired_claims"][:5]
                        ]
                    }
                    if row.get("retired_claims")
                    else {}
                ),
            }
        )
    return out


def build_hub_mcp(
    *,
    hub_dir: str | Path | None = None,
    client: HubClient | None = None,
):
    """Build the MCP server. The Core is resolved per tool call, so a host that
    starts before ``memhub serve`` still gets working tools once the Core is up."""
    try:
        from mcp.server.mcpserver import Context, MCPServer
    except ImportError as exc:
        raise ImportError(
            "MemHub MCP requires: pip install memhub[mcp]"
        ) from exc

    @asynccontextmanager
    async def core() -> AsyncIterator[HubClient]:
        if client is not None:
            yield client
            return
        async with HubClient.from_config(hub_dir) as connected:
            yield connected

    # WARNING, not the INFO default: every tool call is an HTTP request to the Core,
    # and an INFO line per request floods the host's MCP log (stderr).
    mcp = MCPServer("memhub-hub", log_level="WARNING")

    async def excluded(workspace: str | None, ctx: Context) -> bool:
        rules = hub_settings(hub_dir).memhub_excluded_projects
        if not rules:
            return False
        if any(is_excluded_workspace(ws, rules) for ws in (str(Path.cwd()), workspace)):
            return True
        try:
            capabilities = ctx.client_capabilities
        except ValueError:
            # The SDK also supports calling a tool directly, without an MCP peer.
            return False
        if capabilities is None or capabilities.roots is None:
            return False
        roots = await ctx.session.list_roots()
        paths = [urlsplit(str(root.uri)) for root in roots.roots]
        return any(is_excluded_workspace(url2pathname(uri.path), rules) for uri in paths
                   if uri.scheme == "file" and uri.netloc in ("", "localhost"))

    @mcp.tool(
        description=(
            "记住用户明确要求长期保留的一件事：偏好、约定、规矩、决策"
            "（「记住」「别忘了」「以后都按这个」）。"
            "content 写成一条完整、可独立理解的第三人称陈述句，例如"
            "「用户要求前端改动在真实页面里验证」。"
            "返回 ok=true 即写入已接收，status=pending 是正常终态，后台自动抽取。"
            "普通聊天内容由宿主 Hook 自动采集；撤销或修改旧约定请用 memory_list + memory_update。"
            + _SCOPE_NOTE
        )
    )
    async def memory_add(ctx: Context, content: str, workspace: str | None = None) -> dict:
        if await excluded(workspace, ctx):
            return {"ok": False, "skipped": "excluded"}
        content = sanitize_text(content)
        if not content.strip():
            return {"ok": False, "skipped": "private"}
        scope = {"workspace_id": workspace} if workspace else {}
        async with core() as hub:
            receipt = await _call(
                hub.write(content=content, request_id=f"hub-mcp:{uuid.uuid4()}", **scope)
            )
        if not isinstance(receipt, dict) or receipt.get("ok") is False:
            return receipt
        if skipped := (receipt.get("result") or {}).get("skipped"):
            return {"ok": False, "skipped": skipped}
        return {
            "ok": True,
            "request_id": receipt.get("request_id", ""),
            "status": receipt.get("status", "pending"),
            "hint": "写入已接收，后台会自动抽取；status=pending 是正常终态，不需要轮询或重复写入。",
        }

    @mcp.tool(
        description=(
            "语义检索当前生效的个人记忆（画像、事实、意图、工程经验）。"
            f"返回 memories 列表（最多 {SEARCH_MAX_ROWS} 条），每项含 id / content / score / layer；"
            f"content 超过 {SEARCH_ROW_CHARS} 字会截断，需要全文时用 memory_get(id)。"
            "每轮提问注入的 <relevant-memories> 已包含相关记忆，先用注入内容，不够再查；"
            "要编辑记忆请用 memory_list。" + _SCOPE_NOTE
        )
    )
    async def memory_search(ctx: Context, query: str, limit: int = 10, workspace: str | None = None) -> dict:
        if await excluded(workspace, ctx):
            return {"ok": False, "skipped": "excluded"}
        limit = cap(limit, SEARCH_MAX_ROWS)
        async with core() as hub:
            result = await _call(hub.recall(query=query, limit=limit, raw_limit=0))
        if not isinstance(result, dict) or result.get("ok") is False:
            return result
        return {
            "ok": True,
            "memories": _search_rows(result["memories"], limit),
            "hint": "截断的条目用 memory_get(id) 看全文；没有匹配结果说明当前没有相关记忆。",
        }

    @mcp.tool(
        description=(
            "按记忆 ID（完整 ID 或 >=8 位前缀，如 [procedure-index id=a1b2c3d4] 或项目视图中的 id）"
            "精确读取单条记忆的完整内容（含 procedure 的 task / solution / boundary_envs / files 及来源 L0 短 ID）。"
            f"正文超过 {GET_CHARS} 字会截断。"
            "当注入上下文仅展示目录标题索引且与当前任务相关时调用。" + _SCOPE_NOTE
        )
    )
    async def memory_get(ctx: Context, memory_id: str, workspace: str | None = None) -> dict:
        if await excluded(workspace, ctx):
            return {"ok": False, "skipped": "excluded"}
        async with core() as hub:
            item = await _call(hub.get_memory(memory_id))
        if not isinstance(item, dict) or item.get("ok") is False:
            return item
        return {"ok": True, "memory": _memory_view(item)}

    @mcp.tool(
        description=(
            "列出当前生效的记忆条目及其 id，用于「改掉 / 撤销 / 更新 / 忘掉 / 删掉」"
            "某条约定时定位目标。query 为可选关键词（内容子串匹配），不传则按时间倒序返回最近 limit 条；"
            f"最多 {LIST_MAX_ROWS} 条，content 只是 {LIST_ROW_CHARS} 字预览，全文用 memory_get(id)。"
            "返回的 id 可交给 memory_update / memory_delete。"
            "找不到匹配项时直接 memory_add 写新约定。" + _SCOPE_NOTE
        )
    )
    async def memory_list(ctx: Context, query: str = "", limit: int = 20, workspace: str | None = None) -> dict:
        if await excluded(workspace, ctx):
            return {"ok": False, "skipped": "excluded"}
        limit = cap(limit, LIST_MAX_ROWS)
        async with core() as hub:
            rows = await _call(hub.list_memories())
        if not isinstance(rows, list):
            return rows
        return {
            "ok": True,
            "memories": _list_rows(rows, query, limit),
            "hint": "用返回的 id 调用 memory_update 或 memory_delete；没有匹配项时直接 memory_add 写新约定。",
        }

    @mcp.tool(
        description=(
            "把一条已有记忆改成新内容，旧版本保留为历史。"
            "memory_id 取自 memory_list / memory_search 的 id，content 是完整的第三人称陈述句。"
            "404 表示 id 不存在或已不是当前版本。" + _SCOPE_NOTE
        )
    )
    async def memory_update(ctx: Context, memory_id: str, content: str, workspace: str | None = None) -> dict:
        if await excluded(workspace, ctx):
            return {"ok": False, "skipped": "excluded"}
        async with core() as hub:
            result = await _call(hub.update_memory(memory_id, content))
        if not isinstance(result, dict) or result.get("ok") is False:
            return result
        return {
            "ok": True,
            "memory_id": result.get("memory_id", ""),
            "hint": "记忆已更正并生效。",
        }

    @mcp.tool(
        description=(
            "删除（归档）一条已失效的记忆，memory_id 取自 memory_list / memory_search 的 id。"
            "仅在用户明确要求忘掉/删除时使用；给出新约定时优先用 memory_update。" + _SCOPE_NOTE
        )
    )
    async def memory_delete(ctx: Context, memory_id: str, workspace: str | None = None) -> dict:
        if await excluded(workspace, ctx):
            return {"ok": False, "skipped": "excluded"}
        async with core() as hub:
            result = await _call(hub.delete_memory(memory_id))
        if not isinstance(result, dict) or result.get("ok") is False:
            return result
        return {
            "ok": True,
            "memory_id": result.get("memory_id", memory_id),
            "hint": "记忆已删除。",
        }

    @mcp.tool(
        description=(
            "检索历史对话原文，或按 L0 来源 ID 展开完整原话与同会话前后上下文（渐进式披露）。"
            "1) 传 query 检索「我之前说过/问过什么」的原话、时间和来源；"
            "2) 传 source_id（如 <relevant-memories> 里的 `(来源: L0 37c85fe9)` 或 memory_search 返回的 source_id）"
            f"拉取该条记忆对应的 L0 原始对话及前后 context_turns 轮对话（最多 {CONTEXT_MAX_TURNS} 轮）。"
            f"检索最多返回 {CONVERSATION_MAX_ROWS} 段，每段截断到 {CONVERSATION_ROW_CHARS} 字，"
            f"source_id 指向的那段截断到 {ANCHOR_CHARS} 字。"
            "返回 conversations 列表；session_id 形如 codex:<id>，冒号前是来源工具。"
            + _SCOPE_NOTE
        )
    )
    async def conversation_search(
        ctx: Context,
        query: str = "",
        limit: int = 10,
        source_id: str = "",
        context_turns: int = 1,
        workspace: str | None = None,
    ) -> dict:
        if await excluded(workspace, ctx):
            return {"ok": False, "skipped": "excluded"}
        target_id = source_id.strip()
        if not target_id:
            target_id = _looks_like_l0_id(query)
        limit = cap(limit, CONVERSATION_MAX_ROWS)
        context_turns = max(0, min(int(context_turns), CONTEXT_MAX_TURNS))
        async with core() as hub:
            if target_id:
                result = await _call(
                    hub.get_conversation(target_id, context_turns=context_turns)
                )
            else:
                result = await _call(hub.recall(query=query, limit=0, raw_limit=limit))
        if not isinstance(result, dict) or result.get("ok") is False:
            return result
        return {
            "ok": True,
            **({"source_id": result["source_id"]} if "source_id" in result else {}),
            "conversations": _conversation_rows(
                result["conversations"][: 2 * CONTEXT_MAX_TURNS + 1 if target_id else limit]
            ),
            "hint": "这里只有历史对话原文，不是可编辑的记忆条目；截断处要看全文请用 source_id 单独展开。",
        }

    @mcp.tool(
        description=("查看 Hub Core、记忆数量和写入队列状态，用于排查 memhub 自身故障。" + _SCOPE_NOTE)
    )
    async def memory_status() -> dict:
        async with core() as hub:
            status = await _call(hub.status())
        if not isinstance(status, dict) or status.get("ok") is False:
            return status
        return {**status, "hint": "状态查询不影响记忆内容。"}

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="memhub-hub-mcp")
    parser.add_argument("--hub-dir")
    args = parser.parse_args()
    build_hub_mcp(hub_dir=args.hub_dir).run(transport="stdio")


if __name__ == "__main__":
    main()
