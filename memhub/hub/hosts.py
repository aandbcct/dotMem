# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Wire MemHub into each CLI host: the memhub MCP server, a recall
skill, and (where the host has hooks) transcript capture / prompt-time recall.

Every host file is edited in place, keeping all other entries; the first edit
leaves ``<file>.memhub-hub.bak``. YAML hosts are rewritten round-trip, so the
user's comments, quoting and indentation survive.
"""
from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ruamel.yaml import YAML

from memhub.hub.config import hub_root

MCP_NAME = "memhub"
SKILL_NAME = "memhub-recall"
SKILL_MARKER = "<!-- managed by memhub hub -->"
AGENTICA_CONSUMER = "memhub"
TOML_BEGIN = "# >>> memhub hub >>>"
TOML_END = "# <<< memhub hub <<<"
# Our hook commands always run this package, which is how we find them again
# (the prefix also matches hooks written as ``-m memhub.cli.main``).
HOOK_SIGNATURE = "-m memhub.cli"
# agentica.shell_hooks.config.SHELL_HOOK_EVENTS; unlisted events default to on there.
AGENTICA_EVENTS = (
    "run.started",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "tool.started",
    "tool.completed",
    "session.started",
    "session.ended",
    "needs.approval",
    "needs.input",
    "needs.resolved",
    "UserPromptSubmit",
)
# One consumer, one command: ``hub hook`` answers UserPromptSubmit with recalled
# memories (beside agentica's own recall, never replacing it) and captures the
# transcript on the rest.
AGENTICA_HOOK_EVENTS = ("UserPromptSubmit", "run.completed", "session.ended")


@dataclass(frozen=True)
class Launch:
    """Host commands use this interpreter and the Core on this machine."""

    host: str
    hub_dir: Path

    def memhub(self, *args: str) -> list[str]:
        return [sys.executable, "-m", "memhub.cli", *args]

    def mcp(self) -> dict[str, Any]:
        return {
            "command": sys.executable,
            "args": [
                "-m",
                "memhub.hub.mcp",
                "--hub-dir",
                str(self.hub_dir),
            ],
        }

    def hook(self) -> list[str]:
        return self.memhub("hook", self.host, "--hub-dir", str(self.hub_dir))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".memhub-hub.bak")
    if path.is_file() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".memhub-hub.tmp")
    tmp.write_text(text, encoding="utf-8")
    if path.is_file():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8") or "{}") if path.is_file() else {}


def _dump_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _round_trip_yaml() -> YAML:
    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    rt.indent(mapping=2, sequence=4, offset=2)
    return rt


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    return _round_trip_yaml().load(path.read_text(encoding="utf-8")) or {}


def _dump_yaml(path: Path, data: dict) -> None:
    buf = io.StringIO()
    _round_trip_yaml().dump(data, buf)
    _write(path, buf.getvalue())


class Part(Protocol):
    def install(self, path: Path, launch: Launch) -> None: ...
    def uninstall(self, path: Path) -> bool: ...
    def installed(self, path: Path) -> bool: ...


class JsonMcp:
    """``{"mcpServers": {...}}`` — Agentica, Claude Code, CodeBuddy, Cursor, WorkBuddy."""

    def install(self, path: Path, launch: Launch) -> None:
        data = _load_json(path)
        data.setdefault("mcpServers", {})[MCP_NAME] = launch.mcp()
        _dump_json(path, data)

    def uninstall(self, path: Path) -> bool:
        data = _load_json(path)
        if MCP_NAME not in data.get("mcpServers", {}):
            return False
        del data["mcpServers"][MCP_NAME]
        _dump_json(path, data)
        return True

    def installed(self, path: Path) -> bool:
        return MCP_NAME in _load_json(path).get("mcpServers", {})


class OpenCodeMcp:
    """OpenCode ``opencode.json`` ``mcp``: ``{"type": "local", "command": [...]}``."""

    def install(self, path: Path, launch: Launch) -> None:
        data = _load_json(path)
        mcp = launch.mcp()
        data.setdefault("mcp", {})[MCP_NAME] = {
            "type": "local",
            "command": [mcp["command"], *mcp["args"]],
        }
        _dump_json(path, data)

    def uninstall(self, path: Path) -> bool:
        data = _load_json(path)
        if MCP_NAME not in data.get("mcp", {}):
            return False
        del data["mcp"][MCP_NAME]
        _dump_json(path, data)
        return True

    def installed(self, path: Path) -> bool:
        return MCP_NAME in _load_json(path).get("mcp", {})


class YamlMcp:
    """Hermes ``config.yaml`` ``mcp_servers``."""

    def install(self, path: Path, launch: Launch) -> None:
        data = _load_yaml(path)
        data.setdefault("mcp_servers", {})[MCP_NAME] = launch.mcp()
        _dump_yaml(path, data)

    def uninstall(self, path: Path) -> bool:
        data = _load_yaml(path)
        if MCP_NAME not in (data.get("mcp_servers") or {}):
            return False
        del data["mcp_servers"][MCP_NAME]
        _dump_yaml(path, data)
        return True

    def installed(self, path: Path) -> bool:
        return MCP_NAME in (_load_yaml(path).get("mcp_servers") or {})


class TomlMcp:
    """Codex ``config.toml``: a marked block, so the rest of the file stays byte-identical."""

    _block = re.compile(rf"\n?{re.escape(TOML_BEGIN)}.*?{re.escape(TOML_END)}\n", re.DOTALL)

    def install(self, path: Path, launch: Launch) -> None:
        text = self._block.sub("", path.read_text(encoding="utf-8")) if path.is_file() else ""
        mcp = launch.mcp()
        block = (
            f"{TOML_BEGIN}\n[mcp_servers.{MCP_NAME}]\n"
            f"command = {json.dumps(mcp['command'])}\n"
            f"args = {json.dumps(mcp['args'])}\n{TOML_END}\n"
        )
        sep = "\n" if text and not text.endswith("\n") else ""
        _write(path, f"{text}{sep}\n{block}" if text else block)

    def uninstall(self, path: Path) -> bool:
        if not self.installed(path):
            return False
        _write(path, self._block.sub("", path.read_text(encoding="utf-8")))
        return True

    def installed(self, path: Path) -> bool:
        return path.is_file() and TOML_BEGIN in path.read_text(encoding="utf-8")


class AgenticaHooks:
    """``settings.hooks.consumers`` in ~/.agentica/config.yaml: one consumer that
    recalls on ``UserPromptSubmit`` and captures on run end.

    Every event is listed: agentica turns an unlisted one on. Ours are found by
    ``HOOK_SIGNATURE`` in the command, so a consumer from an older install is
    replaced whatever it was named.
    """

    @staticmethod
    def _ours(consumer: dict) -> bool:
        return HOOK_SIGNATURE in " ".join(consumer.get("command") or [])

    def install(self, path: Path, launch: Launch) -> None:
        data = _load_yaml(path)
        hooks = data.setdefault("settings", {}).setdefault("hooks", {})
        hooks["enabled"] = True
        consumers = [c for c in hooks.get("consumers") or [] if not self._ours(c)]
        consumers.append(
            {
                "name": AGENTICA_CONSUMER,
                "command": launch.hook(),
                "events": {e: e in AGENTICA_HOOK_EVENTS for e in AGENTICA_EVENTS},
            }
        )
        hooks["consumers"] = consumers
        _dump_yaml(path, data)

    def uninstall(self, path: Path) -> bool:
        data = _load_yaml(path)
        hooks = (data.get("settings") or {}).get("hooks") or {}
        consumers = hooks.get("consumers") or []
        kept = [c for c in consumers if not self._ours(c)]
        if len(kept) == len(consumers):
            return False
        hooks["consumers"] = kept
        _dump_yaml(path, data)
        return True

    def installed(self, path: Path) -> bool:
        hooks = (_load_yaml(path).get("settings") or {}).get("hooks") or {}
        return any(self._ours(c) for c in hooks.get("consumers") or [])


class ClaudeStyleHooks:
    """Claude Code ``settings.json`` / Codex ``hooks.json``: recall on prompt, capture on Stop."""

    @staticmethod
    def _ours(entry: dict) -> bool:
        return any(HOOK_SIGNATURE in h.get("command", "") for h in entry.get("hooks", []))

    def install(self, path: Path, launch: Launch) -> None:
        data = _load_json(path)
        hooks = data.setdefault("hooks", {})
        command = shlex.join(launch.hook())
        for event in ("UserPromptSubmit", "Stop"):
            entries = [e for e in hooks.get(event, []) if not self._ours(e)]
            entries.append({"hooks": [{"type": "command", "command": command}]})
            hooks[event] = entries
        _dump_json(path, data)

    def uninstall(self, path: Path) -> bool:
        data = _load_json(path)
        hooks = data.get("hooks", {})
        changed = False
        for event, entries in list(hooks.items()):
            kept = [e for e in entries if not self._ours(e)]
            if len(kept) != len(entries):
                hooks[event] = kept
                changed = True
        if changed:
            _dump_json(path, data)
        return changed

    def installed(self, path: Path) -> bool:
        hooks = _load_json(path).get("hooks", {})
        return any(self._ours(e) for entries in hooks.values() for e in entries)


class OpenCodePlugin:
    """OpenCode auto-loaded plugin bridging its events onto ``hub hook opencode``:
    ``chat.message`` → ``UserPromptSubmit`` (recall), ``session.idle`` → ``Stop`` (capture)."""

    def install(self, path: Path, launch: Launch) -> None:
        hook = json.dumps(launch.hook(), ensure_ascii=False)
        script = f"""// {SKILL_MARKER}
