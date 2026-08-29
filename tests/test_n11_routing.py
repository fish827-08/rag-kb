import pytest

pytestmark = pytest.mark.integration


class ScriptedLLM:
    """按调用次序返回预设结果；记录每次收到的 messages。"""
    def __init__(self, script: list[str]):
        self.script = list(script); self.calls = []
    @property
    def status(self):
        from kb.llm import LLMStatus
        return LLMStatus.LOCAL
    def chat(self, messages, prefer="auto", **kw):
        self.calls.append({"messages": messages, "prefer": prefer})
        return self.script.pop(0)


@pytest.fixture
def routed_svc(env_isolated, monkeypatch):
    from kb.service import KBService
    from kb.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("KB_LLM_MODE", "auto")
    llm = ScriptedLLM(script=[])
    s = KBService(llm=llm)
    s.add_memory("普通事实：会议室在 B201")
    s.add_memory("密码：门禁是 8842", tags=["sensitive"])
    s._cloud_client = None   # 无云
    return s, llm


def test_敏感内容强制本地(routed_svc):
    svc, llm = routed_svc
    llm.script = ["SIMPLE", "门禁密码是8842"]
    r = svc.ask("门禁密码是多少")
    assert r["llm"] == "local"
    # 敏感问题即使 SIMPLE/COMPLEX 分类也不出云（无云调用发生）


def test_简单问题本地直答(routed_svc):
    svc, llm = routed_svc
    llm.script = ["SIMPLE", "会议室在B201"]
    r = svc.ask("会议室在哪")
    assert r["answer"] == "会议室在B201"
    assert all(c["prefer"] != "cloud" for c in llm.calls)


def test_缓存命中不重复调用LLM(routed_svc):
    svc, llm = routed_svc
    llm.script = ["SIMPLE", "首次回答"]
    svc.ask("会议室在哪")
    svc.ask("会议室在哪里？")        # 相似问法（bge-small 实测相似度≈0.96，命中）
    svc.ask("会议室在哪")            # 重复原问（必命中）
    assert len(llm.calls) == 2       # 只有首次 ask 调用了分类+生成，后两次走缓存


def test_复杂问题无云走本地(routed_svc):
    svc, llm = routed_svc
    llm.script = ["COMPLEX", "本地兜底回答"]
    r = svc.ask("综合分析全部记忆并给出年度报告")
    assert r["answer"] == "本地兜底回答"


def test_复杂问题有云先压缩(env_isolated, monkeypatch):
    """云端路径：分类→压缩→云端生成；压缩后 prompt 必须短于原始上下文。"""
    from kb.service import KBService

    class FakeCloud:
        def __init__(self): self.received = []
        def chat(self, messages, **kw):
            self.received.append(messages); return "云端回答"

    monkeypatch.setenv("KB_LLM_MODE", "auto")   # 本测试走 auto 智能路由（默认已改 off）
    llm = ScriptedLLM(["COMPLEX", "压缩后的要点"])
    svc = KBService(llm=llm)
    svc._cloud_client = FakeCloud()
    svc.add_memory("项目甲预算三百万" + "细节。" * 300)
    r = svc.ask("综合对比所有项目预算并分析")
    assert r["answer"] == "云端回答" and r["llm"] == "cloud"
    assert len(FakeCloud and svc._cloud_client.received) == 1
