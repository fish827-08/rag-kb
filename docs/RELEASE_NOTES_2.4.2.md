# kb v2.4.2 发布说明（2026-11-03）

审计实测报告后的加固版本，覆盖表格问答、检索、计数、API、安全与 UI。

## 表格问答

- 增加意图门禁：问题没有列名/表格意图词时不劫持（「北京有哪些旅游景点」不再命中城市表）。
- 前缀候选只保留最长命中（问「用户550」不再同时答用户5/55）。
- 项目库与全局库重复导入同一张表时只答一次；`sources` 只包含真正命中的记录。
- 单元格换行转空格；XLSX 同时读缓存值与公式，公式列不再整列丢失。

## 检索

- 新增 `KB_MIN_VECTOR_SCORE`（默认 0.30）：向量原始余弦低于阈值不返回。
- BM25 只剔除 0 分记录（单文档负分仍有效）。
- `type/tag` 过滤提前到 top_k 截断之前；项目加分钳制到 `[0,1]`；关联扩展不突破 top_k。

## 数据一致性与 CLI

- 项目库写/删路径同步 `count.txt`，重启后 healthz / `kb stats` 不再显示 0。
- `update_memory` 先嵌入再删旧，嵌入失败保留旧内容。
- 批量导入同名文件用「父目录/文件名」区分 source。
- `kb stats` / `kb dedup` / `kb ask` 覆盖项目库；目录导入补全 CSV/XLSX/HTML/PPTX。

## API / 安全 / UI

- PATCH 拒绝空内容；422 统一为 `{error,message}`；分页/审计参数范围校验。
- 标签含逗号用 JSON 编码；审计文件名支持 `my.client`；multipart 审计 client=HTTP。
- 非 ASCII API Key 兼容；LLM 运行中途故障返回 502 统一 JSON。
- 单文件上传上限 `KB_MAX_UPLOAD_BYTES`（默认 100MB）。
- 网页入库 SSRF 防护：拒绝回环/私有/链路本地，手动重定向逐跳校验，响应 ≤5MB；
  本地调试可用 `KB_WEB_ALLOW_PRIVATE=1`。
- `C/POSIX` locale 回退 English；移除已弃用的 `locale.getdefaultlocale`。
- `.xls` 明确拒绝并提示另存为 `.xlsx`；空 CSV 错误回显上传文件名。
- 图谱有节点时显示「点击节点」；内部 `kb-monitor`/`kb-dispatch` 不进文档列表。
- 修复切换语言会删除 LLM 模式下拉框的问题。

## 验证

`pytest tests/ -q` → **536 passed, 1 skipped**（2026-11-03 实测，约 3 分钟）。

新增 `tests/test_n41_audit_fixes.py`（21 项）。

## 仍未处理（下一轮）

- BM25 单条写入仍会全量重建；`iter_all` 全量扫描在 10 万条级别仍有性能压力。
- `POST /documents {"path": ...}` 的本地任意文件读取属于本地单用户特性，对外监听时需谨慎。
- MCP 业务错误的 `is_error` 标记与依赖版本区间收敛尚未调整。

> 待人工验证：浏览器切换中英文后 LLM 面板仍完整；网页入库对私有地址返回 400；真实项目库重启后 healthz 计数正确。
