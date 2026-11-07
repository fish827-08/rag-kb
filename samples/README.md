# samples — 模型迁移验收样例数据

这组样例用于验证 v2.5.0 的「迁移嵌入模型不丢数据」能力，覆盖多种数据类型，
不只 Excel：

| 文件 | 类型 | 验证点 |
|---|---|---|
| `memories.json` | Agent 记忆 | 记忆写入与检索 |
| `knowledge/kb-product-guide.md` | Markdown 文档 | 语义检索、关键词检索 |
| `knowledge/faq.txt` | 纯文本文档 | 关键词检索 |
| `knowledge/retrospective.html` | HTML 文档 | 解析管道与来源标注 |
| `knowledge/settings.json` | JSON 文档 | 非结构化文本解析 |
| `knowledge/meeting-minutes.docx` | Word 文档 | docx 解析 |
| `tables/student-scores.csv` | CSV 表格 | 同名消歧（一班/三班王五）、相似姓名 |
| `tables/inventory.xlsx` | 多 sheet Excel | 按表名消歧（华东仓/华南仓） |

## 一键验收

先启动服务（另开一个终端）：

```powershell
python -m kb serve
```

再运行验收脚本（默认把数据导入独立项目 `verify-migration`）：

```powershell
# 只验证入库与检索
python scripts/verify_model_migration.py --skip-migrate

# 入库 → 迁移到 bge-m3 → 迁移后复检
python scripts/verify_model_migration.py --target BAAI/bge-m3

# 迁移完成后顺手删除旧模型数据
python scripts/verify_model_migration.py --target BAAI/bge-m3 --delete-old
```

脚本会依次校验：

1. 3 条记忆 + 7 个文件全部入库（每个文件都必须产生记录）；
2. 语义检索、关键词检索、记忆检索、DOCX/HTML/JSON 命中；
3. CSV 表格问答：`一班王五的数学成绩` → 92（同时存在三班王五，验证不会串班）；
4. 多 sheet 表格问答：`华东仓 螺丝刀 数量` → 120（华南仓同名商品不串）；
5. 迁移后记录数不变、模型维度变化（512 → 1024）、上述检索与问答全部重新通过；
6. 可选：删除非活跃模型数据。

全部通过时退出码为 0，并打印 `结果：PASS`。

## 手工验证

- Web UI：右上角「设置」里选择项目 `verify-migration`，可在「已入库文档」看到全部样例；
- 问答框勾选「仅检索（不调用 LLM）」，输入上面的表格问题即可看到结构化答案；
- 「设置 → 嵌入模型」可观察迁移进度、各模型数据量与删除按钮。

> 迁移期间是只读窗口：写入返回 409，读取与关键词检索继续，语义检索临时降级为 BM25。
