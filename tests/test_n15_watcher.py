import time
import pytest

pytestmark = pytest.mark.integration


def test_新增文件自动入库(env_isolated, monkeypatch):
    from kb.service import KBService
    from kb.watcher import KBWatcher
    watch = env_isolated / "watch"; watch.mkdir()
    s = KBService()
    w = KBWatcher(s, watch, debounce_seconds=0.2); w.start()
    try:
        (watch / "auto.txt").write_text("监听目录自动入库的内容", encoding="utf-8")
        deadline = time.time() + 10
        while time.time() < deadline:
            if any("自动入库" in h["content"] for h in s.search("自动入库", mode="keyword")):
                break
            time.sleep(0.2)
        hits = s.search("自动入库", mode="keyword")
        assert hits and hits[0]["source"] == "auto.txt"
    finally:
        w.stop()


def test_文件删除级联清理(env_isolated):
    from kb.service import KBService
    from kb.watcher import KBWatcher
    watch = env_isolated / "watch"; watch.mkdir()
    s = KBService()
    (watch / "gone.txt").write_text("将被删除的文档内容", encoding="utf-8")
    s.add_document(watch / "gone.txt")
    w = KBWatcher(s, watch, debounce_seconds=0.2); w.start()
    try:
        (watch / "gone.txt").unlink()
        deadline = time.time() + 10
        while time.time() < deadline and s.list_records(q="将被删除")[1] > 0:
            time.sleep(0.2)
        assert s.list_records(q="将被删除")[1] == 0
    finally:
        w.stop()


def test_监听目录与数据目录相同时跳过(tmp_path, caplog):
    """watch_dir == data_dir 时 listener 会把自己的 bm25 语料反复入库，必须跳过。"""
    from kb.watcher import KBWatcher

    class FakeService:
        def add_document(self, path):  # pragma: no cover - 不应被调用
            raise AssertionError("不应入库")

    data = tmp_path / "data"
    data.mkdir()
    w = KBWatcher(FakeService(), data, ignore_dir=data)
    with caplog.at_level("WARNING", logger="kb.watcher"):
        w.start()
    try:
        assert w._observer is None
        assert w._worker is None
        assert "自我入库" in caplog.text
    finally:
        w.stop()


def test_监听目录与数据目录不同仍正常监听(tmp_path):
    """ignore_dir 只拦截与监听目录重合的数据目录，不影响正常监听。"""
    from kb.watcher import KBWatcher

    watch = tmp_path / "watch"; watch.mkdir()
    data = tmp_path / "data"; data.mkdir()
    w = KBWatcher(object(), watch, ignore_dir=data)
    w.start()
    try:
        assert w._observer is not None and w._worker is not None
    finally:
        w.stop()
