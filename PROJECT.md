# PROJECT.md — 项目接力文档

> **给任何接手的 AI 助手**：先读 [AGENTS.md](AGENTS.md)（工作规则与红线），再读本文档（项目状态），然后按第 5 节"接手指南"行动。两份文档读完即可接力，无需追问用户历史。
>
> 最后更新：2026-08-24 ｜ 维护者：文档/测试 AI（按 AGENTS.md 角色分工）｜ 本文件随每个里程碑/节点更新

## 1. 项目是什么（双子系统）

```
┌─────────────────────────────────────────────────┐
│  rag-kb 仓库（本仓库，Windows 本地优先）          │
│                                                 │
│  ① kb —— 本地 Agent 记忆与知识服务（核心产品）    │
│     python -m kb serve 常驻，REST + MCP 双协议    │
│     向 AI 助手提供记忆写入/混合检索/文档网页摄取    │
│                                                 │
│  ② orchestra —— 跨任务 AI 协作系统（实验系统）    │
│     kb 当共享任务板，协调者 AI 拆卡分发            │
│     worker AI（任意模型的其他 TraeWork 任务）领卡   │
└─────────────────────────────────────────────────┘
```

- **设计原则**：本地优先、完全免费、断网可用；显存预算 GPU 6GB（BGE-M3 + qwen3:4b 共 4.3GB）
- **唯一事实来源**：`docs/superpowers/specs/` 下的设计文档（见第 6 节索引）

## 2. 当前进度（看板）

### ① kb 记忆服务 — v1.0.1（生产可用，P1 收口）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1（N1-N6） | BGE-M3 嵌入、ChromaDB 存储、BM25+jieba、RRF 混合检索、CLI | ✅ 2026-08-23 |
| M2（N7-N8） | REST API（memories/search/documents/healthz）、设备四級检测 | ✅ |
| M3（N9-N12） | MCP 8 工具、/ask 智能路由（分类/压缩/缓存/隐私隔离/云端降级） | ✅ |
| M4（N13-N16） | 文档摄取（pdf/docx/md/txt）、网页抓取、目录监听、README | ✅ 2026-08-24 |
| v1.0.1 hotfix | top_k/mode/空内容校验 → 422、MCP 入口校验、SSE charset | ✅ 2026-08-24 |

- 测试：**65 项全绿**（`tests/`）
- 基准：混合检索 26ms、/ask 端到端 ~2s（本地 qwen3:4b）
- tag：`node-01`~`node-16`、`v1.0.0`、`v1.0.1`（均已推 Gitee）
- 已知问题：JSON REST 响应缺 `charset=utf-8`（SSE 已修，JSON 漏了；PowerShell 客户端乱码，Python 不受影响）→ **v1.0.2 候选**

### ② agent-orchestra — MVP（真机实验通过，已合入 main）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| MVP（T1-T10） | board.py CLI 6 命令、协议三件套、orchestra-worker skill、20 项单测 | ✅ 2026-08-24 |
| 真机实验 | worker-1（另一 TraeWork 任务）TDD 实现 list-pending 子命令，全链路 pending→verified | ✅ 2026-08-24 |

- 测试：**24 项全绿**（`orchestra/tests/`）
- 合并提交：`c494e7f`；skill 已装本机 `~\.trae-cn\skills\orchestra-worker\`（**硬链接**，git checkout 后可能断链需重装）
- 并行协作：已支持多 worker（各自 assignee 领卡）；限制 = 手动唤醒、无依赖图、并行卡不共改同一文件

### ③ P2 — 已规划待实施（2026-08-24 人工拍板顺序）

| 优先级 | 功能 | 路线图 |
|---|---|---|
| P2-1 | **日志功能**（整项目统一日志体系） | 见 `docs/superpowers/specs/2026-08-24-logging-design.md` |
| P2-2 | **鉴权**（API Key） | `docs/superpowers/plans/2026-08-24-p2-roadmap.md` |
| P2-3 | **遗忘机制**（衰减/去重/冲突覆盖） | 同上 |
| P2-4 | Web UI（**最后**；方向已定：先 CLI，终端 + agent 交流优先） | 同上 |
| 砍掉 | Qdrant 接入（与本地优先零常驻约束冲突） | — |

## 3. 目录导览

```
rag-kb/
├── AGENTS.md            # AI 工作规则（接力必读第 1 份）
├── PROJECT.md           # 本文档（接力必读第 2 份）
├── README.md            # kb 产品说明（快速开始/MCP 挂载/端点速查）
├── kb/                  # ① kb 服务源码（8 模块，见设计文档 4.2）
├── tests/               # kb 验收测试（65 项）
├── orchestra/           # ② 协作系统（board.py + 协议 + skill + 24 项测试）
│   └── docs/superpowers/  # orchestra 设计文档与实施计划
├── docs/superpowers/    # kb 设计文档（specs）与节点计划（plans）
├── kb_data/             # kb 运行数据（ChromaDB + runtime.json，gitignore）
├── _archive/            # 旧学习项目归档（禁参考其架构）
└── .mcp.json            # 项目级 MCP 挂载（Claude Code/TraeWork 直连 kb）
```

## 4. 关键命令速查

```powershell
# 启动 kb 服务（常驻，先于一切 MCP 挂载）
python -m kb serve                    # 默认 127.0.0.1:8000

