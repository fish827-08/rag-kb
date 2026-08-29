"""N28 CLI 增强测试：kb stats / kb ask（A4 spec §2.1/§2.2）。

覆盖：
- compute_type_distribution：类型分布条数与占比 / 空库
- compute_hot_records：access_count 降序 top N / 零计数排除
- stats 命令：类型分布 / 空库不报错 / 陈旧计数（CliRunner + service 替身）
- ask 命令：答案与来源输出（mock LLM）/ LLM 不可用仍输出检索结果（退出码 1）
stats 纯读测试用替身 store；ask 走 service 替身（对齐 test_n10 模式），不加载真实模型。
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner


def _mock_record(rid, rtype="memory", access_count=0, content="内容",
                 last_accessed="", created_at=None):
    r = MagicMock()
    r.id = rid
    r.type = MagicMock()
    r.type.value = rtype
    r.access_count = access_count
    r.content = content
    r.last_accessed = last_accessed
    r.created_at = created_at or datetime.now().isoformat()
    return r


# ---------- 纯函数 ----------

class TestTypeDistribution:
    """compute_type_distribution：类型条数与占比。"""

    def test_三类分布与占比(self):
        from kb.cli import compute_type_distribution
        records = ([_mock_record(f"m{i}", "memory") for i in range(3)]
                   + [_mock_record("d1", "doc_chunk")]
                   + [_mock_record("w1", "web_chunk"), _mock_record("w2", "web_chunk")])
        dist = compute_type_distribution(records)
        assert dist["memory"]["count"] == 3
        assert dist["memory"]["pct"] == 50.0
        assert dist["doc_chunk"]["count"] == 1
        assert dist["doc_chunk"]["pct"] == 16.7  # 1/6
        assert dist["web_chunk"]["count"] == 2
        assert dist["web_chunk"]["pct"] == 33.3  # 2/6

    def test_空库返回空(self):
        from kb.cli import compute_type_distribution
        assert compute_type_distribution([]) == {}


class TestHotRecords:
    """compute_hot_records：访问热度 top N。"""

    def test_按访问次数降序取top(self):
        from kb.cli import compute_hot_records
        records = [_mock_record("r1", access_count=3),
                   _mock_record("r2", access_count=10),
                   _mock_record("r3", access_count=7)]
        hot = compute_hot_records(records, top=2)
        assert [r.id for r, _ in hot] == ["r2", "r3"]

    def test_零计数排除(self):
        from kb.cli import compute_hot_records
        records = [_mock_record("r1", access_count=0),
                   _mock_record("r2", access_count=1)]
        hot = compute_hot_records(records, top=5)
        assert [r.id for r, _ in hot] == ["r2"]

    def test_全零返回空(self):
        from kb.cli import compute_hot_records
        assert compute_hot_records([_mock_record("r1")], top=5) == []


# ---------- stats 命令（CliRunner + 替身） ----------

class _FakeStatsService:
    """stats 命令替身：stats() 固定返回，store.iter_all() 返回 mock 记录。"""

    def __init__(self, records):
        self._records = records

    def stats(self):
        return {"records": len(self._records), "device": "cpu",
                "llm": "local"}

    class _Store:
        def __init__(self, records):
            self._records = records

        def iter_all(self):
            return iter(self._records)

    @property
    def store(self):
        return _FakeStatsService._Store(self._records)


class TestStatsCommand:
    """kb stats：概览 / 类型分布 / 热度 / 陈旧（rich 输出断言关键词）。"""

    def _invoke(self, monkeypatch, records, args=None):
        from kb.cli import app
        runner = CliRunner()
        monkeypatch.setattr("kb.cli._service",
                            lambda: _FakeStatsService(records))
        return runner.invoke(app, ["stats"] + (args or []))

    def test_类型分布与概览(self, monkeypatch):
        now = datetime.now()
        records = [
            _mock_record("m1", "memory", access_count=5,
                         created_at=now.isoformat()),
            _mock_record("d1", "doc_chunk", access_count=2,
                         created_at=now.isoformat()),
        ]
        result = self._invoke(monkeypatch, records)
        assert result.exit_code == 0
        assert "memory" in result.output
        assert "doc_chunk" in result.output
        assert "cpu" in result.output  # 概览 device

    def test_空库不报错(self, monkeypatch):
        result = self._invoke(monkeypatch, [])
        assert result.exit_code == 0
        assert "记忆库统计" in result.output

    def test_陈旧计数(self, monkeypatch):
        now = datetime.now()
        old = _mock_record(
            "old", last_accessed=(now - timedelta(days=120)).isoformat())
        fresh = _mock_record(
            "fresh", last_accessed=(now - timedelta(days=10)).isoformat())
        result = self._invoke(monkeypatch, [old, fresh],
                              ["--stale-days", "90"])
        assert result.exit_code == 0
        assert "1" in result.output  # 陈旧条数 1

    def test_自定义天数(self, monkeypatch):
        now = datetime.now()
        mid = _mock_record(
            "mid", last_accessed=(now - timedelta(days=30)).isoformat())
        result = self._invoke(monkeypatch, [mid], ["--stale-days", "20"])
        assert result.exit_code == 0


# ---------- ask 命令（service 替身） ----------

class _FakeAskService:
    """ask 命令替身：ask() 按 fixture 行为；search() 返回固定命中。"""

    def __init__(self, ask_result=None, ask_error=None):
        self.ask_result = ask_result
        self.ask_error = ask_error

    def ask(self, question, agent_id="default", client="CLI", project=None):
        if self.ask_error is not None:
            raise self.ask_error
        return self.ask_result

    def search(self, question, top_k=5, mode="hybrid", agent_id="default",
               client="CLI", project=None):
        return [{"id": "r1", "content": "检索命中内容", "score": 0.9,
                 "type": "memory", "source": None, "tags": [],
                 "created_at": ""}]


class TestAskCommand:
    """kb ask：答案与来源输出 / LLM 不可用降级。"""

    def _invoke(self, monkeypatch, svc, args=None):
        from kb.cli import app
        runner = CliRunner()
        monkeypatch.setattr("kb.cli._service", lambda: svc)
        return runner.invoke(app, ["ask", "测试问题"] + (args or []))

    def test_输出答案与来源(self, monkeypatch):
        from kb.service import LLMDisabledError  # noqa: F401 确认可导入
        svc = _FakeAskService(ask_result={
            "answer": "这是答案",
            "sources": [{"id": "r1", "content": "来源内容", "score": 0.9,
                         "source": None}],
            "llm": "local"})
        result = self._invoke(monkeypatch, svc)
        assert result.exit_code == 0
        assert "这是答案" in result.output
        assert "r1" in result.output       # 来源表
        assert "来源内容" in result.output

    def test_llm不可用仍输出检索结果退出码1(self, monkeypatch):
        from kb.service import LLMDisabledError
        svc = _FakeAskService(ask_error=LLMDisabledError("LLM 不可用"))
        result = self._invoke(monkeypatch, svc)
        assert result.exit_code == 1
        # 检索结果仍输出（无 LLM 也有价值）
        assert "检索命中内容" in result.output
        assert "仅检索" in result.output    # 标注"仅检索未生成"
        # 友好提示含配置指引关键词
        assert "Ollama" in result.output or "DeepSeek" in result.output
