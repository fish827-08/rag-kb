import pytest
import math

pytestmark = pytest.mark.integration


def test_延迟加载与维度归一化(env_isolated):
    from kb.embedder import Embedder
    from kb.config import get_settings
    e = Embedder(get_settings().embed_model, device="cpu")
    assert e._model is None            # 未使用不加载
    vecs = e.embed_texts(["苹果手机多少钱", "今天天气不错"])
    assert len(vecs) == 2 and len(vecs[0]) == 512
    for v in vecs:
        assert math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-3)  # 单位向量


def test_相似语义更近(env_isolated):
    from kb.embedder import Embedder
    from kb.config import get_settings
    e = Embedder(get_settings().embed_model, device="cpu")
    q = e.embed_query("机器学习入门教程")
    hits = e.embed_texts(["深度学习与神经网络基础", "今天中午吃什么"])
    def cos(a, b): return sum(x * y for x, y in zip(a, b))
    assert cos(q, hits[0]) > cos(q, hits[1])  # 语义相近者余弦更高