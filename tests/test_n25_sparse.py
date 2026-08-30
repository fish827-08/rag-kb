"""N25 稀疏向量测试：BGE-M3 sparse 头 + SparseIndex + 三路 RRF（A3.5 spec §3.2/§3.3）。

覆盖：
- aggregate_sparse：同 token_id 多位置取 max 聚合 + L2 归一化（纯函数）
- SparseIndex：倒排点积打分 / 增量维护 / rebuild / 持久化往返 / id 漂移校验
- rrf_fuse 三路融合：手工可算的固定排名（双路行为回归不变）
- SparseEmbedder：结构探测失败 / sparse 头缺失 → SparseUnavailableError
- service 组装：默认关（None）/ 探测失败降级双路 / 三路端到端生效 / 写入路径维护
全部 mock 模型，不加载真实编码器权重。
"""
import json
import math
from pathlib import Path

import pytest


# ---------- 纯函数：aggregate_sparse ----------

class TestAggregateSparse:
    """同 token_id 多位置取 max 聚合 + L2 归一化（spec §3.2）。"""

    def test_同token_id取max聚合(self):
        from kb.sparse import aggregate_sparse
        # token 5 出现两次（权重 0.3 / 0.9），取 max=0.9；token 7 出现一次
        # L2 归一化对整个向量做：norm = sqrt(0.9²+0.4²) = sqrt(0.97)
        norm = math.sqrt(0.9 ** 2 + 0.4 ** 2)
        vec = aggregate_sparse([5, 7, 5], [0.3, 0.4, 0.9])
        assert vec[5] == pytest.approx(0.9 / norm, abs=1e-9)
        assert vec[7] == pytest.approx(0.4 / norm, abs=1e-9)
        # max 语义：不是求和（求和归一化后会得 1.2/sqrt(1.2²+0.4²)≈0.949 > 0.914）
        assert vec[5] < 1.2 / math.sqrt(1.2 ** 2 + 0.4 ** 2)

    def test_L2归一化(self):
        from kb.sparse import aggregate_sparse
        # 聚合后 {1: 0.6, 2: 0.8}，模长 1.0 → 归一化不变
        vec = aggregate_sparse([1, 2], [0.6, 0.8])
        assert vec[1] == pytest.approx(0.6, abs=1e-9)
        assert vec[2] == pytest.approx(0.8, abs=1e-9)
        norm = math.sqrt(sum(w * w for w in vec.values()))
        assert norm == pytest.approx(1.0, abs=1e-9)
        # 非单位向量被缩放：{1: 3.0} → {1: 1.0}
        vec2 = aggregate_sparse([1], [3.0])
        assert vec2[1] == pytest.approx(1.0, abs=1e-9)

    def test_空输入返回空(self):
        from kb.sparse import aggregate_sparse
        assert aggregate_sparse([], []) == {}

    def test_全零权重返回空(self):
        """全零向量无法归一化（除零防护），返回空 dict。"""
        from kb.sparse import aggregate_sparse
        assert aggregate_sparse([1, 2], [0.0, 0.0]) == {}


# ---------- SparseIndex ----------

