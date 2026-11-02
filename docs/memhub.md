# MemHub：跨 AI 工具共享你的个人记忆

在 Cursor 里交代过的偏好，Codex 并不知道；在 Claude Code 里定下的规范，到了 Hermes 又要重新说明。

**MemHub 让你的 AI 工具共享一份个人记忆。** 记忆是你的长期资产，不被任何单一工具锁定，每次开口都从懂你开始。

```text
   Claude Code   Codex   Cursor   Hermes   Agentica
        \          |        |        |       /
         ─────────────  MemHub  ─────────────
                         │
            你的个人记忆（每台电脑一份，可跨设备同步）
```

## 你会得到什么

- **说一次，处处生效**：在一个工具里提过的偏好、规范和项目背景，其他工具自动知道。
- **零配置**：工具自动发现本机的记忆服务，不用填地址，也不用复制密钥。
- **不拖慢工作**：对话先存下来立刻返回，整理在后台进行。
- **历史不浪费**：已有的会话记录一键导入，第一天就"懂你"。
- **数据归你**：默认只存在你的电脑上；多台电脑可以经你自己的网盘文件夹、git 仓库或服务器同步。

当前支持：**Claude Code、Codex、Cursor、Hermes、Agentica**。OpenCode、WorkBuddy 等更多工具正在接入，见 [Roadmap](https://github.com/shibing624/dual-mem#roadmap)。

## 三步开始

```bash
pip install "dual-mem[all]"

# 1. 启动记忆服务（登录后自动运行）
dual-mem hub install-service

# 2. 接入本机的 AI 工具。all 会写入下面五个的配置；只用其中几个就写出名字
dual-mem hub install-host all
# dual-mem hub install-host claude-code
# dual-mem hub install-host codex

# 3. 导入过去的对话
dual-mem hub scan --source all
```

然后就可以问了：

```bash
dual-mem hub recall "我之前做过哪些技术选择"
dual-mem hub status
dual-mem hub ui        # 在浏览器里按项目浏览、搜索、编辑、删除记忆
```

> 自启服务不继承 shell 环境变量，模型 API key 请写在 `~/.dual-mem/config.yaml`。

## 多台电脑共用一份记忆

每台电脑一条命令，连到同一个同步目标：

```bash
dual-mem hub connect dir "~/Library/Mobile Documents/com~apple~CloudDocs/dual-mem"   # iCloud 等同步文件夹
dual-mem hub connect git git@github.com:you/my-vibecoding-memory.git               # 私有 git 仓库
dual-mem hub connect ssh user@your-server                                          # 服务器上的 Core
```

每台电脑保留完整的本地记忆，检索只查本机；Core 在写入后、定时、以及检索发现数据旧了时在后台交换变更。完整说明（怎么选、谁来归纳、冲突怎么合并）见 [多设备同步](memhub-server.md)。

不想同步了：`dual-mem hub disconnect`。本机记忆和同步目标上的数据都保留。

### 开发者实操：Agentica CLI + dual-mem MCP + 私有 Git 仓库

如果你日常用 `agentica` CLI（或同时用 Claude Code、Codex）写代码，想让每次排障经验、项目规矩自动沉淀，并备份/同步到自己的 GitHub 私有仓库（如 `my-vibecoding-memory`），按下面 4 步配好即可：

```bash
# 1. 启动本机常驻 Core（确保 ~/.dual-mem/config.yaml 已填好三把 API key）
dual-mem hub install-service

# 2. 接入 agentica CLI（同时写好 MCP、Skill 与 Hook，保留原 YAML 注释）
dual-mem hub install-host agentica

# 3. 导入已有的 agentica 历史编码会话（可选）
dual-mem hub scan --source agentica

# 4. 关联到你的 GitHub 私有仓库（先建一个私有空仓库，如 my-vibecoding-memory）
gh repo create my-vibecoding-memory --private
dual-mem hub connect git git@github.com:<你的用户名>/my-vibecoding-memory.git
```

`install-host agentica` 会自动为你配好三件事（无需手改配置文件）：

1. **MCP 工具（`~/.agentica/mcp_config.json`）**：注册 `dual-mem` MCP 服务，Agent 可主动调用 `memory_search`、`memory_add`、`conversation_search`、`memory_status`。
2. **每轮提问前自动召回（`~/.agentica/config.yaml` → `dual-mem-hub-recall`）**：订阅 `UserPromptSubmit` 事件。你每次在 `agentica` 输入提示词时，几十毫秒内从本机库召回相关画像、事实与最多 3 条 `[procedure]` 工程排障经验，以 `<relevant-memories>` 注入上下文（与 Agentica 原生的 `<memory_recall>` / `<session_recall>` 并存，直接给出抽取好的结论与最新路径，减少翻查原始日志的工具调用与 token 消耗）。
3. **每轮结束后台沉淀（`~/.agentica/config.yaml` → `dual-mem-hub`）**：订阅 `run.completed` 与 `session.ended`。每轮结束后后台增量读取用户请求、最终回答以及 `tool_calls` 涉及的文件路径（工具输出不采集），连同当前工作目录 `cwd` 和 `git_branch` 交给本机 Core 沉淀，并在几秒后自动把新增的变更日志（含算好的向量）推送到 `my-vibecoding-memory` 仓库。

换到第二台电脑（例如家里的 Mac）时，装好 `dual-mem`、跑完上面第 1、2 步后，执行同一条 `dual-mem hub connect git git@github.com:<你的用户名>/my-vibecoding-memory.git`，之前所有的记忆和向量就会直接拉回新电脑本机库，不重新调任何模型。

### 记忆是怎么流动的

```text
┌──────────────────── 每台电脑 ─────────────────────┐
│ Claude Code ┐                                     │
│ Codex       ├ capture Hook ──┐                    │
│ Agentica    ┘（一轮对话结束）  ├─▶ 127.0.0.1:8765  │
│                              │   dual-mem hub serve│──▶ 同步目标（可选）
│ 你输入提示词 ─ hook-search ───┤   （本机唯一写者）   │◀── 其它电脑的变更
│ Agent 主动查 ─ MCP memory_* ──┘   ~/.dual-mem/store │
└───────────────────────────────────────────────────┘
```

1. **写**：一轮对话结束，宿主触发 capture Hook（Claude Code `Stop`、Agentica `run.completed`、Codex `Stop`）。它按本机 `cursors.db` 的游标读出会话日志里新增的轮次，交给本机 Core 后立刻返回，不等 LLM。
2. **整理**：Core 先把 Evidence 写进持久队列 `hub.db`，再由唯一 worker 在后台抽取、JEV 裁决、写入记忆；dual 模式下，距上次成功归纳满一天且仍有新证据时，在写入空档自动 `digest()`（多设备时只有一台负责）。
3. **读**：你提交提示词时，recall Hook（`hook-search`）把相关记忆注入上下文；Agent 也可以通过 MCP 工具（`memory_search` 等）主动查。都只读本机库。
4. **同步**（`connect` 之后）：本机库的每次变更带着向量记进变更日志，Core 在后台把它推到同步目标，并拉回其它电脑的变更。

## 常用命令

| 想做什么 | 命令 |
|---|---|
| 只接入某个工具 | `dual-mem hub install-host cursor` |
| 取消接入 | `dual-mem hub uninstall-host cursor` |
| 只导入某个工具的历史 | `dual-mem hub scan --source codex` |
| 快速导入、稍后再整理 | `dual-mem hub scan --source all --raw-only`，之后 `dual-mem hub extract` |
| 重试失败的整理任务 | `dual-mem hub retry` |
| 立刻跨设备同步一次 | `dual-mem hub sync` |
| 查看、搜索、编辑、删除记忆 | `dual-mem hub ui` |
| 重启 Core | 再执行一次 `dual-mem hub install-service` |
| 停止开机自启 | `dual-mem hub uninstall-service` |

---

<details markdown="1">
<summary>可选阅读：Core 存在哪、怎么鉴权、同步日志怎么合并</summary>

### 单 Core 架构

每台设备一个 Core，它是本机库的唯一写者：

```text
CLI transcript / MCP / Hook
        ↓ loopback HTTP
dual-mem hub serve（唯一写者）
        ↓
~/.dual-mem/{hub.db,store}
```

前台调试用 `dual-mem hub serve`；日常用 `install-service`（macOS LaunchAgent / Linux systemd --user）。没有单独的 restart：再执行一次 `dual-mem hub install-service` 就会卸掉再拉起（macOS `launchctl unload` 后 `load`，Linux `systemctl --user restart`）。

### 发现与鉴权

- Core 只监听 `127.0.0.1:8765`，启动时自动生成 `runtime-token` 并发布 `runtime.json`，同机的 CLI、MCP 和 Hook 自动发现，不需要配置。
- Bearer Token 只保存在权限为 `0600` 的 `~/.dual-mem/runtime-token`，不会复制到各宿主的配置里。
- `install-host` 会安装 MCP、Skill，以及该工具可用的 capture / recall Hook。改写 YAML 配置（Agentica、Hermes）时保留原有注释、引号与缩进；`uninstall-host` 后文件恢复原样。
- MemHub 自带的 MCP 服务（`python -m dual_mem.memhub.mcp`，由 `install-host` 写入宿主配置）提供 `memory_search`、`memory_add`、`conversation_search`、`memory_status`，请求同样发给本机发现的 Core。需要 mcp 2.x（`pip install "dual-mem[mcp]"`）。

### 写入流程

- Core 收到原始 Evidence 后先写入 `hub.db`，随即返回回执。之后由唯一的 worker 按事件时间（`memory_at`）串行调用 `MemoryClient`，所以扫描和 Hook 都不用等 LLM。
- 模型调用失败会按退避自动重试；重试用尽的写入可以用 `dual-mem hub retry` 重新排队。
- 会话扫描游标存在发起扫描那台机器的 `cursors.db`，只越过 Core 已接收的批次；Core 不可达时游标不动。
- 冷启动可以先 `scan --raw-only`，只存 L0 原文、不调抽取模型；之后 `dual-mem hub extract` 在后台补跑抽取，复用已存的 L0。
- 普通扫描按会话分批写入，`session_id` 带来源前缀，例如 `codex:<session-id>`。
- Claude Code / Codex / Agentica 会话里的工具调用随对话一起送达（只用于提取涉及的文件；工具输出不采集），会话的工作目录和 git 分支作为 `workspace_id` / `branch`。Core 据此在对话记忆之外沉淀 procedure 工程经验；`recall` 和 `hook-search` 默认返回最多 3 条相关经验，在注入块里标为 `[procedure]`。
- 三家宿主的每轮召回走同一个 `UserPromptSubmit` hook 协议（stdin `prompt`，stdout `hookSpecificOutput.additionalContext`）；`install-host agentica` 在 `settings.hooks.consumers` 里订阅 `UserPromptSubmit`，召回块与 agentica 内置的 `<memory_recall>` / `<session_recall>` 并存。
- Core 以 dual 模式运行时（`hub serve --mode dual`）自己 `digest()`，一天最多一次。写入队列排空后，距上次成功已满 `--digest-every` 小时（默认 24），并且还有待归纳证据，才跑。`--digest-every 0` 关闭。各台电脑不用自己触发。

### 多设备复制

- 本机库的每次变更（裁决后的版本计划、L0 Evidence、元数据补丁、删除）按 `(origin, seq)` 记在 `store/versions.db` 的 `log` 表，带完整内容和向量。同步只搬这些条目，接收方不调模型。
- 同一来源的条目严格按序应用；条目引用了本机还没有的记忆（来自第三台设备）时先等待，那条记忆到了再应用。
- 合并按字段进行，与到达顺序无关：`is_latest` 只能从真变假，`supersedes` / `superseded_by` 取并集，删除留墓碑。
- `dir` / `git` 目标里每台设备只写自己的 `log/<origin>/` 目录，两台设备永远不会写同一个文件；`manifest.json` 记录负责归纳的设备和当前快照。日志累积后由这台设备折叠进 `snapshot/`，新设备先导入快照，再回放它之后的日志。
- `ssh` 每次同步是一条 `ssh <server> dual-mem hub relay ...` 命令，不常驻隧道。`http` 目标必须是 HTTPS（loopback 除外）。

### 其他命令

```bash
dual-mem hub hook-search            # 供宿主 Hook 调用：搜索并输出注入块
dual-mem hub capture claude-code    # 供宿主 Hook 调用：上报当前会话
```

</details>
