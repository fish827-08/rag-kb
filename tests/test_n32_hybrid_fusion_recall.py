"""N32 / N33 混合检索融合修复回归测试（2026-09-01）。

背景：hybrid 默认走 RRF 排名融合 Σ 1/(60+rank)，区分度被压缩到 ~0.001 噪声级。
实测：某条记忆在 vector、keyword 单路均排第 1（Poker Face / 中文 query「用户最喜欢的音乐」），
但 RRF 融合后与无关记录仅差 0.0006，top_k=5 截断时被挤出——用户经豆包写入的英文歌名
记忆在 Trae 中文检索下"查不到"。
N32 修复：hybrid 默认改归一化评分加权融合（weighted_fuse）+ 候选池放宽 max(3×top_k, 30)。
N33 修复：fusion 分母从"总路数"改为"生效路数"——跨语言记录（纯英文/日文记忆）在
BM25 路必然缺席（无词面重叠），统一 ÷2 会把向量分砍半再次挤出 top_k；按生效路数均权后
同语言场景零行为变化，跨语言只靠向量路命中的记录不再被稀释。
English: Regression tests for the N32/N33 hybrid fusion fixes — cross-language/proper-noun
recall used to be lost at top_k truncation under RRF and again under the N32 ÷route-count
averaging when a record misses the BM25 route entirely."""
import pytest

pytestmark = pytest.mark.integration


# ---- 单元：weighted_fuse 数学 ----

def test_weighted_fuse_归一化均权():
    from kb.retriever import weighted_fuse
    v = [("a", 0.9), ("x", 0.5), ("y", 0.1)]  # minmax: a=1.0, x=0.5, y=0.0
    k = [("a", 5.0), ("x", 0.0)]              # minmax: a=1.0, x=0.0
    out = dict(weighted_fuse(v, k, top_k=3))
    assert out["a"] == pytest.approx(1.0)     # 双路同现：均权 (1.0+1.0)/2
    assert out["x"] == pytest.approx((0.5 + 0.0) / 2)
    assert out["y"] == pytest.approx(0.0)


def test_weighted_fuse_区分度保留():
    """双路第 1 的记录融合分 1.0，第 16 名 0.0——不像 RRF 那样挤成一团。"""
    from kb.retriever import weighted_fuse
    v = [(f"r{i}", float(100 - i)) for i in range(16)]
    k = [(f"r{i}", float(100 - i) * 2) for i in range(16)]
    out = dict(weighted_fuse(v, k, top_k=16))
    assert out["r0"] == pytest.approx(1.0)
    assert out["r15"] == pytest.approx(0.0, abs=1e-9)


def test_weighted_fuse_单路命中按生效路数均权():
    """N33：只在一路命中的记录融合分 = 该路归一化分（不再 ÷ 总路数），
    跨语言记录缺席 BM25 路不被稀释。"""
    from kb.retriever import weighted_fuse
    v = [("only_vec", 1.0)]
    k = [("only_key", 2.0), ("both", 1.0)]
    out = dict(weighted_fuse(v, k, top_k=3))
    assert out["only_vec"] == pytest.approx(1.0)
    assert out["only_key"] == pytest.approx(1.0)
    assert out["both"] == pytest.approx(0.0, abs=1e-9)  # 仅 k 路出现且归一化 0


def test_weighted_fuse_单元素路():
    """该路 max==min：非零记 1，全零记 0。"""
    from kb.retriever import weighted_fuse
    assert dict(weighted_fuse([("solo", 0.5)], top_k=1))["solo"] == pytest.approx(1.0)
    assert dict(weighted_fuse([("zz", 0.0)], top_k=1))["zz"] == pytest.approx(0.0)


# ---- 集成：复刻用户报告的外语/专有名词召回场景 ----

@pytest.fixture
def svc(env_isolated):
    from kb.service import KBService
    s = KBService()
    s.add_memory("用户喜欢猫咪，偏好养猫", tags=["偏好"])
    s.add_memory("项目例会每周五下午三点，会议室 B201", tags=["日程"])
    s.add_memory("worker-2 状态：空闲", tags=[])
    s.add_memory("规约验证记忆", tags=[])
    s.add_memory("TASK-100 进度：分文件审计上线", tags=[])
    s.add_memory("用户最喜欢的音乐是 Poker Face。", tags=["偏好", "音乐"])
    s.add_memory("The hybrid retriever fuses BGE-M3 vector search with BM25 keyword ranking.",
                 tags=["i18n", "english"])
    return s


def test_外语歌名中文查询hybrid可召回(svc):
    """用户报告主场景：豆包写入英文歌名，Trae 中文查「用户最喜欢的音乐」必须命中。"""
    hits = svc.search("用户最喜欢的音乐", top_k=5, mode="hybrid")
    assert hits, "hybrid 检索应返回结果"
    assert any("Poker Face" in h["content"] for h in hits), \
        "含英文专有名词的记忆不得被 top_k 截断挤掉"


def test_小top_k下英文记录排名靠前(svc):
    """top_k=3 时英文记录应进入前列，而非仅靠查关键词才出现。"""
    hits = svc.search("用户最喜欢的音乐", top_k=3, mode="hybrid")
    assert any("Poker Face" in h["content"] for h in hits)


def test_纯英文记忆中文查询不被稀释挤出(svc):
    """N33：纯英文记忆与中文 query 无词面重叠（BM25 路 0 分），
    只有向量路命中时应按生效路数均权得原分，仍回收到 top_k。"""
    hits = svc.search("混合检索是如何融合向量和关键词的", top_k=5, mode="hybrid")
    assert hits, "hybrid 检索应返回结果"
    assert any("BGE-M3" in h["content"] for h in hits), \
        "缺席 BM25 路的跨语言记忆不得被 ÷总路数稀释出 top_k"