# PROJECT.md — 项目接力文档

> **给任何接手的 AI 助手**：先读 [AGENTS.md](AGENTS.md)（工作规则与红线），再读本文档（项目状态），然后按第 5 节"接手指南"行动。两份文档读完即可接力，无需追问用户历史。
>
> 最后更新：2026-10-19 ｜ 维护者：文档/测试 AI（按 AGENTS.md 角色分工）｜ 本文件随每个里程碑/节点更新
>
> English: [PROJECT_EN.md](PROJECT_EN.md)

## 1. 项目是什么

```
┌─────────────────────────────────────────────────┐
│  rag-kb 仓库（本仓库，Windows 本地优先）          │
│                                                 │
│  kb —— 本地 Agent 记忆与知识服务                  │
│     python -m kb serve 常驻，REST + MCP 双协议    │
│     向 AI 助手提供记忆写入/混合检索/文档网页摄取    │
└─────────────────────────────────────────────────┘
```

> **交付边界（2026-10-19）**：kb 是唯一产品与对外交付物。
> kb 默认嵌入模型为轻量 `BAAI/bge-small-zh-v1.5`（约 92MB），显式可换 BGE-M3。

- **设计原则**：本地优先、完全免费、断网可用；显存预算 6GB（默认 bge-small；选 BGE-M3 时与 qwen3:4b 共 4.3GB）
- **唯一事实来源**：`docs/superpowers/specs/` 下的设计文档（见第 6 节索引）

## 2. 当前进度（看板）

### kb 记忆服务 — v2.0.0 交付加固（P1/P2 主线持续迭代）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1（N1-N6） | BGE-M3 嵌入、ChromaDB 存储、BM25+jieba、RRF 混合检索、CLI | ✅ 2026-08-23 |
| M2（N7-N8） | REST API（memories/search/documents/healthz）、设备四級检测 | ✅ |
| M3（N9-N12） | MCP 8 工具、/ask 智能路由（分类/压缩/缓存/隐私隔离/云端降级） | ✅ |
| M4（N13-N16） | 文档摄取（pdf/docx/md/txt）、网页抓取、目录监听、README | ✅ 2026-08-24 |
| v1.0.1 hotfix | top_k/mode/空内容校验 → 422、MCP 入口校验、SSE charset | ✅ 2026-08-24 |

- 测试基线：**395 项全绿**（2026-10-19 全量回归；历史：A3.5+A4 新增 68 项：reranker/sparse/eval/perf/cli 五个测试文件，分支 `feature/a-line-remaining`）
- 基准：混合检索 26ms、/ask 端到端 ~2s（本地 qwen3:4b）
- tag：`node-01`~`node-16`、`v1.0.0`、`v1.0.1`（均已推 Gitee）
- v1.0.2：JSON charset 已修复（TASK-0003/0038/0045 交付，含测试断言）
- **A2 鉴权已交付**（N19 ApiKeyMiddleware + N20 客户端自动带 key，TASK-0062/0064，空 key 不鉴权零摩擦）
- **monitor 纯文本模式默认 off**（TASK-0065）：本地无 LLM 完整可用（KB_MONITOR_LLM=off/auto）
- **A3 记忆治理全线交付**（2026-08-28，TASK-0066~0076，spec+双层实现）：
  - 规则层：访问频率衰减（λ=0.02）+ 语义去重（阈值 0.92，409 拦截）+ 新鲜度权重（β=0.05，上限 1.3×）——三者默认关，零行为变化
  - 维护面：`kb forget/dedup` CLI + `/api/v1/governance/stats|config` 端点 + 结构化审计日志（kb/audit.py）
  - 智能层：consolidation 基础框架（kb/consolidation.py + spec，置信度门槛 + human 兜底，默认关）
