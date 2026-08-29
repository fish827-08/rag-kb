"""B3 成本管控纯函数测试（TASK-0053）：配额表 / rounds 解析递增 / summary 校验。

依据：docs/superpowers/specs/2026-08-26-b3-cost-control-design.md
  §1.1 ROUNDS 格式、§1.2 SUMMARY 保留标签、§3 配额表。
纯函数单测，不调 LLM 不发 HTTP。
"""
import pytest

import b3


# ---- §3 配额表 ----
class TestQuota:
    def test_simple配额(self):
        q = b3.get_quota("simple")
        assert q == {"precheck": 1, "milestone": 1, "total": 3}

    def test_medium配额(self):
        q = b3.get_quota("medium")
        assert q == {"precheck": 2, "milestone": 2, "total": 5}

    def test_complex配额(self):
        q = b3.get_quota("complex")
        assert q == {"precheck": 2, "milestone": 3, "total": 8}

    def test_未注明默认medium(self):
        assert b3.get_quota("") == b3.get_quota("medium")
        assert b3.get_quota() == {"precheck": 2, "milestone": 2, "total": 5}

    def test_非法复杂度回退medium(self):
        assert b3.get_quota("unknown") == b3.get_quota("medium")

    def test_大小写不敏感(self):
        assert b3.get_quota("SIMPLE") == b3.get_quota("simple")
        assert b3.get_quota("Complex") == b3.get_quota("complex")

    def test_返回副本不影响内部表(self):
        q = b3.get_quota("simple")
        q["precheck"] = 999
        assert b3.get_quota("simple")["precheck"] == 1


# ---- §1.1 ROUNDS 解析与递增 ----
class TestRounds:
    def test_parse_rounds_标准格式(self):
        content = ("ROUNDS TASK-0046 | precheck=1 milestone=0 review=0 total=1\n"
                   "复杂度：simple\n"
                   "配额：precheck=1 milestone=1 total=3")
        r = b3.parse_rounds(content)
        assert r["task_id"] == "TASK-0046"
        assert r["precheck"] == 1
        assert r["milestone"] == 0
        assert r["review"] == 0
        assert r["total"] == 1
        assert r["complexity"] == "simple"
        assert r["quota"] == {"precheck": 1, "milestone": 1, "total": 3}

    def test_parse_rounds_默认复杂度medium(self):
        content = "ROUNDS TASK-0001 | precheck=0 milestone=0 review=0 total=0"
        r = b3.parse_rounds(content)
        assert r["complexity"] == "medium"
        assert r["quota"] == {"precheck": 2, "milestone": 2, "total": 5}

    def test_parse_rounds_首行错误抛ValueError(self):
        with pytest.raises(ValueError, match="ROUNDS"):
            b3.parse_rounds("不是 ROUNDS 记录")

    def test_increment_rounds_precheck(self):
        content = ("ROUNDS TASK-0046 | precheck=1 milestone=0 review=0 total=1\n"
                   "复杂度：medium\n"
                   "配额：precheck=2 milestone=2 total=5")
        new = b3.increment_rounds(content, "precheck")
        r = b3.parse_rounds(new)
        assert r["precheck"] == 2
        assert r["total"] == 2
        assert r["milestone"] == 0
        assert r["complexity"] == "medium"

    def test_increment_rounds_milestone(self):
        content = ("ROUNDS TASK-0046 | precheck=1 milestone=0 review=0 total=1\n"
                   "复杂度：complex\n"
                   "配额：precheck=2 milestone=3 total=8")
        new = b3.increment_rounds(content, "milestone")
        r = b3.parse_rounds(new)
        assert r["milestone"] == 1
        assert r["total"] == 2

    def test_increment_rounds_review不计配额但递增(self):
        content = ("ROUNDS TASK-0046 | precheck=1 milestone=1 review=0 total=2\n"
                   "复杂度：simple\n"
                   "配额：precheck=1 milestone=1 total=3")
        new = b3.increment_rounds(content, "review")
        r = b3.parse_rounds(new)
        assert r["review"] == 1
        assert r["total"] == 3
        # 配额不变（review 不计配额）
        assert r["quota"] == {"precheck": 1, "milestone": 1, "total": 3}

    def test_increment_rounds_非法节点抛ValueError(self):
        content = "ROUNDS TASK-0001 | precheck=0 milestone=0 review=0 total=0\n复杂度：medium"
        with pytest.raises(ValueError, match="节点"):
            b3.increment_rounds(content, "unknown")

    def test_render_rounds_全零(self):
        content = b3.render_rounds("TASK-0099", "complex")
        r = b3.parse_rounds(content)
        assert r["task_id"] == "TASK-0099"
        assert r["precheck"] == 0
        assert r["milestone"] == 0
        assert r["review"] == 0
        assert r["total"] == 0
        assert r["complexity"] == "complex"
        assert r["quota"] == {"precheck": 2, "milestone": 3, "total": 8}

    def test_render_rounds_默认medium(self):
        content = b3.render_rounds("TASK-0100")
        r = b3.parse_rounds(content)
        assert r["complexity"] == "medium"

    def test_increment_rounds_往返一致(self):
        """递增后再解析，字段完整往返。"""
        orig = b3.render_rounds("TASK-0050", "simple")
        cur = orig
        for node in ("precheck", "milestone", "review", "precheck"):
            cur = b3.increment_rounds(cur, node)
        r = b3.parse_rounds(cur)
        assert r["precheck"] == 2
        assert r["milestone"] == 1
        assert r["review"] == 1
        assert r["total"] == 4


