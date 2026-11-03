# kb 审计加固设计（2026-11-03，v2.4.2）

> 依据用户实测审计报告，修复一批测试未覆盖的隐藏问题。基线仍为 v2.4 设计文档。

## 1. 表格问答收紧（tables.py）

- **意图门禁**：问题必须命中列名，或包含表格意图词（成绩/分数/总分/平均/电话/地址等），
  否则不判为表格问题（「北京有哪些旅游景点」不再被城市表劫持）。
- **最长匹配**：多个前缀候选（用户5/55/550）只保留最长命中值对应的行。
- **去重**：同内容行（项目库 + 全局库重复导入）只答一次。
- **来源收敛**：`sources` 只包含真正命中的记录。
- **数据完整性**：单元格换行转空格；XLSX 同时读取缓存值与公式，公式列不再整列丢失。

## 2. 检索相关性（retriever.py）

- 新增 `KB_MIN_VECTOR_SCORE`（默认 0.30）：向量原始余弦低于阈值直接丢弃。
- BM25 只剔除 0 分记录（单文档语料可能出现负分，负分仍有效）。
- `type` / `tag` 过滤提前到 top_k 截断之前。
- 项目加分结果钳制到 `[0,1]`；关联扩展只填补 top_k 剩余名额。

## 3. 计数与写入一致性

- 所有项目库写/删路径调用 `scopes.sync_count`，重启后 `healthz`/`kb stats` 不再显示 0。
- `update_memory` 改为「先嵌入再删旧」，嵌入失败不会丢失旧向量。
- 批量导入同名文件用「父目录/文件名」区分 source。
- CLI `stats` / `dedup` / `ask` 覆盖项目库；目录导入补全表格/网页/Office 扩展名。

## 4. API 契约与健壮性

- PATCH 记忆拒绝空内容；`RequestValidationError` 统一为 `{error,message}`。
- 分页参数 `limit/offset`、审计 `days/limit` 做范围校验。
- `tags` 含逗号时用 JSON 编码，旧逗号格式保持兼容。
- 审计文件名解析不再按 `.` 切割（支持 `my.client`）。
- multipart 上传审计 client 记为 `HTTP`。
- 非 ASCII API Key 用 UTF-8 bytes 常量时间比较。
- LLM 运行中途故障返回 502 统一 JSON。
- 单文件上传上限 `KB_MAX_UPLOAD_BYTES`（默认 100MB）。

## 5. 安全与体验

- 网页入库 SSRF 防护：仅 http(s)，拒绝回环/私有/链路本地/保留地址；手动跟随 ≤3 次重定向并逐跳校验；响应体 ≤5MB；本地调试用 `KB_WEB_ALLOW_PRIVATE=1`。
- `C` / `POSIX` locale 回退 English，移除 `locale.getdefaultlocale` 弃用调用。
- `.xls` 由可选依赖 `xlrd` 解析；损坏/非 BIFF 文件报 `UnsupportedFormatError`，不再提示另存为 `.xlsx`。
- 空 CSV 错误回显上传文件名而非临时文件名。
- 图谱有节点时显示「点击节点」提示，不再显示「暂无数据」。
- 内部 `kb-monitor` / `kb-dispatch` 记录不进「已入库文档」列表。
- UI 语言切换：`llm.modeLabel` 移入独立 `<span>`，不再替换整个 label 导致下拉框被删除。

## 6. 验收

- `tests/test_n41_audit_fixes.py`：表格劫持/前缀/去重、检索阈值/过滤顺序、项目计数、更新安全、
  tags/audit/API/SSRF/locale/内部记录/UI 结构。
- 全量回归：`pytest tests/ -q` → **536 passed, 1 skipped**。