class TestSparseIndex:
    """倒排索引：点积打分 / 增量维护 / rebuild / 持久化。"""

    def _idx(self):
        from kb.sparse import SparseIndex
        return SparseIndex()

    def test_点积打分与排序(self):
        """score(q,d) = Σ q_w[tid]×d_w[tid]（归一化后即余弦）。"""
        idx = self._idx()
        # q 与 d1 共享 tid=1，与 d2 共享 tid=2 但权重低
        q = {1: 0.6, 2: 0.8}
        idx.add("d1", {1: 1.0})
        idx.add("d2", {2: 0.5})
        idx.add("d3", {9: 1.0})  # 无交集
        hits = idx.search(q, top_n=3)
        assert hits[0] == ("d1", pytest.approx(0.6, abs=1e-9))
        assert hits[1] == ("d2", pytest.approx(0.4, abs=1e-9))
        assert all(rid != "d3" for rid, _ in hits)

    def test_多token交集累加(self):
        idx = self._idx()
        q = {1: 0.6, 2: 0.8}
        idx.add("d1", {1: 0.5, 2: 0.5})  # 0.3 + 0.4 = 0.7
        hits = idx.search(q, top_n=1)
        assert hits[0] == ("d1", pytest.approx(0.7, abs=1e-9))

    def test_add_remove增量维护(self):
        idx = self._idx()
        idx.add("d1", {1: 1.0})
        idx.add("d2", {1: 0.5})
        assert len(idx.search({1: 1.0}, top_n=10)) == 2
        idx.remove("d1")
        hits = idx.search({1: 1.0}, top_n=10)
        assert [rid for rid, _ in hits] == ["d2"]
        idx.remove("d1")  # 重复删除幂等
        assert [rid for rid, _ in idx.search({1: 1.0}, top_n=10)] == ["d2"]

    def test_remove后倒排表清理空token(self):
        """删除后空 posting 的 token 从倒排表移除（不泄漏）。"""
        idx = self._idx()
        idx.add("d1", {42: 1.0})
        idx.remove("d1")
        assert 42 not in idx._inverted

    def test_rebuild全量替换(self):
        idx = self._idx()
        idx.add("old", {1: 1.0})
        idx.rebuild([("a", {2: 1.0}), ("b", {3: 1.0})])
        assert [rid for rid, _ in idx.search({1: 1.0}, top_n=10)] == []
        assert [rid for rid, _ in idx.search({2: 1.0}, top_n=10)] == ["a"]

    def test_空查询返回空(self):
        idx = self._idx()
        idx.add("d1", {1: 1.0})
        assert idx.search({}, top_n=5) == []

    def test_save_load往返(self, tmp_path):
        idx = self._idx()
        idx.add("d1", {1: 0.6, 2: 0.8})
        idx.add("d2", {3: 1.0})
        cache = tmp_path / "sparse_index.json"
        idx.save(cache)
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert set(data.keys()) == {"d1", "d2"}

        idx2 = self._idx()
        assert idx2.load(cache, ["d1", "d2"]) is True
        hits = idx2.search({1: 0.6, 2: 0.8}, top_n=2)
        assert hits[0][0] == "d1"

    def test_load_id集合漂移返回False(self, tmp_path):
        """持久化 id 集合与库不一致 → False（调用方全量重建）。"""
        idx = self._idx()
        idx.add("d1", {1: 1.0})
        cache = tmp_path / "sparse_index.json"
        idx.save(cache)
        idx2 = self._idx()
        assert idx2.load(cache, ["d1", "d2"]) is False   # 多了一条
        assert idx2.load(cache, []) is False              # 少了一条
        assert idx2.load(tmp_path / "missing.json", ["d1"]) is False

    def test_load损坏JSON返回False(self, tmp_path):
        cache = tmp_path / "bad.json"
        cache.write_text("not json", encoding="utf-8")
        idx = self._idx()
        assert idx.load(cache, []) is False


# ---------- 三路 RRF ----------

class TestThreeWayRRF:
    """rrf_fuse 可变参数化（2 或 3 路），spec §3.3。"""

    def test_三路融合手工可算(self):
        from kb.retriever import rrf_fuse, RRF_K
        v = [("a", 9.0), ("b", 8.0), ("c", 7.0)]
        k = [("b", 9.0), ("a", 8.0), ("d", 7.0)]
        s = [("c", 9.0), ("a", 8.0), ("e", 7.0)]
        out = rrf_fuse(v, k, s, top_k=5)
        # 手工计算：a=1/(K+1)+1/(K+2)+1/(K+2) 三路全命中必第一
        exp_a = 1 / (RRF_K + 1) + 1 / (RRF_K + 2) + 1 / (RRF_K + 2)
        assert out[0][0] == "a"
        assert out[0][1] == pytest.approx(exp_a, abs=1e-12)
        # b（两路）> c（两路）> d/e（各一路）
        ids = [rid for rid, _ in out]
        assert ids.index("b") < ids.index("c")
        assert "d" in ids and "e" in ids

    def test_双路融合回归不变(self):
        """既有两路调用（位置参数 + top_k 关键字）行为不变。"""
        from kb.retriever import rrf_fuse
        v = [("a", 9.0), ("b", 8.0)]
        k = [("b", 9.0), ("a", 8.0)]
        out = rrf_fuse(v, k, top_k=4)
        # a: 1/61+1/62；b: 1/61+1/62 → 并列（sorted 稳定，a 先出现）
        assert out[0][0] == "a" and out[1][0] == "b"
        assert out[0][1] == pytest.approx(out[1][1], abs=1e-12)

    def test_单路也合法(self):
        from kb.retriever import rrf_fuse
        out = rrf_fuse([("x", 1.0)], top_k=3)
        assert out == [("x", pytest.approx(1 / 61, abs=1e-12))]


