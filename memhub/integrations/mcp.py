# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: MCP 后端入口 — 复用 memhub.mcp.server 的 MCPServer builder。
"""
from __future__ import annotations

from typing import Any, Optional

from memhub.client import MemoryClient
from memhub.config import Settings


def build_mcp_backend(
    *,
    client: Optional[MemoryClient] = None,
    settings: Optional[Settings] = None,
    storage_dir: Optional[str] = None,
    mode: Optional[str] = None,
    embed: Any = None,
    llm: Any = None,
) -> Any:
    """Build the memhub MCP server (mcp 2.x ``MCPServer``).

    Constructs a ``MemoryClient`` from the provided settings / storage_dir / mode /
    injected ``embed`` / ``llm`` when ``client`` is not given, then hands it to the
    shared ``build_mcp`` so the integration layer and the standalone ``memhub-mcp``
    CLI use one implementation. Host/port belong to ``server.run(transport=...,
    host=..., port=...)``, not to construction.
    """
    from memhub.mcp.server import build_mcp

    if client is None:
        if settings is None:
            overrides: dict[str, Any] = {}
            if storage_dir is not None:
                overrides["storage_dir"] = storage_dir
            if mode is not None:
                overrides["mode"] = mode
            settings = Settings(**overrides)
        client = MemoryClient(settings=settings, embed=embed, llm=llm)
    return build_mcp(client=client)
