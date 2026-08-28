# agent-orchestra 协议总纲

版本：v1.7（2026-08-28，B5 挂载常驻：父/子协调者分层 + worker/designer 挂载监听）｜ v1.6（2026-08-26，B3 成本管控）｜ v1.5（2026-08-26，skill 化双 skill 节 + 调度监测/designer 拆卡）｜ v1.4（2026-08-26，反馈节点）｜ v1.3（2026-08-26，worktree 隔离）｜ v1.2（2026-08-26）｜ v1.1（2026-08-24）｜ 依据：docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md + 总线 ROADMAP.md B1 规划

## 1. 角色

| 角色 | 载体 | 挂载方式 | 职责 |
|---|---|---|---|
| 用户 | 人 | — | 发起需求、唤醒父协调者、终验 |
| 父协调者 | 一个 AI 会话 | 不挂载（按需唤醒） | 面向用户接收高层目标 → 派活给子协调者 → 终验汇报（协议见 parent-coordinator-prompt.md） |
| 子协调者 | 一个 AI 会话 | 持续挂载（常驻） | 拆卡、分发、监听、核验、仲裁、合并推送（确定性动作交机械臂；协议见 coordinator-prompt.md） |
| 设计者 | 一个 AI 会话 | 挂载 15 分钟 | 写设计书与验收测试草案、评审交付；**不写业务实现代码**（协议见 designer-prompt.md） |
| worker | 其他 AI 会话（任意模型） | 挂载 15 分钟 | 领卡、执行、回写（协议见 worker-prompt.md） |

> 四角色编制（父协调者→子协调者→设计者/workers）2026-08-28 生效（B5 挂载常驻，详见 §15）。子协调者拆技术方案、定验收、拆细卡；设计者写设计书与验收草案；worker 实现；父协调者面向用户、只派活与终验。

## 2. 任务卡

kb 记录（tag=taskboard），首行状态行 + 六字段：

TASK-0003 pending worker-1 | 标题
目标：…（≤300 字符）
输入：…（≤300）
约束：…（≤200）
验收：…（≤200）
结果：…（≤1000，worker 回写）

> "约束"字段可含 `分支 task/TASK-NNNN`、`配额 simple|medium|complex`、`主题 X`（供挂载连续相关≤5 判定，见 §15）。

## 3. 状态机

pending → claimed → done → verified（终态）
                  → failed → verified（终态）
done/failed 可被协调者打回 → pending

## 4. 硬纪律（全体 agent）

1. 单卡单轮：默认一次唤醒只处理一张卡；用户发"继续"且仍有可领卡时按批量模式连续处理（上限 5 轮，见 §9）
2. 禁止轮询：回合结束即待命，不主动重复查询
3. 禁止超范围：只做卡片"目标"内的事
4. 字符上限：结果 ≤1000，执行摘要 ≤200
5. 超时：claimed 超 30 分钟无 done → 协调者打回 pending

## 5. 任务分支模式（v1.1 新增；v1.3 增补 worktree 隔离）

- 协调者建卡时在"约束"字段注明分支名：`分支 task/TASK-NNNN`；分支由协调者预建
- **worktree 物理隔离（v1.3 治本，杜绝并发串扰）**：每张有分支的任务卡在独立目录 `rag-kb/.worktrees/TASK-NNNN` 内工作：
  - `board.py worktree setup TASK-NNNN`：在 `.worktrees/TASK-NNNN` 检出 `task/TASK-NNNN` 分支（worktree 独占该分支，主工作区无法再 checkout → 天然防串扰）；分支不存在 / 目录已注册 / 目标目录脏（非空）时拒绝
  - `board.py worktree enter TASK-NNNN`：打印进入路径，worker 在该目录内开发、测试与提交
  - `board.py worktree clean TASK-NNNN`：清理目录（提交保留在分支上，可再 setup 重建）
  - `.worktrees/` 已被 `.gitignore` 忽略
- worker 在 worktree 内开发并提交（**禁止直接提交 main**）；卡内未指定分支时按旧模式工作、不提交，留给协调者
- worker 回写 done 时在"结果"中注明分支提交哈希
- 协调者核验通过后：`git merge --no-ff task/TASK-NNNN` 合入 main → verify → 推送 → 删分支 → `worktree clean` 对应卡目录
- 并行各卡分支零文件交集（拆卡时保证），合并冲突即打回

## 6. 服务重启管控（v1.1 新增，铁律）

**kb 服务（`python -m kb serve`）是全员共享记忆，重启窗口内所有人无法写库。**

