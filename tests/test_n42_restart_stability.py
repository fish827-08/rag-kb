"""N42 重启稳定性与 iter_all 分页契约（v2.4.3）。

本轮排查结论：680 条库的重启失败是外部清理 journal 导致的环境问题，不是产品 bug；
但仍补两件事防止未来回归：
1) `iter_all` 分页读取，避免大库单次全量 get 的峰值内存；
2) 680 条库跨 5 次进程重启的读取一致性守卫。
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kb.models import Record
from kb.storage import ITER_BATCH, ChromaStore


class _FakeCol:
    """记录每次 get 的 limit/offset，并按切片返回，用于验证分页契约。"""

    def __init__(self, total: int):
        self.total = total
        self.calls: list[tuple[int | None, int]] = []

    def get(self, include=None, limit=None, offset=None):
        start = offset or 0
        end = self.total if limit is None else min(start + limit, self.total)
        self.calls.append((limit, start))
        return {
            "ids": [f"id{i}" for i in range(start, end)],
            "documents": [f"内容 {i}" for i in range(start, end)],
            "metadatas": [{"source": "s"} for _ in range(start, end)],
        }


@pytest.mark.parametrize("total", [0, 1, 500, 501, 680])
def test_iter_all分页边界(total):
    col = _FakeCol(total)
    store = SimpleNamespace(_col=col)
    records = list(ChromaStore.iter_all(store))
    assert len(records) == total
    if total:
        assert records[0].id == "id0" and records[-1].id == f"id{total - 1}"
    # 每次 get 都必须带明确的 limit，且不超过批大小
    assert all(limit == ITER_BATCH for limit, _ in col.calls)


def test_iter_all不该无界全量读取():
    col = _FakeCol(680)
    list(ChromaStore.iter_all(SimpleNamespace(_col=col)))
    assert [limit for limit, _ in col.calls] == [ITER_BATCH, ITER_BATCH]
    assert [offset for _, offset in col.calls] == [0, ITER_BATCH]


def _run(script: str, data_dir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-c", script, str(data_dir)],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


@pytest.mark.integration
def test_680条库跨五次进程重启可读(tmp_path):
    data = tmp_path / "data"
    write = """
import sys
from pathlib import Path
from kb.models import Record
from kb.storage import ChromaStore
base = Path(sys.argv[1])
store = ChromaStore(base / "chroma")
store.add([Record(content="压测记录 %d 项目隔离 一致性" % i) for i in range(680)],
          [[float(i % 7 + 1)] * 16 for i in range(680)])
print(store.count())
"""
    out = _run(write, data)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("680")

    reopen = """
import sys
from pathlib import Path
from kb.storage import ChromaStore
store = ChromaStore(Path(sys.argv[1]) / "chroma")
print(store.count(), sum(1 for _ in store.iter_all()))
"""
    for _ in range(5):
        out = _run(reopen, data)
        assert out.returncode == 0, out.stderr
        assert out.stdout.split() == ["680", "680"]
