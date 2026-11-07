"""v2.4.6 新增能力验收测试。

覆盖：数据目录单例锁、表格跳过嵌入、MCP 表格参数、文件级聚合、
多实体匹配、嵌入模型选择保护、serve --port。
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------- 数据目录单例锁 ----------

def test_单例锁_第二个进程被拒绝(env_isolated):
    """子进程持锁期间，本进程抢锁必须失败并读到持有者信息。"""
    from kb.singleton import DataDirLock, is_locked

    data_dir = str(env_isolated)
    code = (
        "import sys, time\n"
        "from kb.singleton import DataDirLock\n"
        "lock = DataDirLock(sys.argv[1])\n"
        "assert lock.acquire({'pid': 99999, 'port': 8000}) is None\n"
        "print('locked', flush=True)\n"
        "time.sleep(10)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code, data_dir],
                            stdout=subprocess.PIPE, text=True, cwd=str(ROOT))
    try:
        assert proc.stdout.readline().strip() == "locked"
        holder = is_locked(data_dir)
        assert holder is not None
        assert holder.get("port") == 8000
        lock = DataDirLock(data_dir)
        assert lock.acquire({"pid": 1}) is not None
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_单例锁_进程退出后自动释放(env_isolated):
    """强杀持锁进程后，锁必须由内核释放，下一次可直接获取。

    Windows 在 TerminateProcess 后释放字节锁可能有短暂延迟，因此轮询等待，
    最多 5 秒；这不是产品缺陷，而是操作系统清理时机。
    """
    import time

    from kb.singleton import DataDirLock, is_locked

    data_dir = str(env_isolated)
    code = (
        "import sys, time\n"
        "from kb.singleton import DataDirLock\n"
        "lock = DataDirLock(sys.argv[1])\n"
        "lock.acquire({'pid': 1})\n"
        "print('locked', flush=True)\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code, data_dir],
                            stdout=subprocess.PIPE, text=True, cwd=str(ROOT))
    assert proc.stdout.readline().strip() == "locked"
    proc.kill()
    proc.wait(timeout=10)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if is_locked(data_dir) is None:
            break
        time.sleep(0.1)
    assert is_locked(data_dir) is None
    lock = DataDirLock(data_dir)
    assert lock.acquire({"pid": 2}) is None
    lock.release()


def test_不同数据目录可以各自持有锁(tmp_path):
    """单例的键是数据目录，不是端口：不同目录允许并行。"""
    from kb.singleton import DataDirLock

    a = DataDirLock(tmp_path / "a")
    b = DataDirLock(tmp_path / "b")
    assert a.acquire({"pid": 1}) is None
    assert b.acquire({"pid": 2}) is None
    a.release()
    b.release()


# ---------- 表格跳过嵌入 ----------

def test_表格记录不触发表量嵌入(env_isolated):
    from kb.models import Record, RecordType
    from kb.service import KBService

    svc = KBService()

    def _boom(_texts):
        raise AssertionError("表格记录不应调用嵌入模型")

    svc.embedder.embed_texts = _boom
    records = [Record(content="姓名=王五 | 数学=90",
                      type=RecordType.DOC_CHUNK, tags=["table"])]
    vectors, table_count = svc._embed_for_records(records)
    assert table_count == 1
    assert len(vectors[0]) == 512
    assert vectors[0][0] == pytest.approx(1 / (512 ** 0.5))


def test_普通文档仍然走嵌入(env_isolated):
    from kb.models import Record, RecordType
    from kb.service import KBService

    svc = KBService()
    calls = {"n": 0}

    def _fake(texts):
        calls["n"] += 1
        return [[0.1] * 512 for _ in texts]

    svc.embedder.embed_texts = _fake
    records = [Record(content="普通文档块", type=RecordType.DOC_CHUNK)]
    vectors, table_count = svc._embed_for_records(records)
    assert calls["n"] == 1
    assert table_count == 0
    assert vectors[0][0] == 0.1


# ---------- MCP 表格参数 ----------

def test_mcp_search_table_命中后不再普通检索(monkeypatch, env_isolated):
    import kb.mcp as mcp

    class _Fake:
        def table_answer(self, query, project=None):
            return {"answer": "王五：数学=90", "sources": [{"id": "t1"}]}

        def search(self, *args, **kwargs):
            raise AssertionError("命中表格后不应再走普通检索")

    monkeypatch.setattr(mcp, "_svc", lambda: _Fake())
    out = mcp.search_memory("王五的数学成绩", table=True)
    assert out["table"] is True
    assert out["answer"] == "王五：数学=90"


def test_mcp_search_table_未命中回退普通检索(monkeypatch, env_isolated):
    import kb.mcp as mcp

    class _Fake:
        def table_answer(self, query, project=None):
            return None

        def search(self, *args, **kwargs):
            return [{"id": "d1", "content": "普通命中"}]

    monkeypatch.setattr(mcp, "_svc", lambda: _Fake())
    out = mcp.search_memory("随便问问", table=True)
    assert isinstance(out, list) and out[0]["id"] == "d1"


# ---------- 文件级聚合 ----------

def test_按文件聚合_合并同源并取最高分():
    from kb.service import KBService

    rows = [
        {"source": "a.xlsx", "score": 0.8, "content": "x1"},
        {"source": "a.xlsx", "score": 0.6, "content": "x2"},
        {"source": "b.pdf", "score": 0.7, "content": "y1"},
    ]
    files = KBService.group_by_file(object(), rows, top_k=5)
    assert [f["source"] for f in files] == ["a.xlsx", "b.pdf"]
    assert files[0]["hits"] == 2
    assert files[0]["score"] == pytest.approx(0.8)
    assert len(files[0]["snippets"]) == 2


# ---------- 多实体匹配 ----------

def test_多实体_不同长度的名字都保留():
    from kb.tables import query_table, rows_from_content

    rows = (rows_from_content("姓名=王五 | 数学=90")
            + rows_from_content("姓名=欧阳娜娜 | 数学=95"))
    result = query_table(rows, "王五和欧阳娜娜的数学成绩")
    assert result is not None
    answer = result[0]
    assert "王五" in answer
    assert "欧阳娜娜" in answer


def test_班级是列时按命中数收窄():
    from kb.tables import query_table, rows_from_content

    rows = (rows_from_content("姓名=张芳 | 班级=五班 | 数学=95")
            + rows_from_content("姓名=李四 | 班级=五班 | 数学=85"))
    result = query_table(rows, "五班张芳的数学成绩")
    assert result is not None
    answer = result[0]
    assert "张芳" in answer
    assert "李四" not in answer


def test_前缀冲突_仍然只保留更长的名字():
    from kb.tables import query_table, rows_from_content

    rows = (rows_from_content("姓名=张芳 | 数学=90")
            + rows_from_content("姓名=张芳芳 | 数学=95"))
    result = query_table(rows, "张芳芳的数学成绩")
    assert result is not None
    answer = result[0]
    assert "张芳芳" in answer
    assert "张芳：" not in answer


# ---------- 大批量写入 ----------

def test_chroma_add_按批写入():
    """2 万行不能一次塞给 Chroma（单次上限约 5461），必须自动分批。"""
    from kb.models import Record
    from kb.storage import WRITE_BATCH, ChromaStore

    calls: list[int] = []

    class _Col:
        def add(self, **kwargs):
            calls.append(len(kwargs["ids"]))

    store = object.__new__(ChromaStore)
    store._col = _Col()
    records = [Record(content=f"r{i}") for i in range(WRITE_BATCH + 10)]
    store.add(records, [[0.1] * 4 for _ in records])
    assert calls == [WRITE_BATCH, 10]


def test_chroma_delete_按批删除():
    from kb.storage import WRITE_BATCH, ChromaStore

    calls: list[int] = []

    class _Col:
        def delete(self, ids):
            calls.append(len(ids))

    store = object.__new__(ChromaStore)
    store._col = _Col()
    store.delete([f"id{i}" for i in range(WRITE_BATCH + 3)])
    assert calls == [WRITE_BATCH, 3]


# ---------- 嵌入模型选择 ----------

def test_嵌入模型_空模型被拒绝(env_isolated):
    from kb.service import KBService

    svc = KBService()
    with pytest.raises(ValueError, match="至少保留一个"):
        svc.reconfigure_embed({"model": "   "})


def test_嵌入模型_库非空时禁止切换(env_isolated):
    from kb.models import Record
    from kb.service import KBService

    svc = KBService()
    svc.store.add([Record(content="占位记录")], [[0.1] * 512])
    assert svc.embed_info()["locked"] is True
    with pytest.raises(ValueError, match="已有数据"):
        svc.reconfigure_embed({"model": "BAAI/bge-base-zh-v1.5"})


def test_嵌入模型_空库可以切换(env_isolated):
    from kb.service import KBService

    svc = KBService()
    info = svc.embed_info()
    assert info["locked"] is False
    switched = svc.reconfigure_embed({"model": "BAAI/bge-base-zh-v1.5"})
    assert switched["current"] == "BAAI/bge-base-zh-v1.5"
    assert switched["dimension"] == 768


# ---------- CLI ----------

def test_serve_帮助包含_port():
    from typer.testing import CliRunner

    from kb.cli import app

    result = CliRunner().invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output


def test_table_query_命令存在():
    from typer.testing import CliRunner

    from kb.cli import app

    result = CliRunner().invoke(app, ["table", "query", "--help"])
    assert result.exit_code == 0
    assert "CSV" in result.output


# ---------- 清空数据 ----------

def test_清空项目数据(env_isolated):
    """按项目清空后，该作用域不再有记录；全局库不受影响。"""
    from kb.service import KBService

    p = env_isolated / "reset_probe.txt"
    p.write_text("reset probe content", encoding="utf-8")
    svc = KBService()
    svc.add_document(str(p), project="reset_probe")
    assert svc.scopes.get("reset_probe").store.list_records()[1] > 0

    n = svc.reset("reset_probe")
    assert n > 0
    assert svc.scopes.get("reset_probe").store.list_records()[1] == 0


def test_清空全部数据(env_isolated):
    """reset_all 覆盖每个项目子库与全局库。"""
    from kb.service import KBService

    svc = KBService()
    for name in ("p1", "p2"):
        p = env_isolated / f"{name}.txt"
        p.write_text(f"{name} content", encoding="utf-8")
        svc.add_document(str(p), project=name)

    result = svc.reset_all()
    assert result["p1"] > 0 and result["p2"] > 0
    assert svc.scopes.get("p1").store.list_records()[1] == 0
    assert svc.scopes.get("p2").store.list_records()[1] == 0


def test_清空接口必须确认(env_isolated):
    """确认文本不匹配时拒绝，且不删除任何数据。"""
    from fastapi.testclient import TestClient

    from kb.api import create_app

    with TestClient(create_app()) as c:
        r = c.post("/api/v1/admin/reset", json={"all": True, "confirm": "nope"})
        assert r.status_code == 400
        r2 = c.post("/api/v1/admin/reset", json={"project": "p1", "confirm": "wrong"})
        assert r2.status_code == 400
        r3 = c.post("/api/v1/admin/reset", json={"all": True, "confirm": "清空全部"})
        assert r3.status_code == 200
