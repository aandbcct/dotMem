# 安装

需要 **Python ≥ 3.11**。

## PyPI

```bash
pip install memhub              # 核心 SDK（MemoryClient）
pip install memhub[all]         # SDK + REST + CLI + MCP
pip install memhub[api]         # REST（fastapi + uvicorn）
pip install memhub[cli]         # CLI（typer）
pip install memhub[mcp]         # MCP server（mcp 2.x，可单独安装）
```

## 配置

首次 `MemoryClient(...)` 或 `Settings()` 会在 **`~/.memhub/config.yaml`** 自动创建默认配置（与包内 `memhub/config.default.yaml` 一致），只需填入 API key：

```yaml
jev_api_key: ...
llm_api_key: sk-...
embed_api_key: sk-...
```

构造需要三组凭证：JEV（`jev_api_key`）、生成 LLM（`llm_api_key`）、Embedding（`embed_api_key`）。缺任何一组都无法启动。

配置优先级：显式传参 > `MEMHUB_*` 环境变量 > YAML > 默认值。默认 `storage_dir` 是 `./.memhub_data`。

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