# ---- §1.2 SUMMARY 保留标签校验 ----
class TestSummaryCheck:
    def test_四类标签齐全返回空列表(self):
        content = ("SUMMARY TASK-0046 round-2 | 关键结论摘要\n"
                   "保留标签：决策|参数|验收标准|阻塞点\n"
                   "决策：采用方案A\n参数：阈值=5\n验收标准：全绿\n阻塞点：无")
        assert b3.check_summary_tags(content) == []

    def test_缺失决策检出(self):
        content = "SUMMARY TASK-0046 round-1 | 摘要\n参数：x=1\n验收标准：全绿\n阻塞点：无"
        missing = b3.check_summary_tags(content)
        assert "决策" in missing
        assert "参数" not in missing

    def test_缺失多个检出(self):
        content = "SUMMARY TASK-0046 round-1 | 只有摘要正文，没有任何保留标签"
        missing = b3.check_summary_tags(content)
        assert set(missing) == {"决策", "参数", "验收标准", "阻塞点"}

    def test_标签在正文也算齐全(self):
        """标签不一定要在'保留标签：'行，正文包含也算。"""
        content = ("SUMMARY TASK-0046 round-2 | 摘要\n"
                   "本任务决策：选B；参数：k=2；验收标准：测试过；阻塞点：依赖X")
        assert b3.check_summary_tags(content) == []

    def test_空内容全部缺失(self):
        assert b3.check_summary_tags("") == ["决策", "参数", "验收标准", "阻塞点"]


# ---- TASK-0054：建卡联动与中断恢复 ----
class TestExtractComplexity:
    def test_匹配配额simple(self):
        assert b3.extract_complexity("配额 simple\n其他约束") == "simple"

    def test_匹配复杂度冒号medium(self):
        assert b3.extract_complexity("复杂度：complex\n约束") == "complex"

    def test_未注明返回空(self):
        assert b3.extract_complexity("常规约束，无复杂度标注") == ""

    def test_空字符串返回空(self):
        assert b3.extract_complexity("") == ""

    def test_非法值返回空(self):
        assert b3.extract_complexity("配额 unknown") == ""

    def test_大小写不敏感(self):
        assert b3.extract_complexity("配额 SIMPLE") == "simple"


