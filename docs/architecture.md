# memhub 架构说明

> 可选阅读。安装、写记忆、接 MemHub 都不用看这一页。

memhub 采用「核心 SDK + 生态接入层」的分层设计。**SDK 是唯一的核心**，对外的
CLI / REST / MCP / Skill 都是围绕它的并列接入方式（frontends），互不依赖、可按需启用。

## 分层总览

```
        Agent / Cursor / Claude Desktop / 你的应用
                          │
        ┌─────────────┬───┴────────┬──────────────┐
        │             │            │              │
     Skill          MCP          REST            CLI          ← 生态接入层（并列）
   (使用说明)  (本地/云端*)    (FastAPI)    (memhub 命令)
        └─────────────┴───┬────────┴──────────────┘
                          │  都调用同一套 Python API
                ┌─────────▼─────────┐
                │   memhub SDK    │              ← 核心：MemoryClient
                │  (MemoryClient)   │
                └─────────┬─────────┘
                          │
        ┌─────────────────┼─────────────────────┐
        │                 │                      │
   Vector (Chroma)   Graph (Kuzu)        KV / 历史 (SQLite)   ← 存储层
```

Agent 侧推荐链路（与 Mem0 / Zep 一致的黄金架构）：

```
Agent / Cursor  ──MCP──▶  MCP Server（受 Skill 指导）  ──▶  memhub SDK  ──▶  Vector + KV
```

- **Skill** 不是代码层，而是给 agent 的「何时调用、如何解读结果」说明书，引导 agent 正确使用 MCP/CLI。
- **MCP** 是面向 agent 的标准协议接入，把 SDK 能力暴露为工具。*本地 uvx MCP 已实现；云端 HTTP MCP 规划走 REST 同一契约，见 [`mcp_integration.md`](./mcp_integration.md)。
- **SDK** 是全部业务逻辑所在；REST/CLI/MCP 都只是薄封装。

## 各层职责

| 层 | 模块 | 职责 | 依赖 |
|---|---|---|---|
| 核心 SDK | `memhub.client.MemoryClient` | 写入、JEV 裁决、检索、演化、显式 digest，全 async | 仅依赖存储与 provider |
| MemHub（跨工具中枢） | `memhub.hub`（`memhub serve` / `install-service` / `install-host` / `ui` / `connect`） | 每台设备一个本地 Core，采集 18 种 AI 工具会话、按项目本地召回、提供浏览器管理页 `/ui`，并经 `dir` / `git` / `ssh` / `http` 同步变更日志 | → SDK |
| CLI（SDK 前端） | `memhub.cli`（`memhub`） | Hub 与 SDK 的统一命令行入口，`asyncio.run` 包装 | → SDK |
| REST | `memhub.api`（`memhub serve-api`） | 独立多租户 HTTP 接口，鉴权 + 统一错误，契约见 [`mcp_integration.md`](./mcp_integration.md) | → SDK |
| MCP（生态） | `memhub.mcp`（`memhub-mcp`） | 面向 agent 的工具协议；**本地** stdio / streamable-http 已实现；**云端** HTTP MCP 规划走 REST 同一契约 | → SDK（云端经 REST → SDK） |
| Skill（生态） | `memhub/skills/memhub-sdk/SKILL.md` | 指导 agent 何时/如何调用 MCP/CLI | → MCP/CLI |
| 存储 | `memhub.storage` | Chroma 向量是当前可读性；Kuzu 只投影 dual digest 的证据关系；SQLite 保存历史、版本计划与跨设备复制日志（`log` / `tombstones`） | — |

> 关于目录结构：MCP/CLI 代码物理上仍位于 `memhub.*` 子包内（便于单包安装与
> 复用 `MemoryClient`），但**职责上与 SDK 并列**——它们只做协议转换，不含业务逻辑。
> 这与 Mem0 等项目把 `mem0` 核心与 `openmemory`(MCP) 同仓并存的取舍一致。

## 四个处理阶段

层定义见 [memory_layers.md](memory_layers.md)，调用契约与迁移见
[jev-memory-design.md](jev-memory-design.md)，可交互流程见
`docs/design/jev-memory-flow.html`（本地设计文件，Git 忽略）。

