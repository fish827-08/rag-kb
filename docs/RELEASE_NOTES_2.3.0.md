# kb v2.3.0 发布说明（2026-11-03）

本版围绕「人用起来顺」和「跑起来不卡」两件事：浏览器界面、运行时 LLM 配置、批量导入、
双语与超时提示，以及几个会导致卡死/串答案的正确性修复。

## 新增

- **浏览器界面 `/ui`**：单文件静态页，上传文件 / 抓取网页 / 文档列表与删除 / RAG 问答（含来源与分数）。
  - 支持**整文件夹选择**（`webkitdirectory`），自动过滤不支持的格式。
  - **中英双语**（右上角 `EN / 中文`），与 README 双语文档一致。
  - 保存 LLM 配置有成功/失败 toast；问答显示已等待秒数与超时提示。
  - 内联 SVG favicon，浏览器不再请求 `/favicon.ico`（消除 404）。
- **运行时 LLM 配置**：页面里切换 `off / local(Ollama) / auto / cloud`，保存即热重建客户端，无需重启；
  配置写 `kb_data/llm_config.json`（不入库），并提供 `/api/v1/llm`、`/api/v1/llm/config`、
  `/api/v1/llm/ollama/models` 三个接口。
- **批量导入 `kb import`**：一次编码、一次写库、一次 BM25 重建；226 个文件 2337 块从 367s 降到 233s。
- **问答 `top_k` 参数**：默认 5，长文档可调 8~10。

## 修复

- **首次查询卡死**：`runtime.json` 残留 cuda 但 torch 为 CPU 版时，模型会退回在线加载并卡在
  HuggingFace 重试；现在自动回退 cpu，并把在线兜底超时收紧到 10s/30s。
- **答案缓存跨项目串答案**：缓存按「问题向量」命中，会把 A 项目的答案返回给 B 项目；
  现按 `project + top_k` 隔离。
- **云端 LLM 无超时**：默认 600s，现 60s、0 重试。
- **前端无超时/无进度**：现 120s（仅检索）/ 180s（调 LLM），带秒数与加载提示。
- **SSRF 面收窄**：云端 Base URL 拒绝回环/私有 IP 字面量（确需本地服务时设
  `KB_LLM_ALLOW_PRIVATE_BASE_URL=1`）。
- **LLM 配置含 Key 的文件权限**：写入后收紧（POSIX 600，Windows icacls 仅当前用户）。

## 限制（未变）

- 单个 Chroma 集合超过约 800 条后，HNSW 索引重启可能读不回来；大语料请分项目子库（见 PROJECT_GUIDE 6.1）。
- 默认嵌入模型 `bge-small-zh-v1.5`；若库由 `bge-m3` 构建会沿用 m3（冷启动约 23s、RSS ≈2GB）。

## 验证

`pytest tests/ -q` → **487 passed, 1 skipped**（2026-11-03 实测，约 2.5 分钟）。
