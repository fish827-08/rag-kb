"""监控 Agent（N18）：本地 qwen3 常驻汇总（TASK-0017）。

TDD 红灯基准来自 0015 设计书第 6 节；仅 mock service/llm，不依赖真实
KBService（线程启停用 FakeService 直测，不做慢速集成）。
"""
import logging

import pytest

from kb.llm import LLMError
from kb.monitor import (MonitorAgent, build_messages, build_snapshot,
                        maybe_open_dashboard)


# ---- 测试替身 ----
class FakeLLM:
    """记录 chat 调用；result 可设返回或抛错。"""

    def __init__(self, result="已完成任务：xxx"):
        self.result = result
        self.error = None
        self.calls = []

    def chat(self, messages, max_tokens=None, prefer="auto"):
        self.calls.append({"max_tokens": max_tokens, "prefer": prefer})
        if self.error is not None:
            raise self.error
        return self.result


def _rec(content, tags=(), source=None, updated_at="2026-08-25T10:00:00"):
    """构造假 Record（仅 monitor 用到 content/tags/source/updated_at）。"""

    class _R:
        pass

    r = _R()
    r.content = content
    r.tags = list(tags)
    r.source = source
    r.updated_at = updated_at
    return r


class FakeService:
    """按 tag 过滤的假 service；list_records 结果可控，add_memory 记入 added。"""

    def __init__(self, records):
        self.records = records
        self.added = []
        self.llm = FakeLLM()

    def list_records(self, tag=None, limit=1000):
        items = [r for r in self.records if tag is None or tag in r.tags]
        return items[:limit], len(items)

    def add_memory(self, content, tags=None, source=None, namespace="default"):
        self.added.append({"content": content, "tags": tags, "source": source})


# ---- TDD 红灯基准 ----
def test_快照收集纯函数():
    records = [
        _rec("TASK-0001 pending worker-1 | 标题A", ["taskboard"]),
        _rec("TASK-0002 claimed worker-2 | 标题B", ["taskboard"]),
        _rec('{"worker":"worker-1","model":"GLM-5.3","status":"idle",'
             '"last_seen":"t1"}', ["registry"]),
        _rec("comm:done | x", ["comm:done"], source="worker-1"),
    ]
    svc = FakeService(records)
    snap = build_snapshot(svc)
    assert "【任务板】" in snap and "【worker】" in snap and "【交流窗】" in snap
    assert "TASK-0001 pending worker-1 | 标题A" in snap
    assert "worker-1 GLM-5.3 idle t1" in snap
    assert "worker-1: comm:done | x" in snap


def test_快照行数受限():
    records = [_rec(f"TASK-{i:04d} pending worker-1 | 标题{i}",
                    ["taskboard"]) for i in range(10)]
    svc = FakeService(records)
    snap = build_snapshot(svc)
    # pending 上限 4 行 → 第 5 张卡（TASK-0004）不应出现，前 4 张保留
    assert "TASK-0004 pending" not in snap
    assert "TASK-0003 pending" in snap


def test_提示词模板token预算():
    big = "\n".join(f"TASK-{i:04d} pending worker-1 | 标题{i}" for i in range(8))
    snap = f"【任务板】\n{big}\n【worker】\n{big}\n【交流窗】\n{big}"
    msgs = build_messages(snap, "2026-08-25 10:00")
    # 保守估算：1 中文字 ≈1.5 token；远低于 1500 硬上限
    est = sum(len(m["content"]) for m in msgs) * 1.5
    assert est < 1500


def test_LLM调用_prefer_local_max_tokens():
    svc = FakeService([])
    agent = MonitorAgent(svc, max_tokens=300)
    agent._run_once()
    assert svc.llm.calls
    assert svc.llm.calls[0]["prefer"] == "local"
    assert svc.llm.calls[0]["max_tokens"] == 300


def test_摘要写入comm_monitor():
    svc = FakeService([])
    svc.llm.result = "当前进行中任务：TASK-0002"
    agent = MonitorAgent(svc, max_tokens=300)
    agent._run_once()
    assert svc.added, "应当写入 comm:monitor"
    assert svc.added[0]["tags"] == ["comm:monitor"]
    assert svc.added[0]["source"] == "kb-monitor"
    assert "TASK-0002" in svc.added[0]["content"]


def test_LLM不可用兜底(caplog):
    svc = FakeService([])
    svc.llm.error = LLMError("本地不可用")
    agent = MonitorAgent(svc, max_tokens=300)
    agent._run_once()  # 不崩溃
    assert svc.added == []  # 不写 comm:monitor
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_interval非法回退():
    svc = FakeService([])
    assert MonitorAgent(svc, interval_minutes=0).interval == 10
    assert MonitorAgent(svc, interval_minutes=3).interval == 3


def test_看板自启动开关():
    class S:
        dashboard_autoopen = False
        dashboard_url = "http://x"

    calls = []
    maybe_open_dashboard(S(), opener=lambda u: calls.append(u))
    assert calls == []
    S.dashboard_autoopen = True
    maybe_open_dashboard(S(), opener=lambda u: calls.append(u))
    assert calls == ["http://x"]