# ---------- SparseEmbedder 探测与降级 ----------

class TestSparseEmbedder:
    """结构探测 / sparse 头加载失败 → SparseUnavailableError（spec §3.2 降级链）。"""

    def _embedder_stub(self, monkeypatch, first_module=None, has_tokenizer=True):
        """构造 mock Embedder：_ensure_loaded 为 no-op，_model 呈给定结构。"""
        embedder = type("E", (), {"_ensure_loaded": lambda self: None})()
        embedder.model_name = "mock-model"
        embedder.device = "cpu"

        class _Model:
            def __getitem__(self, i):
                if first_module is Exception:
                    raise IndexError("空模块")
                return first_module

        model = _Model()
        if has_tokenizer:
            model.tokenizer = object()
        embedder._model = model
        return embedder

    def test_结构探测失败抛异常(self, monkeypatch):
        """底层模型无 auto_model 属性 → SparseUnavailableError。"""
        from kb.sparse import SparseEmbedder, SparseUnavailableError
        embedder = self._embedder_stub(monkeypatch, first_module=object())
        se = SparseEmbedder(embedder, "mock-model")
        with pytest.raises(SparseUnavailableError):
            se.encode(["文本"])

    def test_缺tokenizer抛异常(self, monkeypatch):
        from kb.sparse import SparseEmbedder, SparseUnavailableError
        module = type("M", (), {"auto_model": object()})()
        embedder = self._embedder_stub(monkeypatch, first_module=module,
                                       has_tokenizer=False)
        se = SparseEmbedder(embedder, "mock-model")
        with pytest.raises(SparseUnavailableError):
            se.encode(["文本"])

    def test_sparse头缺失抛异常(self, monkeypatch):
        """hf_hub_download 失败（模型仓库无 sparse_linear.pt）→ SparseUnavailableError。"""
        from kb.sparse import SparseEmbedder, SparseUnavailableError
        module = type("M", (), {"auto_model": object()})()
        embedder = self._embedder_stub(monkeypatch, first_module=module)

        import kb.sparse as sparse_mod
        def boom(*a, **kw):
            raise FileNotFoundError("no sparse_linear.pt")
        monkeypatch.setattr(sparse_mod, "_download_sparse_linear", boom)

        se = SparseEmbedder(embedder, "mock-model")
        with pytest.raises(SparseUnavailableError):
            se.encode(["文本"])

    def test_编码器探测成功后不重复加载(self, monkeypatch):
        """_ensure_loaded 幂等：成功后二次 encode 不再探测。"""
        from kb.sparse import SparseEmbedder
        module = type("M", (), {"auto_model": object()})()
        embedder = self._embedder_stub(monkeypatch, first_module=module)
        se = SparseEmbedder(embedder, "mock-model")
        se._sparse_linear = object()  # 直接置为已加载
        se._encoder = object()
        se._tokenizer = object()
        # encode 内部不再探测（tokenizer 无 __call__ 会抛 TypeError，但不会是
        # SparseUnavailableError；这里只验证 _ensure_loaded 早退）
        se._ensure_loaded()  # 不应抛异常


# ---------- service 组装与端到端 ----------

