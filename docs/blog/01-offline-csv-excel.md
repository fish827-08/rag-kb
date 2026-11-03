# 不联网也能查表格：把 CSV/Excel 变成可提问的本地资料

做测试和交付的人大概都有这种经历：一个项目几十个用例表、导出报表、配置清单，散在十几个目录里。
想确认“用户550的分数是多少”，要打开 Excel、筛选、跨表找；数据涉及业务字段，又不敢随手传到在线文档。

这篇介绍一个本地小工具 **kb**：把 CSV/Excel 整文件夹丢进去，逐行入库，之后直接用一句话查；
全程离线，不配置大模型也能答表格题。它同时提供 REST 和 MCP，记忆和知识库都能展示、删除。

## 它解决什么

- **离线**：模型和索引都落本机，断网可用；数据不出电脑。
- **轻量**：默认嵌入模型约 92MB，CPU 可跑，不需要显卡。
- **表格优先**：CSV 用标准库解析，XLSX 用 openpyxl 逐 sheet 读；每行变成 `列名=值 | ...` 文本。
  问“用户550的分数”时走确定性匹配，不调用 LLM；问“这批用例里哪些和登录有关”时走向量 + BM25 混合检索。
- **双协议**：同一进程同时暴露 REST 与 MCP，Agent 能读写，脚本也能调。

它不替代 SQL/BI。适合“在一堆表里快速找一个值/一行记录”，不适合做复杂聚合和报表。

## 五分钟上手

```powershell
pip install "kb-memory[ingest]"
python -m kb serve
```

打开 `http://127.0.0.1:8000/ui`，把整个文件夹拖进页面；也可以命令行批量导入：

```powershell
python -m kb import "D:\用例数据" --project smoke
```

然后直接问：

```powershell
python -m kb ask "用户550的分数是多少" --project smoke
# → 用户550：分数=30
```

多 sheet 的 Excel 会按表名消歧：问“五班张芳”只答五班，不会把全年级同名的人混在一起。
问“总分”“平均分”也会做简单聚合；没命中的问题仍走原来的 RAG 流程。

## REST：展示与删除

写入一条记忆：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/memories -Method Post `
  -ContentType 'application/json' -Body '{"content":"冒烟用例必须先登录"}'
```

查看已入库资料（按 source 聚合，标注 doc/web/table）：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/documents
```

删除一份资料的全部 chunk：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/documents/用例.csv" -Method Delete
```

## MCP：让 Agent 自己管知识库

`.mcp.json` 挂载后，客户端会看到 13 个工具。和这次场景直接相关的是：

- `add_document(path, project?)`：导入本地表格/文档
- `list_documents(project?)`：展示知识库里有什么
- `delete_document(source, project?)`：按文件名删除整份资料
- `write_memory` / `search_memory` / `delete_memory`：记忆的增查删

也就是说，Claude Code、Cursor、TraeWork 这类客户端不需要你手动切 REST，就能完成
“导入 → 查看 → 提问 → 删除”的闭环。

## 实测与边界

- 本机（CPU、6GB 显存笔记本）：红楼梦 120 回 + 人物页共 2337 块，批量导入约 233 秒；
  批量导入比逐文件导入快约 36%。
- 680 条项目库跨 5 次进程重启读取一致；全量测试 544 项通过 / 1 项跳过（Windows 平台门）。
- 表格问答不依赖 LLM；非表格问题、且未配置 LLM 时返回 503 并给出配置指引。
- 大批量表格建议用 `kb import` 整目录导入，一次编码、一次建索引，比逐个文件快。

## 适合谁

- 测试/实施/运维：经常面对 CSV、Excel、配置清单，需要离线快速定位一行数据。
- 本地 Agent 用户：想让 Claude Code / Cursor 记住偏好、读写本地资料，又不想把数据上云。
- 小团队：一台 2C2G 的机器就能常驻，不需要向量库服务。

## 链接

- 仓库：https://github.com/fish827-08/rag-kb
- Gitee：https://gitee.com/little-fishy/rag-kb
- 安装：`pip install "kb-memory[ingest]"`
- 发布说明：`docs/RELEASE_NOTES_2.4.4.md`

如果你也在用 CSV/Excel 做测试数据，欢迎说说你最想“一句话查出来”的字段是什么。
