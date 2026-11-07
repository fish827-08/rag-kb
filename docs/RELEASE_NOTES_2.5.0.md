# kb v2.5.0 发布说明（2026-11-08）

运行安全、导入速度、检索可读性与嵌入模型迁移。五个重点：**同一数据目录只允许一个实例**、
**表格行不再做向量嵌入**、**检索结果可按文件聚合**、**嵌入模型可在前端选择并迁移**、
**清空数据有了 Web UI 入口**。

## 运行与部署

### 1. 数据目录单例锁（换端口也不能绕过）

旧版本的端口预检只能防止同端口双开。用户用 `--port 8001` 仍可在 8000 已运行时
再起一个进程——两个进程会同时写 Chroma、`bm25_corpus.json`、`links.db`，轻则索引
不同步，重则直接损坏。

现在 `kb serve` 与 `kb mcp`（stdio）启动时都会在 `KB_DATA_DIR/kb.lock` 上抢一把
操作系统级独占锁：

- 锁由内核在进程退出（含强杀）时自动释放，**不会留陈旧锁**；
- 锁文件与持有者信息文件分离，规避 Windows 字节锁阻止其他进程读取的问题；
- 抢锁失败会打印持有者的 pid / 地址 / 启动时间，并提示“换端口不能绕过，
  需要并行请用不同的 `KB_DATA_DIR`”。

### 2. `kb serve --port`

```powershell
python -m kb serve --port 8010
```

端口被占时的报错也会提示这个用法；`KB_API_PORT` 环境变量仍然有效。

### 3. CLI 写入警告

`kb import` / `kb add` / `kb forget --no-dry-run` / `kb dedup --no-dry-run`
在服务运行时会打印警告：命令行直接写库不会更新服务进程的内存 BM25，
新数据的向量可检索，但关键词检索需重启服务。推荐优先用 Web UI / MCP 导入。

### 4. 首次运行的模型下载提示

嵌入模型加载失败且错误信息包含连接/超时/下载特征时，错误文案会追加
“国内可设置 `HF_ENDPOINT=https://hf-mirror.com`”。首次启动约需下载 92MB。

## 导入性能

- **表格行跳过向量嵌入**：表格记录改用常量单位向量入库，不再调用嵌入模型。
  800 行表格的入库从约 24 秒降到约 2 秒。表格查询本来就走确定性路径，
  行级向量检索对同模板数据没有价值，常量向量会被 `KB_MIN_VECTOR_SCORE` 过滤；
- 已知模型维度内置映射（bge-small 512 / bge-base 768 / bge-m3 1024），
  表格入库不需要为拿维度而加载模型；
- `kb import` 输出增加吞吐（块/秒）与“其中表格行 N 块”的说明。

### 2 万行实测（CPU，嵌入模型全程 unloaded）

| 数据 | 解析 | 入库 | 吞吐 | 首次查询 | 稳态查询 |
|---|---|---|---|---|---|
| 800 行 / 4 sheet（5 列） | 0.02s | 1.17s | ~680 行/秒 | — | — |
| 20,000 行 / 40 sheet（5 列） | 3.26s | 15.53s | 1288 行/秒 | 1.21s（建缓存） | 7.5ms（带表名） |
| 20,000 行 / 18 sheet（真实业务表 14 列） | 7.97s | 31.80s | 629 行/秒 | 1.57s（建缓存） | 326ms（不带表名） |

稳态差距来自“是否先按 sheet 收窄”：带表名/班级时只扫目标 sheet；不带时扫描全部
2 万行。首次查询需要从 Chroma 读回并解析表格行建缓存。

复现：`python scripts/bench_table_query.py --ingest --sheets 40 --rows 500`。

### 2 万行暴露并修复的两个问题

1. **Chroma 单次写入上限**：单次 `add` 超过约 5461 条会直接报错。`ChromaStore.add`
   与 `delete` 现在按 2000 条分批，2 万行可正常入库。
2. **表格问答全库重扫**：原先每次问答都从 Chroma 读回并重新解析所有表格行，
   2 万行时单次约 1.3 秒。现在按作用域缓存解析后的行与来源信息，写入时自动失效；
   首次查询建缓存，之后稳态降到 7.5–326ms。

