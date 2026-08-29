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
    """内部维护 {record_id: tokens}；每次变更同步重建 BM25Okapi（个人级规模毫秒级）。"""

    def __init__(self) -> None:
        self._docs: dict[str, list[str]] = {}
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
        self._rebuild_bm25()

    def add(self, record: Record) -> None:
        """新增一条记录并重建索引。"""
        self._docs[record.id] = tokenize(record.content)
        self._rebuild_bm25()

    def remove(self, record_id: str) -> None:
        """删除一条记录并重建索引。"""
        self._docs.pop(record_id, None)
        self._rebuild_bm25()

    def search(self, query: str, top_n: int = 10) -> list[tuple[str, float]]:
        """返回 (record_id, bm25分数) 降序；空索引返回 []。"""
        if self._bm25 is None:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)
        ranked = sorted(zip(self._docs.keys(), scores), key=lambda x: x[1], reverse=True)
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