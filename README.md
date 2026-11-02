<h1 align="center">memhub</h1>

<p align="center"><b>多个 Agent 共用一份个人记忆；当场记下事实，事后看出规律。</b><br />Claude Code、Codex、Cursor、Agentica 等 8 种工具读写同一份本地记忆，支持跨设备同步；底层由 System 1（实时分层写入与版本演化）与 System 2（跨证据规律归纳）双系统驱动。</p>

<p align="center">
  <a href="https://badge.fury.io/py/memhub"><img src="https://badge.fury.io/py/memhub.svg" alt="PyPI version" /></a>
  <a href="https://github.com/shibing624/memhub/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License Apache 2.0" /></a>
  <a href="https://github.com/shibing624/memhub/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/Python-3.11%2B-green.svg" alt="Python 3.11+" /></a>
  <a href="https://github.com/shibing624/memhub/issues"><img src="https://img.shields.io/github/issues/shibing624/memhub.svg" alt="GitHub issues" /></a>
  <a href="#社区与支持"><img src="https://img.shields.io/badge/wechat-group-green.svg?logo=wechat" alt="Wechat Group" /></a>
</p>

<p align="center">简体中文 | <a href="https://github.com/shibing624/memhub/blob/main/README_EN.md">English</a> | <a href="https://shibing624.github.io/memhub">在线文档</a></p>

## 为什么选 memhub

在 Cursor 里交代过的代码规范，Codex 并不知道；你已经告诉 AI 三次「我现在不吃辣了」，它还在推荐川菜馆；聊了几十轮，它背得出每一句原话，却说不出你做事的稳定习惯。

### 1. 🔗 多个 Agent，一份记忆（Memory Hub）

`memhub` 把本机上的多个 AI 工具连到同一份个人记忆：Claude Code、Codex、Cursor、Hermes、Agentica、CodeBuddy、OpenCode、WorkBuddy 读写同一个本地库，数据默认留在你的电脑上。

```bash
pip install "memhub[all]"

memhub install-service       # 后台常驻，开机自启（卸载用 uninstall-service）
memhub install-host all      # 一键接入本机已安装的 AI 工具（卸载用 uninstall-host all）
memhub scan --source all     # 把过去的对话也变成记忆

memhub recall "我之前做过哪些技术选择"
memhub ui                    # 在浏览器打开 http://127.0.0.1:8765/ui 按项目查看、搜索、编辑、删除记忆
```

<p align="center">
  <img src="https://raw.githubusercontent.com/shibing624/memhub/main/docs/public/ui-mem.png" width="820" alt="memhub ui 记忆管理页" />
</p>

零配置发现本机服务，对话先存下来立刻返回，整理在后台进行。一轮对话结束时宿主的 Hook 把新增内容交给本机 Core，你下一次输入提示词时按当前项目召回相关记忆自动注入；Agent 也能通过 MCP 主动查。写代码的会话连同工具调用涉及的文件路径一起送达，沉淀成可复用的工程经验。

多台电脑共用一份记忆：每台电脑都留一份完整的本地记忆，检索只查本机（断网照常），各台之间同步变更日志。普通人用 iCloud / Dropbox 之类的同步文件夹，开发者用一个私有 git 仓库，有服务器的连服务器上的 Core——

```bash
memhub connect dir "~/Library/Mobile Documents/com~apple~CloudDocs/memhub"
memhub connect git git@github.com:you/my-memory.git
memhub connect ssh user@your-server
```