## 检索结果

### 文件级聚合

`POST /api/v1/search` 新增 `group_by_file`：

```json
{
  "query": "Java 三年经验",
  "group_by_file": true,
  "top_k": 5
}
```

返回原有的 `results`，并额外返回 `files`：

```json
{
  "results": [...],
  "files": [
    {
      "source": "张三-简历.pdf",
      "score": 0.87,
      "hits": 3,
      "snippets": [{"content": "...", "score": 0.87}]
    }
  ]
}
```

同一文件的多个 chunk 合并为一个候选，`hits` 是命中块数，`snippets` 保留最多 3 条。

### MCP 表格参数

`search_memory(query, table=true)` 会先尝试确定性表格问答：

```json
{"table": true, "answer": "五班 张芳：数学=95", "sources": [...]}
```

未命中则回退普通检索。Agent 因此也能拿到精确到单元格的答案，而不是原始 chunk。

### 多实体匹配修复

旧规则“全局取最长命中”会把多实体问题里的短名字丢掉。现在只裁剪
“本行最长命中被另一行命中真包含”的行：

- `张芳 / 张芳芳` → 仍只保留张芳芳；
- `王五 / 欧阳娜娜` → 两行都保留。

## 嵌入模型

- 新增 `GET /api/v1/embed`、`POST /api/v1/embed/config`、`POST/GET /api/v1/embed/migrate`
  与 `POST /api/v1/embed/models/delete`；
- Web UI「设置 → 嵌入模型」可选择：默认 bge-small（92MB）、bge-base（约 400MB）、
  bge-m3（约 2.2GB）；
- **至少保留一个模型**：空模型名返回 422，并提示“清空后服务将无法检索”。

### 迁移已有数据（v2.5.0 新增，只读窗口）

每个模型在各自作用域里有独立 collection（`kb_records` / `kb_records__<hash>`），
换模型不再要求清空数据：

- **后台迁移**：逐作用域按批编码并 upsert 到目标 collection，最后做一次镜像删除
  （目标里多出的 id 会被清掉）；
- **迁移期间只读**：所有写路径经写锁门禁直接返回 409 `MIGRATION_IN_PROGRESS`；
  读取继续，语义检索暂时降级为 BM25（`mode` 被强制为 `keyword`）；
- **全量成功才切换**：任何一步失败都保留旧库与旧模型，状态为 `failed`，可直接重试；
- **低内存友好**：迁移前先卸载旧模型，两个模型不会同时驻留；
- **旧数据保留**：迁移完成后旧模型数据仍在磁盘，可在设置面板逐个删除，
  删除当前模型会被拒绝。

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/embed/migrate -H "Content-Type: application/json" -d '{"model":"BAAI/bge-m3"}'
curl http://127.0.0.1:8000/api/v1/embed/migrate
```

## 清空数据

- Web UI「设置 → 数据管理」：**清空当前项目** / **清空全部数据**
- API：`POST /api/v1/admin/reset`，必须提供 `confirm`（项目名，或“清空全部”）
- 同步清理 Chroma、BM25、稀疏索引、关联边与计数；表格缓存通过 epoch 失效
- 确认文本不匹配时返回 400，不删除任何数据

## 验收样例

仓库新增 `samples/` 与 `scripts/verify_model_migration.py`，覆盖记忆、Markdown、纯文本、
HTML、JSON、DOCX、CSV 与多 sheet XLSX，迁移前后各跑一遍检索与表格问答：

```powershell
python -m kb serve
python scripts/verify_model_migration.py --target BAAI/bge-m3
```

脚本会校验记录数不变、目标模型已生效、旧模型数据保留，并可选 `--delete-old` 清理。
样例语料说明见 `samples/README.md`。

## 其他

- 新增 `kb table query <文件> <问题>`：直接查单个 CSV/XLS/XLSX，不建库、不加载模型；
- 新增 `scripts/bench_table_query.py`：可复现的准确率与 P50/P95 基准（支持 `--smoke` / `--json`）；
- 删除 `scripts/windows/start_kb_console.bat`（与 `python -m kb serve` 重复），
  相关文档已更新；
- 全量测试通过（v2.5.0 新增 v2.4.6 功能验收用例与 8 个迁移用例）。
