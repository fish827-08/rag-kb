"""监控 Agent（N18）：本地 qwen3 常驻汇总（TASK-0017）。

TDD 红灯基准来自 0015 设计书第 6 节；仅 mock service/llm，不依赖真实
KBService（线程启停用 FakeService 直测，不做慢速集成）。
"""
import logging
from datetime import datetime, timedelta

import pytest

from kb.llm import LLMError
from kb.monitor import (MonitorAgent, _detect_anomalies, build_messages,
                        build_snapshot, maybe_open_dashboard)


# ---- 测试替身 ----
class FakeLLM:
    """记录 chat 调用；result 可设返回或抛错；status 模拟 LLMClient（stats() 读取 .value）。"""

    class _Status:
        value = "local"

    def __init__(self, result="已完成任务：xxx"):
        self.result = result
        self.error = None
        self.calls = []
        self.status = self._Status()

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


# ---- TASK-0021：去常驻改按需——端点测试（mock llm，不依赖真实 LLM/Ollama）----
def _make_client(env_isolated, monkeypatch,
                 llm_result="当前协作正常：TASK-0016 完成，TASK-0021 进行中。",
                 llm_error=None):
    """装配 app（monitor_enabled 默认 False 不启线程）并注入 FakeLLM 为 KBService.llm。

    create_app() 末尾被 charset/请求日志中间件包装为 ASGI 函数（无 .state），
    故不直接改 app.state；改经 KBService.__init__ 的 llm 注入点
    （monkeypatch kb.service.LLMClient 为返回 FakeLLM 的工厂）。
    """
    from kb import config
    config.get_settings.cache_clear()
    fllm = FakeLLM(result=llm_result)
    if llm_error is not None:
        fllm.error = llm_error
    monkeypatch.setattr("kb.service.LLMClient", lambda settings: fllm)
    from kb.api import create_app
    from fastapi.testclient import TestClient
    return TestClient(create_app()), fllm


