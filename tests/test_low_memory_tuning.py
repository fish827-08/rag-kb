"""低配服务器（2 核 2G）CPU 调优与设备 auto 解析单元测试。"""
import pytest


class _Vec(list):
    """模拟 numpy 向量的 tolist 接口，避免测试依赖真实模型。"""

    def tolist(self):
        return list(self)


class _FakeModel:
    def __init__(self):
        self.kwargs = None

    def encode(self, texts, **kwargs):
        self.kwargs = kwargs
        return [_Vec([0.6, 0.8]) for _ in texts]


def test_线程数自动解析不超过四核():
    from kb.embedder import resolve_torch_threads
    assert resolve_torch_threads(0, cpus=8) == 4
    assert resolve_torch_threads(0, cpus=2) == 2
    assert resolve_torch_threads(0, cpus=1) == 1
    assert resolve_torch_threads(3, cpus=8) == 3


def test_可用CPU取affinity与cgroup较小值(monkeypatch):
    import os

    from kb import embedder
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0, 1, 2, 3},
                        raising=False)
    monkeypatch.setattr(embedder, "_cgroup_cpu_limit", lambda: 2)
    assert embedder.available_cpus() == 2


def test_嵌入批大小传入encode():
    from kb.embedder import Embedder
    e = Embedder("fake-model", embed_batch_size=8)
    e._model = _FakeModel()
    e._state = "ok"
    out = e.embed_texts(["a", "b"])
    assert e._model.kwargs["batch_size"] == 8
    assert e._model.kwargs["normalize_embeddings"] is True
    assert out == [[0.6, 0.8], [0.6, 0.8]]


def test_批大小非法值回落默认():
    from kb.embedder import Embedder
    assert Embedder("fake-model", embed_batch_size=0).embed_batch_size == 32
    assert Embedder("fake-model", embed_batch_size=-4).embed_batch_size == 1


def test_嵌入器状态暴露低配参数():
    from kb.embedder import Embedder
    status = Embedder("fake-model", torch_threads=2,
                      embed_batch_size=16).status()
    assert status["torch_threads"] == 2
    assert status["embed_batch_size"] == 16


def test_配置默认值(env_isolated, monkeypatch):
    monkeypatch.delenv("KB_TORCH_THREADS", raising=False)
    monkeypatch.delenv("KB_EMBED_BATCH_SIZE", raising=False)
    from kb.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.torch_threads == 0
    assert s.embed_batch_size == 32


def test_设备auto非交互回退cpu(tmp_path, env_isolated):
    from kb.cli import resolve_device
    from kb.config import Settings
    s = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs",
                 device="auto", llm_mode="off", watch_dir="")
    assert resolve_device(s, interactive=False) == "cpu"


def test_设备auto遵循已存runtime选择(tmp_path, env_isolated):
    from kb.cli import resolve_device
    from kb.config import Settings
    s = Settings(data_dir=tmp_path / "data", log_dir=tmp_path / "logs",
                 device="auto", llm_mode="off", watch_dir="")
    s.runtime_file.parent.mkdir(parents=True, exist_ok=True)
    s.runtime_file.write_text('{"device": "cuda"}', encoding="utf-8")
    assert resolve_device(s, interactive=False) == "cuda"


def test_容器默认预缓存轻量模型():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    text = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG KB_EMBED_MODEL=BAAI/bge-small-zh-v1.5" in text
    assert "BAAI/bge-m3" not in text.split("ARG KB_EMBED_MODEL=")[1].split("\n")[0]


def test_systemd模板不使用无效auto设备():
    """torch 不接受 device='auto'，模板必须写 cpu 或留空走自动检测。"""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    text = (root / "scripts" / "linux" / "kb.service").read_text(
        encoding="utf-8")
    assert "KB_DEVICE=auto" not in text
    assert "KB_DEVICE=cpu" in text
