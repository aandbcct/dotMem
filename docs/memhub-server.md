# 多设备同步

每台电脑都有一份**完整的本地记忆**。检索只查本机：你每次输入提示词，`memhub hook` 都在本机几十毫秒内返回，断网、合盖刚唤醒都不受影响。电脑之间交换的是**变更日志**：已经裁决好的记忆版本、原始 Evidence 和它们的向量。接收方直接写进本地库，不调用任何模型。

```text
 笔记本                                      台式机
 Claude Code / Codex ── 本机 Core ── 本地库    本地库 ── 本机 Core ── Cursor / Hermes
                          │                     │
                          └── 变更日志 ──▶ 同步目标 ◀── 变更日志 ──┘
                    （iCloud 文件夹 / git 仓库 / 服务器上的 Core）
```

## 选哪种同步方式

| 你的情况 | 用法 | 需要什么 |
|---|---|---|
| 普通用户，几台电脑都登录了同一个网盘 | `connect dir <文件夹>` | iCloud Drive、Dropbox、OneDrive、坚果云、Syncthing、NAS 挂载……任何会自动同步的文件夹 |
| 开发者，有 GitHub / GitLab 账号 | `connect git <仓库地址>` | 一个**私有**空仓库，本机 `git` 能免密推送 |
| 有一台常开的服务器 | `connect ssh user@server` | 服务器上跑着 `memhub install-service`，SSH 免密登录 |
| 服务器已经有 HTTPS 反代 | `connect http https://... --api-key ...` | 服务器 Core 的 `runtime-token` |

只需要选一种。每台电脑都执行同一条 `connect`，连到同一个目标。

S3 / R2、Cloudflare D1 这类对象存储没有内置：普通用户不会去配 AccessKey 和 Bucket，有这个能力的人用 git 或服务器更简单；把对象存储挂成本地目录（rclone mount 之类）后，也可以直接当 `dir` 用。

## 开始

