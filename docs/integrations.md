# 生态集成

dual-mem 可以接进你已经在用的 Agent 框架和 AI 工具。所有集成共用同一个记忆库，同一个 `user_id` 在哪个工具里写下的记忆，其他工具都能读到。

| 你在用 | 推荐方式 | 效果 |
|---|---|---|
| Claude Code、Codex、Cursor、Hermes、Agentica、CodeBuddy、OpenCode、WorkBuddy 中的几个 | [MemHub](memhub.md) | 一条命令接入全部，跨工具共享记忆 |
| Agentica 框架开发 Agent | `DualMemWorkspace` | Agent 自动召回、自动保存长期记忆 |
| Claude Code（不用 MemHub） | `dual-mem-hook` | 提问前自动带出相关记忆，会话结束自动保存 |
| Hermes / OpenClaw | 记忆插件 | 被动注入记忆，后台异步写入 |
| 支持 MCP 的工具 | `dual-mem-mcp` | 工具按需调用记忆读写，见 [MCP 接入](mcp_integration.md) |

---

## 各集成的接入代码

### Agentica

#### 方式一：Agentica CLI（MemHub MCP + Hook + 私有 Git 同步，推荐）

日常使用 `agentica` 命令行写代码时，直接通过 MemHub 接入，无需写任何 Python 代码：

```bash
pip install "dual-mem[all]"

# 1. 启动本机常驻记忆服务（模型 key 配在 ~/.dual-mem/config.yaml）
dual-mem hub install-service

# 2. 一键把 dual-mem 接入 agentica CLI
dual-mem hub install-host agentica

# 3. 导入过往的 agentica 会话记录（可选）
dual-mem hub scan --source agentica

# 4. 关联到你的 GitHub 私有仓库，自动备份与跨机同步（可选）
dual-mem hub connect git git@github.com:<你的用户名>/my-vibecoding-memory.git

# 5. 在浏览器中按项目浏览、搜索、编辑、删除记忆（可选）
dual-mem hub ui
```

> 卸载：停止并移除开机自启服务用 `dual-mem hub uninstall-service`，移除写入 `agentica` 的 MCP / Hook 配置用 `dual-mem hub uninstall-host agentica`。

`install-host agentica` 会在保留你原有配置注释的前提下，自动完成两处集成：

- **MCP 工具（`~/.agentica/mcp_config.json`）**：挂载 `dual-mem` MCP 服务（`memory_search`、`memory_add`、`conversation_search`、`memory_status`）。
- **自动召回与采集 Hook（`~/.agentica/config.yaml`）**：
  - 一个消费者 `dual-mem`（`dual-mem hub hook agentica`），按事件分派。`UserPromptSubmit`：每轮提问前从本机库毫秒级召回画像、事实与 `[procedure]` 工程排障经验，以 `<relevant-memories>` 注入（与 Agentica 内置的 `<memory_recall>` / `<session_recall>` 并存）。
  - `run.completed` / `session.ended`：每轮结束后台采集用户请求、最终回答与工具调用涉及的文件路径（工具输出不采集），沉淀记忆并在后台同步到你的私有 Git 仓库。

#### 方式二：Agentica Python SDK（`DualMemWorkspace`）

用 Agentica 框架在代码里构建自定义 Agent 时，可用 `DualMemWorkspace` 适配器：

```bash
pip install "dual-mem[agentica]"
```

```python
from agentica import Agent
from dual_mem.integrations.agentica import DualMemMemory

mem = DualMemMemory(user_id="alice")
agent = Agent(workspace=mem.as_workspace(workspace_dir=".agentica"), enable_long_term_memory=True)
```

`DualMemWorkspace` 只接管语义长期记忆。`AGENTS.md`、`USER.md`、skills 等文件型上下文仍由原生 Workspace 管理。内置的 `save_memory` / `search_memory` 工具会转到 dual-mem。

### Claude Code Hook

在 Claude Code 的 hooks 配置里注册两条命令：

| Hook | 命令 | 作用 |
|---|---|---|
| `UserPromptSubmit` | `dual-mem-hook search` | 搜索相关记忆，以 `additionalContext` 注入 |
| `Stop` | `dual-mem-hook ingest` | 把本轮对话写入记忆 |

需要设置环境变量 `DUAL_MEM_USER_ID`。可选 `DUAL_MEM_MODE`（`system1` / `dual`）、`DUAL_MEM_STORAGE_DIR` 和 `DUAL_MEM_AGENT_ID`（默认 `claude-code`）。Hook 失败只记日志，不会阻断会话。

使用 MemHub 时不需要手动配置，`dual-mem hub install-host claude-code` 会自动装好。

### Hermes / OpenClaw

`DualMemHermesProvider` 和 `DualMemOpenClawProvider` 实现两边的 MemoryProvider 契约：对话前 prefetch 注入记忆，对话后 `sync_turn` 异步写入。设置了 `DUAL_MEM_USER_ID` 时插件才会启用。

### 统一的记忆注入格式

所有集成注入的记忆块都由同一个格式化器生成，包在 `<relevant-memories>` 标签里，所以不同宿主看到的上下文结构一致。