class _FakeSparseEmbedder:
    """测试替身：encode 按内容首字符构造稳定稀疏向量（探测直接成功）。"""

    def __init__(self):
        self.encode_calls = []

    def _ensure_loaded(self):
        """no-op：探测成功（service 启动路径要求）。"""
        pass

    def encode(self, texts):
        self.encode_calls.append(list(texts))
        out = []
        for t in texts:
            # 稳定映射：tid = ord(首字符)，权重 1.0（归一化）
            tid = ord(t[0]) if t else 0
            out.append({tid: 1.0})
        return out


class TestServiceSparse:
    """service 组装 / 降级 / 三路端到端 / 写入路径维护。"""

    def test_默认关_不组装稀疏件(self, env_isolated):
        from kb.config import Settings
        from kb.service import KBService
        s = KBService(Settings(), llm=object())
        assert s.sparse_embedder is None
        assert s.sparse_index is None
        assert s.retriever.sparse_embedder is None

    def test_探测失败_降级双路服务正常(self, env_isolated, monkeypatch):
        """SparseEmbedder 探测异常 → WARNING + 稀疏路关闭，服务可用。"""
        from kb.config import Settings
        from kb import service as service_mod
        from kb.sparse import SparseUnavailableError

        class _Broken:
            def __init__(self, *a, **kw):
                pass
            def _ensure_loaded(self):
                raise SparseUnavailableError("mock 探测失败")

        monkeypatch.setattr(service_mod, "_SparseEmbedderClass", _Broken)
        s = service_mod.KBService(Settings(sparse_enabled=True), llm=object())
        assert s.sparse_embedder is None
        assert s.sparse_index is None
        # 双路照常：检索路径可达（keyword 模式不碰 embedding）
        hits = s.search("任意词", top_k=3, mode="keyword")
        assert isinstance(hits, list)

    def test_三路端到端_稀疏路影响排序(self, env_isolated, monkeypatch):
        """Fake 稀疏向量 → 三路 RRF 生效：仅稀疏路命中的记录被召回提升。"""
        from kb.config import Settings
        from kb import service as service_mod
        fake = _FakeSparseEmbedder()
        monkeypatch.setattr(service_mod, "_SparseEmbedderClass",
                            lambda *a, **kw: fake)
        s = service_mod.KBService(Settings(sparse_enabled=True), llm=object())
        assert s.sparse_embedder is fake
        assert s.sparse_index is not None

        # 写入三条不同首字符记忆（稀疏向量 tid 不同）
        s.add_memory("甲向量检索测试", tags=[])
        s.add_memory("乙关键词匹配测试", tags=[])
        s.add_memory("丙稀疏召回测试", tags=[])

        # 稀疏查询命中"丙"（首字符 tid）：三路融合后"丙"必须出现在结果中
        # （keyword/vector 双路用真实小模型可能召回，也可能不召回——
        #  稀疏路保证其进入融合池）
        results = s.retriever.search("丙稀疏召回测试", top_k=3, mode="hybrid")
        ids = [r["id"] for r in results]
        rec_c = [r for r in s.store.iter_all() if r.content.startswith("丙")]
        assert rec_c[0].id in ids

    def test_写入路径维护稀疏索引与持久化(self, env_isolated, monkeypatch):
        """add/delete 同步维护内存稀疏索引并落盘 sparse_index.json。"""
        from kb.config import Settings
        from kb import service as service_mod
        fake = _FakeSparseEmbedder()
        monkeypatch.setattr(service_mod, "_SparseEmbedderClass",
                            lambda *a, **kw: fake)
        s = service_mod.KBService(Settings(sparse_enabled=True), llm=object())
        cache = Path(s.settings.data_dir) / "sparse_index.json"

        rec = s.add_memory("甲测试记忆", tags=[])
        assert rec.id in s.sparse_index._vecs
        assert cache.exists()
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert rec.id in data

        s.delete_memory(rec.id)
        assert rec.id not in s.sparse_index._vecs
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert rec.id not in data

    def test_二次启动加载持久化索引(self, env_isolated, monkeypatch):
        """sparse_index.json 命中时启动免全量 encode（探测仅一次）。"""
        from kb.config import Settings
        from kb import service as service_mod

        fake = _FakeSparseEmbedder()
        monkeypatch.setattr(service_mod, "_SparseEmbedderClass",
                            lambda *a, **kw: fake)
        s1 = service_mod.KBService(Settings(sparse_enabled=True), llm=object())
        s1.add_memory("甲启动缓存测试", tags=[])
        encode_count_first = len(fake.encode_calls)

        # 第二次启动（同 data_dir）：索引来自持久化，不对存量记录 encode
        fake2 = _FakeSparseEmbedder()
        monkeypatch.setattr(service_mod, "_SparseEmbedderClass",
                            lambda *a, **kw: fake2)
        s2 = service_mod.KBService(Settings(sparse_enabled=True), llm=object())
        assert len(fake2.encode_calls) == 0
        # 内存索引已恢复
        recs = list(s2.store.iter_all())
        assert recs[0].id in s2.sparse_index._vecs
        assert encode_count_first >= 1  # 第一次启动确实 encode 过


