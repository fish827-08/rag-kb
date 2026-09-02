"""BM25 索引：jieba 分词 + rank_bm25，内存维护，变更时同步重建。"""
import jieba
from rank_bm25 import BM25Okapi

from kb.models import Record


def tokenize(text: str) -> list[str]:
    """jieba 细粒度分词（cut_for_search，含子词，更适合关键词检索）
    + 小写化 + 去空白/单字符标点。"""
    toks = []
    for t in jieba.cut_for_search(text):
        t = t.strip().lower()
        if t and not (len(t) == 1 and (t.isascii() and (not t.isalnum()))):
            toks.append(t)
    return toks


class BM25Index:
    """内部维护 {record_id: tokens}；每次变更同步重建 BM25Okapi（个人级规模毫秒级）。

    另维护轻量元信息 {record_id: (client, project, type)}（改进项 3：检索隔离
    下推到 BM25 排序前过滤用）；启动 load_corpus 成功后由 service.set_meta 回填。
    English: Internally maintains {record_id: tokens}; rebuilds BM25Okapi on every change
    (millisecond-level at personal scale). Also keeps lightweight per-record metadata
    {record_id: (client, project, type)} (improvement #3: isolation pushdown into the
    BM25 pre-ranking filter); after a successful load_corpus the service backfills it
    via set_meta."""

    def __init__(self) -> None:
        self._docs: dict[str, list[str]] = {}
        self._meta: dict[str, tuple[str, str, str]] = {}
        self._bm25 = None

    def _rebuild_bm25(self) -> None:
        """依据当前语料重建 BM25Okapi；空语料置 None。"""
        if not self._docs:
            self._bm25 = None
            return
        self._bm25 = BM25Okapi(list(self._docs.values()))

    def rebuild(self, records) -> None:
        """用一组记录全量重建索引（启动时传 store.iter_all()）。"""
        self._docs = {r.id: tokenize(r.content) for r in records}
        self._meta = {r.id: (r.client, r.project, r.type.value) for r in records}
        self._rebuild_bm25()

    def set_meta(self, records) -> None:
        """回填轻量元信息（load_corpus 成功路径；不触发重建）。"""
        self._meta = {r.id: (r.client, r.project, r.type.value) for r in records}

    def meta_of(self, record_id: str) -> tuple[str, str, str] | None:
        """返回该 id 的 (client, project, type) 元信息；未知 id 返回 None（防御不拦截）。
        English: Return (client, project, type) for an id; None when unknown (defensive)."""
        return self._meta.get(record_id)

    def add(self, record: Record) -> None:
        """新增一条记录并重建索引。"""
        self._docs[record.id] = tokenize(record.content)
        self._meta[record.id] = (record.client, record.project, record.type.value)
        self._rebuild_bm25()

    def add_many(self, records) -> None:
        """批量新增（改进项 1B）：全部并入语料后只重建一次，避免 N 条 N 次全量重建。

        用于 add_document / add_webpage 的 chunk 批量入库（此前循环 add 对每个
        chunk 重建一次，N 个 chunk = N 次 O(语料) 重建）。
        English: Batch add (improvement #1B): merge all records into the corpus then
        rebuild exactly once, avoiding one full rebuild per record (previously the
        chunk-ingest loop rebuilt per chunk, i.e. N rebuilds for N chunks)."""
        for r in records:
            self._docs[r.id] = tokenize(r.content)
            self._meta[r.id] = (r.client, r.project, r.type.value)
        self._rebuild_bm25()

    def remove(self, record_id: str) -> None:
        """删除一条记录并重建索引。"""
        self._docs.pop(record_id, None)
        self._meta.pop(record_id, None)
        self._rebuild_bm25()

    def remove_many(self, record_ids) -> None:
        """批量删除（改进项 1B）：全部移出后只重建一次（delete_document 用）。"""
        for rid in record_ids:
            self._docs.pop(rid, None)
            self._meta.pop(rid, None)
        self._rebuild_bm25()

    def search(self, query: str, top_n: int = 10,
               filter_fn=None) -> list[tuple[str, float]]:
        """返回 (record_id, bm25分数) 降序；空索引返回 []。

        无词面重叠的记录不返回（N33 修复）：跨语言记录（如中文 query 下纯英文
        记忆）与查询无任何分词重叠，BM25 分数恒为 0。若此类记录留在 keyword 路，
        weighted_fuse 会按"生效路数"均权时把它当作"该路出现"计入分母，把向量分
        ÷2 稀释——N33 声称按生效路数均权可防止稀释，但实际前提是跨语言记录
        不出现在 keyword 路的候选列表中。此前 BM25 返回全部 top_n（含 0 分记录），
        导致 N33 的前提不成立。本修复按 token 重叠过滤，确保跨语言记录缺席
        keyword 路。
        注意：不能按分数 > 0 过滤——小语料下词项出现在恰好半数文档时 IDF = 0
        （log(1) = 0），此时有词面重叠的记录分数也是 0，按分数过滤会误剔。
        filter_fn(record_id)->bool（可选）在排序前过滤（改进项 3：检索隔离下推——
        BM25 分数由全语料 DF 决定，过滤只剔除不可见记录，不改变剩余记录分数）。
        English: Return (record_id, bm25 score) descending; an empty index returns [].
        Records with no lexical overlap are excluded (N33 fix): cross-language records
        (e.g. a pure-English memory under a Chinese query) share no tokens with the query
        and always score 0. Leaving them in the keyword route makes weighted_fuse count
        the route as "present" in the denominator, halving the vector score — N33 claimed
        averaging over present routes fixes this, but only holds when cross-language
        records are absent from the keyword candidate list. Previously BM25 returned all
        top_n (including 0-score records), violating that premise. This fix filters by
        token overlap, not by score: when a term appears in exactly half the corpus the
        IDF is 0 (log(1)=0), so records with genuine overlap also score 0, and a score
        filter would wrongly exclude them.
        Optional filter_fn(record_id)->bool filters before ranking (improvement #3:
        isolation pushdown — BM25 scores depend on corpus-wide DF, so filtering only
        drops invisible records without altering the scores of the rest)."""
        if self._bm25 is None:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        query_token_set = set(query_tokens)
        scores = self._bm25.get_scores(query_tokens)
        if filter_fn is None:
            ranked = sorted(zip(self._docs.keys(), scores),
                            key=lambda x: x[1], reverse=True)
        else:
            ranked = sorted(
                ((rid, s) for rid, s in zip(self._docs.keys(), scores)
                 if filter_fn(rid)),
                key=lambda x: x[1], reverse=True)
        # 按词面重叠过滤：无重叠的记录（跨语言等）不返回，否则融合时被当作
        # "该路出现"稀释向量分。不能用分数 > 0 过滤——小语料下 IDF 可能为 0
        # 导致有重叠的记录也 0 分
        ranked = [(rid, s) for rid, s in ranked
                  if query_token_set & set(self._docs.get(rid, []))]
        return [(rid, float(score)) for rid, score in ranked[:top_n]]

    # ---- 语料持久化（N27，A3.5 spec §3.4）----

    def save_corpus(self, path) -> None:
        """{record_id: tokens} 序列化 JSON 落盘（启动免全量 jieba 分词）。"""
        import json
        from pathlib import Path
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._docs, ensure_ascii=False),
                        encoding="utf-8")

    def load_corpus(self, path, valid_ids) -> bool:
        """加载持久化语料；id 集合与库一致才生效，否则 False（调用方全量重建）。

        文件缺失 / JSON 损坏 / id 集合漂移均返回 False（安全降级为重建）。
        """
        import json
        from pathlib import Path
        path = Path(path)
        if not path.exists():
            return False
        try:
            docs = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return False
        if not isinstance(docs, dict) or set(docs.keys()) != set(valid_ids):
            return False
        self._docs = {rid: list(toks) for rid, toks in docs.items()}
        self._rebuild_bm25()
        return True