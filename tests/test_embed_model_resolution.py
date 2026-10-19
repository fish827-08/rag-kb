"""嵌入模型解析（设计文档 §3.1，2026-10-19 轻量优先修订）。

背景：默认嵌入模型由 bge-m3（1024 维）改为 bge-small-zh（512 维）。
ChromaDB 集合维度由首次写入的向量决定，**换模型后旧数据无法检索**，
所以"库里有数据"时必须沿用建库时使用的模型，绝不能把新默认静默套到旧库上。
本文件覆盖 resolve_embed_model 的三条分支与 runtime.json 记录的读写。

English: Covers resolve_embed_model's three branches plus runtime.json bookkeeping.
The new lightweight default must never be applied silently to an existing store, because a
ChromaDB collection's dimension is fixed by the first vector written.
"""
import json

from kb.config import Settings
from kb.service import LEGACY_DEFAULT_EMBED_MODEL, resolve_embed_model

LIGHT = "BAAI/bge-small-zh-v1.5"


def _mk(tmp_path, monkeypatch, explicit_model: str | None = None) -> Settings:
    """构造隔离的 Settings；conftest 会设 KB_EMBED_MODEL，这里清掉以控制"是否显式配置"。"""
    monkeypatch.delenv("KB_EMBED_MODEL", raising=False)
    kwargs: dict = {"data_dir": tmp_path}
    if explicit_model is not None:
        kwargs["embed_model"] = explicit_model
    return Settings(_env_file=None, **kwargs)


def _recorded(tmp_path) -> dict:
    """读 runtime.json 内容。"""
    return json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))


class TestEmptyStore:
    """空库：配置说了算，并记录选择（用户随时可换，不会破坏数据）。"""

    def test_空库跟随配置并记录(self, tmp_path, monkeypatch):
        s = _mk(tmp_path, monkeypatch)
        assert resolve_embed_model(s, has_data=False) == LIGHT
        assert _recorded(tmp_path)["embed_model"] == LIGHT

    def test_空库显式配置也跟随(self, tmp_path, monkeypatch):
        s = _mk(tmp_path, monkeypatch, explicit_model=LEGACY_DEFAULT_EMBED_MODEL)
        assert resolve_embed_model(s, has_data=False) == LEGACY_DEFAULT_EMBED_MODEL
        assert _recorded(tmp_path)["embed_model"] == LEGACY_DEFAULT_EMBED_MODEL


class TestExistingStore:
    """有数据：必须沿用建库模型，避免维度不匹配。"""

    def test_历史库无记录且未显式配置_沿用历史默认(self, tmp_path, monkeypatch):
        """早于本特性的旧库：按历史唯一默认 bge-m3 沿用。"""
        s = _mk(tmp_path, monkeypatch)
        assert resolve_embed_model(s, has_data=True) == LEGACY_DEFAULT_EMBED_MODEL
        assert _recorded(tmp_path)["embed_model"] == LEGACY_DEFAULT_EMBED_MODEL

    def test_历史库无记录但显式配置_尊重用户配置(self, tmp_path, monkeypatch):
        """用户显式设了 KB_EMBED_MODEL，说明他知道自己在换什么，不强行改回。"""
        s = _mk(tmp_path, monkeypatch, explicit_model=LIGHT)
        assert resolve_embed_model(s, has_data=True) == LIGHT

    def test_库由m3建成且未显式配置_沿用m3且不刷警告(self, tmp_path, monkeypatch, caplog):
        """老用户升级：内置默认换成轻量，但库里是 m3 → 沿用 m3；差异只因默认值变化，不刷 WARNING。"""
        (tmp_path / "runtime.json").write_text(
            json.dumps({"device": "cuda", "embed_model": LEGACY_DEFAULT_EMBED_MODEL}),
            encoding="utf-8")
        s = _mk(tmp_path, monkeypatch)
        with caplog.at_level("INFO"):
            got = resolve_embed_model(s, has_data=True)
        assert got == LEGACY_DEFAULT_EMBED_MODEL
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_用户显式换模型却被沿用_给出可操作警告(self, tmp_path, monkeypatch, caplog):
        """用户显式配置轻量模型，但库是 m3 建的 → 沿用 m3，并 WARNING 告知怎么迁移。"""
        (tmp_path / "runtime.json").write_text(
            json.dumps({"embed_model": LEGACY_DEFAULT_EMBED_MODEL}), encoding="utf-8")
        s = _mk(tmp_path, monkeypatch, explicit_model=LIGHT)
        with caplog.at_level("WARNING"):
            got = resolve_embed_model(s, has_data=True)
        assert got == LEGACY_DEFAULT_EMBED_MODEL
        assert any("维度不匹配" in r.message for r in caplog.records)

    def test_已记录模型与配置一致_原样返回(self, tmp_path, monkeypatch, caplog):
        (tmp_path / "runtime.json").write_text(
            json.dumps({"embed_model": LIGHT}), encoding="utf-8")
        s = _mk(tmp_path, monkeypatch)
        with caplog.at_level("WARNING"):
            assert resolve_embed_model(s, has_data=True) == LIGHT
        assert not caplog.records  # 一致时不该有告警


class TestRuntimeFileRobustness:
    """runtime.json 的健壮性：保留其他键、损坏文件不炸启动。"""

    def test_写入模型名时保留device等其他键(self, tmp_path, monkeypatch):
        (tmp_path / "runtime.json").write_text(
            json.dumps({"device": "cuda"}), encoding="utf-8")
        s = _mk(tmp_path, monkeypatch)
        resolve_embed_model(s, has_data=False)
        data = _recorded(tmp_path)
        assert data["device"] == "cuda"          # 原有键未被覆盖
        assert data["embed_model"] == LIGHT

    def test_runtime文件损坏时按无记录处理(self, tmp_path, monkeypatch):
        (tmp_path / "runtime.json").write_text("{ 不是合法 JSON", encoding="utf-8")
        s = _mk(tmp_path, monkeypatch)
        # 损坏 → 读为"无记录" → 有数据时沿用历史默认（不抛异常）
        assert resolve_embed_model(s, has_data=True) == LEGACY_DEFAULT_EMBED_MODEL
