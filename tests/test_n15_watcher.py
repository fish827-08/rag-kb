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