import {{ spawn }} from "child_process";

const HOOK = {hook};

function run(payload) {{
  return new Promise((resolve) => {{
    try {{
      const child = spawn(HOOK[0], HOOK.slice(1), {{ stdio: ["pipe", "pipe", "ignore"] }});
      let out = "";
      child.stdout.on("data", (chunk) => {{ out += chunk.toString(); }});
      child.on("close", () => resolve(out));
      child.on("error", () => resolve(""));
      child.stdin.end(JSON.stringify(payload));
    }} catch {{
      resolve("");
    }}
  }});
}}

export default async () => ({{
  "chat.message": async (input, output) => {{
    const prompt = output.parts
      .filter((p) => p.type === "text" && !p.synthetic && p.text)
      .map((p) => p.text)
      .join("\\n")
      .trim();
    if (!prompt) return;
    const raw = await run({{ hook_event_name: "UserPromptSubmit", session_id: input.sessionID, prompt }});
    if (!raw) return;
    const ctx = JSON.parse(raw).hookSpecificOutput?.additionalContext;
    if (ctx) output.message.system = output.message.system ? `${{output.message.system}}\\n\\n${{ctx}}` : ctx;
  }},
  event: async ({{ event }}) => {{
    if (event.type === "session.idle") {{
      await run({{ hook_event_name: "Stop", session_id: event.properties.sessionID }});
    }}
  }},
}});
"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(script, encoding="utf-8")

    def uninstall(self, path: Path) -> bool:
        if not self.installed(path):
            return False
        path.unlink()
        return True

    def installed(self, path: Path) -> bool:
        return path.is_file() and SKILL_MARKER in path.read_text(encoding="utf-8")