- **A3.5 检索质量已交付**（2026-08-28，N24-N27，分支 `feature/a-line-remaining`，spec：`2026-08-28-a35-retrieval-quality-design.md`）：
  - N24 交叉重排：kb/reranker.py（bge-reranker-v2-m3）+ 检索管道挂接，`KB_RERANK_ENABLED` 默认关
  - N25 稀疏第三路：kb/sparse.py（BGE-M3 sparse 头直载 + 倒排索引）+ 三路 RRF，`KB_SPARSE_ENABLED` 默认关、失败降级双路
  - N26 评测基准：kb/eval.py + 50 条中文 QA（tests/eval_zh_50.jsonl）+ `kb eval` CLI（Recall@1/@5 + MRR + 分难度）
  - N27 性能：N+1 修复（store.get_many 批量）+ BM25 语料持久化
- **A4 易用性已交付**（2026-08-28，N28，CLI 优先；**Web UI 砍掉**——评估报告定论，spec：`2026-08-28-a4-cli-design.md`）：
  - `kb stats`：类型分布 / 访问热度 top / 陈旧分布（纯读）
  - `kb ask`：终端 RAG 问答，LLM 缺席时降级输出检索命中（退出码 1）
- **v3 记忆全共享已收口**（2026-08-31，spec：`docs/superpowers/specs/2026-08-31-memory-share-refactor-design.md`）：
  - 取消 (client, project) 隔离：所有记忆/知识全共享，跨 agent/任务可读同一份用户偏好
  - client/project 降级为**审计归类与元数据**，不隔离读写（MCP/REST/CLI/检索全部同步）
  - 验证协议定稿：小改动只跑相关测试、核心架构改变才全量（AGENTS.md §0）
  - 测试：395 项全绿（2026-10-19 全量回归，隔离断言已反转测试隔离语义）
- **混合检索融合修复收口**（2026-09-01，N32/N33，回归测试 `tests/test_n32_hybrid_fusion_recall.py` 7 项）：
  - hybrid 默认融合从 RRF 排名融合改为**归一化评分加权融合**（每路 min-max 归一化后按**生效路数**均权，候选池放宽 `max(3×top_k, 30)`）
  - 解决跨语言/专有名词召回：中文 query 查不到英文歌名（Poker Face）、纯英文/日文记忆缺席 BM25 路时不再被 ÷总路数稀释挤出 top_k；同语言场景零行为变化
  - 三语言（中/英/日）模拟写入读取与交叉检索实测通过
- **v2.0.0 交付加固收口**（2026-10-19，面向真实新用户与 Agensi 交付）：
  - 轻量默认：`BAAI/bge-small-zh-v1.5`（约 92MB / 512 维），显式可换回 BGE-M3；embedder 懒加载，healthz 暴露 `embedder.state/error`
  - 失败可诊断：嵌入模型不可用返回 503 `EMBED_MODEL_UNAVAILABLE`（REST/MCP 同语义），不再 500
  - 入库原子性：同 source 重复导入改为“先算向量再替换”，失败不丢旧数据；文档删除支持 URL 查询参数
  - 参数校验：空白搜索/提问 422；`kb eval` 强制隔离治理开关，评测结果可复现
  - Windows 一键脚本：`.env` 端口解析、pythonw 无控制台不卡 GPU 询问、PID+端口+命令行三路停止
  - 独立 skill 包：`skills/kb-memory/` + `scripts/build_skill_package.py`（zip 根 `SKILL.md`，不含测试/密钥），打包校验 7 项
  - 用户视角 E2E：基础 28/28 + 补充 34/34（隔离实例验证鉴权/坏模型/CLI/MCP stdio/skill 包）

### 发展总线 — kb 单主线（2026-08-27 战略调整）

项目总线见 **[ROADMAP.md](ROADMAP.md)**（树形路线+进度，人类入口）。战略调整后单主线：

| 主线 | 当前阶段 | 下一步 |
|---|---|---|
| **kb 记忆服务**（唯一开发主线） | **规划内节点全部交付**：A1/A2/A3 ✅，A3.5+A4 ✅（分支 `feature/a-line-remaining`，N24-N28）；A2.5 仅剩 MCP Registry 提交（待用户操作） | 新需求先修订设计文档再立项（AGENTS.md §6） |
| 支线 | — | 冻结（评估报告建议：不做 Web UI/多用户/商业化；A4 Web UI 已正式砍掉） |

