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


def test_torch后端可用时预检通过():
    from kb.embedder import torch_backend_issue
    assert torch_backend_issue() == ""


def test_torch版本过旧时直说版本不兼容(monkeypatch):
    """transformers 静默禁用 PyTorch 后底层只报 NameError，预检必须点出根因。"""
    from kb import embedder
    monkeypatch.setattr("transformers.utils.is_torch_available", lambda: False)
    monkeypatch.setattr(
        embedder, "_installed_version",
        lambda name: {"torch": "2.5.1", "transformers": "5.20.2"}[name])
    issue = embedder.torch_backend_issue()
    assert "torch 版本不满足 transformers 要求" in issue
    assert "2.5.1" in issue and "5.20.2" in issue
    assert "升级 torch" in issue


def test_torch未安装时提示安装(monkeypatch):
    from kb import embedder
    monkeypatch.setattr("transformers.utils.is_torch_available", lambda: False)
    monkeypatch.setattr(embedder, "_installed_version", lambda _name: "未安装")
    issue = embedder.torch_backend_issue()
    assert "torch 未安装" in issue
    assert "版本不满足" not in issue


def test_预检无法判定时不阻断加载(monkeypatch):
    """老版本 transformers 取不到判定函数时，退回原有报错路径。"""
    from kb import embedder
    monkeypatch.setattr(embedder, "_is_torch_backend_disabled", lambda: None)
    assert embedder.torch_backend_issue() == ""


def test_加载模型前先做torch后端预检(monkeypatch):
    from kb import embedder
    from kb.embedder import Embedder, EmbeddingUnavailableError
    monkeypatch.setattr(
        embedder, "torch_backend_issue",
        lambda: "torch 版本不满足 transformers 要求：torch 2.5.1 + transformers 5.20.2")
    e = Embedder("fake-model")
    with pytest.raises(EmbeddingUnavailableError) as excinfo:
        e.embed_texts(["你好"])
    assert "torch 版本不满足 transformers 要求" in str(excinfo.value)
    assert "torch 版本不满足 transformers 要求" in e.status()["error"]
    assert e._model is None


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