同步交换的是已经裁决好的记忆和压缩向量，接收方不调任何模型。见 [跨工具记忆中枢（Hub）](https://github.com/shibing624/memhub/blob/main/docs/memhub.md) 与 [多设备同步](https://github.com/shibing624/memhub/blob/main/docs/memhub-server.md)。

### 2. 🏆 同一个模型，记得更准、写得更快

抽取、作答、评分用同一个模型（Qwen3.8-27B），同一套 embedding（bge-m3），同一批题目——**只换记忆系统**：

| 记忆系统 | LoCoMo<br>(308 题) | LongMemEval<br>(24 题) | PersonaMem-v2<br>(476 题) | 单篇长对话写入耗时<br>(秒，越低越快) |
|---|:---:|:---:|:---:|:---:|
| **memhub** | **74.7** | **91.7** | **50.0** | **503** |
| hy-memory 2.0.4 | 66.6 | 70.8 | 43.5 | 2485 |
| mem0 | 65.6 | 83.3 | 47.9 | 808 |

**三场公开评测都排第一，写入大约是 hy-memory 的五分之一。** 协议、显著性、题型细分和复现命令见 [评测说明](https://github.com/shibing624/memhub/blob/main/docs/benchmarks.md)。

### 3. 🧠 快慢双系统：当场记事实，事后看规律

`memhub` 的底层记忆引擎采用快慢双系统分层架构：

- **System 1（默认 `mode="system1"`）**：对话进行时实时写入。原话（`L0_RAW`）、事实与意图（`L1_FACT`）、个人画像（`L2_PROFILE`）分层存放，由专用决策模型（JEV）裁决新增、更新与冲突；你改了主意、换了城市或更新了方案，它追加新版本并保留历史，回答默认用最新的。
- **System 2 / dual（`mode="dual"`，再调用 `digest()`）**：会话告一段落时，跨多条证据聚类归纳行为规律（`schema` / `core_schema`）和长期画像目录（`persona_catalog`，`L3_DERIVED`）。

对应示例：[`examples/02_dual.py`](https://github.com/shibing624/memhub/blob/main/examples/02_dual.py)。

### 4. 🛠️ 工程排障也能留下来

chat 和 coding 是同一个 `add()` / `search()`。`messages` 里带工具轨迹（`role="tool"` 或 assistant 的 `tool_calls`）、且 JEV 判成工程会话时，除了照常写入的事实和画像，还会抽出可复用的工程经验（`kind="procedure"`：任务、方案、文件、适用边界）。它和对话记忆共用存储、版本链和 JEV 裁决，`search(procedure_limit=3)` 单独返回 `procedures` 组；`dual` 模式下也参与 `digest()` 归纳。用法见 [快速开始 · Coding 记忆](https://github.com/shibing624/memhub/blob/main/docs/getting-started/quickstart.md#coding)。

## 安装

```bash
pip install memhub
```

含 REST / CLI / MCP / MemHub：`pip install "memhub[all]"`；各 extra 也可以单独装，MCP 基于 mcp 2.x。首次运行会把包内 [`config.default.yaml`](https://github.com/shibing624/memhub/blob/main/memhub/config.default.yaml) 复制成 `~/.memhub/config.yaml`。启动要三把钥匙，缺一就拒绝构造：

```yaml
jev_api_key: ""    # 写入裁决。System One，不是 Chat Completions
jev_endpoint: https://api.typesafe.ai/v1/systemone

llm_api_key: ""    # 抽取与 System 2。OpenAI 兼容，地址可改
llm_base_url: https://api.openai.com/v1

embed_api_key: ""  # 向量索引与检索。OpenAI 兼容，地址可改
embed_base_url: https://api.openai.com/v1
```

三把钥匙和三个地址都可以改成环境变量：`MEMHUB_JEV_API_KEY` / `MEMHUB_JEV_ENDPOINT`、`MEMHUB_LLM_API_KEY` / `MEMHUB_LLM_BASE_URL`、`MEMHUB_EMBED_API_KEY` / `MEMHUB_EMBED_BASE_URL`。

## 快速开始

### 日常记忆

```python
from memhub import SyncMemoryClient

with SyncMemoryClient() as memory:
    memory.add(content="我住在上海，最近开始不吃辣了。", user_id="alice")
    memory.add(content="下个月我要搬去北京工作。", user_id="alice")

    res = memory.search(query="给 alice 推荐一家餐厅", user_id="alice")
    for m in res.memories.profile + res.memories.normal:
        print(m.content)
```

它会记住 alice 不吃辣、即将搬去北京，并且知道「上海」已经是过去时。

### Coding 记忆

不需要开关，也没有第二个检索入口。`messages` 里出现工具轨迹（某条是 `role="tool"`，或者 assistant 带了 `tool_calls`）是结构条件；接着 JEV 判断整段是不是工程会话，是才另外抽出 `task` / `solution` / `files`，作为 `kind="procedure"` 的 L1 记忆。L0_RAW、procedure 抽取和 JEV 裁决都只看用户请求与最终回答；工具调用只用来提取涉及的 `files`，工具输出不进入任何模型。同一段会话里用户顺口说的偏好（例如「我习惯用 uv」）照常进事实和画像。

把几个 `.py` 文件的正文放进 `add(content=...)` 或普通 user 消息，没有工具轨迹，只走 chat 记忆。

```python
from memhub import SyncMemoryClient

with SyncMemoryClient() as memory:
    memory.add(
        messages=[
            {"role": "user", "content": "高并发写入 SQLite 偶发 database is locked，帮我排查。"},
            {
                "role": "assistant",
                "content": "我先读取存储层代码查看事务开启方式。",
                "tool_calls": [{"id": "t1", "name": "read_file", "arguments": {"path": "app/db.py"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "t1",
                "content": "conn = sqlite3.connect(db_path, timeout=5.0)  # default DEFERRED transaction",
            },
            {
                "role": "assistant",
                "content": "原因：DEFERRED 事务先读后写，升级写锁时若另一连接已持有写锁，SQLite 直接返回 SQLITE_BUSY 而不等待 timeout。改为 BEGIN IMMEDIATE 提前获取写锁，并将 timeout 调至 30s。",
            },
        ],
        user_id="alice",
        workspace_id="~/code/app",  # procedure 的版本边界，可省略
    )

    res = memory.search(
        query="SQLite 并发写入报 database is locked 怎么解决",
        user_id="alice",
        procedure_limit=3,  # 默认 0：chat 检索结果和以前完全一样
    )
    for m in res.memories.procedures:
        print(m.procedure.task, "->", m.procedure.solution, m.procedure.files)
```

### 示例

两条最值得看的：[`examples/02_dual.py`](https://github.com/shibing624/memhub/blob/main/examples/02_dual.py) 是 `digest()` 归纳 L3，[`examples/06_coding_memory.py`](https://github.com/shibing624/memhub/blob/main/examples/06_coding_memory.py) 是工具轨迹写入、方案演化和 `procedures` 组检索。REST、CLI、生命周期在 [`examples/`](https://github.com/shibing624/memhub/tree/main/examples) 目录里。异步客户端见 [快速开始](https://github.com/shibing624/memhub/blob/main/docs/getting-started/quickstart.md)。

## 功能特性

**记忆**

- **版本演化** — 「住上海 → 搬北京」是一次更新，旧版本留作历史，回答默认用最新的
- **分层存放** — 原话（L0）、事实与意图（L1）、画像（L2）、跨证据规律（L3）分开，按需取用
- **决策模型(Jev)把关写入** — 新信息、重复，还是更新，由轻量模型判断
- **原话可查** — 整理过的记忆之外，原始对话始终保留

**接入**

- **跨工具记忆中枢（Hub）** — `memhub install-service`、`memhub install-host all`、`memhub ui`、`memhub connect`
- **Python SDK** — `pip install memhub`（`MemoryClient` / `SyncMemoryClient`）
- **MCP / REST / CLI** — `memhub-mcp`、`memhub serve-api`、`memhub add` / `memhub search`
- **框架插件** — Agentica、Claude Code Hook、Hermes、OpenClaw，见 [集成文档](https://github.com/shibing624/memhub/blob/main/docs/integrations.md)

## Roadmap

- [x] System 1 分层记忆 + 版本演化
- [x] dual / System 2：显式 `digest()` 归纳 L3
- [x] 专用决策模型把关写入
- [x] 跨工具记忆中枢：8 个 AI 工具（Claude Code / Codex / Cursor / Hermes / Agentica / CodeBuddy / OpenCode / WorkBuddy）共享记忆、历史会话导入、后台整理
- [x] 多设备同步：每台本地检索、离线可用，经同步文件夹 / git / 服务器交换变更
- [x] chat 与 coding 统一：工程会话沉淀 procedure 经验，`dual` 也归纳工程习惯
- [x] LoCoMo / LongMemEval / PersonaMem-v2 同场评测领先
- [x] 记忆可视化管理：`memhub ui` 按项目查看、搜索、编辑、删除记忆
- [ ] 云端 HTTP MCP：远程 Agent 直接连接托管记忆
- [ ] 多跳推理类问题的检索增强（LoCoMo multi-hop 仍有提升空间）
- [ ] 补充更多公开评测（CodeMemoryBench 等，待数据公开）

欢迎在 [Issues](https://github.com/shibing624/memhub/issues) 投票或提需求。

## News

- [2026/11/02] **v0.2.2**：项目与 PyPI 包名正式升级为 **`memhub`**（`pip install memhub`、`import memhub`、CLI `memhub`、配置 `~/.memhub/config.yaml`、默认存储 `./.memhub_data`），所有 Hub 命令提升为 `memhub` 一级子命令（`memhub install-service`、`memhub install-host`、`memhub ui`、`memhub connect` 等，独立多租户 REST 服务为 `memhub serve-api`）。新增本机可视化管理页 `memhub ui`（按项目浏览、混合检索、编辑生成新版本、删除同步）；新增支持 CodeBuddy、OpenCode、WorkBuddy（共 8 种宿主工具）；同步目标升级为 float16 向量压缩 + 快照折叠（不再无限增长）；每轮召回按项目限定并只截取相关对话段落，离线评测达到 35/36。详见 [Release-v0.2.2](https://github.com/shibing624/memhub/releases/tag/v0.2.2)。
- [2026/11/01] **v0.2.1**：多设备改为每台电脑本地检索，用网盘文件夹、私有 git 仓库或服务器交换变更日志，删掉了常驻 SSH 隧道。模型配置与 Hub 目录统一。工程记忆只保留问题和最终回答，工具输出不再送进模型。自动归纳改为一天最多一次。详见 [Release-v0.2.1](https://github.com/shibing624/memhub/releases/tag/v0.2.1)。
- [2026/10/24] **v0.2.0**：chat 与 coding 统一成同一套 `add()` / `search()`。带工具轨迹的工程会话在对话记忆之外沉淀 `kind="procedure"` 工程经验，共用存储、版本链和 JEV 裁决，`search(procedure_limit=3)` 单独返回 `procedures` 组；`dual` 的 `digest()` 也覆盖工程经验。删除 `search_coding` 与独立的 coding 库。chat 路径的 prompt、裁决与召回不变。采集 Claude Code / Codex / Agentica 的工具轨迹，dual 模式空闲时自动 digest；MCP 迁移到 mcp 2.x；`install-host` 改写 YAML 时保留注释。详见 [Release-v0.2.0](https://github.com/shibing624/memhub/releases/tag/v0.2.0)。

<details>
<summary>更早版本</summary>

- [2026/10/23] **v0.1.5**：修复 Coding 记忆接真实模型时写不进去的问题。抽取改为返回 `{"candidates": [...]}` 对象，适配默认 `llm_json_mode`；去掉 coverage 硬门槛；同一批写入可以覆盖同一条旧方案；裁决只看当前版本边界内的记录；同一用户的并发写入按 scope 串行。快速上手补充 Coding 记忆章节，`examples/06_coding_memory.py` 增加方案更新后只返回当前版本的示例。详见 [Release-v0.1.5](https://github.com/shibing624/memhub/releases/tag/v0.1.5)。
- [2026/10/23] **v0.1.4**：新增本地单 Core 跨工具记忆中枢（支持 Claude Code / Codex / Cursor / Hermes / Agentica 会话增量导入、后台持久队列写入、开机自启与云端隧道连接）；升级 System 2 v3 聚类认知与版本桥召回；补充 Coding 记忆示例并精简无收益配置项；在 LoCoMo（74.7）、LongMemEval（91.7）、PersonaMem-v2（50.0）同场评测中全面领先 hy-memory 2.0.4 与 mem0。详见 [Release-v0.1.4](https://github.com/shibing624/memhub/releases/tag/v0.1.4)。
- [2026/10/15] **v0.1.3**：升级为 L0_RAW / L1_FACT / L2_PROFILE / L3_DERIVED 四层记忆架构与 JEV 专用决策模型写路径，支持 target-first 关系裁决、版本演化链与历史回溯；新增 Agentica / Claude Code / Hermes / OpenClaw 原生生态集成与 Coding 记忆子系统（存储格式不兼容旧版，需使用新目录）。详见 [Release-v0.1.3](https://github.com/shibing624/memhub/releases/tag/v0.1.3)。
- [2026/06/20] **v0.1.2**：新增 `SyncMemoryClient` 同步客户端封装，支持首次运行自动生成默认配置，完善 REST / MCP / CLI 统一契约与在线文档站点。详见 [Release-v0.1.2](https://github.com/shibing624/memhub/releases/tag/v0.1.2)。
- [2026/06/19] **v0.1.1**：发布双系统分层记忆 SDK，支持语义 + BM25 + RRF 混合检索及 CLI / REST / MCP 接口。详见 [Release-v0.1.1](https://github.com/shibing624/memhub/releases/tag/v0.1.1)。
- [2026/06/18] **v0.1.0**：首版开源。

</details>

完整变更见 [CHANGELOG](https://github.com/shibing624/memhub/blob/main/CHANGELOG.md)。

## 文档

完整使用文档：**[https://shibing624.github.io/memhub](https://shibing624.github.io/memhub)**

| 文档 | 内容 |
|---|---|
| [安装与配置](https://github.com/shibing624/memhub/blob/main/docs/getting-started/installation.md) | extras、配置文件、凭证与优先级 |
| [快速开始](https://github.com/shibing624/memhub/blob/main/docs/getting-started/quickstart.md) | 同步 / 异步客户端、Coding 记忆、多轮写入、Agent 集成 |
| [跨工具记忆中枢（Hub）](https://github.com/shibing624/memhub/blob/main/docs/memhub.md) | 本地 Core、8 个宿主接入、历史会话导入、`memhub ui` |
| [多设备同步](https://github.com/shibing624/memhub/blob/main/docs/memhub-server.md) | `dir` / `git` / `ssh` / `http` 变更日志复制，每台本地检索 |
| [生态集成](https://github.com/shibing624/memhub/blob/main/docs/integrations.md) | Agentica / Claude Code / Hermes / OpenClaw |
| [MCP / REST](https://github.com/shibing624/memhub/blob/main/docs/mcp_integration.md) | 工具 ↔ HTTP ↔ SDK 对照 |
| [评测说明](https://github.com/shibing624/memhub/blob/main/docs/benchmarks.md) | 协议、题集、复现与结果细分 |
| [架构说明](https://github.com/shibing624/memhub/blob/main/docs/architecture.md) | 可选阅读：SDK 与各接入层的关系、存储 |
| [四层记忆](https://github.com/shibing624/memhub/blob/main/docs/memory_layers.md) | 可选阅读：L0 原文 / L1 事实 / L2 画像 / L3 推导 |
| [写入决策设计](https://github.com/shibing624/memhub/blob/main/docs/jev-memory-design.md) | 可选阅读：决策模型动作与关系、版本链 |
| [示例说明](https://github.com/shibing624/memhub/blob/main/examples/README.md) | 6 个可运行的端到端示例脚本说明 |

本地预览：`pip install -r requirements-docs.txt && mkdocs serve`

## 社区与支持

> 如果 memhub 帮到了你，欢迎点个 ⭐ Star，让更多人看到。

- **GitHub Issues** — [提交 issue](https://github.com/shibing624/memhub/issues)
- **微信群** — 添加微信号 `xuming624`，备注 "llm"，加入技术交流群

<img src="https://github.com/shibing624/agentica/blob/main/docs/assets/wechat.jpeg" width="200" alt="微信群二维码" />

## 引用

如果您在研究中使用了 memhub，请引用：

> Xu, M. (2026). memhub: Personal Memory Hub and Dual-System Layered Memory SDK for AI Agents. GitHub. https://github.com/shibing624/memhub

```bibtex
@misc{xu2026memhub,
  author    = {Xu, Ming},
  title     = {memhub: Personal Memory Hub and Dual-System Layered Memory SDK for AI Agents},
  year      = {2026},
  publisher = {GitHub},
  url       = {https://github.com/shibing624/memhub}
}
```

## 许可证

[Apache License 2.0](https://github.com/shibing624/memhub/blob/main/LICENSE)

## 贡献

欢迎贡献，见 [CONTRIBUTING.md](https://github.com/shibing624/memhub/blob/main/CONTRIBUTING.md)。

## 致谢

- [Chroma](https://github.com/chroma-core/chroma)
- [Kuzu](https://github.com/kuzudb/kuzu)
- [FastAPI](https://github.com/tiangolo/fastapi)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [mem0](https://github.com/mem0ai/mem0)
