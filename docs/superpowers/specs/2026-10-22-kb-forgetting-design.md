# kb 遗忘机制升级设计

> 状态：✅ 有效（2026-10-22）。本文件是 `kb forget` 维护命令的唯一设计依据；
> 未涉及部分继续沿用 `2026-08-23-kb-memory-service-design.md`、
> `2026-10-20-kb-v2.1-design.md` 与 `2026-10-22-kb-memory-links-design.md`。

## 1. 目标与非目标

**目标**：让陈旧的 memory 能被安全、完整地清理，而不是只清掉全局库里的半套数据。

- 扫描范围覆盖全局库 + 全部项目子库，也可限定单个项目或只看全局；
- 默认只清理 `memory` 记录；文档 chunk / 网页 chunk 需显式纳入，避免误删知识库；
- 删除统一走服务层批量路径，级联清理 Chroma、BM25、稀疏索引、关联边与计数缓存；
- 保留 dry-run 默认与二次确认，并新增删除前本地备份、标签保护、关联保护、天数下限。

**非目标**：

- 不做自动 / 定时硬删除——遗忘是不可逆操作，必须保留人工确认；
- 不开放 REST / MCP 删除入口——维护型破坏操作只保留 CLI；
- 不改变既有软遗忘（decay / freshness 排序）语义；
- 不引入新常驻服务或新依赖。

## 2. 候选筛选规则

- 陈旧判定沿用 `find_stale_records`：`last_accessed` 为空时用 `created_at`，
  严格大于阈值天数才入选；`--days` 最小为 1。
- 作用域：
  - 默认：全局库 + 磁盘上所有项目子库；
  - `--project NAME`：只扫描该项目的子库；
  - `--global-only`：只扫描全局库；与 `--project` 互斥。
- 类型：默认仅 `memory`；`--include-documents` 时一并纳入 `doc_chunk` / `web_chunk`。
- 标签保护：`--keep-tag TAG` 可重复，命中任一标签的陈旧记录不入选。
- 关联保护：默认跳过仍有关联边的陈旧记忆（知识图谱中的节点不轻易删除）；
  `--include-linked` 解除该保护。
- 统计输出按「过期记录总数 / 文档跳过 / 标签保护 / 关联保护 / 候选」口径展示，
  候选表增加「作用域」列。

## 3. 删除路径

新增 `KBService.batch_delete_records(record_ids, project=None, agent_id, client) -> int`：

1. 按 `project` 或记录所在作用域分组（同 ID 去重）；
2. 每个作用域批量执行：Chroma 删除 → BM25 `remove_many` → 稀疏索引移除 →
   `LinkStore.remove_records` → 同步 `count.txt`；
3. 每个作用域写一条 `forget` 审计事件（`hits` = 删除数量）；
4. 返回实际删除数，不存在或类型不匹配的记录跳过。

CLI 仅在用户确认后执行删除；删除前默认把候选完整内容写入
`<data_dir>/forget-backups/forget-YYYYmmdd-HHMMSS.jsonl`（JSON Lines，UTF-8），
备份失败则中止删除。`--no-backup` 可显式关闭。

## 4. CLI 参数

```text
kb forget --stale --days 90 [--dry-run/--no-dry-run]
          [--project NAME | --global-only]
          [--include-documents] [--keep-tag TAG]... [--include-linked]
          [--backup/--no-backup]
```

- `--dry-run` 默认开启，只输出候选与统计；
- `--no-dry-run` 需输入 `yes` 二次确认，随后备份并删除；
- 删除按作用域汇总输出实际删除数量。

## 5. 兼容性

- `find_stale_records(records, days, now)` 原签名与返回值保持不变；
  新增可选关键字过滤 `types` / `keep_tags` / `keep_ids`。
- 既有 `kb forget --stale --days 90` 用法保持 dry-run 行为不变；
  memory-only 是唯一的行为收紧（文档 chunk 不再被默认清理）。
- `delete_memory` / `delete_document` 语义不变；批量删除只新增入口。

## 6. 验收清单

1. `find_stale_records` 的类型 / 标签 / ID 过滤单测通过；
2. 全局 + 两个项目子库的候选收集测试通过，`--project` / `--global-only` 正确限定；
3. 默认跳过文档 chunk，`--include-documents` 后纳入；
4. 标签保护与关联保护生效，`--include-linked` 可解除；
5. 批量删除后：记录消失、BM25 移除、关联边清理、审计出现 `forget`、count.txt 同步；
6. 删除备份文件内容完整且可解析；
7. 相关回归通过（`tests/test_n23_cli.py`、`tests/test_memory_links.py`、新增测试）。
