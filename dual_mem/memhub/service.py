# -*- coding: utf-8 -*-
"""Login service for the MemHub Core.

macOS uses a LaunchAgent, Linux a systemd user unit. It starts at login and
restarts on failure. No credentials are copied into the service file: like any
dual-mem process, the Core reads model keys from ``~/.dual-mem/config.yaml``
(a login service does not see variables exported in a shell profile).
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dual_mem.memhub.config import hub_root

LABEL = "com.dual-mem.hub"
SYSTEMD_UNIT = "dual-mem-hub.service"

Runner = Callable[[list[str]], dict[str, Any]]


@dataclass(frozen=True)
class _Service:
    label: str
    unit: str
    description: str
    log_name: str


CORE = _Service(LABEL, SYSTEMD_UNIT, "dual-mem MemHub Core", "serve.log")


def _argv(hub_dir: Path, host: str, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "dual_mem.cli",
        "hub",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--hub-dir",
        str(hub_dir),
    ]


def launchd_plist_path(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def systemd_unit_path(unit: str = SYSTEMD_UNIT) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user" / unit


def _plist(service: _Service, argv: list[str], workdir: Path, logs: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": service.label,
            "ProgramArguments": argv,
            "RunAtLoad": True,
            # The Core exits 0 only when asked to stop.
            "KeepAlive": {"SuccessfulExit": False},
            # launchd throttles respawns to this interval (seconds).
            "ThrottleInterval": 10,
            "WorkingDirectory": str(workdir),
            "StandardOutPath": str(logs / service.log_name),
            "StandardErrorPath": str(logs / service.log_name),
        }
    )


def _unit(service: _Service, argv: list[str], workdir: Path, logs: Path) -> str:
    return "\n".join(
        [
            "[Unit]",
            f"Description={service.description}",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={workdir}",
            f"ExecStart={shlex.join(argv)}",
            "Restart=on-failure",
            "RestartSec=5",
            f"StandardOutput=append:{logs / service.log_name}",
            f"StandardError=append:{logs / service.log_name}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def render_launchd_plist(
    *, hub_dir: Path, host: str = "127.0.0.1", port: int = 8765, log_dir: Path | None = None
) -> bytes:
    return _plist(CORE, _argv(hub_dir, host, port), hub_dir, log_dir or hub_dir / "logs")


def render_systemd_unit(
    *, hub_dir: Path, host: str = "127.0.0.1", port: int = 8765, log_dir: Path | None = None
) -> str:
    return _unit(CORE, _argv(hub_dir, host, port), hub_dir, log_dir or hub_dir / "logs")


def _run(argv: list[str]) -> dict[str, Any]:
    result = subprocess.run(argv, capture_output=True, text=True)
    return {"command": argv, "returncode": result.returncode, "stderr": result.stderr.strip()}


def _install(service: _Service, argv: list[str], root: Path, runner: Runner) -> dict[str, Any]:
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    if sys.platform == "darwin":
        path = launchd_plist_path(service.label)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_plist(service, argv, root, logs))
        actions = [
            runner(["launchctl", "unload", str(path)]),
            runner(["launchctl", "load", str(path)]),
        ]
    else:
        path = systemd_unit_path(service.unit)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_unit(service, argv, root, logs), encoding="utf-8")
        actions = [
            runner(["systemctl", "--user", "daemon-reload"]),
            # restart: a re-run must pick up a changed port.
            runner(["systemctl", "--user", "enable", service.unit]),
            runner(["systemctl", "--user", "restart", service.unit]),
        ]
    return {
        "service_file": str(path),
        "log": str(logs / service.log_name),
        "actions": actions,
    }


def _uninstall(service: _Service, runner: Runner) -> dict[str, Any]:
    if sys.platform == "darwin":
        path = launchd_plist_path(service.label)
        actions = [runner(["launchctl", "unload", str(path)])] if path.is_file() else []
    else:
        path = systemd_unit_path(service.unit)
        actions = (
            [runner(["systemctl", "--user", "disable", "--now", service.unit])]
            if path.is_file()
            else []
        )
    removed = path.is_file()
    if removed:
        path.unlink()
    return {"service_file": str(path), "removed": removed, "actions": actions}


def install_service(
    *,
    hub_dir: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Write and load the Core login service for this Hub directory."""
    root = hub_root(hub_dir)
    return _install(CORE, _argv(root, host, port), root, runner)


def uninstall_service(*, runner: Runner = _run) -> dict[str, Any]:
    return _uninstall(CORE, runner)