每台电脑上（先完成 [MemHub 三步开始](memhub.md#三步开始)）：

```bash
# macOS iCloud Drive
memhub connect dir "~/Library/Mobile Documents/com~apple~CloudDocs/memhub"

# 或者：私有 git 仓库
memhub connect git git@github.com:you/my-vibecoding-memory.git

# 或者：服务器上的 Core（每次同步是一条 ssh 命令，不常驻隧道）
memhub connect ssh user@your-server
```

`connect` 先访问一次目标确认可用，把配置写进本机 `~/.memhub/hub.json`（权限 `0600`），然后让本机 Core 立刻同步一次。之后 Core 自动同步：

- 本机写入一轮对话之后（几秒内合并成一次）；
- 每 5 分钟一次；
- 检索时发现上次拉取已超过 1 分钟，就在后台拉一次。**这次检索不等它**，新记忆在下一次检索出现。

```bash
memhub sync      # 立刻同步一次，返回 pushed / received / applied / pending
memhub status    # sync 字段：目标、上次时间、上次结果、上次错误、本机是否负责归纳
memhub disconnect  # 停止同步；本机记忆和同步目标上的数据都保留
```

已经用了一段时间、本机已有记忆的电脑，第一次 `connect` 时会把已有的记忆也发出去。

## 私有 Git 仓库（`connect git`，推荐开发者）

不需要任何云服务器，用一个 GitHub / GitLab **私有仓库**就能在多台开发机之间同步，同时作为个人记忆的云端冷备：

```bash
# 1. 创建一个私有空仓库（用 gh CLI 或在 GitHub 网页上新建 Private 仓库）
gh repo create my-vibecoding-memory --private

# 2. 在第一台电脑上接入工具并关联该仓库（确保本机 git 能免密 push）
memhub install-service
memhub install-host agentica   # 或 claude-code / codex / all
memhub connect git git@github.com:<你的用户名>/my-vibecoding-memory.git

# 3. 验证同步状态
memhub status
```

工作原理与多机接入：

- Core 在 `~/.memhub/sync-git/` 下维护该仓库的本地克隆（`main` 分支）。
- 同步只读写 `manifest.json`、`log/<origin>/<seq>.json` 和 `snapshot/`。每台电脑拥有独立的 `origin` UUID，**只往自己的子目录追加新文件**，因此多台电脑并发 `git pull --rebase` + `git push` 不会在日志文件上冲突。
- 仓库里可以另外放 `README.md`、`memory/<项目>.md` 看板或生成脚本（如 GitHub Actions 自动把同步日志渲染成按项目分类的 Markdown 页面）。这些文件不会被导入记忆库，也不会被检索到；同步提交也不会改它们。注意：**不要在 GitHub 上直接手改 Markdown 或 JSON 来修改记忆**（改文字需要重新计算向量并追加版本链），要修改或删除记忆请在本机运行 `memhub ui`。
- `~/.memhub/config.yaml`（含你的 API key）**不会**进入这个仓库。日志文件里是记忆正文、元数据和向量。
- 在第二台电脑上，装好 `memhub`、配好 `~/.memhub/config.yaml` 并启动 `install-service` 后，执行**完全相同**的一行 `memhub connect git git@github.com:<你的用户名>/my-vibecoding-memory.git`，即刻拉回全部记忆与向量。

## 服务器（`connect ssh` / `connect http`）

服务器上就是一台普通的 MemHub：

```bash
pip install "memhub[all]"
memhub install-service
sudo loginctl enable-linger "$USER"   # Linux：SSH 断开后服务继续运行
```

`connect ssh` 的每次同步是一条 `ssh user@server memhub relay ...` 命令，由服务器的 CLI 转给它本机的 Core，用完即断。所以：

- SSH 要能免密登录（key 或 agent）。非默认端口用 `--ssh-arg=-p --ssh-arg=2222`。
- 服务器上 `memhub` 不在 `PATH` 里时，用 `--remote-command /path/to/venv/bin/memhub`。
- Core 仍只监听服务器的 `127.0.0.1`，不开放任何端口。
- 笔记本合盖、换网络都没关系：下一次同步重新 ssh，失败就等下一次，本机记忆照常可用。

已有 HTTPS 反代（Caddy、Nginx、Cloudflare Tunnel）时用 `connect http`：

```bash
memhub connect http https://mem.example.com --api-key "$(ssh user@server 'cat ~/.memhub/runtime-token')"
```

明文 `http://` 只允许 loopback 地址。

## 谁来归纳（dual 模式）

System 2 的 `digest()` 用 LLM 从多条事实归纳 L3。两台电脑各自归纳同一批事实会得到两套不同的 schema，所以**同一组设备只有一台归纳**，其余的接收它的结果：

- `ssh` / `http`：服务器归纳。服务器要配好模型，并且模型地址要**从服务器能访问到**（只在办公内网可达的网关，放到公网服务器上会调不通）。
- `dir` / `git`：第一台同步的电脑归纳，记在同步目标的 `manifest.json`（`digester` 字段）。这台电脑要以 `dual` 模式运行。`memhub status` 的 `sync.digests_here` 显示本机是否负责。

抽取和 JEV 裁决仍然在写入那台电脑上做：在哪台电脑上聊，就由哪台电脑的模型配置处理。

## 两台电脑同时改了同一条记忆

离线时两台电脑可能各自把同一条记忆更新成不同的新版本。同步后的结果在每台电脑上都一样，按下面的规则合并：

- 旧版本被任意一边取代，它就不再是当前版本；`superseded_by` 记下两边的新版本；
- 两个新版本都保留为当前版本，检索时都可能出现，各自带着来源；
- 在一台电脑上删除的记忆，同步后在所有电脑上都删除，不会被另一台带回来；
- 其它字段以最后收到的修改为准。

同步不会丢记忆，最坏的情况是同一件事出现两个当前版本。之后再聊到这件事时，JEV 裁决会看到这两个版本，可以把它们合成一个。

## 要求与限制

- 同步目标上是你的全部记忆原文。git 仓库必须是私有的；网盘目录不要共享给别人。
- 游标（`cursors.db`）在每台电脑本地，各自只采集本机的会话。
- 同步目标的大小跟着当前记忆走，不随写入次数累积：向量以 float16 传输；负责 digest 的设备在日志累积到 400 条后折叠成 `snapshot/state-<epoch>.json`，也可以用 `memhub sync --compact` 手动触发。`git` 目标会用新的根提交强推替换历史，**不要在这个仓库的 `main` 上保留需要长期留存的手工提交历史**；脚本、README 这类文件本身会保留在新根提交里，其它设备下次同步时自动跟随。
