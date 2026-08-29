import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(env_isolated):
    from kb.service import KBService
    s = KBService()
    s.add_memory("张三的生日是3月15日", tags=["人物"], source="t")
    s.add_memory("李四负责前端开发，使用 React 和 TypeScript", tags=["人物"], source="t")
    s.add_memory("项目例会每周五下午三点，会议室 B201", tags=["日程"], source="t")
    s.add_memory("The quick brown fox jumps over the lazy dog", tags=["en"], source="t")
    return s


def test_rrf融合数学():
    from kb.retriever import rrf_fuse
    v = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
    k = [("b", 5.0), ("a", 4.0), ("d", 3.0)]
    out = rrf_fuse(v, k, top_k=4)
    # a: 1/61+1/62  b: 1/62+1/61  c: 1/63  d: 1/63 → a=b > c=d
    assert out[0][0] in ("a", "b") and out[1][0] in ("a", "b")
    assert dict(out)["c"] == pytest.approx(1 / (60 + 3), abs=1e-9)
    assert dict(out)["d"] == pytest.approx(1 / (60 + 3), abs=1e-9)


def test_关键词查询命中(svc):
    hits = svc.search("张三 生日", top_k=3, mode="keyword")
    assert hits and "张三" in hits[0]["content"]


def test_语义查询命中(svc):
    # 无字面重叠的语义近义查询
    hits = svc.search("敏捷的棕色狐狸跳过懒狗", top_k=2, mode="vector")
    assert hits and "fox" in hits[0]["content"]


def test_混合模式与类型过滤(svc):
    hits = svc.search("例会", top_k=3, mode="hybrid", tag="日程")
    assert hits and "B201" in hits[0]["content"]
    assert all("日程" in h["tags"] for h in hits)


def test_增改删全流程(svc):
    r = svc.add_memory("临时记忆内容")
    assert svc.get_memory(r.id).content == "临时记忆内容"
    updated = svc.update_memory(r.id, content="改后的记忆内容")
    assert updated.content == "改后的记忆内容"
    assert svc.search("改后的记忆内容", top_k=1, mode="keyword")
    assert svc.delete_memory(r.id) is True
    assert svc.get_memory(r.id) is None
    assert svc.delete_memory(r.id) is False


def test_cli_add与search(env_isolated):
    from typer.testing import CliRunner
    from kb.cli import app
    runner = CliRunner()
    assert runner.invoke(app, ["add", "CLI写入的测试记忆"]).exit_code == 0
    res = runner.invoke(app, ["search", "CLI写入", "--mode", "keyword"])
    assert res.exit_code == 0 and "CLI写入的测试记忆" in res.output