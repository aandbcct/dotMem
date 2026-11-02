# memhub

**多个 Agent 共用一份个人记忆；当场记下事实，事后看出规律。**

- 🔗 **跨工具记忆中枢（Hub）**：[MemHub](memhub.md) 让 Claude Code、Codex、Cursor、Hermes、Agentica、CodeBuddy、OpenCode、WorkBuddy 8 种工具读写同一份本地记忆，支持 `memhub ui` 可视化管理与跨设备同步（[多设备同步](memhub-server.md)）。
- ⚡ **System 1（实时分层写入）**：对话进行时写入。原话（`L0_RAW`）、事实与意图（`L1_FACT`）、个人画像（`L2_PROFILE`）分层存放，专用决策模型（JEV）裁决新增与更新；你改了主意，它追加新版本并保留历史。
- 🧠 **System 2 / dual（跨证据规律归纳）**：`mode="dual"` 下显式调用 `digest()`，跨多条证据聚类归纳行为规律（`schema` / `core_schema`）和长期画像目录（`persona_catalog`，`L3_DERIVED`）。
- 🛠️ **Coding 记忆（工程排障经验）**：带工具调用的编码会话自动沉淀可复用的 `procedure` 经验（问题、方案、涉及文件、适用边界），与日常对话共用同一个 `add()` / `search()`。
- 🏆 **同场评测领先**：同模型、同题集，LoCoMo **74.7**（hy-memory 66.6、mem0 65.6）、LongMemEval **91.7**、PersonaMem-v2 **50.0**，写入耗时约为 hy-memory 的五分之一，详见 [评测说明](benchmarks.md)。

## 从哪里开始

| 我想… | 看这里 |
|---|---|
| 让我常用的 AI 工具共享记忆（含 `memhub ui` 与跨电脑同步） | [MemHub](memhub.md) · [多设备同步](memhub-server.md) |
| 给自己的 Agent / 应用加长期记忆（含 Coding 经验） | [安装](getting-started/installation.md) → [快速开始](getting-started/quickstart.md) |
| 接入 Agentica、Claude Code、Hermes 等框架 | [生态集成](integrations.md) |
| 通过 MCP 或 REST 接入 | [MCP / REST](mcp_integration.md) |
| 看效果对比与复现方法 | [评测说明](benchmarks.md) |

## 三步接入本机 AI 工具

```bash
pip install "memhub[all]"

memhub install-service       # 后台常驻 Core，开机自启
memhub install-host all      # 一键接入 Claude Code / Codex / Cursor / Agentica 等 8 种工具
memhub scan --source all     # 导入已有历史会话
memhub ui                    # 在浏览器按项目浏览、搜索、编辑、删除记忆
```

## 三行 Python SDK

```bash
pip install memhub
```

```python
from memhub import SyncMemoryClient

with SyncMemoryClient() as memory:
    memory.add(content="我住在上海，最近开始不吃辣了。", user_id="alice")
    res = memory.search(query="给 alice 推荐一家餐厅", user_id="alice")
```

---

## 可选阅读

使用和接入不用看下面这些。想了解底层原理时再读：

1. [架构说明](architecture.md)：SDK 与 Hub、CLI、REST、MCP、Skill 的关系，以及存储层
2. [四层记忆](memory_layers.md)：L0 原文、L1 事实与工程经验、L2 画像、L3 推导
3. [写入决策设计](jev-memory-design.md)：决策模型的动作与关系、版本链、并发与恢复