| 层 | 含义 | 产生方 |
| --- | --- | --- |
| L0_RAW | 原始对话证据（用户请求与最终回答） | 写入即保留 |
| L1_FACT | 明确事实、独立事件、明确意图、工程经验（`procedure`） | 生成候选 → JEV 关系裁决 |
| L2_PROFILE | 身份、画像、状态维度 | 生成候选分拣 → JEV 关系裁决 |
| L3_DERIVED | 跨证据模式（`schema` / `core_schema`）、人格目录（`persona_catalog`） | 显式 digest → 聚类生成与证据校验 |

System1 两种模式共用：保留来源 → 生成候选（事实 / 画像 / 意图，工程会话并行抽 procedure） → 相似度达到 `reconcile_relation_threshold` 的候选进入批量 JEV target/action 裁决（低于阈值直接作为独立记忆追加） → 批量 embedding → 校验 scope epoch/revision → durable append/link（同步写入 `versions.db` 复制日志 `log`）。
默认 latest 读取，历史可显式查看。

dual 写入后登记 scope；digest 对未归纳的 L1/L2（含 procedure）做层次聚类，生成 schema、跨领域 core schema 与 persona catalog 并校验证据引用。不同 scope 默认并行 4，同 scope 有序处理。失败任务保留 pending。
版本提交与图投影（含追加到已有 schema 的证据边）之间通过持久待处理记录恢复，图只作为证据关系投影。

混合检索调用 Embedding，不调用语义决策模型。画像与事实只召回 latest；
图结果须对照 VDB 最新状态。关键词保留在读侧重排，与写入判断无关。

| 维度 | system1 | dual |
| --- | --- | --- |
| 写入 | 同一生成 + JEV 管线 | 同左 |
| 产物 | L0 / L1 / L2 | 同左，digest 增加 L3 |
| System2 / 图库 | 关闭 | 显式 digest |
| 版本保留 | 全部保留 | 全部保留 |

## 召回缓存与时延

一次 Hub 召回（`POST /v1/hub/recall`，宿主 hook 每轮提问都会调用）走：
同步拉取 → 项目视图 → 两路混合检索（语义 + BM25）→ 历史对话 → 注入预算裁剪。
向量侧是 Chroma HNSW 加固定 `top_k`，不随记忆条数线性增长；随库增大的主要是
Python 侧的全量语料读取与缓存重建。

| 缓存 | 失效键 | 重建内容 |
| --- | --- | --- |
| `_LexicalCache` | `vector.generation` + `where` | 该 scope 的 BM25 倒排（逐文档分词） |
| `ProjectViewCache` | `vector.generation` + workspace，另有 300s `max_age` | 项目约定视图与会话边界 |
| `_RetiredClaimsIndex` | `vector.generation` | 已退役说法的反向来源索引 |

`generation` 记在共享 journal 里，本机写入、跨设备同步、其它进程写入都会递增，
所以缓存不会读到陈旧数据；代价是**每轮采集写入后，下一轮提问必然重建这些缓存**。
项目视图计算会话边界只读取 L0 的元数据（`get_metadata_nodes`），不把原始对话
正文拉进 Python；本机 1895 条 L0 实测这一步的读取耗时与读全文相当，收益是内存
占用而不是时延。原始对话的 BM25 语料也限定 `is_latest`。注入文本有
`hub_inject_max_chars`（默认 3000）等硬上限，长度不随库增长。

**怎么观测。** `/v1/hub/status` 的 `recall` 按 `warm` / `after_write` / `cold` /
`embedding` 分桶记录最近 15 分钟样本，`memhub doctor` 只在最近 5 个样本里至少
3 个超过 3s 时告警。`after_write` 偏慢是正常的重建代价，不是写入失败；若持续
超过 3s，说明库内文本量已让重建变重，可以逐条归档无用条目，或检查后台采集
是否高频写入。稳态 `warm` 只花一次 query embedding。

**本机受控实测**（约 7.5k 条当前记忆、1895 条 L0）：写一条 `raw_only` 证据后立刻
召回，写后首次召回中位 2.77s、随后稳态 0.89s。偶发的 7s 以上样本与后台写入或
机器负载同时出现，不能稳定复现，不要拿单次样本判断回归；`doctor` 也要求最近
5 个样本里至少 3 个超阈值才告警。真正确认长尾要在固定查询集上重复多轮取分位数。

更多接入与部署细节见 [mcp_integration.md](mcp_integration.md)。
