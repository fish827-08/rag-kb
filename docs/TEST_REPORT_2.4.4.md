# kb v2.4.4 测试报告（2026-11-04）

## 环境

- Windows 10/11，Python 3.10.8
- ChromaDB 1.5.9，CPU 推理（无 CUDA）
- 嵌入模型 `BAAI/bge-small-zh-v1.5`（512 维）
- 测试代码位于 `rag-kb`（HEAD `fa31305` + 本轮改动）；依赖环境使用独立的测试 venv

## 全量结果

```powershell
python -m pytest tests/ -q
```

| 指标 | 结果 |
|---|---|
| 通过 | **544** |
| 跳过 | 1（Windows 启动项由 `.cmd` 实现，平台门） |
| 失败 | 0 |
| 耗时 | 101.06s |

## 新增/关键用例

| 文件 | 覆盖 |
|---|---|
| `tests/test_n42_restart_stability.py` | `iter_all` 分页边界 0/1/500/501/680；680 条库跨 5 次独立进程重启的 `count` / `iter_all` 一致性（7 项） |
| `tests/test_n12_mcp.py` | MCP 13 个工具注册；`list_documents` / `delete_document` / 空 source 校验 |
| `tests/test_n37_tables.py` | CSV/XLSX 解析、多 sheet、单元格/聚合问答 |
| `tests/test_n41_audit_fixes.py` | 审计加固回归（表格劫持、检索阈值、422、SSRF 等） |

## 说明

- v2.4.3 曾出现“680 条库重启报 HNSW 错误”的排查记录。复测结论：同一负载在非沙箱、同一条终端链路下
  680 条 × 5 次重启全部通过；原报错由外部清理 Chroma journal 引起，不是代码缺陷。本报告不把它计为产品失败。
- 表中耗时为本机实测，不是预算值；失败集合为空。
