---
name: memhub-recall
description: 用户的全局个人记忆（memhub MemHub，跨 Agentica / Claude Code / CodeBuddy / Codex / Cursor / Hermes / OpenCode / WorkBuddy 共享）。回答涉及用户偏好、兴趣、长期约定、过往决策，或用户问「我之前问过什么 / 之前在别的 CLI 聊到哪了」时使用。
---

<!-- managed by memhub hub -->

# memhub MemHub 个人记忆

本机所有工具共用一个 Hub Core，当前宿主是 `{host}`。下面的工具和命令都已绑定个人记忆，
不需要也不能传 `user_id` / `app_id` / `agent_id`。

每轮提问时，相关记忆通常已经以 `<relevant-memories>` 注入上下文。先用注入的内容，
不够再查。普通对话结束后会自动采集，不用再写一遍。

| 要做的事 | MCP 工具（`memhub`） | 没有 MCP 时的命令 |
|---|---|---|
| 查偏好、画像、事实、历史结论 | `memory_search(query)` | `{recall} "要查的内容"` |
| 查「我之前说过/问过什么」的原话 | `conversation_search(query)` | 同上，输出里的「历史对话」 |
| 用户明确要求记住某件事 | `memory_add(content)` | `{remember} "要记住的内容"` |

- 一次调用就够，不要去读 `~/.memhub` 下的数据库或配置。
- 不要用 `memhub add` / `memhub search`：它们是 SDK 命令，不经过 Hub Core，会写进当前目录下另一个库。
- `conversation_search` 结果的 `session_id` 形如 `codex:<id>`，冒号前是来源工具。
