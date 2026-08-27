"""N23c 智能层 consolidation 基础模块（TASK-0076，A3 智能层 spec §3/§4）。

覆盖：
- detect_conflict 启发式冲突检测纯函数（无 LLM 可用）
- consolidate_pair LLM 归并决策（mock LLMClient，不调真实 LLM）
- consolidate_dry_run dry-run 骨架（只返回建议不写库；默认关）
- MergeResult 字段完整性
"""
from datetime import datetime, timedelta

import pytest

from kb.config import Settings
from kb.llm import LLMError
from kb.models import Record
from kb.consolidation import (
    ConsolidationError,
    MergeResult,
    consolidate_dry_run,
    consolidate_pair,
    detect_conflict,
)


def _rec(content: str, updated_at: str = "2026-08-01T00:00:00") -> Record:
    """构造测试记录。"""
    return Record(content=content, updated_at=updated_at,
                  created_at=updated_at)


class _FakeLLMClient:
    """LLMClient 桩：按预设返回（或抛 LLMError），记录 chat 调用入参。"""

    def __init__(self, reply: str = "", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    def chat(self, messages, max_tokens=None, prefer="auto"):
        self.calls.append({"messages": messages, "prefer": prefer})
        if self.error:
            raise self.error
        return self.reply


# ---- detect_conflict：启发式冲突检测纯函数（spec §3.1） ----

class TestDetectConflict:

    def test_同属性不同值_属性冲突(self):
        """同 key 不同值 → attribute_conflict。"""
        a = _rec("用户偏好：深色主题")
        b = _rec("用户偏好：浅色主题")
        assert detect_conflict(a, b) == "attribute_conflict"

    def test_兼容值_非冲突(self):
        """值包含关系（Python vs Python3.10）→ none（spec §3.1 兼容示例）。"""
        a = _rec("语言：Python")
        b = _rec("语言：Python 3.10")
        assert detect_conflict(a, b) == "none"

    def test_同属性同值_非冲突(self):
        """同 key 同值 → none。"""
        a = _rec("语言：Python")
        b = _rec("语言：Python")
        assert detect_conflict(a, b) == "none"

    def test_时间矛盾(self):
        """同主题关键词重叠 + updated_at 差>30天 + 断定词 → temporal。"""
        a = _rec("部署框架用的是 FastAPI", updated_at="2026-01-01T00:00:00")
        b = _rec("部署框架现在是 Litestar", updated_at="2026-08-01T00:00:00")
        assert detect_conflict(a, b) == "temporal"

    def test_时间接近_非时间矛盾(self):
        """updated_at 差<30 天 → 不判 temporal（时间过近不构成新旧矛盾）。"""
        a = _rec("部署框架用的是 FastAPI", updated_at="2026-07-20T00:00:00")
        b = _rec("部署框架现在是 Litestar", updated_at="2026-08-01T00:00:00")
        assert detect_conflict(a, b) == "none"

    def test_无关键词交集_非冲突(self):
        """主题无关的两条记录 → none。"""
        a = _rec("用户偏好：深色主题")
        b = _rec("数据库用的 PostgreSQL")
        assert detect_conflict(a, b) == "none"

    def test_纯函数_无LLM依赖(self):
        """无 LLM 环境下可用（不触碰任何网络/客户端）。"""
        a = _rec("编辑器：Vim")
        b = _rec("编辑器：Emacs")
        assert detect_conflict(a, b) == "attribute_conflict"


# ---- MergeResult：字段完整性（spec §4.3） ----

class TestMergeResult:

    def test_字段完整(self):
        """MergeResult 含 action/merged_content/confidence/reason/conflict_type。"""
        r = MergeResult(action="merge", merged_content="合并内容",
                        confidence=0.9, reason="同主题互补",
                        conflict_type="none")
        assert r.action == "merge"
        assert r.merged_content == "合并内容"
        assert r.confidence == 0.9
        assert r.reason == "同主题互补"
        assert r.conflict_type == "none"

    def test_默认值(self):
        """非 merge 决策时 merged_content 可为空、confidence 默认 0。"""
        r = MergeResult(action="human")
        assert r.merged_content is None
        assert r.confidence == 0.0
        assert r.conflict_type == "none"


# ---- consolidate_pair：LLM 归并决策（mock，spec §4/§6） ----

class TestConsolidatePair:

    def test_正常merge决策(self):
        """LLM 返回合法 JSON merge → MergeResult(action=merge)。"""
        reply = ('{"decision":"merge","reason":"同主题互补",'
                 '"merged_content":"用户偏好深色主题，编辑器 Vim",'
                 '"conflict_type":"none","confidence":0.9}')
        client = _FakeLLMClient(reply=reply)
        result = consolidate_pair(_rec("用户偏好深色主题"),
                                  _rec("编辑器：Vim"), client)
        assert result.action == "merge"
        assert result.merged_content == "用户偏好深色主题，编辑器 Vim"
        assert result.confidence == pytest.approx(0.9)
        assert result.conflict_type == "none"

    def test_低置信度_强制human(self):
        """confidence < 0.6 → 强制升级 human（spec §4.3）。"""
        reply = ('{"decision":"merge","reason":"ok",'
                 '"merged_content":"x","conflict_type":"none",'
                 '"confidence":0.5}')
        client = _FakeLLMClient(reply=reply)
        result = consolidate_pair(_rec("a"), _rec("b"), client)
        assert result.action == "human"
        assert result.reason  # 记录升级原因

    def test_非法JSON_降级human(self):
        """LLM 输出非合法 JSON → 降级 human，不抛异常（spec §4.3）。"""
        client = _FakeLLMClient(reply="这不是JSON")
        result = consolidate_pair(_rec("a"), _rec("b"), client)
        assert result.action == "human"

    def test_枚举外decision_降级human(self):
        """decision 不在 merge/independent/human 枚举 → 降级 human。"""
        reply = ('{"decision":"maybe","reason":"x","merged_content":null,'
                 '"conflict_type":"none","confidence":0.9}')
        client = _FakeLLMClient(reply=reply)
        result = consolidate_pair(_rec("a"), _rec("b"), client)
        assert result.action == "human"

    def test_merge但merged_content为空_降级human(self):
        """decision=merge 而 merged_content 空 → 校验失败降级 human。"""
        reply = ('{"decision":"merge","reason":"x","merged_content":"",'
                 '"conflict_type":"none","confidence":0.9}')
        client = _FakeLLMClient(reply=reply)
        result = consolidate_pair(_rec("a"), _rec("b"), client)
        assert result.action == "human"

    def test_LLM不可用_降级human(self):
        """LLMError（本地不可用/cloud 禁用）→ 该对降级 human（spec §6）。"""
        client = _FakeLLMClient(error=LLMError("本地 LLM 不可用"))
        result = consolidate_pair(_rec("a"), _rec("b"), client)
        assert result.action == "human"
        assert "不可用" in result.reason

    def test_仅用本地_禁云端(self):
        """chat 以 prefer=local 调用（智能层禁止云端外传，spec §6）。"""
        reply = ('{"decision":"independent","reason":"主题不同",'
                 '"merged_content":null,"conflict_type":"none",'
                 '"confidence":0.9}')
        client = _FakeLLMClient(reply=reply)
        consolidate_pair(_rec("a"), _rec("b"), client)
        assert client.calls and client.calls[0]["prefer"] == "local"

    def test_提示词含系统提示与记录内容(self):
        """messages 含系统提示（记忆归并代理）与两条记录内容（spec §4.1/4.2）。"""
        reply = ('{"decision":"independent","reason":"主题不同",'
                 '"merged_content":null,"conflict_type":"none",'
                 '"confidence":0.9}')
        client = _FakeLLMClient(reply=reply)
        consolidate_pair(_rec("用户偏好：深色"), _rec("数据库用的 PG"), client)
        messages = client.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        assert "记忆归并" in messages[0]["content"]
        user_text = messages[1]["content"]
        assert "用户偏好：深色" in user_text
        assert "数据库用的 PG" in user_text

    def test_预筛冲突标记传入用户提示(self):
        """预筛 conflict_type 作为上下文传入用户提示（spec §4.2）。"""
        reply = ('{"decision":"human","reason":"矛盾",'
                 '"merged_content":null,"conflict_type":"attribute_conflict",'
                 '"confidence":0.9}')
        client = _FakeLLMClient(reply=reply)
        consolidate_pair(_rec("编辑器：Vim"), _rec("编辑器：Emacs"), client)
        user_text = client.calls[0]["messages"][1]["content"]
        assert "attribute_conflict" in user_text


# ---- consolidate_dry_run：dry-run 骨架（spec §7 preview 语义） ----

class TestConsolidateDryRun:

    def test_默认关_拒绝执行(self):
        """consolidation_enabled=False → 抛 ConsolidationError。"""
        settings = Settings(consolidation_enabled=False)
        client = _FakeLLMClient()
        with pytest.raises(ConsolidationError):
            consolidate_dry_run([(_rec("a"), _rec("b"))], client, settings)

    def test_开启_返回建议不写库(self):
        """启用后逐对返回 MergeResult 建议（纯计算，无任何写库副作用）。"""
        reply = ('{"decision":"merge","reason":"互补",'
                 '"merged_content":"ab","conflict_type":"none",'
                 '"confidence":0.9}')
        client = _FakeLLMClient(reply=reply)
        settings = Settings(consolidation_enabled=True)
        results = consolidate_dry_run([(_rec("a"), _rec("b")),
                                       (_rec("c"), _rec("d"))],
                                      client, settings)
        assert len(results) == 2
        assert all(isinstance(r, MergeResult) for r in results)
        assert all(r.action == "merge" for r in results)

    def test_单对失败不阻塞整批(self):
        """某对 LLM 失败降级 human，其余对正常返回（spec §6）。"""
        # 用会触发降级的非法输出
        client = _FakeLLMClient(reply="bad json")
        settings = Settings(consolidation_enabled=True)
        results = consolidate_dry_run([(_rec("a"), _rec("b"))],
                                       client, settings)
        assert results[0].action == "human"