# ---------- retriever 稀疏挂接（单元级） ----------

class TestRetrieverSparseHook:
    """HybridRetriever 稀疏第三路挂接：默认关零行为变化 / 开启后三路。"""

    def _make(self, records, sparse_embedder=None, sparse_index=None,
              sparse_enabled=False):
        """构造挂好 mock 组件的 HybridRetriever；records 为 [{id, content}]。"""
        from kb.retriever import HybridRetriever

        class _Rec:
            pass

        recs = []
        for r in records:
            rec = _Rec()
            rec.id = r["id"]
            rec.content = r["content"]
            rec.type = type("T", (), {"value": "memory"})()
            rec.source = None
            rec.tags = []
            rec.created_at = ""
            rec.agent_id = "default"  # A 节点：Agent 归属（默认 default 隔离不生效）
            recs.append(rec)

        class _Store:
            def __init__(self, recs):
                self._records = {r.id: r for r in recs}

            def query(self, vec, top_k=10, where=None):
                return [(r, 0.9 - i * 0.1)
                        for i, r in enumerate(self._records.values())][:top_k]

            def get_many(self, ids):
                return {rid: r for rid in ids
                        if (r := self._records.get(rid)) is not None}

            def increment_access(self, ids):
                pass

        class _Embedder:
            def embed_query(self, q):
                return [0.1, 0.2, 0.3]

        class _BM25:
            def search(self, q, top_n=10, filter_fn=None):
                return []

            def meta_of(self, record_id):
                """改进项 3 隔离过滤回调：mock 无元信息 → 返回 None（不拦截）。"""
                return None

        class _Settings:
            def __init__(self, sparse_enabled):
                self.sparse_enabled = sparse_enabled

        retriever = HybridRetriever(_Store(recs), _BM25(), _Embedder(),
                                    settings=_Settings(sparse_enabled),
                                    sparse_embedder=sparse_embedder,
                                    sparse_index=sparse_index)
        return retriever

    def test_默认关_稀疏件不被调用(self):
        """sparse_enabled=false：注入了稀疏件也不调用（零行为变化）。"""
        calls = {"encode": 0}

        class _SE:
            def encode(self, texts):
                calls["encode"] += 1
                return [{1: 1.0} for _ in texts]

        class _SI:
            def search(self, q, top_n=10):
                return [("x", 1.0)]

        retriever = self._make([{"id": "a", "content": "文本"}],
                               sparse_embedder=_SE(), sparse_index=_SI(),
                               sparse_enabled=False)
        results = retriever.search("查询", top_k=3)
        assert calls["encode"] == 0
        assert len(results) >= 1

    def test_开启后_三路融合生效(self):
        """sparse_enabled=true：稀疏路的独占命中进入融合结果。"""

        class _SE:
            def encode(self, texts):
                return [{99: 1.0} for _ in texts]

        class _SI:
            def search(self, q, top_n=10):
                return [("only_sparse", 1.0)]  # 稀疏路独占命中

        retriever = self._make([{"id": "only_sparse", "content": "稀疏命中"}],
                               sparse_embedder=_SE(), sparse_index=_SI(),
                               sparse_enabled=True)
        results = retriever.search("查询", top_k=3)
        assert any(r["id"] == "only_sparse" for r in results)