1. worker **无权重启**服务：需重启（如改了 kb 代码需生效）时，在卡内"结果"写明 `申请重启：原因`，然后结束回合等待
2. 协调者收到重启申请：确认无其他 worker 处于 claimed 中途（有则等其回写）→ 停止服务 → 重启 → `healthz` 验证 → 在交流窗发 `comm:system 服务已重启（时间/原因）`
3. 重启期间所有 worker 不得写库，收到协调者通知后方可继续
4. worker 发现服务不可用（连接失败）：不自行拉起服务，回写 failed 说明后待命

## 7. 交流窗（comm: 频道，v1.1 新增）

worker 有权把**重要过程信息**写入 kb 供其他 agent 检索（用现有 write_memory，标签以 `comm:` 开头）：

| 频道标签 | 用途 |
|---|---|
| `comm:done` | 任务完成的关键结论（谁/哪张卡/产出物路径） |
| `comm:issue` | 遇到的问题与风险（阻塞点/影响面） |
| `comm:test` | 待测试项 / 测试结果 |
| `comm:system` | 系统级事件（重启/环境变更，协调者专用） |
| `comm:feedback` | 反馈节点结论（异议/风险/澄清的裁决结果，B2） |
| `comm:dispatch` | DispatchAgent 监测播报（卡池告急/claimed 滞留/done 积压/反馈悬置，v1.5） |

纪律：交流窗只写**结论级**信息（≤300 字符），不写过程流水账；与任务卡重复的信息只进卡不进窗。

## 8. 查卡方式

- worker：`search_memory("TASK pending {我的名字}")` + `search_memory("TASK claimed {我的名字}")`（中断恢复优先续做 claimed）
- 协调者：`board.py status`（一行一卡，不读整卡）
- 全员状态了解：`search_memory("comm:")` 按频道检索

## 9. 批量模式（v1.2 新增）

- 触发：用户对 worker 本回合回复"继续"且仍有可领的 pending 卡 → 连续领卡执行
- 上限：单次唤醒累计最多 5 轮（含续做的 claimed 卡）
- 保险丝：达轮次上限（5 轮）或时间窗（30 分钟，先到为准）即停止待命
- 停止：无 pending 卡立即停止，不空转、不轮询
- 交流窗：每卡完成即写 comm:done（结论级 ≤300 字符）
- 默认仍是单卡单轮；批量是"继续"下的增量行为，不改变单卡原子语义

## 10. 代码结构（包化分层，2026-08-26 起）

orchestra 包化分层（方案一），board.py 按职责拆分为多模块，依赖方向单向：

