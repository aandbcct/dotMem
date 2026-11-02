"""examples 公共工具：每个 demo 用独立干净的本地存储目录。

配置从 ~/.memhub/config.yaml 读取，只保留 Settings 认识的键，再覆盖 mode 和
storage_dir。已删除的旧键（例如 system2_trigger_mode）不会进入 Settings。
JEV 凭据用 yaml 的 jev_api_key，或环境变量 MEMHUB_JEV_API_KEY / MEMHUB_JEV_ENDPOINT。
"""
import os
import shutil
import sys
from pathlib import Path

import yaml

EXAMPLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLES_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_py_paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
if str(REPO_ROOT) not in _py_paths:
    os.environ["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), *_py_paths])

from memhub.config import DEFAULT_CONFIG_PATH, Settings, config_path
from memhub.sdk_models import MemoryItem, SearchMemories, WriteResult

DATA_ROOT = EXAMPLES_DIR / ".data"


def _user_config() -> dict:
    """读取用户 yaml。MEMHUB_CONFIG_FILE 已指向本目录临时文件时，仍读家目录配置。"""
    path = config_path()
    if path.is_relative_to(DATA_ROOT) or not path.exists():
        path = DEFAULT_CONFIG_PATH
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} 必须是平铺的键值配置")
    return loaded


def example_settings(mode: str, name: str, **overrides: object) -> Settings:
    """构造 demo 用的 Settings：丢掉未知键，使用干净 storage_dir。"""
    raw = _user_config()
    allowed = set(Settings.model_fields)
    cfg = {key: value for key, value in raw.items() if key in allowed}
    cfg["mode"] = mode
    cfg["storage_dir"] = fresh_storage(name)
    cfg.update(overrides)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_cfg = DATA_ROOT / f"{name}_config.yaml"
    tmp_cfg.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.environ["MEMHUB_CONFIG_FILE"] = str(tmp_cfg)
    return Settings()


def fresh_storage(name: str) -> str:
    """返回一个干净的存储目录（每次运行清空，保证 demo 可复现）。"""
    path = DATA_ROOT / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def show_write(res: WriteResult, text: str = "") -> None:
    """打印一次 add 的 L0 id 和 JEV 裁决。"""
    if text:
        print(f"  + {text}")
    decisions = ", ".join(
        f"{item.action}/{item.update_type or '-'}" for item in res.decisions
    ) or "无结构化版本"
    print(
        f"    -> raw={res.memory_id[:8]}  versions={res.extracted_count}  "
        f"commit={res.commit_passed}  decisions={decisions}  "
        f"scene={res.scene}  procedures={len(res.procedure_ids)}  "
        f"({res.processing_time_ms / 1000:.2f}s)"
    )


def _render_chain(item: MemoryItem) -> str:
    """渲染演化链（最新→最旧）的简短文本，未发生演化时返回空串。"""
    chain = item.evolution_chain or []
    if len(chain) >= 2:
        parts = [f"{node.layer}:{node.content[:24]}" for node in chain]
        return "\n      演化链(最新→最旧): " + " → ".join(parts)
    if item.previous:
        parts = [f"{node.layer}:{node.content[:24]}" for node in item.previous]
        return "\n      上一版本: " + ", ".join(parts)
    return ""


def show_memories(memories: SearchMemories) -> None:
    """统一渲染召回结果：profile / proactive / normal，以及非空的 procedures / schema / catalog。"""
    groups: list[tuple[str, list[MemoryItem]]] = [
        ("profile", memories.profile),
        ("proactive", memories.proactive),
        ("normal", memories.normal),
    ]
    for extra_name, extra_items in (
        ("procedures", memories.procedures),
        ("schema", memories.schema),
        ("catalog", memories.catalog),
    ):
        if extra_items:
            groups.append((extra_name, extra_items))
    for group, items in groups:
        print(f"  [{group}] {len(items)} 条")
        for item in items:
            chain_tail = _render_chain(item)
            kind = item.kind or item.category
            print(
                f"    - ({item.layer or '?'} {kind} score={item.score:.2f}) "
                f"{item.content}{chain_tail}"
            )
