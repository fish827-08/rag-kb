import pytest

pytestmark = pytest.mark.integration


class FakeLLM:
    def __init__(self, answer="模拟回答"):
        self.answer = answer; self.calls = []
    @property
    def status(self):
        from kb.llm import LLMStatus
        return LLMStatus.LOCAL
    def chat(self, messages, **kw):
        self.calls.append(messages); return self.answer


@pytest.fixture
def svc(env_isolated):
    from kb.service import KBService
    s = KBService(llm=FakeLLM())
    for i in range(8):
        s.add_memory(f"第{i}条超长记忆" + "背景说明。" * 200)  # 每条约 1000 字
    return s


def test_ask返回答案与来源(svc):
    r = svc.ask("第3条记忆")
    assert r["answer"] == "模拟回答"
    assert len(r["sources"]) >= 1 and r["llm"] == "local"
    assert all("id" in s and "score" in s for s in r["sources"])


def test_上下文预算截断(svc):
    from kb.config import get_settings
    svc.ask("第3条记忆")
    user_msg = svc.llm.calls[0][-1]["content"]
    budget = get_settings().context_token_limit * 2
    assert len(user_msg) <= budget + 200   # 允许模板外壳少量超出


def test_护栏系统提示(svc):
    svc.ask("任意问题")
    system_msg = svc.llm.calls[0][0]["content"]
    assert "禁止编造" in system_msg


def test_禁用转503(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/ask", json={"question": "测试"})
        assert r.status_code == 503
        assert r.json()["error"] == "LLM_DISABLED"
        assert "Ollama" in r.json()["message"]