| 模块 | 职责 |
|---|---|
| `board.py` | 仅 CLI 入口：argparse 子命令调度（status/add/claim/show/verify/new-worker/register/workers/report/list-comm/watch/worktree），内部 `from cards import ...` 等按模块导入 |
| `client.py` | kb REST HTTP 客户端：`KB_BASE` / `_request` / `BoardUnavailable`；仅标准库 urllib，依赖最底层 |
| `cards.py` | 卡片模块：纯函数（`LIMITS`/`STATUSES`、`render_card`/`parse_header`/`check_limits`/`_fmt_time`/`_next_task_id`）+ 任务卡 CRUD（`cmd_status`/`cmd_add`/`cmd_claim`/`cmd_show`/`cmd_verify`/`cmd_list_pending`/`cmd_new_worker`，含 `--docs` 文档同步清单与核验硬门禁） |
| `registry.py` | worker 注册表：`REGISTRY_TAG` 常量、`cmd_register`/`cmd_workers`/`_now_iso`；经 `client._request` 读写 tag=registry 记录 |
| `comm.py` | 交流窗：`COMM_CHANNELS`/`COMM_TEXT_LIMIT` 常量、`cmd_report`/`cmd_list_comm`/`_comm_tag`/`_truncate`；时间格式化复用 `cards._fmt_time` |
| `worktree.py` | git worktree 隔离：`cmd_worktree_setup`/`cmd_worktree_enter`/`cmd_worktree_clean`（TASK-0025 治本分支串扰）；仅标准库 subprocess/pathlib |
| `watch.py` | 终端看板：`_watch_frame` 渲染（worker 段 + 卡段 + **open 反馈卡段**（B2/TASK-0035，无 open 不显示） + 可选交流窗段）、`cmd_watch` 前台轮询（`--once` 单轮） |
| `feedback.py` | B2 反馈卡（TASK-0032/0033）：`FBK-NNNN` 编号（tag=feedback 关联 TASK-NNNN）、add/list/show/**decide**、三类型必附字段校验（objection 必附替代方案等）、结果状态机 open→accepted/rejected、分节点配额硬门禁（precheck/milestone 各 2 轮、单任务总 5 轮封顶，超限拒绝新卡转仲裁）、裁决后结论级写 `comm:feedback` 归档 |

- 依赖方向：`board.py → 各模块 → client.py`；底层只有 client.py 依赖 urllib
- 对外命令接口零变化：`python orchestra\board.py <子命令>` 照旧（board.py 内 import 各模块）
- 新增模块的职责与搬移范围以 `orchestra/docs/superpowers/specs/2026-08-26-orchestra-packaging-design.md` 为准

## 11. 反馈节点（v1.4 新增，B2）

三节点：执行前预审 precheck / 执行中里程碑 milestone / 完成后复盘 review。
反馈写为 kb 记录（tag=feedback，关联目标卡），结论级同步 `comm:feedback`。

| 类型 | 必附字段 | 判定 |
|---|---|---|
| objection 方案异议 | 摘要 + 替代方案 | 无替代方案直接驳回 |
| risk 风险阻塞 | 摘要 + 阻塞点 + 影响面 | 阻塞当前节点，等裁决 |
| clarify 需求澄清 | 摘要 + 澄清问题 | 回答后继续 |

- 配额：precheck ≤2 轮、milestone ≤2 轮、单任务总上限 5 轮；超限转协调者仲裁
- 铁律：无替代方案的异议直接驳回，杜绝只否定不建设
- 阻塞规则：目标卡存在 open 状态反馈时，协调者不得将其 verified

## 12. Skill 化（B1.7，2026-08-26 新增；v1.5 补全要素）

两类角色均已包装为 Trae skill：仓库源在 `orchestra/skills/`，安装副本落本机 `C:\Users\<用户>\.trae-cn\skills\`，普通复制（非链接）。

| 要素 | `orchestra-worker` | `orchestra-coordinator` |
|---|---|---|
| 用途 | worker（执行者）：被唤醒后到 kb 任务板领卡、执行、回写、待命 | 协调者：拆卡分发、核验合并、重启管控与接力状态维护 |
| 仓库源 | `orchestra/skills/orchestra-worker/SKILL.md` | `orchestra/skills/orchestra-coordinator/SKILL.md` |
| 安装路径 | `C:\Users\<用户>\.trae-cn\skills\orchestra-worker\SKILL.md` | `C:\Users\<用户>\.trae-cn\skills\orchestra-coordinator\SKILL.md` |
| 唤醒方式 | 用户说"你是 worker-N，开始工作"，或要求领任务/查任务卡 | 用户说"你是协调者"、粘贴唤醒提示词，或要求核验/合并/拆卡/派活/汇报任务板状态；也可直接调 skill |
| 完整规约 | `orchestra/worker-prompt.md` | `orchestra/coordinator-prompt.md` |

- SKILL.md 只含 frontmatter（name/description）+ 精简上岗流程与硬纪律；详细规则一律以对应规约文件为准（正文指向它，不复制大段）
- 规约更新后需同步修订对应 SKILL.md 并重装副本（仓库版与安装副本保持逐字一致）
- 对外协作接口零变化：skill 仅是唤醒入口，任务卡/分支/交流窗/反馈机制照旧

## 13. 调度监测与 designer 拆卡（v1.5 新增）

### 13.1 三层反馈闭环

| 层 | 载体 | 职责 | 节奏 |
|---|---|---|---|
| 监测层 | DispatchAgent（qwen3:4b，寄生 kb serve） | 只监测播报，不做派发决策；跑四条异常检测规则，异常写 `comm:dispatch` | 每 5 分钟 |
| 执行层 | 常驻协调者循环（`coordinator_loop.py`） | 自动核验 done 卡（open FBK 检查→merge→pytest→verify→push→清理）；failed 打回 pending | 每 60 秒 |
| 裁决层 | 主协调者对话 AI（面向用户） | 深度核查、异常处置决策、合并冲突/测试失败仲裁、重启审批 | 人工唤醒 |

- DispatchAgent 复用 `kb/monitor.py` 快照（`build_snapshot`）+ `_detect_anomalies` 纯函数；强制本地 qwen3:4b，不路由云端，不直接改任务板状态
- 协调者循环不拆卡（卡池告急时由 DispatchAgent 播报，子协调者补卡）

### 13.2 四条检测规则（`_detect_anomalies`）

| 规则 | 触发条件 | 输出 type |
|---|---|---|
| 卡池告急 | pending 卡数 < 2 | `pool_low` |
| claimed 滞留 | claimed 卡超 30 分钟未更新（按 `updated_at`） | `claimed_stale` |
| done 积压 | done 卡超 5 分钟未核验（协调者循环可能挂了） | `verify_backlog` |
| 反馈悬置 | open FBK 超 10 分钟无人裁决 | `fbk_pending` |

每条异常输出结构化 dict：`{type, task_id, detail}`；无异常返回空列表。纯函数不调 LLM/HTTP。

### 13.3 子协调者拆卡职责（v1.7 调整：拆卡自 designer 移交子协调者）

- 拆卡归子协调者：从 ROADMAP 待办（叶子节点）或父协调者委派需求，拆成可执行细卡，直接写入 pending 池（`board.py add`）
- 拆卡粒度：一卡一任务、五字段齐、并行卡零文件交集（同 coordinator-prompt 拆卡原则）；卡内"约束"标注 `主题 X`（供 worker 挂载连续相关≤5 判定）
- 安全兜底：拆出的卡靠 precheck 预审（§11）+ 协调者循环自动测试双保险；有问题走反馈卡 objection/risk
- 设计者（designer）只写设计书与验收测试草案，不再拆卡；子协调者据设计书拆实现卡

## 14. 成本管控纪律（v1.6 新增，B3 第一批）

无状态 Agent + 有状态知识库：上下文不堆在 agent 里，靠 kb 检索召回；四机制落地自 `docs/superpowers/specs/2026-08-26-b3-cost-control-design.md` §4.1/4.2/4.3/4.5。

### 14.1 分阶段上下文切片（spec §4.1）

- 三节点（precheck/milestone/review，即 §11 反馈节点）各自独立上下文窗口，是切片的天然边界
- 切换节点即清空 agent 上下文，仅从 kb 检索本节点所需：任务卡 + 本节点反馈卡 + 相关 summary
- 协议层纪律（worker/designer prompt 按节点加载），不引入代码组件

### 14.2 滚动窗口（spec §4.2）

- agent 上下文仅保留近 3 轮原文（当前轮 + 前 2 轮）
- 更早轮次原文不保留，只留 summary 摘要；需要时 `search_memory` 检索召回
- prompt 纪律 + summary 记录，不做自动截断代码（agent 自律，协调者核验）

### 14.3 动态轮次配额（spec §4.3，细化 §11）

| 复杂度 | precheck | milestone | 总上限 | 适用场景 |
|---|---|---|---|---|
| simple | 1 | 1 | **3** | 单文件小改、文档更新、冒烟 |
| medium | 2 | 2 | **5** | 常规功能卡（默认，与 B2 一致） |
| complex | 2 | 3 | **8** | 多模块/设计评审/跨子系统 |

- 复杂度由拆卡者按"改动文件数/子系统数/是否含设计决策"标注，在卡内"约束"注明（如 `配额 complex`）；未注明默认 medium
- 超限处理：强制截断转协调者仲裁（复用 §11 B2 仲裁机制，不另建）
- review 复盘不计配额（沉淀性，同 B2）；目标卡存在 open 反馈时不得 verified 的阻塞规则照旧（§11）
- §11 固定配额（2/2/5）= 本表 medium 档，向下兼容：未标注复杂度的卡按 medium 执行

### 14.4 自动增量沉淀（spec §4.5）

- 每 2 轮（或节点结束/任务中断时）把关键结论写 summary（tag=summary，含保留标签与来源轮次），写完即清空 agent 上下文 → 中断可从 summary 恢复
- **保留标签强制不压缩**：决策、参数、验收标准、阻塞点四类必须原样保留（阻塞点/决策的重要来源是 §11 反馈卡）
- 摘要后校验：回读 summary 检查四类标签是否齐全，缺失则重生成（最多 1 次）
- 中断恢复：agent 唤醒时先查 claimed 卡的 summary，从摘要续做（不依赖对话历史）

## 15. 挂载常驻（v1.7 新增，B5）

详见设计 `orchestra/docs/superpowers/specs/2026-08-28-orchestra-mount-design.md`。要点：

- **挂载模型**：worker/designer 完成任务后进入挂载监听（自循环），空闲 15 分钟无新卡自动停机；有新卡即继续，做完回到监听。子协调者持续挂载（TTL=0），父协调者不挂载、按需唤醒。
- **挂载循环**：`board.py mount <名> --role <角色> --ttl N` 启动 → 查卡 → 有卡 `mount-claim --topic X`→执行→回写→`mount-idle`；无卡 `heartbeat` + sleep(60s)；空闲满 TTL `unmount` 停机。
- **连续相关≤5**：卡内"约束"的 `主题 X` 用于连续相关链计数；同主题连续达 5 强制上下文重置（写 summary → `unmount` → 重挂载）。
- **父→子委派**：父协调者建"拆卡卡"（assignee=subcoordinator）→ 子协调者拆设计卡(designer)/实现卡(worker) → 核验 → 回写拆卡卡 done → 父协调者汇报。
- **机械臂**：coordinator_loop.py 保留机械核验 + 新增挂载心跳监测（失联告警）。
