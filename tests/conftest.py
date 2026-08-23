import os
import pytest

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 全部测试统一使用小模型，避免下载 2GB 的 BGE-M3
os.environ["KB_EMBED_MODEL"] = "BAAI/bge-small-zh-v1.5"


@pytest.fixture
def env_isolated(monkeypatch, tmp_path):
    """每个测试独立的 KB_DATA_DIR，并清掉配置单例缓存。"""
    from kb import config
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()