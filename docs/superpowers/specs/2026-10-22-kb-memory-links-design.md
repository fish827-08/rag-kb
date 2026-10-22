# kb 轻量记忆关联（Memory Links）设计

> 状态：✅ 有效（2026-10-22）。本文件是「记忆与记忆之间的关系」的唯一设计依据，
> 与 v2.1 设计并列；未涉及部分继续沿用 `2026-08-23-kb-memory-service-design.md`
> 与 `2026-10-20-kb-v2.1-design.md`。

## 1. 目标与非目标

**目标**：用最小成本让记忆之间可显式关联，并在检索时做一跳扩展，帮助 Agent
找回「换了说法、但语义相关」的记忆。

**非目标**：
- 不引入图数据库或新常驻服务（遵守零额外服务约束）；
- 不做 LLM 自动抽取实体/关系，不构建全自动知识图谱；
- 不替代向量 + BM25 检索，只在基础命中之上做有界增强；
- 不支持文档 chunk / 网页 chunk 参与关联（仅 memory 记录）。

## 2. 数据模型

每个作用域（全局库 / 各项目子库）一个独立 `links.db`（SQLite，标准库 `sqlite3`），
与 Chroma、BM25 同目录：

```sql
CREATE TABLE links (
    from_id    TEXT NOT NULL,
    to_id      TEXT NOT NULL,
    relation   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, relation)
);
CREATE INDEX idx_links_to ON links(to_id);
```

- 关系白名单：`related` / `supersedes` / `depends_on` / `part_of` / `contradicts`；
  默认 `related`。非法关系拒绝。
- 边有方向（便于表达 `supersedes` / `depends_on`），遍历邻居时双向查询；
  列表结果带 `direction`（`out` / `in` / `both`）。
- 同一 `(from_id, to_id, relation)` 幂等，重复写入不报错（`created=false`）。
- 禁止自关联；两端必须存在、必须是 memory 类型、必须位于同一作用域。
- 单条记录关联数上限 `KB_LINK_MAX_PER_RECORD`（默认 50），防止单点变成巨型枢纽。
- 删除记忆、按 source 删除文档时同步清理相关边；邻居读取时跳过已不存在的记录。

## 3. 服务接口

`KBService` 新增：

| 方法 | 说明 |
|---|---|
| `link_memories(from_id, to_id, relation="related", project=None, ...)` | 建边；返回边信息与 `created` |
| `unlink_memories(from_id, to_id, relation=None, project=None, ...)` | 删边（任一方向；relation 为空删除该对全部关系）；返回删除数 |
| `list_links(record_id, project=None, relation=None)` | 列邻居（含 content/type/tags/direction） |

错误统一抛 `kb.links.LinkError(code, message)`：
`NOT_FOUND`（404）、`SELF_LINK` / `INVALID_RELATION` / `MEMORY_ONLY` /
`CROSS_SCOPE` / `LIMIT_REACHED`（422）。

## 4. 对外接口

### REST

| 方法与路径 | 说明 |
|---|---|
| `POST /api/v1/memories/{record_id}/links` | body `{"to_id", "relation"}`，record_id 为起点 |
| `GET /api/v1/memories/{record_id}/links?project=&relation=` | 列邻居 |
| `DELETE /api/v1/memories/{record_id}/links/{to_id}?project=&relation=` | 取消关联 |

### MCP（工具总数 8 → 11）

- `link_memory(from_id, to_id, relation="related", project=None)`
- `unlink_memory(from_id, to_id, relation=None, project=None)`
- `list_links(record_id, relation=None, project=None)`

失败返回 `{"error": <code>, "message": ...}`；成功返回与 REST 同构的 JSON。

## 5. 一跳检索扩展

`KBService.search()` 在基础结果之后执行（`ask` 复用 search，自动生效）：

1. 取基础结果前 3 条 memory 记录作为种子；
2. 查各自作用域的一跳邻居，跳过已在结果中的 ID、非 memory、缺失记录；
3. 遵守 `type` / `tag` 过滤；
4. 邻居分数 = 种子分数 × `KB_LINK_EXPAND_SCORE_RATIO`（默认 0.5）；
5. 总数上限 `top_k + KB_LINK_EXPAND_LIMIT`（默认 3），合并后按分数排序；
6. 扩展项附 `via: {"id": 种子ID, "relation": 关系}`，基础项不带 `via`。

开关 `KB_LINK_EXPAND_ENABLED`（默认开）。无关联数据时零行为变化。

## 6. 配置

| 键 | 默认 | 说明 |
|---|---|---|
| `KB_LINK_EXPAND_ENABLED` | `true` | 检索一跳扩展开关 |
| `KB_LINK_EXPAND_LIMIT` | `3` | 每次检索最多追加的邻居数 |
| `KB_LINK_EXPAND_SCORE_RATIO` | `0.5` | 邻居分数相对种子的比例 |
| `KB_LINK_MAX_PER_RECORD` | `50` | 单条记录关联边上限 |

## 7. 验收清单

- [ ] 建边/列边/删边；双向可见且方向正确；重复建边幂等；
- [ ] 自关联、非法关系、非 memory、跨作用域、越界上限均被拒绝；
- [ ] 删除记忆后相关边清理，不再出现在邻居列表；
- [ ] 检索自动扩展一跳邻居并带 `via`；开关关闭或 `type` 不匹配时不扩展；
- [ ] 项目子库的关联与全局库物理隔离；
- [ ] REST 三个端点与 MCP 三个工具可用；工具总数更新为 11；
- [ ] 全量回归通过，无关联数据时既有检索结果零变化。
