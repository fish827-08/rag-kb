"""N34 批量导入验收测试（2026-11-03，长文档提速）。

覆盖：
1) 多文件一次入库：files/chunks 计数正确，检索能命中两个来源；
2) 同 source 重复导入走「先删旧再写新」，replaced > 0；
3) 不支持格式跳过并记入 failed，不影响其余文件入库。
"""
import pytest

pytestmark = pytest.mark.integration


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_批量导入多文件(env_isolated, tmp_path):
    from kb.service import KBService

    a = _write(tmp_path / "甲.txt", "甲文档：混合检索使用向量与关键词双路召回。")
    b = _write(tmp_path / "乙.txt", "乙文档：项目记忆存放在 kb_data/projects 下的子库。")

    svc = KBService()
    r = svc.add_documents_bulk([a, b], project="bulk")
    assert r["files"] == 2
    assert r["chunks"] >= 2
    assert r["failed"] == []

    hits = svc.search("向量与关键词双路召回", project="bulk")
    assert any(h["source"] == "甲.txt" for h in hits)

    hits2 = svc.search("项目子库", project="bulk")
    assert any(h["source"] == "乙.txt" for h in hits2)


def test_批量导入同源覆盖(env_isolated, tmp_path):
    from kb.service import KBService

    a = _write(tmp_path / "文档.txt", "第一版内容：alpha 关键词。")
    svc = KBService()
    assert svc.add_documents_bulk([a], project="bulk")["replaced"] == 0

    _write(a, "第二版内容：beta 关键词，长度更长一些。")
    second = svc.add_documents_bulk([a], project="bulk")
    assert second["replaced"] > 0
    hits = svc.search("beta 关键词", project="bulk")
    assert any(h["source"] == "文档.txt" for h in hits)


def test_批量导入跳过不支持格式(env_isolated, tmp_path):
    from kb.service import KBService

    ok = _write(tmp_path / "正常.txt", "正常文档内容。")
    bad = tmp_path / "坏格式.xyz"
    bad.write_text("x", encoding="utf-8")

    r = KBService().add_documents_bulk([ok, bad], project="bulk")
    assert r["files"] == 1
    assert r["chunks"] >= 1
    assert len(r["failed"]) == 1
    assert r["failed"][0]["source"] == "坏格式.xyz"
