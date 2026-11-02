# -*- coding: utf-8 -*-
"""Rows for the MemHub memory browser (``/ui``) and its JSON API.

A memory belongs to a project when the write that produced it carried a
workspace: a procedure stores its ``workspace_id``; the raw dialogue it came
from and every other memory extracted from that dialogue share it through
``source_ids``. Memories of plain chats have no project.
"""

from __future__ import annotations

from typing import Any

from dual_mem.coding.types import procedure_detail
from dual_mem.types import MemoryNode


def workspaces_by_source(nodes: list[MemoryNode]) -> dict[str, str]:
    """Raw dialogue id -> workspace of the procedures extracted from it."""
    out: dict[str, str] = {}
    for node in nodes:
        detail = procedure_detail(node)
        if detail is None or not detail.workspace_id:
            continue
        for source_id in (node.custom or {})["source_ids"]:
            out.setdefault(source_id, detail.workspace_id)
    return out


def browse_rows(nodes: list[MemoryNode]) -> list[dict[str, Any]]:
    """Every version, newest first, with its project and source host."""
    by_source = workspaces_by_source(nodes)
    rows = []
    for node in nodes:
        detail = procedure_detail(node)
        source_ids = list((node.custom or {}).get("source_ids", []))
        if detail is not None and detail.workspace_id:
            project = detail.workspace_id
        else:
            project = by_source.get(node.node_id) or next(
                (by_source[s] for s in source_ids if s in by_source), ""
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


__all__ = ["browse_rows", "workspaces_by_source"]