class CursorHooks:
    """``~/.cursor/hooks.json``: capture on ``stop``. Cursor's prompt hook
    (``beforeSubmitPrompt``) can only allow or block, so recall stays with MCP + skill."""

    @staticmethod
    def _ours(entry: dict) -> bool:
        return HOOK_SIGNATURE in entry.get("command", "")

    def install(self, path: Path, launch: Launch) -> None:
        data = _load_json(path)
        data.setdefault("version", 1)
        hooks = data.setdefault("hooks", {})
        entries = [e for e in hooks.get("stop", []) if not self._ours(e)]
        entries.append({"command": shlex.join(launch.hook())})
        hooks["stop"] = entries
        _dump_json(path, data)

    def uninstall(self, path: Path) -> bool:
        data = _load_json(path)
        hooks = data.get("hooks", {})
        entries = hooks.get("stop", [])
        kept = [e for e in entries if not self._ours(e)]
        if len(kept) == len(entries):
            return False
        hooks["stop"] = kept
        _dump_json(path, data)
        return True

    def installed(self, path: Path) -> bool:
        return any(self._ours(e) for e in _load_json(path).get("hooks", {}).get("stop", []))


@dataclass(frozen=True)
class HostSpec:
    # Relative to $HOME; the skill lives at <home_dir>/skills/<SKILL_NAME>/SKILL.md.
    home_dir: str
    mcp: tuple[Part, str]
    hooks: tuple[Part, str] | None = None


