# orchestra 挂载常驻改造方案（B5）

> 总线定位：B 线（orchestra）运行形态改造——从"唤醒式 + 禁止轮询"升级为"挂载常驻 + 自动监听"。
> 制定：2026-08-28 ｜ 状态：✅ 定稿（用户确认） ｜ 依据：用户 2026-08-28 指令、`orchestra/protocol.md` v1.6、`ROADMAP.md` B 线
> 关键选型（用户 2026-08-28 逐项确认）：① Agent 自循环驻留 ② 按主题链 + 上下文重置 ③ 子协调者拆卡/核验、设计者写设计书 ④ 保留并扩展 coordinator_loop 为子协调者机械臂

## 0. 背景与目标

现状：B 线 orchestra 为唤醒式协作——worker/designer 仅在用户粘贴唤醒语时行动，单卡单轮后待命，"禁止轮询"是硬纪律；协调者为按需唤醒的单个 AI 会话，另有非 LLM 的常驻 Python 循环 `coordinator_loop.py` 机械核验 done 卡。

目标：改造为三层常驻体系——

```
用户 ──(按需唤醒)──▶ 父协调者（不挂载）
                        │ 派活（建"拆卡卡"）
                        ▼
                    子协调者（持续挂载，常驻）
                        ├─拆卡→ 设计卡 ─▶ designer（挂载 15min）
                        ├─拆卡→ 实现卡 ─▶ worker（挂载 15min）
                        └─核验/合并（机械臂兜底确定性动作）
```

## 1. 角色与挂载模型

| 角色 | 挂载方式 | 空闲 TTL | 职责 |
|---|---|---|---|
| 父协调者 | 不挂载，用户按需唤醒 | — | 接收用户高层目标 → 派活给子协调者 → 终验汇报 |
| 子协调者 | 持续挂载（常驻） | ∞（用户手动停） | 拆卡、分发、监听完成情况、核验、仲裁；确定性动作交机械臂 |
| designer | 自循环驻留 | 15 分钟 | 写设计书 + 验收测试草案 |
| worker | 自循环驻留 | 15 分钟 | 领卡实现、回写 |

## 2. 挂载循环（Agent 自循环，核心机制）

```
挂载(role, name, ttl=900s):
  写挂载态(status=mounted)
  循环:
    卡 = 查卡(我的 pending / claimed)            # search_memory
    if 有卡:
       领卡 → 执行 → 回写 done/failed → 写 comm:done
       更新主题链; if 连续相关 ≥5: 上下文重置(写 summary → 退出挂载 → 提示重挂载)
    else:
       if 累计空闲 ≥ ttl: 写挂载态(exited) → 结束会话(停机)
       else: sleep(60s)                         # PowerShell Start-Sleep -Seconds 60
```

- 空闲计时：每轮无卡刷新 `idle_since`；干活时置空。15 分钟 ≈ 15 次查卡，token 开销可控。
- 子协调者同构，仅 TTL=∞，循环体为"查 done/failed 卡→核验；查待拆需求→拆卡"。

## 3. 挂载状态存储（kb 记录）

- 新增挂载态记录（tag=`mount_state`，代码 `orchestra/mount.py`，自包含、不改 registry）：`agent`/`role`/`session_id`/`mount_status`(mounted/working/exited)/`mounted_at`/`last_heartbeat`/`idle_since`/`ttl`/`topic_chain`/`topic_streak`/`reset_reason`。
- 新增 CLI：`mount` / `heartbeat` / `unmount` / `mount-status` / `mount-claim` / `mount-idle`。

## 4. 连续相关 ≤5（上下文管理）

- 任务卡"约束"区标注 `主题 X`（子协调者建卡时注入，缺省不参与计数）。
- 领卡登记主题（`mount-claim --topic X`）：与 `topic_chain` 末尾相同 → streak+1；不同 → 链重置为 `[X]`、streak=1。
- `topic_streak` 达 5 → 上下文重置：写 summary（复用 B3 四类保留标签）→ `unmount`（退出本次挂载）→ 重挂载（新会话、链清零）。

## 5. 父 → 子协调者委派

1. 用户唤醒父协调者提需求。
2. 父协调者建**拆卡卡**（`assignee=subcoordinator`，目标=拆卡该需求）。
3. 子协调者（常驻）领到 → 拆**设计卡**（assignee=designer-1）。
4. designer 领到 → 写设计书 + 验收 → done。
5. 子协调者核验设计 → 拆**实现卡**（assignee=worker-1）。
6. worker 领到 → 实现 → done → 继续监听（受 ≤5 约束）。
7. 子协调者 + 机械臂核验/合并/推送 → 回写拆卡卡 done。
8. 父协调者汇报交付。

## 6. 机械臂扩展（coordinator_loop.py）

- 保留现有机械核验：done 卡 merge → pytest → verify → push → clean。
- 新增：挂载心跳监测（`last_heartbeat` 超时 / agent 失联 → `comm:dispatch` 告警）、挂载看板数据聚合。
- 子协调者 LLM 只做判断类：拆卡、核验结论、异常处置、反馈裁决。

## 7. 分支与存储规范

- 独立分支：`feature/orchestra-mount`（从 `main` 新建，全部改造在此进行，收口后合入 `main`）。
- 方案 → `orchestra/docs/superpowers/plans/2026-08-28-orchestra-mount-plan.md`
- 设计 → `orchestra/docs/superpowers/specs/2026-08-28-orchestra-mount-design.md`
- 协议改写 → `protocol.md` / `worker-prompt.md` / `designer-prompt.md`；新增 `parent-coordinator-prompt.md` + 子协调者常驻规约
- 任务卡 → kb `tag=taskboard`；挂载态 → `tag=mount_state`

## 8. 里程碑（节点门禁 + TDD）

| 节点 | 内容 |
|---|---|
| B5-0 | 方案定稿 + 建分支 + 落盘方案文档 |
| B5-1 | 挂载状态数据模型 mount.py + mount/heartbeat/unmount/mount-status/mount-claim/mount-idle CLI（含测试） |
| B5-2 | 挂载循环协议（worker/designer 自循环 + 15min TTL）+ 主题链≤5 上下文重置（含测试） |
| B5-3 | 子协调者常驻协议 + 父协调者协议 + 父→子委派流程（含测试） |
| B5-4 | 机械臂扩展（挂载心跳监测/告警，含测试） |
| B5-5 | 集成验证 + 真机实验（挂载全链路）+ 文档同步 |

## 9. 风险与规避

| 风险 | 规避 |
|---|---|
| 客户端不支持长驻循环 | B5-5 前先做最小真机验证：worker 自循环 sleep 能否稳定跑满 15min |
| sleep 阻塞与 token 消耗 | 60s 间隔 ≈ 15 次/15min；无卡时不写库、只查 |
| 多 agent 并发写库/串扰 | 复用 worktree 分支隔离 + 挂载心跳去重领卡 |
| 子协调者单点失联 | heartbeat 停了机械臂告警，父协调者负责重启子协调者 |
| 上下文重置丢信息 | 复用 B3 summary 四类保留标签，重置前强制落 summary |
