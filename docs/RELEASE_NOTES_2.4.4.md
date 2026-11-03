# kb v2.4.4 发布说明（2026-11-04）

v2.4.3 的收尾版本：补上 MCP 侧的文档列表/删除能力，并把 README 改成“场景先行”的写法。

## 新增

### MCP 也能展示与删除 RAG 知识库

此前 MCP 能写记忆、检索、导入文档，但只能通过 REST 查看/删除已入库资料。v2.4.4 新增两个 MCP 工具（共 13 个）：

| 工具 | 作用 |
|---|---|
| `list_documents(project?)` | 按 source 聚合列出已入库文档/网页/表格（含 chunks / chars / kind） |
| `delete_document(source, project?)` | 按 source 删除该资料的全部 chunk，并级联清理 BM25/向量索引 |

这样 Claude Code / Cursor / TraeWork 等客户端在 MCP 通道里就能完成“写入 → 展示 → 删除”的闭环，不必再切 REST。

### README 增加两个真实场景

- 测试/实施：把一堆 CSV/Excel 整文件夹导入，离线问「用户550的分数」；不配置 LLM 也能命中单元格。
- Agent/脚本：REST 与 MCP 的对照表，覆盖记忆和 RAG 知识库的增删查。

## 验证

- 全量：**544 passed / 1 skipped / 101.06s**（Windows，Python 3.10，ChromaDB 1.5.9）。
- 新增 MCP 用例：工具注册集合（13 个）+ 文档列表/删除/空 source 校验。
- v2.4.3 的分页读取与 680 条 × 5 次重启回归继续保留。

详细测试记录见 [TEST_REPORT_2.4.4.md](TEST_REPORT_2.4.4.md)。

## 升级

```bash
pip install -U "kb-memory[ingest]"
```

无需迁移数据。MCP 工具名变化只增不减，旧客户端不受影响。
