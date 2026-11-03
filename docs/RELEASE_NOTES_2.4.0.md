# kb v2.4.0 发布说明（2026-11-03）

## 新增

- **CSV / Excel 表格导入与精准问答**：CSV 用标准库解析，XLSX 用可选依赖 `openpyxl` 逐 sheet 读取；每行入库为 `列名=值 | ...` 文本并打 `table` 标签。`/ask` 会先做确定性表格匹配，例如问「小明的语文成绩」直接返回「小明：语文=95」，**不需要 LLM**；也支持「总分 / 平均」聚合。未命中时仍走原 RAG 流程。
- **知识图谱可视化**：新增 `GET /api/v1/graph?project=&limit=`，返回 memory 节点与 `links.db` 中的关联边；Web UI 底部新增「记忆图谱」卡片，用 SVG 环形布局渲染，点击节点查看内容。文档 / 网页 / 表格不会进入图谱。
- **RAG 与记忆分离**：衰减与新鲜度只作用于 `type=memory`；`doc_chunk` / `web_chunk`（含表格）不参与遗忘与降权，只在显式删除时移除。文档列表新增 `kind=doc/web/table` 标识。

## 修复与体验

- **`/favicon.ico` 404**：新增图标端点（返回内联 SVG），根导航页与 `/ui` 均声明 favicon；鉴权白名单包含该路径。
- **LLM 配置去强绑定**：云端 Base URL / 模型默认值改为空，不再预填 DeepSeek 过时地址与模型名；页面支持任意 OpenAI 兼容端点。
- **保存提示**：保存 LLM 配置成功 / 失败均有 toast 与行内提示；配置了 Key 却缺 Base URL/模型时返回 422 并说明原因。
- **整文件夹批量导入**：保留 `webkitdirectory` 选择，自动过滤不支持的扩展名。

## 兼容性

- 新增可选依赖 `openpyxl`（仅 `[ingest]` / `[full]` extra）；不安装时 CSV 不受影响，XLSX 会返回安装指引。
- REST 契约向后兼容：仅新增 `/api/v1/graph`；`/ask` 仅新增可选 `table` 字段与 `llm="table"`。
- 旧 `KB_DEEPSEEK_*` 环境变量仍兼容读取；但不再有内置默认值。

## 自动化验证

`pytest tests/ -q` → **510 passed, 1 skipped**（2026-11-03 实测，约 2.5 分钟）。

新增测试：

- `tests/test_n37_tables.py`：CSV/XLSX 解析、逐行入库、精准问答、聚合
- `tests/test_n38_graph.py`：图谱节点/边、文档块不入选、项目合并、limit
- `tests/test_n39_separation.py`：衰减/新鲜度只作用 memory、`forget` 默认 memory-only
- `tests/test_n40_ui_fixes.py`：favicon 200、LLM 默认不绑定 DeepSeek、云端缺参保存失败、UI 含图谱

> 待人工验证：在浏览器中上传真实成绩表并提问；打开 `/ui` 观察图谱布局与节点点击；使用真实 Ollama / 云端服务商保存配置。
