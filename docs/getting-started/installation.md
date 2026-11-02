# 安装

需要 **Python ≥ 3.11**。

## PyPI

```bash
pip install memhub              # 核心 SDK（MemoryClient / SyncMemoryClient）
pip install "memhub[all]"       # SDK + Hub + REST + CLI + MCP
pip install "memhub[api]"       # REST 与 Hub Core（fastapi + uvicorn）
pip install "memhub[cli]"       # CLI（typer）
pip install "memhub[mcp]"       # MCP server（mcp 2.x，可单独安装）
```

## 配置

首次 `MemoryClient(...)` 或 `Settings()` 会在 **`~/.memhub/config.yaml`** 自动创建默认配置（与包内 `memhub/config.default.yaml` 一致），填入三把 API key（对应地址均可修改）：

```yaml
jev_api_key: ""    # 写入裁决。System One，不是 Chat Completions
jev_endpoint: https://api.typesafe.ai/v1/systemone

llm_api_key: ""    # 抽取与 System 2。OpenAI 兼容，地址可改
llm_base_url: https://api.openai.com/v1

embed_api_key: ""  # 向量索引与检索。OpenAI 兼容，地址可改
embed_base_url: https://api.openai.com/v1
```

构造需要三组凭证：JEV（`jev_api_key`）、生成 LLM（`llm_api_key`）、Embedding（`embed_api_key`）。缺任何一组都无法启动。

这六个配置项也支持环境变量：`MEMHUB_JEV_API_KEY` / `MEMHUB_JEV_ENDPOINT`、`MEMHUB_LLM_API_KEY` / `MEMHUB_LLM_BASE_URL`、`MEMHUB_EMBED_API_KEY` / `MEMHUB_EMBED_BASE_URL`。

配置优先级：显式传参 > `MEMHUB_*` 环境变量 > YAML（`~/.memhub/config.yaml`，可通过 `MEMHUB_CONFIG_FILE` 覆盖） > 默认值。默认 `storage_dir` 是 `./.memhub_data`。

<details markdown="1">
<summary>可选阅读：改源码、依赖分别是什么</summary>

从源码安装：

```bash
git clone https://github.com/shibing624/memhub.git
cd memhub
pip install -e ".[dev]"
```

核心依赖：`pydantic`、`pyyaml`、`ruamel.yaml`、`openai`、`chromadb`、`kuzu`、`scikit-learn`、`numpy`、`httpx`。`scikit-learn` 给 `dual` 的 `digest()` 做聚类；`ruamel.yaml` 让 `memhub install-host` 改写宿主配置时保留注释。

`[api]` 是 FastAPI，`[cli]` 是命令行，`[mcp]` 是 mcp 2.x。`[all]` 是这三项一起。测试或自建后端可以注入 `jev=` / `llm=` / `embed=`，注入后不再检查对应密钥。

</details>

