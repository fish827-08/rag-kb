"""改进项 2 验证：并发写入加锁（线程安全冒烟测试）。

多线程同时写记忆/文档，验证：无异常、记录数正确、主键不重复、
写入后全部可检索。锁串行化写路径后结果应确定。
English: Verification for improvement #2 (concurrency write lock): concurrent writes
of memories/documents must complete without exceptions, with correct counts, unique
primary keys, and full retrievability afterwards.
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(env_isolated):
    from kb.config import get_settings
    from kb.service import KBService
    get_settings.cache_clear()
    return KBService()


def test_并发写入记忆_无丢失无异常(svc):
    from concurrent.futures import ThreadPoolExecutor

    def write(i: int) -> str:
        r = svc.add_memory(f"并发记忆 {i}：线程写的内容编号 {i}",
                           client="pytest", project="conc")
        return r.id

    with ThreadPoolExecutor(max_workers=8) as ex:
        ids = list(ex.map(write, range(40)))
    assert len(ids) == 40
    assert len(set(ids)) == 40  # 主键服务端生成且不重复
    _, total = svc.list_records(type="memory")
    assert total == 40
    # 全部可检索（隔离键匹配）
    hits = svc.search("并发记忆", client="pytest", project="conc", top_k=40)
    assert len(hits) == 40


def test_并发文档入库_无异常且块数一致(svc, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    f = tmp_path / "并发.txt"
    f.write_text("并发文档内容 甲乙丙丁戊己庚辛壬癸" * 30, encoding="utf-8")

    def ingest(i: int) -> dict:
        return svc.add_document(f, source=f"doc-{i}",
                                client="pytest", project="conc")

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(ingest, range(8)))
    assert all(r["chunks"] > 0 for r in results)
    _, total = svc.list_records(type="doc_chunk")
    assert total == sum(r["chunks"] for r in results)
