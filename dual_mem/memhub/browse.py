# -*- coding: utf-8 -*-
"""Rows for the MemHub memory browser (``/ui``) and its JSON API.

A memory belongs to a project when the write that produced it carried a
workspace: the raw dialogue keeps it as ``custom["workspace_id"]`` and a
procedure as its ``workspace_id``; every other memory extracted from that
dialogue shares it through ``source_ids``. Memories of plain chats have no
project. Both live in the replicated store, so every device sees the same
projects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_mem.coding.types import procedure_detail
from dual_mem.types import MemoryNode


def node_workspace(node: MemoryNode) -> str:
    """The workspace a raw dialogue or a procedure was written from, else ``""``."""
    detail = procedure_detail(node)
    if detail is not None:
        return detail.workspace_id or ""
    return str((node.custom or {}).get("workspace_id") or "")


def workspaces_by_source(nodes: list[MemoryNode]) -> dict[str, str]:
    """Raw dialogue id -> its workspace (its own, else its procedures')."""
    out: dict[str, str] = {}
    for node in nodes:
        workspace = node_workspace(node)
        if not workspace:
            continue
        if procedure_detail(node) is None:
            out[node.node_id] = workspace
            continue
        for source_id in (node.custom or {}).get("source_ids", []):
            out.setdefault(source_id, workspace)
    return out


def workspace_sessions(nodes: list[MemoryNode], workspace: str) -> list[str]:
    """Session ids of the dialogues written from ``workspace`` (paths resolved).

    ``nodes`` are raw dialogues and procedures; every memory extracted from a
    dialogue carries its session id, so these ids scope all of them.
    """
    target = str(Path(workspace).resolve())
    resolved: dict[str, str] = {}
    sessions: set[str] = set()
    for node in nodes:
        found = node_workspace(node)
        if not found or not node.session_id:
            continue
        if found not in resolved:
            resolved[found] = str(Path(found).resolve())
        if resolved[found] == target:
            sessions.add(node.session_id)
    return sorted(sessions)


def browse_rows(nodes: list[MemoryNode]) -> list[dict[str, Any]]:
    """Every version, newest first, with its project and source host."""
    by_source = workspaces_by_source(nodes)
    rows = []
    for node in nodes:
        detail = procedure_detail(node)
        source_ids = list((node.custom or {}).get("source_ids", []))
        project = (
            node_workspace(node)
            or by_source.get(node.node_id)
            or next((by_source[s] for s in source_ids if s in by_source), "")
        )
        rows.append(
            {
                "id": node.node_id,
                "layer": node.layer.value,
                "kind": node.kind,
                "content": node.content,
                "project": project,
                "source": node.session_id.partition(":")[0],
                "session_id": node.session_id,
                "at": node.memory_at or node.gmt_created,
                "is_latest": node.is_latest,
                # Retired by a delete, as opposed to replaced by a newer version.
                "deleted": bool((node.custom or {}).get("archived")),
                "supersedes": list(node.supersedes),
                "superseded_by": list(node.superseded_by),
                "source_ids": source_ids,
                "procedure": detail.to_dict() if detail is not None else None,
            }
        )
    rows.sort(key=lambda row: row["at"], reverse=True)
    return rows


__all__ = [
    "browse_rows",
    "node_workspace",
    "workspace_sessions",
    "workspaces_by_source",
]
