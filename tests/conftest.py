import os
import sys
from pathlib import Path

import pytest

# 仓库根加入 sys.path（对齐 kb serve 运行时工作目录，使 orchestra.b3 等可 import）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 全部测试统一使用小模型，避免下载 2GB 的 BGE-M3
os.environ["KB_EMBED_MODEL"] = "BAAI/bge-small-zh-v1.5"


@pytest.fixture
def env_isolated(monkeypatch, tmp_path):
    """每个测试独立的 KB_DATA_DIR，并清掉配置单例缓存。
    Ollama 基址指向不可达端口：LLM 探测确定性失败，测试不依赖本机 Ollama 运行状态。"""
    from kb import config
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("KB_OLLAMA_BASE_URL", "http://127.0.0.1:1")
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()