- **战略调整（2026-08-27，依据《评估报告》双报告 + 用户决策）**：kb 为核心产品（"个人开发者的本地记忆 MCP server"）；远期跨 Agent 方向评估 A2A 协议兼容而非自建
- **LICENSE Apache-2.0 已补**（评估报告 P0 最高优先级项，法律合规闭环）
- 运行形态：**monitor 纯文本模式默认 off**——本地无 LLM 完整可用

## 3. 目录导览

```
rag-kb/
├── AGENTS.md            # AI 工作规则（接力必读第 1 份）
├── PROJECT.md           # 本文档（接力必读第 2 份）
├── ROADMAP.md           # 项目发展总线设计书（人类入口，树形路线+进度）
├── README.md            # kb 产品说明（快速开始/MCP 挂载/端点速查）
├── kb/                  # kb 服务源码（见设计文档 4.2；含 reranker/sparse/eval 等 A3.5/A4 模块）
├── tests/               # kb 验收测试（395 项，含 eval_zh_50.jsonl 评测数据集）
├── docs/superpowers/    # kb 设计文档（specs）与节点计划（plans）
├── skills/kb-memory/    # kb 接入规约 skill（Anthropic 开格式、客户端无关；AGENT_PROMPT 的可触发封装，安装见 scripts/README.md）
├── kb_data/             # kb 运行数据（ChromaDB + runtime.json，gitignore）
├── _archive/            # 旧学习项目归档（禁参考其架构）
└── .mcp.json            # 项目级 MCP 挂载（Claude Code/TraeWork 直连 kb）
```

## 4. 关键命令速查

```powershell
# 启动 kb 服务（常驻，先于一切 MCP 挂载）
python -m kb serve                    # 默认 127.0.0.1:8000

# kb 测试（venv 内）
venv\Scripts\python.exe -m pytest tests/ -q            # 395 项
```

## 5. 接手指南（新 AI 按此行动）

1. **读规则**：AGENTS.md（角色分工、红线、节点门禁制）
2. **读状态**：本文档 + 当前正在实施的设计文档（见第 6 节索引）
3. **验证环境**：`python -m kb serve` 能启动、测试全绿、git 状态干净
4. **确认角色**：你是开发 AI（写实现）还是文档/测试 AI（写验收/核验）？按 AGENTS.md 第 5 节职责行动
5. **接续总线**：读 `ROADMAP.md` 确认当前位置，按对应计划节点走 TDD + 节点门禁制

## 6. 文档索引（唯一有效集）

| 文档 | 内容 |
|---|---|
| `ROADMAP.md` | **项目发展总线设计书**（人类入口：树形路线+进度+优先级） |
| `docs/USER_GUIDE.md` | **用户使用手册**（人类用户入口：安装/挂载/FAQ） |
| `docs/superpowers/specs/2026-08-23-kb-memory-service-design.md` | kb 唯一设计（需求/架构/API/基准） |
| `docs/superpowers/plans/2026-08-23-kb-dev-nodes.md` | kb 节点计划 N1-N16（全部 ✅） |
| `docs/superpowers/specs/2026-08-24-logging-design.md` | 日志设计（总线 A1.2，N17-N18） |
| `docs/superpowers/plans/2026-08-24-p2-roadmap.md` | kb 功能路线（总线 A2-A4） |

## 7. 环境备忘（详见 AGENTS.md 第 7 节）

- torch 2.11.0+cu128（阿里云镜像装的）；HF 走 hf-mirror.com；Ollama 模型在 `D:\ollama_models`
- qwen3 本地必须 `think:false`（Ollama 原生 /api/chat，非 SDK）
- Ollama 必须从开始菜单/托盘启动（沙箱终端拉起会数据库读写失败）
- BGE-M3 与 Ollama 模型均已离线缓存，断网可完整运行（验收标准）
