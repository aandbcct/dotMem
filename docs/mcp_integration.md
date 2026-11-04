# MCP / REST 接入

memhub 面向 Agent 提供 **两条 MCP 接入路径**，工具名与 REST 路由一一对应，共享 `MemoryOperations` 实现层：

| 路径 | 状态 | 适用场景 |
|---|---|---|
| **本地 uvx Python MCP** | ✅ 已实现 | Cursor / Claude Desktop 本机直连，`uvx --from "memhub[mcp]" memhub-mcp` 或 `pip install "memhub[mcp]"` |
| **Cloud-hosted HTTP MCP** | 🔜 规划中 | 托管记忆服务 + 远程 Agent；基于 REST API 暴露同一套 `memory_*` 契约 |

**当前**：仅本地 Python MCP Server（stdio / streamable-http）可用。  
**后续**：REST 层（`memhub serve-api` + `GET /v1/capabilities`）已对齐 MCP 工具契约，可直接作为 HTTP MCP 的传输底座——无需重复实现业务逻辑，只需在 REST 之上封装 MCP streamable-http 协议（或 npm/TypeScript 薄客户端）。

**依赖**：MCP 服务基于 mcp 2.x 的 `MCPServer`（`[mcp]` extra 为 `mcp>=2.2,<3`）。mcp 1.x 的 `mcp.server.fastmcp` 已不再使用；只装 `[mcp]` 即可运行，不需要 `[api]` 的 fastapi。

```
Agent (Cursor / Claude / 自研)
    │
    ├─ 本地 ──stdio/HTTP──▶ memhub-mcp ──▶ MemoryOperations ──▶ MemoryClient
    │
    └─ 云端 ──HTTP MCP──▶ memhub REST (/v1/...) ──▶ MemoryOperations ──▶ MemoryClient
                              ↑ 同一套 memory_* 工具名与 JSON 契约
```

## 工具 ↔ HTTP ↔ SDK

| 工具 / 操作 | HTTP | MemoryClient |
|---|---|---|
| `memory_add` | `POST /v1/memories/` | `add` |
| `memory_search` | `POST /v1/memories/search` | `search` |
| `conversation_search` | `POST /v1/memories/search_conversation` | `search_conversation` |
| `memory_list` | `GET /v1/memories/?include_history=` | `list` |
| `memory_get` | `GET /v1/memories/{id}` | `get` |
| `memory_update` | `PUT /v1/memories/{id}` | `update`（返回新版本 id） |
| `memory_delete` | `DELETE /v1/memories/{id}` | `delete`（归档当前版本） |
| `memory_delete_scope` | `DELETE /v1/memories/?confirm=true` | `delete_bulk` |
| `memory_list_scopes` | `GET /v1/scopes/` | `list_scopes` |
| `memory_digest` | `POST /v1/digest/` | `digest` |

发现端点：`GET /v1/capabilities` — 返回 `tools` 数组（含 method/path）。本仓库没有 npm 包；云端 HTTP MCP 仍是规划，本地用 Python `memhub-mcp`。

`memory_list` 默认只列当前版本，`include_history=true` 才连同已归档版本。`memory_update` 追加一版并返回新 id。`memory_delete` 归档，不物理删除。`memory_digest` 只在 dual 生成 `L3_DERIVED`；system1 返回 `processed=0`。

`memory_add` 接受 `workspace_id` / `branch`（procedure 的版本边界）；`memory_search` 接受 `procedure_limit`（默认 0）/ `procedure_min_score`，工程经验在返回的 `memories.procedures` 组里，与 `profile` / `proactive` / `normal` / `raw` / `schema` / `catalog` 并列。

## 宿主 Hub MCP（`install-host`）

上表是 SDK 级 MCP（`memhub-mcp`，需要显式传 `user_id`）。各 CLI 宿主通过 `memhub install-host`
安装的是 `python -m memhub.hub.mcp`：它已绑定本机个人 scope，不接收 `user_id` / `app_id` / `agent_id`，
请求统一发给本机的 Hub Core。

| 用户意图 | 工具 |
|---|---|
| 查偏好、画像、事实、历史结论 | `memory_search(query)` |
| 查历史对话原话 | `conversation_search(query)` |
| 记住 / 别忘了 / 以后都按这个 | `memory_add(content)` |
| 改掉 / 撤销 / 更新约定 | `memory_list(query)` 取 `id` → `memory_update(memory_id, content)` |
| 忘掉 / 删掉一条记忆 | `memory_list(query)` 取 `id` → `memory_delete(memory_id)` |
| 排查 Hub 自身故障 | `memory_status()` |

`memory_add` 返回 `ok=true` 即写入已接收，`status=pending` 是正常终态（后台自动抽取）。
找不到旧条目时直接 `memory_add` 写新约定即可。`conversation_search` 结果的 `session_id`
形如 `codex:<id>`，冒号前是来源工具。

优先用宿主内置的 `memhub` MCP 工具。MCP 未加载或未激活时改用 Hub CLI：入口是
`python3 -m memhub.cli <子命令>`，安装后也可写 `memhub <子命令>`，须使用装有 memhub 的解释器
（`install-host` 写进 skill 的命令已带绝对路径和 `--hub-dir`）。子命令：`recall`（查）、
`remember`（写）、`memory-list`（列出并取 id）、`memory-update <id> <content>`（更正）、
`memory-delete <id>`（删除）、`status`（状态），都走同一个本机 Core。
`memhub add` / `search` / `update` / `delete` / `list` 是 SDK 命令，指向当前目录下另一个库，不是备选。

## 本地 MCP（已实现）

```bash
# uvx 零安装（推荐）
uvx --from "memhub[mcp]" memhub-mcp
uvx --from "memhub[mcp]" memhub-mcp --transport streamable-http --port 8765

# 或 pip 安装后
memhub-mcp
memhub-mcp --transport streamable-http --port 8765
memhub mcp
```

在代码里装配：`build_mcp(client=...)` 只注册工具，返回 `MCPServer` 实例；监听地址在运行时传入，例如 `server.run(transport="streamable-http", host="127.0.0.1", port=8765)`。

### Cursor（本地 stdio）

```json
{
  "mcpServers": {
    "memhub": { "command": "uvx", "args": ["--from", "memhub[mcp]", "memhub-mcp"] }
  }
}
```

配置：`~/.memhub/config.yaml` 或 `MEMHUB_*` 环境变量。用 MemHub 的话不必手配，`memhub install-host cursor` 会写好指向 Core 的 MCP。

## REST / HTTP MCP 底座（已实现 REST，HTTP MCP 待封装）

REST 服务本身已可用，可作为 cloud-hosted HTTP MCP 的后端：

```bash
memhub serve-api --host 0.0.0.0 --port 8000
curl http://localhost:8000/v1/capabilities
curl http://localhost:8000/openapi.json
```

后续 HTTP MCP 接入方式（规划）：

- **自托管**：REST + MCP streamable-http 网关（同一 `memory_*` 工具，Bearer 鉴权走 REST）
- **Cloud-hosted**：托管 `memhub serve-api` 实例，Agent 通过 URL + appkey 连接，无需本机 Python 环境

Cursor 侧预期形态（示意，尚未提供官方 npm 包）：

```json
{
  "mcpServers": {
    "memhub-cloud": {
      "url": "https://your-host/mcp",
      "headers": { "Authorization": "Bearer <appkey>" }
    }
  }
}
```

## Skill

`memhub/skills/memhub-sdk/SKILL.md` — 指导 agent 何时读写记忆、如何解读 profile/proactive/normal 与演化链。
