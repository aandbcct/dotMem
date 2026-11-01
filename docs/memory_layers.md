# 四层记忆模型

分层表达处理阶段；`kind` 表达语义，不再为每一种内容设置独立层。

| 层 | 内容 | producer | 读取 |
| --- | --- | --- | --- |
| `L0_RAW` | 原始输入证据（含工具轨迹） | writer，每次写入先保留 | `get` / `search_conversation` / `raw_limit > 0` |
| `L1_FACT` | 显式事实、独立事件、明确意图、工程经验（`procedure`） | 生成模型提供候选，JEV 裁决 | normal；`kind=intention` 进入 proactive；`kind=procedure` 进入 procedures |
| `L2_PROFILE` | 状态、身份、画像维度 | 生成模型分拣 + JEV 关系裁决 | profile；关闭 profile 路时进入 normal |
| `L3_DERIVED` | 跨证据模式（`schema` / `core_schema`）、人格目录（`persona_catalog`） | dual 的 `digest()` | catalog（默认开启）与 schema（`derived_context_limit > 0` 时）；带实际 evidence IDs |

`system1` 写 L0/L1/L2；`dual` 共享完全相同的写入步骤，在 `digest()` 时生成 L3。SDK 不在后台巩固。MemHub 的 dual Core 会在距上次成功自动 digest 满 `--digest-every` 小时（默认 24，`0` 关闭）且仍有待归纳证据时，于队列排空后自动 digest 一次。默认 `search` 不返回 L0；`search_conversation` 只搜 L0。`intention_limit=0` 时 proactive 为空；`procedure_limit=0` 时 procedures 为空。
显式意图放 L1；推导模式放 L3。`kind` 可取 `fact`、`profile`、`intention`、`procedure`、`schema`、`core_schema`、`persona_catalog`、`summary`。

版本由 `supersedes` / `superseded_by` 链接，退休表示保留原文、向量和时间，
`is_latest=false`。NEGATE 写入否定版本；TEMPORAL 保留基线并带有效期；CONFLICT
保留两侧并记录 `conflicts_with`。不存在按相似度把“更新”当作“已知”的写入门。

本次为破坏性变更：删除旧层名、旧状态与旧配置兼容，不自动迁移旧存储。
旧目录需离线导出后重新写入新目录。历史版本保持 ACTIVE，统一由 is_latest
及归档元数据区分当前可读性。Kuzu 是证据关系投影，VDB 决定当前可读性。