HOSTS: dict[str, HostSpec] = {
    "agentica": HostSpec(
        ".agentica", (JsonMcp(), ".agentica/mcp_config.json"), (AgenticaHooks(), ".agentica/config.yaml")
    ),
    "claude-code": HostSpec(
        ".claude", (JsonMcp(), ".claude.json"), (ClaudeStyleHooks(), ".claude/settings.json")
    ),
    "codebuddy": HostSpec(
        ".codebuddy", (JsonMcp(), ".codebuddy/mcp.json"), (ClaudeStyleHooks(), ".codebuddy/settings.json")
    ),
    # Codex runs hooks only with `[features] hooks = true` and after the user trusts them.
    "codex": HostSpec(
        ".codex", (TomlMcp(), ".codex/config.toml"), (ClaudeStyleHooks(), ".codex/hooks.json")
    ),
    "cursor": HostSpec(".cursor", (JsonMcp(), ".cursor/mcp.json"), (CursorHooks(), ".cursor/hooks.json")),
    # Hermes is scanned from its state.db; it gets MCP + skill, no hooks.
    "hermes": HostSpec(".hermes", (YamlMcp(), ".hermes/config.yaml")),
    "opencode": HostSpec(
        ".config/opencode",
        (OpenCodeMcp(), ".config/opencode/opencode.json"),
        (OpenCodePlugin(), ".config/opencode/plugins/memhub-hub.js"),
    ),
    "workbuddy": HostSpec(
        ".workbuddy", (JsonMcp(), ".workbuddy/mcp.json"), (ClaudeStyleHooks(), ".workbuddy/settings.json")
    ),
}


def render_skill(*, host: str, recall_argv: list[str]) -> str:
    return f"""---
name: {SKILL_NAME}
description: 用户的全局个人记忆（memhub MemHub，跨 Agentica / Claude Code / CodeBuddy / Codex / Cursor / Hermes / OpenCode / WorkBuddy 共享）。回答涉及用户偏好、兴趣、长期约定、过往决策，或用户问「我之前问过什么 / 之前在别的 CLI 聊到哪了」时使用。
---

{SKILL_MARKER}

# memhub MemHub 个人记忆

本机所有 CLI 通过 loopback HTTP 访问同一个 Hub Core，当前宿主是 `{host}`。Core 是唯一写者，
MCP 工具已经绑定个人 scope，不传 `user_id` / `agent_id` / `app_id`。

- 偏好、兴趣、画像、历史结论：`memory_search(query)`。
- 「我之前问了什么 / 原话是什么」：`conversation_search(query)`。结果的 `session_id`
  形如 `codex:<id>`，冒号前就是来源 CLI。
- 用户明确表达新的长期偏好或约定：`memory_add(content)`。普通对话会被自动采集，无需重复写入。

没有加载 MCP 工具时，用命令行同时查记忆与历史原话：

```bash
{shlex.join(recall_argv)} "要查的内容"
```
"""


def _paths(host: str, home: Path) -> tuple[HostSpec, Path]:
    spec = HOSTS[host]
    return spec, home / spec.home_dir / "skills" / SKILL_NAME / "SKILL.md"


def install_host(
    host: str, *, hub_dir: str | Path | None = None, home: Path | None = None
) -> dict[str, Any]:
    home = home or Path.home()
    spec, skill = _paths(host, home)
    launch = Launch(host, hub_root(hub_dir))
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        render_skill(
            host=host,
            recall_argv=launch.memhub("recall", "--hub-dir", str(launch.hub_dir)),
        ),
        encoding="utf-8",
    )
    written = [str(skill)]
    for part, rel in [spec.mcp, *([spec.hooks] if spec.hooks else [])]:
        part.install(home / rel, launch)
        written.append(str(home / rel))
    return {"host": host, "files": written}


def uninstall_host(host: str, *, home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
    spec, skill = _paths(host, home)
    skill_removed = skill.is_file() and SKILL_MARKER in skill.read_text(encoding="utf-8")
    if skill_removed:
        shutil.rmtree(skill.parent)
    mcp_part, mcp_rel = spec.mcp
    return {
        "host": host,
        "skill": skill_removed,
        "mcp": mcp_part.uninstall(home / mcp_rel),
        "hooks": spec.hooks[0].uninstall(home / spec.hooks[1]) if spec.hooks else False,
    }


def hosts_status(*, home: Path | None = None) -> dict[str, dict[str, bool | None]]:
    home = home or Path.home()
    out: dict[str, dict[str, bool | None]] = {}
    for host, spec in HOSTS.items():
        _, skill = _paths(host, home)
        mcp_part, mcp_rel = spec.mcp
        out[host] = {
            "skill": skill.is_file() and SKILL_MARKER in skill.read_text(encoding="utf-8"),
            "mcp": mcp_part.installed(home / mcp_rel),
            "hooks": spec.hooks[0].installed(home / spec.hooks[1]) if spec.hooks else None,
        }
    return out
