<p align="center">
  <a href="https://github.com/shibing624/memhub">
    <img src="https://raw.githubusercontent.com/shibing624/memhub/main/docs/public/logo.png" height="120" alt="memhub Logo" />
  </a>
</p>

# memhub

**多个 Agent 共用一份个人记忆；当场记下事实，事后看出规律。**

- 🔗 **跨工具记忆中枢（Hub）**：[MemHub](https://github.com/shibing624/memhub/blob/main/docs/memhub.md) 让 Claude Code、Codex、Cursor、Agentica、Hermes、CodeBuddy、OpenCode、WorkBuddy、Kimi、DeepSeek Harness、Trae 11 种工具读写同一份本地记忆，支持 `memhub ui` 可视化管理与跨设备同步（[多设备同步](https://github.com/shibing624/memhub/blob/main/docs/memhub-server.md)）。
- 🏆 **同场评测领先**：同模型、同题集，LoCoMo **74.7**（hy-memory 66.6、mem0 65.6）、LongMemEval **91.7**、PersonaMem-v2 **50.0**，写入耗时约为 hy-memory 的五分之一，详见 [评测说明](https://github.com/shibing624/memhub/blob/main/docs/benchmarks.md)。
- 🧠 **快慢双系统**：System 1 在对话时实时记住你的事实、偏好和状态变化（旧说法自动保留在历史链）；System 2 在后台跨多次对话归纳你的长期习惯与行事风格。
- 🛠️ **工程排障自动沉淀**：编码会话自动把「问题 → 根因与方案 → 涉及文件」提炼为可复用的工程经验，下次遇到类似问题直接带出。

## 从哪里开始

| 我想… | 看这里 |
|---|---|
| 让我常用的 AI 工具共享记忆（含 `memhub ui` 与跨电脑同步） | [MemHub](https://github.com/shibing624/memhub/blob/main/docs/memhub.md) · [多设备同步](https://github.com/shibing624/memhub/blob/main/docs/memhub-server.md) |
| 给自己的 Agent / 应用加长期记忆（含 Coding 经验） | [安装](https://github.com/shibing624/memhub/blob/main/docs/getting-started/installation.md) → [快速开始](https://github.com/shibing624/memhub/blob/main/docs/getting-started/quickstart.md) |
| 接入 Agentica、Claude Code、Hermes 等框架 | [生态集成](https://github.com/shibing624/memhub/blob/main/docs/integrations.md) |
| 通过 MCP 或 REST 接入 | [MCP / REST](https://github.com/shibing624/memhub/blob/main/docs/mcp_integration.md) |
| 看效果对比与复现方法 | [评测说明](https://github.com/shibing624/memhub/blob/main/docs/benchmarks.md) |

## 接入本机 AI 工具

```bash
pip install "memhub[all]"

# 1. 启动后台常驻服务（开机自启）
memhub install-service

# 2. 按需接入你正在用的 AI 工具（或用 memhub install-host all 一键接入全部 8 种）
memhub install-host claude-code
memhub install-host codex

# 3.（可选）导入已有历史会话
memhub scan --source claude-code

# 4. 在浏览器可视化管理记忆，或绑定私有 Git 仓库跨设备同步
memhub ui
memhub connect git git@github.com:you/my-memory.git
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

1. [架构说明](https://github.com/shibing624/memhub/blob/main/docs/architecture.md)：SDK 与 Hub、CLI、REST、MCP、Skill 的关系，以及存储层
2. [四层记忆](https://github.com/shibing624/memhub/blob/main/docs/memory_layers.md)：L0 原文、L1 事实与工程经验、L2 画像、L3 推导
3. [写入决策设计](https://github.com/shibing624/memhub/blob/main/docs/jev-memory-design.md)：决策模型的动作与关系、版本链、并发与恢复
