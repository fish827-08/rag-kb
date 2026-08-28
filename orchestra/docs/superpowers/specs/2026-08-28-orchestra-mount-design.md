# orchestra 挂载常驻改造设计（B 线 v3）

- 日期：2026-08-28
- 状态：设计书（B5-1 实施依据），依据方案 `../plans/2026-08-28-orchestra-mount-plan.md`
- 载体：`orchestra/mount.py`（新增）+ `orchestra/board.py`（接线）+ 协议三件套改写（B5-2/B5-3）

## 1. 需求

### 1.1 问题

- 当前"唤醒式 + 禁止轮询"：worker/designer 只在人工粘贴唤醒语时行动，无法自动监听新任务。
- 子协调者目前是"按需唤醒 + 常驻 Python 循环"拼合，缺少一个持续挂载、能拆卡/核验/监听的 LLM 协调者。
- 长挂载会令 Agent 上下文持续膨胀，缺少"连续相关任务数"护栏。

### 1.2 目标

- worker/designer 完成任务后进入挂载监听，空闲 15 分钟无新任务自动停机；有新任务即继续，做完回到监听。
- 连续相关任务 ≤5（防上下文膨胀），达上限触发上下文重置。
- 子协调者持续挂载，负责拆卡/分发/核验/监听/仲裁；父协调者按需唤醒向子协调者派活。
- 全部状态按项目规范落 kb；改造独立分支开发。

### 1.3 非目标（YAGNI）

- 不改 kb 核心（A 线）、不动 MCP 工具集（worker 继续用 search_memory/update_memory/write_memory）。
- 不做自动调度进程拉起 agent（选型：Agent 自循环驻留）。
- 不改任务卡六字段结构（主题以 `主题 X` 注入"约束"区，不新增顶层字段）。

## 2. 架构

### 2.1 角色与生命周期

```
parent(不挂载) ──派活(拆卡卡)──▶ subcoordinator(常驻 ttl=0)
                                    ├─拆卡→设计卡 ─▶ designer(挂载 ttl=900)
                                    └─拆卡→实现卡 ─▶ worker(挂载 ttl=900)
```

挂载状态机：`exited → mount → mounted → mount-claim → working → mount-idle → mounted →(空闲满 TTL / 上下文重置 / 手动)→ exited`

### 2.2 数据流（挂载循环）

```
worker 挂载循环:
  mount → 循环:
    查卡(search_memory)
    有卡 → mount-claim(登记主题) → 干活 → 回写 done → comm:done → mount-idle
           streak≥5 时 → 写 summary → unmount(reason=连续相关≥5) → 提示重挂载
    无卡 → 空闲≥TTL ? unmount+停机 : heartbeat + sleep(60s)
```

## 3. 数据模型（kb 记录）

### 3.1 挂载态记录（tag=`mount_state`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `agent` | str | agent 名（worker-1 / designer-1 / subcoordinator） |
| `role` | str | worker / designer / subcoordinator / parent |
| `session_id` | str | 挂载会话 ID（`M-<时间戳>`，重挂载即换新） |
| `mount_status` | str | mounted / working / exited |
| `mounted_at` | str(ISO) | 本次挂载开始时间 |
| `last_heartbeat` | str(ISO) | 最近心跳（机械臂据此判失联） |
| `idle_since` | str(ISO) | 进入空闲时刻；working 时为空串 |
| `ttl` | int | 空闲挂载 TTL 秒（900 默认，0=常驻） |
| `topic_chain` | list[str] | 连续相关主题链（用于 ≤5 判定） |
| `topic_streak` | int | 当前连续相关计数（=len(topic_chain)） |
| `reset_reason` | str | 退出原因（空=正常/手动） |

- 每 agent 一条挂载态记录（按 `agent` 查重；重挂载 PATCH 不重复建）。
- 旧记录无新字段时 `_find_mount` 按 JSON 解析跳过（向后兼容）。

## 4. CLI 设计（board.py 新增 6 子命令）

