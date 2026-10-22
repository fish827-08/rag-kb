"""轻量记忆关联：每个作用域一个 SQLite 边库（无额外服务、无新增依赖）。

边有方向（便于表达 supersedes / depends_on），邻居遍历双向进行；
同一 (from_id, to_id, relation) 幂等。设计见
`docs/superpowers/specs/2026-10-22-kb-memory-links-design.md`。

English: Lightweight memory links: one SQLite edge store per scope (no extra service or dependency).
Edges are directed (to express supersedes / depends_on) and traversed in both directions;
the same (from_id, to_id, relation) is idempotent.
"""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

RELATIONS = ("related", "supersedes", "depends_on", "part_of", "contradicts")
DEFAULT_RELATION = "related"


class LinkError(Exception):
    """关联操作错误；code 供 REST/MCP 映射为统一错误 JSON。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_relation(relation: str | None) -> str:
    """校验关系名；空值取默认 related。非法抛 LinkError(INVALID_RELATION)。
    English: Validate a relation name; empty means the default `related`."""
    value = (relation or DEFAULT_RELATION).strip()
    if value not in RELATIONS:
        raise LinkError("INVALID_RELATION",
                        f"relation 非法：{relation!r}；可选 {', '.join(RELATIONS)}")
    return value


class LinkStore:
    """一个作用域的关联边存储；SQLite 连接跨线程，读写用实例锁串行化。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=3000")
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS links (
                    from_id    TEXT NOT NULL,
                    to_id      TEXT NOT NULL,
                    relation   TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (from_id, to_id, relation)
                );
                CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id);
                """
            )
            self._conn.commit()

    def add(self, from_id: str, to_id: str, relation: str) -> bool:
        """写入一条边；已存在返回 False（幂等）。"""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO links(from_id, to_id, relation, created_at) "
                "VALUES (?, ?, ?, ?)",
                (from_id, to_id, relation, datetime.now().isoformat()))
            self._conn.commit()
            return cur.rowcount > 0

    def has(self, from_id: str, to_id: str, relation: str) -> bool:
        """判断指定方向与关系的边是否已存在。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM links WHERE from_id = ? AND to_id = ? "
                "AND relation = ? LIMIT 1",
                (from_id, to_id, relation)).fetchone()
            return row is not None

    def remove(self, from_id: str, to_id: str,
               relation: str | None = None) -> int:
        """删除两点之间的边（任一方向）；relation 为空时删除该对全部关系。"""
        with self._lock:
            if relation:
                cur = self._conn.execute(
                    "DELETE FROM links WHERE relation = ? AND "
                    "((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))",
                    (relation, from_id, to_id, to_id, from_id))
            else:
                cur = self._conn.execute(
                    "DELETE FROM links WHERE "
                    "((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))",
                    (from_id, to_id, to_id, from_id))
            self._conn.commit()
            return cur.rowcount

    def remove_record(self, record_id: str) -> int:
        """删除与某条记录相关的全部边（记录删除时清理）。"""
        return self.remove_records([record_id])

    def remove_records(self, record_ids: list[str]) -> int:
        """批量删除与指定记录相关的全部边。"""
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM links WHERE from_id IN ({marks}) "
                f"OR to_id IN ({marks})", ids + ids)
            self._conn.commit()
            return cur.rowcount

    def linked_ids(self) -> set[str]:
        """所有参与过关联的记录 ID 集合（单向 UNION 去重，供遗忘保护用）。

        English: All record IDs that participate in at least one edge (deduplicated
        union, used by forgetting protection)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT from_id FROM links UNION SELECT to_id FROM links"
            ).fetchall()
            return {row[0] for row in rows}

    def count_for(self, record_id: str) -> int:
        """某条记录当前的关联边数（双向合计）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM links WHERE from_id = ? OR to_id = ?",
                (record_id, record_id)).fetchone()
            return int(row[0]) if row else 0

    def neighbors(self, record_id: str) -> list[dict]:
        """列一跳邻居，双向查询；同邻居多条关系各占一项。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT to_id, relation, created_at, 'out' FROM links "
                "WHERE from_id = ? "
                "UNION ALL "
                "SELECT from_id, relation, created_at, 'in' FROM links "
                "WHERE to_id = ?",
                (record_id, record_id)).fetchall()
        merged: dict[tuple[str, str], dict] = {}
        for neighbor_id, relation, created_at, direction in rows:
            key = (neighbor_id, relation)
            item = merged.get(key)
            if item is None:
                merged[key] = {"id": neighbor_id, "relation": relation,
                               "direction": direction,
                               "created_at": created_at}
                continue
            if item["direction"] != direction:
                item["direction"] = "both"
            if created_at > item["created_at"]:
                item["created_at"] = created_at
        return sorted(merged.values(),
                      key=lambda x: (x["created_at"], x["id"]), reverse=True)

    def close(self) -> None:
        """关闭连接（作用域 LRU 淘汰时调用）。"""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
