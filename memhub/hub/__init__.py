# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: MemHub — one local Core and one personal memory shared by every CLI.

- ``config``: the local Core's receipt, the sync target, store paths and scan cursors.
- ``client``: bearer HTTP client used by CLI hosts and MCP.
- ``sources``: read Agentica / Claude Code / Codex / Cursor / Hermes transcripts
  into turns and submit them incrementally.
- ``hosts``: install the MCP server, recall skill and capture hooks into each CLI.
- ``service``: run the Core as a login service (launchd / systemd --user).
- ``sync``: replicate the store's change log through a folder, git, or another Core.
"""
from memhub.hub.client import HubClient
from memhub.hub.config import (
    CursorStore,
    HubConfig,
    SyncConfig,
    hub_root,
    hub_store_dir,
    load_sync_config,
    save_sync_config,
)
from memhub.hub.hosts import HOSTS, hosts_status, install_host, uninstall_host
from memhub.hub.sources import (
    SOURCES,
    build_turns,
    default_source_roots,
    locate_transcript,
    scan_file,
    scan_sources,
)

__all__ = [
    "HOSTS",
    "SOURCES",
    "CursorStore",
    "HubClient",
    "HubConfig",
    "SyncConfig",
    "build_turns",
    "default_source_roots",
    "hosts_status",
    "hub_root",
    "hub_store_dir",
    "install_host",
    "load_sync_config",
    "locate_transcript",
    "save_sync_config",
    "scan_file",
    "scan_sources",
    "uninstall_host",
]