| 命令 | 说明 |
|---|---|
| `mount <name> --role X [--ttl N]` | 开始/重挂载（mounted，清空主题链，换新 session_id） |
| `heartbeat <name>` | 刷新 last_heartbeat |
| `unmount <name> [--reason R]` | 退出挂载（exited） |
| `mount-status [--role X]` | 一行一 agent：名字/角色/状态/心跳/ttl/streak |
| `mount-claim <name> --topic T` | 领卡登记主题（working，更新链/streak，达 5 打印重置标记） |
| `mount-idle <name>` | 完成任务转回空闲监听（mounted，刷新 idle_since/心跳） |

- 常量（`orchestra/mount.py`）：`TTL_DEFAULT=900`、`TTL_INFINITE=0`、`STREAK_LIMIT=5`、`ROLES=(worker,designer,subcoordinator,parent)`、`MOUNT_STATUSES=(mounted,working,exited)`。

## 5. 连续相关 ≤5 判定

- 卡内"约束"区含 `主题 X`；worker 领卡后调 `mount-claim --topic X`。
- 判定：`topic == topic_chain[-1]` → 追加、streak=len；否则链重置为 `[topic]`、streak=1。
- 达 `STREAK_LIMIT(5)`：mount-claim 输出 `⚠ 连续相关已达 N（≥5），本卡完成后需上下文重置`。
- 重置动作（agent 执行）：写 summary（四类保留标签）→ `unmount --reason 连续相关≥5上下文重置` → `mount` 重挂载（链清零、上下文全新）。

## 6. 父 → 子协调者委派

- 父协调者建卡 `assignee=subcoordinator`（目标=拆卡某需求），子协调者常驻领到后按现状流程拆卡分发。
- 委派卡本身走任务卡状态机，不新增记录类型。

## 7. 机械臂扩展（B5-4）

- `coordinator_loop.py` 每轮增读 mount_state：`last_heartbeat` 超过阈值（默认 5 分钟）且 mount_status≠exited → `comm:dispatch` 告警 `{agent} 挂载失联`。

## 8. 里程碑拆分（每节点 TDD，先测试后实现）

| 节点 | 内容 | 门禁 |
|---|---|---|
| B5-1 | mount.py（数据模型 + 6 CLI）+ board.py 接线 + conftest 补 mount patch + test_mount.py | 标准 |
| B5-2 | worker-prompt.md / designer-prompt.md 挂载循环协议 + 主题链≤5 上下文重置 | 标准 |
| B5-3 | protocol.md 角色表改三层 + 新增 parent-coordinator-prompt.md + 子协调者常驻规约 | 标准 |
| B5-4 | coordinator_loop.py 挂载心跳监测/告警 | 标准 |
| B5-5 | 集成验证 + 真机实验 + USER_GUIDE/PROJECT/ROADMAP 文档同步 | 人工门禁 |

## 9. 测试策略（B5-1 验收测试）

- mount：新挂载写 mount_state（tag/agent/role/ttl/streak=0）；重挂载 PATCH 不重复建且链清零；非法 role/ttl 报错不发请求。
- heartbeat：刷新 last_heartbeat；未挂载报错；exited 报错。
- unmount：置 exited + reset_reason；未挂载报错。
- mount-status：一行一 agent；空表提示；按 role 过滤；非 JSON 记录跳过。
- mount-claim：首主题 streak=1；同主题连续递增；不同主题重置为 1；达 5 打印重置标记；未挂载报错。
- mount-idle：转 mounted + 刷新 idle_since/心跳；未挂载报错。

## 10. 配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| TTL（worker/designer） | 900 | 空闲挂载秒数，CLI `--ttl` 覆盖 |
| TTL（subcoordinator） | 0 | 常驻不超时 |
| STREAK_LIMIT | 5 | 连续相关任务上限 |
| 心跳间隔 | 60s | 挂载循环 sleep 时长（协议约定） |
| 失联阈值（机械臂） | 300s | last_heartbeat 超此值告警 |

## 11. 风险与避坑

| 风险 | 规避 |
|---|---|
| 挂载态记录脏数据 | JSON 解析失败跳过 + 警告；按 agent 去重 |
| 挂载/心跳高频写库 | 心跳 60s 一次，PATCH 单条记录 |
| 主题链误判 | 主题非空校验；不同主题即重置链 |
| 与现有测试冲突 | conftest 仅增 mount patch；registry 不改（零回归） |