# kb 测试 / orchestra 测试（venv 内）
venv\Scripts\python.exe -m pytest tests/ -q            # 65 项
venv\Scripts\python.exe -m pytest orchestra/tests/ -q  # 24 项

# orchestra 任务板（协调者工具）
venv\Scripts\python.exe orchestra\board.py status        # 一行一卡看板
venv\Scripts\python.exe orchestra\board.py add --assignee worker-1 --title ... --goal ... --input ... --constraints ... --acceptance ...
venv\Scripts\python.exe orchestra\board.py verify TASK-0001 --pass|--reject --note 原因
venv\Scripts\python.exe orchestra\board.py new-worker worker-1   # 生成 worker 引导语
```

## 5. 接手指南（新 AI 按此行动）

1. **读规则**：AGENTS.md（角色分工、红线、节点门禁制）
2. **读状态**：本文档 + 当前正在实施的设计文档（见第 6 节索引）
3. **验证环境**：`python -m kb serve` 能启动、两套测试全绿、git 状态干净
4. **确认角色**：你是开发 AI（写实现）还是文档/测试 AI（写验收/核验）？按 AGENTS.md 第 5 节职责行动
5. **协作开发**（orchestra 流程）：
   - 协调者任务：用户提需求 → 你拆卡（board.py add，五字段齐+assignee）→ 用户开 worker 任务粘贴 new-worker 引导语 → worker 领卡执行 → 你核验（diff+测试+真服务复验）→ verify 流转 → 统一提交
   - 你当 worker（被粘贴引导语时）：加载 orchestra-worker skill → 查卡 → 单卡单轮 → 回写 → 停止
6. **接续 P2**：按 `docs/superpowers/plans/2026-08-24-p2-roadmap.md` 的节点顺序，走 TDD + 节点门禁制

## 6. 文档索引（唯一有效集）

| 文档 | 内容 |
|---|---|
| `docs/superpowers/specs/2026-08-23-kb-memory-service-design.md` | kb 唯一设计（需求/架构/API/基准） |
| `docs/superpowers/plans/2026-08-23-kb-dev-nodes.md` | kb 节点计划 N1-N16（全部 ✅） |
| `orchestra/docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md` | orchestra 设计 |
| `orchestra/docs/superpowers/plans/2026-08-24-agent-orchestra-mvp.md` | orchestra 实施计划（全部 ✅） |
| `docs/superpowers/specs/2026-08-24-logging-design.md` | P2-1 日志设计 |
| `docs/superpowers/plans/2026-08-24-p2-roadmap.md` | P2 路线图（本表第 2③节顺序） |
| `orchestra/protocol.md` + `worker-prompt.md` + `coordinator-prompt.md` | 协作协议三件套 |
| `orchestra/EXPERIMENT.md` | 真机实验指引（可复用作下次实验模板） |

## 7. 环境备忘（详见 AGENTS.md 第 7 节）

- torch 2.11.0+cu128（阿里云镜像装的）；HF 走 hf-mirror.com；Ollama 模型在 `D:\ollama_models`
- qwen3 本地必须 `think:false`（Ollama 原生 /api/chat，非 SDK）
- Ollama 必须从开始菜单/托盘启动（沙箱终端拉起会数据库读写失败）
- BGE-M3 与 Ollama 模型均已离线缓存，断网可完整运行（验收标准）