def test_按需端点返回摘要与id(env_isolated, monkeypatch):
    """POST /api/v1/monitor/summary：单轮跑通，返回 {summary, id}，且写 comm:monitor。"""
    c, fllm = _make_client(env_isolated, monkeypatch)
    with c:
        r = c.post("/api/v1/monitor/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["summary"]
        assert data["id"]
        assert fllm.calls[0]["prefer"] == "local"
        # 摘要确实写入 comm:monitor（经 REST 校验，避免依赖 app.state）
        lst = c.get("/api/v1/memories?tag=comm:monitor&limit=10")
        assert lst.status_code == 200
        items = lst.json()["items"]
        assert any(it["id"] == data["id"] for it in items)


def test_按需端点LLM不可用返回502(env_isolated, monkeypatch):
    """LLM 不可用时端点 502 MONITOR_UNAVAILABLE，不写 comm:monitor。"""
    from kb.llm import LLMError
    c, _ = _make_client(env_isolated, monkeypatch, llm_error=LLMError("本地不可用"))
    with c:
        r = c.post("/api/v1/monitor/summary")
    assert r.status_code == 502
    assert r.json()["error"] == "MONITOR_UNAVAILABLE"


def test_配置去常驻默认(env_isolated, monkeypatch):
    """去常驻：monitor_enabled 默认 False（不启线程）；KB_MONITOR_AUTOTIMER 默认 0。

    GET /api/v1/config 暴露 monitor_autotimer 供看板前端读取（>0 时前端定时调）。
    """
    from kb.config import Settings
    s = Settings()
    assert s.monitor_enabled is False
    assert s.monitor_autotimer == 0
    c, _ = _make_client(env_isolated, monkeypatch)
    with c:
        r = c.get("/api/v1/config")
    assert r.status_code == 200
    assert r.json() == {"monitor_autotimer": 0}


# ---- TASK-0048：_detect_anomalies 异常检测纯函数 ----
_NOW = datetime(2026, 8, 26, 12, 0, 0)


def _task(task_id, status, minutes_ago=0):
    """构造 snapshot tasks 条目（updated_at = NOW - minutes_ago）。"""
    return {"task_id": task_id, "status": status,
            "updated_at": (_NOW - timedelta(minutes=minutes_ago)).isoformat()}


def _fbk(fbk_id, status, minutes_ago=0):
    """构造 snapshot feedbacks 条目。"""
    return {"fbk_id": fbk_id, "status": status,
            "updated_at": (_NOW - timedelta(minutes=minutes_ago)).isoformat()}


class TestDetectAnomalies:
    """_detect_anomalies 四条规则：触发/不触发/边界（TASK-0048）。"""

    # ① pending 数 < 2
    def test_pending_low_触发_pending为1(self):
        snap = {"tasks": [_task("TASK-0001", "pending")], "feedbacks": []}
        anomalies = _detect_anomalies(snap, now=_NOW)
        types = [a["type"] for a in anomalies]
        assert "pending_low" in types
        a = [a for a in anomalies if a["type"] == "pending_low"][0]
        assert a["task_id"] is None
        assert "pending 数=1" in a["detail"]

    def test_pending_low_不触发_pending为2(self):
        snap = {"tasks": [_task("TASK-0001", "pending"), _task("TASK-0002", "pending")],
                "feedbacks": []}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "pending_low" for a in anomalies)

    # ② claimed 超 30 分钟
    def test_claimed_stale_触发_31分钟(self):
        snap = {"tasks": [_task("TASK-0001", "claimed", minutes_ago=31)], "feedbacks": []}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert any(a["type"] == "claimed_stale" and a["task_id"] == "TASK-0001"
                   for a in anomalies)

    def test_claimed_stale_不触发_29分钟(self):
        snap = {"tasks": [_task("TASK-0001", "claimed", minutes_ago=29)], "feedbacks": []}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "claimed_stale" for a in anomalies)

    def test_claimed_stale_边界恰好30分钟不触发(self):
        snap = {"tasks": [_task("TASK-0001", "claimed", minutes_ago=30)], "feedbacks": []}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "claimed_stale" for a in anomalies)

    # ③ done 超 5 分钟未核验
    def test_done_unverified_触发_6分钟(self):
        snap = {"tasks": [_task("TASK-0001", "done", minutes_ago=6)], "feedbacks": []}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert any(a["type"] == "done_unverified" and a["task_id"] == "TASK-0001"
                   for a in anomalies)

    def test_done_unverified_不触发_4分钟(self):
        snap = {"tasks": [_task("TASK-0001", "done", minutes_ago=4)], "feedbacks": []}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "done_unverified" for a in anomalies)

    def test_done_unverified_边界恰好5分钟不触发(self):
        snap = {"tasks": [_task("TASK-0001", "done", minutes_ago=5)], "feedbacks": []}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "done_unverified" for a in anomalies)

    # ④ open FBK 超 10 分钟
    def test_fbk_open_stale_触发_11分钟(self):
        snap = {"tasks": [], "feedbacks": [_fbk("FBK-0001", "open", minutes_ago=11)]}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert any(a["type"] == "fbk_open_stale" and a["task_id"] == "FBK-0001"
                   for a in anomalies)

    def test_fbk_open_stale_不触发_9分钟(self):
        snap = {"tasks": [], "feedbacks": [_fbk("FBK-0001", "open", minutes_ago=9)]}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "fbk_open_stale" for a in anomalies)

    def test_fbk_open_stale_边界恰好10分钟不触发(self):
        snap = {"tasks": [], "feedbacks": [_fbk("FBK-0001", "open", minutes_ago=10)]}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "fbk_open_stale" for a in anomalies)

    # 非 open 状态 FBK 不检测（accepted/rejected 不触发）
    def test_fbk_non_open不检测(self):
        snap = {"tasks": [], "feedbacks": [
            _fbk("FBK-0001", "accepted", minutes_ago=60),
            _fbk("FBK-0002", "rejected", minutes_ago=60),
        ]}
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "fbk_open_stale" for a in anomalies)

    # 时间解析失败跳过不报错
    def test_时间解析失败跳过不报错(self):
        snap = {"tasks": [{"task_id": "TASK-0001", "status": "claimed",
                            "updated_at": "非法时间"}], "feedbacks": []}
        # 不抛异常，且该卡不被计为 stale（时间解析失败跳过）
        anomalies = _detect_anomalies(snap, now=_NOW)
        assert all(a["type"] != "claimed_stale" for a in anomalies)

    # 空快照：仅 pending_low（pending=0<2），无其他异常
    def test_空快照仅pending_low(self):
        anomalies = _detect_anomalies({}, now=_NOW)
        assert len(anomalies) == 1
        assert anomalies[0]["type"] == "pending_low"

    # 综合：四条规则同时触发
    def test_综合四条规则同时触发(self):
        snap = {
            "tasks": [
                _task("TASK-0001", "pending"),  # pending=1 < 2
                _task("TASK-0002", "claimed", minutes_ago=45),  # >30
                _task("TASK-0003", "done", minutes_ago=10),  # >5
            ],
            "feedbacks": [_fbk("FBK-0001", "open", minutes_ago=20)],  # >10
        }
        anomalies = _detect_anomalies(snap, now=_NOW)
        types = sorted(a["type"] for a in anomalies)
        assert types == sorted(["pending_low", "claimed_stale",
                                "done_unverified", "fbk_open_stale"])
