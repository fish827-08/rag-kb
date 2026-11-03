# kb Web UI 与长文档问答设计（2026-11-03）

> 本文件是 v2.2 之后新增的 Web UI 与长文档问答设计依据。基础需求/架构/API 见
> `2026-08-23-kb-memory-service-design.md`，项目分库与隔离见
> `2026-10-20-kb-v2.1-design.md`；冲突时以本文件为准。

## 1. 目标与决策

| 主题 | 决策 |
|---|---|
| 面向人群 | 人类用户（浏览器上传、问答），不改变现有 Agent/MCP 通道 |
| 形态 | 单文件静态页 `kb/static/index.html`，原生 HTML/CSS/JS，无构建链、无 CDN、离线可用 |
| 挂载 | `GET /ui`（`StaticFiles(..., html=True)`），根路径 `/` 保留极简导航并加一条入口 |
| 打包 | `pyproject.toml` 增加 `[tool.setuptools.package-data]`，保证 `pip install` 后页面随之分发 |
| 鉴权 | `KB_API_KEY` 非空时放行 `GET /` 与 `GET /ui*`（页面本身无数据），数据端点仍强制鉴权 |
| 问答 | 新增 `top_k` 请求参数，透传到 `KBService.ask`，长文档场景可提高召回条数 |
| 不新增依赖 | `StaticFiles` 来自 FastAPI 已依赖的 Starlette，不引入任何包 |

非目标：不做多轮对话历史、不做流式输出、不做用户账号体系、不做前端框架与打包工具、
不改变 `/ask` 现有单轮语义。

## 2. 现状与缺口

后端能力已完整，缺的只有界面：

| 能力 | 端点 | 位置 |
|---|---|---|
| 文件上传入库 | `POST /api/v1/documents`（multipart 字段 `file`，或 JSON `{"path":...}`） | `kb/api.py` `add_document` |
| 网页入库 | `POST /api/v1/ingest/web`（`{"url":...}`） | `kb/api.py` `ingest_web` |
| 文档列表 | `GET /api/v1/documents?project=` | `kb/api.py` `list_documents` |
| 文档删除 | `DELETE /api/v1/documents?source=&project=` | `kb/api.py` `delete_document_by_query` |
| 混合检索 | `POST /api/v1/search` | `kb/api.py` `search` |
| RAG 问答 | `POST /api/v1/ask` | `kb/api.py` `ask` |

现状仅 `GET /` 返回 `_NAV_HTML`（两个链接的卡片页），无上传、无问答入口。

## 3. 方案

### 3.1 页面结构

`kb/static/index.html` 单文件，左右两栏：

- 左栏「知识入库」：拖拽/选择文件（多选）、URL 入库、目标 project、入库结果与文档列表（可删除）。
- 右栏「问答」：问题输入、`top_k` 选择、「仅检索（不调用 LLM）」开关、答案与来源卡片（含分数、原文片段）。

页面自带：

- 服务状态指示（启动时探 `GET /api/v1/healthz`）。
- 设置区：`KB_API_KEY`（存 `localStorage`，请求带 `X-API-Key`）、默认 project（存 `localStorage`）。
- 错误统一展示后端 `{"error","message"}` 的人话字段。
- 深浅色跟随 `prefers-color-scheme`，不依赖外部字体与图标。

### 3.2 挂载与鉴权

在 `create_app()` 中、鉴权中间件包装之前挂载：

```python
static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="web-ui")
```

鉴权中间件白名单追加 `GET /` 与 `GET /ui` 前缀（仅 GET，仅静态资源，不含数据端点）。
页面需要访问 `/api/v1/*` 时，由用户在设置区填入 Key。

### 3.3 打包

```toml
[tool.setuptools.package-data]
kb = ["static/*"]
```

否则 wheel 内不含 HTML，`pip install kb-memory` 后 `/ui` 404。

### 3.4 问答 top_k

`AskRequest` 增加 `top_k: int = Field(default=5, ge=1, le=50)`；`KBService.ask` 增加同名
参数并传给 `self.search(...)`。默认值保持 5，旧调用方零行为变化。

## 4. 验收标准

1. `GET /ui` 返回 200 且 `text/html`，页面包含 `/api/v1/documents`、`/api/v1/ask` 字样。
2. `GET /` 页面包含指向 `/ui` 的链接。
3. `KB_API_KEY` 非空时：`GET /ui` 与 `GET /` 免 Key 可访问；`GET /api/v1/documents` 无 Key 返回 401。
4. `POST /api/v1/ask` 传 `top_k=0` 或不传时分别返回 422 与使用默认值（LLM 关闭时为 503）。
5. `KBService.ask(top_k=8)` 时检索调用收到的 `top_k` 为 8（单测用替身断言）。
6. 全量 `pytest tests/` 通过，且既有测试无改动。

## 5. 风险与边界

- 上传大文件受 `KB_CHUNK_SIZE` 与嵌入耗时限制，页面需给出「处理中」状态，不做并发上传队列。
- 多文件上传逐个串行提交，避免 Chroma 写锁竞争。
- `/ask` 单轮：页面不做会话历史，问题需自带上下文。
- 未配置 LLM（默认 `KB_LLM_MODE=off`）时 `/ask` 返回 503，页面提示改用「仅检索」。

## 6. 运行时 LLM 配置（2026-11-03 追加）

目标：不改 `.env`、不重启服务，浏览器里就能切换云端 / 本地 Ollama。

| 主题 | 决策 |
|---|---|
| 存储 | `kb_data/llm_config.json`（gitignore，含 Key 时仅本机可读） |
| 优先级 | 运行时文件 > 环境变量 / `.env`（文件存在即覆盖对应字段） |
| 热生效 | 保存后重建 `LLMClient` 并清空 `/ask` 答案缓存，无需重启 |
| 空值语义 | 请求中空串 / 缺省字段 = 不修改；API Key 留空不会被清除 |
| 鉴权 | 配置了 `KB_API_KEY` 时由中间件校验；未配置时仅允许回环地址写 |
| 字段映射 | UI 的 `cloud_base_url/cloud_model/cloud_api_key` → Settings 的可写字段 `llm_base_url/llm_cloud_model/llm_api_key`（`cloud_*` 是只读 property） |

接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/llm` | 当前模式、状态、本地/云端端点、Key 是否已设置（不回明文） |
| POST | `/api/v1/llm/config` | 保存并热生效；非法 mode/URL → 422 |
| GET | `/api/v1/llm/ollama/models?base_url=` | 代理 `{base}/api/tags`，Ollama 不在线返回 `ok=false` |

验收：`tests/test_n35_llm_config.py`（7 项）覆盖默认值、落盘、非法值、Key 不回显、重启沿用、Ollama 不可达。