class TestFindLatestSummary:
    def test_找到匹配(self):
        summaries = [
            {"content": "SUMMARY TASK-0046 round-1 | 旧摘要", "updated_at": "2026-08-26T10:00:00"},
            {"content": "SUMMARY TASK-0046 round-2 | 新摘要", "updated_at": "2026-08-26T11:00:00"},
        ]
        result = b3.find_latest_summary(summaries, "TASK-0046")
        assert "新摘要" in result

    def test_无匹配返回None(self):
        summaries = [{"content": "SUMMARY TASK-0099 round-1 | 其他卡", "updated_at": "2026-08-26T10:00:00"}]
        assert b3.find_latest_summary(summaries, "TASK-0046") is None

    def test_按updated_at降序取最新(self):
        summaries = [
            {"content": "SUMMARY TASK-0046 round-1 | 最早", "updated_at": "2026-08-26T09:00:00"},
            {"content": "SUMMARY TASK-0046 round-3 | 最新", "updated_at": "2026-08-26T12:00:00"},
            {"content": "SUMMARY TASK-0046 round-2 | 中间", "updated_at": "2026-08-26T10:00:00"},
        ]
        result = b3.find_latest_summary(summaries, "TASK-0046")
        assert "最新" in result

    def test_空列表返回None(self):
        assert b3.find_latest_summary([], "TASK-0046") is None


class TestCmdAddWithRounds:
    """cmd_add_with_rounds：建卡+初始化 rounds（mock _request）。"""

    def _make_fake(self, monkeypatch, task_id="TASK-0001"):
        """构造 fake _request：GET 返回空卡列表，POST 返回记录 id。"""
        calls = []

        def fake(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                return {"items": [], "total": 0}
            return {"id": f"rec-{len(calls)}"}
        monkeypatch.setattr(b3, "_request", fake)
        return calls

    def test_默认medium初始化rounds(self, monkeypatch, capsys):
        calls = self._make_fake(monkeypatch)
        tid = b3.cmd_add_with_rounds("worker-1", "标题", "目标", "输入",
                                       "常规约束", "验收", docs="")
        assert tid == "TASK-0001"
        # 两次 POST：任务卡 + rounds 卡
        posts = [c for c in calls if c[0] == "POST"]
        assert len(posts) == 2
        # rounds 卡的 content 含默认 medium
        rounds_content = posts[1][2]["content"]
        assert "ROUNDS TASK-0001" in rounds_content
        assert "复杂度：medium" in rounds_content
        assert "precheck=2 milestone=2 total=5" in rounds_content  # medium 配额
        assert "tags" in posts[1][2] and "rounds" in posts[1][2]["tags"]
        out = capsys.readouterr().out
        assert "rounds 已初始化" in out
        assert "medium(默认)" in out

    def test_指定simple复杂度(self, monkeypatch):
        calls = self._make_fake(monkeypatch)
        b3.cmd_add_with_rounds("worker-1", "标题", "目标", "输入",
                                "配额 simple\n约束", "验收")
        posts = [c for c in calls if c[0] == "POST"]
        rounds_content = posts[1][2]["content"]
        assert "复杂度：simple" in rounds_content
        assert "precheck=1 milestone=1 total=3" in rounds_content  # simple 配额

    def test_指定complex复杂度(self, monkeypatch):
        calls = self._make_fake(monkeypatch)
        b3.cmd_add_with_rounds("worker-1", "标题", "目标", "输入",
                                "复杂度：complex", "验收")
        posts = [c for c in calls if c[0] == "POST"]
        rounds_content = posts[1][2]["content"]
        assert "复杂度：complex" in rounds_content
        assert "precheck=2 milestone=3 total=8" in rounds_content  # complex 配额


class TestCmdResume:
    """cmd_resume：claimed 卡唤醒续做，先读该卡 summary（mock _request）。"""

    def test_有summary输出(self, monkeypatch, capsys):
        def fake(method, path, body=None):
            return {"items": [
                {"content": "SUMMARY TASK-0046 round-2 | 关键结论\n决策：选A", "updated_at": "2026-08-26T11:00:00"},
            ], "total": 1}
        monkeypatch.setattr(b3, "_request", fake)
        b3.cmd_resume("TASK-0046")
        out = capsys.readouterr().out
        assert "[resume] TASK-0046 最新 summary" in out
        assert "关键结论" in out

    def test_无summary提示(self, monkeypatch, capsys):
        def fake(method, path, body=None):
            return {"items": [], "total": 0}
        monkeypatch.setattr(b3, "_request", fake)
        b3.cmd_resume("TASK-0046")
        out = capsys.readouterr().out
        assert "无 summary 记录" in out
        assert "从任务卡原文续做" in out
