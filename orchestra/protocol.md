# agent-orchestra 协议总纲

版本：v1.3（2026-08-26，worktree 隔离）｜ v1.2（2026-08-26）｜ v1.1（2026-08-24）｜ 依据：docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md + 总线 ROADMAP.md B1 规划

## 1. 角色

| 角色 | 载体 | 职责 |
|---|---|---|
| 用户 | 人 | 发起需求、开 worker 任务、终验 |
| 协调者 | 一个 TraeWork 任务 | 拆卡、分发、核验、打回、合并分支、重启服务 |
| 设计者 | 一个 TraeWork 任务 | 写设计书与验收测试草案、评审交付；**不写业务实现代码**（协议见 designer-prompt.md，2026-08-24 用户提前激活三角色编制） |
| worker | 其他 TraeWork 任务（任意模型） | 领卡、执行、回写 |

> 三角色编制（协调者→设计者→workers）2026-08-24 生效。设计者拆技术方案定验收，协调者只管调度/合并/重启/汇报。

## 2. 任务卡

kb 记录（tag=taskboard），首行状态行 + 六字段：

TASK-0003 pending worker-1 | 标题
目标：…（≤300 字符）
输入：…（≤300）
约束：…（≤200）
验收：…（≤200）
结果：…（≤1000，worker 回写）

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
| `board.py` | 仅 CLI 入口：argparse 子命令调度（status/add/claim/show/verify/new-worker/register/workers/report/list-comm/watch/worktree），内部 `from client import ...` 等 |
| `client.py` | kb REST HTTP 客户端：`KB_BASE` / `_request` / `BoardUnavailable`；仅标准库 urllib，依赖最底层 |
| `cards.py` | 卡片纯函数（无 HTTP 依赖）：`LIMITS`/`STATUSES` 常量、`render_card`/`parse_header`/`check_limits`/`_fmt_time`/`_next_task_id` |
| `registry.py` | worker 注册表：`REGISTRY_TAG` 常量、`cmd_register`/`cmd_workers`/`_now_iso`；经 `client._request` 读写 tag=registry 记录 |
| `comm.py` | 交流窗：`COMM_CHANNELS`/`COMM_TEXT_LIMIT` 常量、`cmd_report`/`cmd_list_comm`/`_comm_tag`/`_truncate`；时间格式化复用 `cards._fmt_time` |
| （后续） | worktree/watch 拆分进行中，各自模块文件到位后补本表 |

- 依赖方向：`board.py → 各模块 → client.py`；底层只有 client.py 依赖 urllib
- 对外命令接口零变化：`python orchestra\board.py <子命令>` 照旧（board.py 内 import 各模块）
- 新增模块的职责与搬移范围以 `orchestra/docs/superpowers/specs/2026-08-26-orchestra-packaging-design.md` 为准
