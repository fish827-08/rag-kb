# kb Monitor Agent 设计（含 DispatchAgent 扩展）

> 本文件记录 kb 本地监控 Agent（N18）与 DispatchAgent 异常调度扩展（TASK-0048 / TASK-0049）。
> MonitorAgent 主体设计见 0015 设计书；本文件聚焦 DispatchAgent 扩展节。

## 1. MonitorAgent 现状（N18 / TASK-0021 去常驻）

- 零常驻：线程寄生 kb serve 进程，Event 控制停止；
- 去常驻：默认 `monitor_enabled=False`，由 `POST /api/v1/monitor/summary` 按需触发；
- 强制本地：`service.llm.chat(prefer="local")`，绝不路由云端；
- 单轮逻辑唯一：`run_once_summary(service, max_tokens)` 被 `MonitorAgent._run_once` 与端点共用；
- 异常兜底：LLM 不可用 / 摘要为空记 WARNING 跳过本轮，不崩溃。

## 2. DispatchAgent 扩展（TASK-0048 / TASK-0049）

### 2.1 目标

在监控单轮后，检测协作快照异常；异常非空时由本地 LLM 组织 ≤100 字中文调度摘要，
写入 `comm:dispatch` 新频道（`source=kb-dispatch`），供协调者 / worker 关注。
无异常不写（避免刷屏）。

### 2.2 异常检测四规则（TASK-0048，`_detect_anomalies` 纯函数）

输入 `snapshot = {"tasks": [...], "feedbacks": [...]}`，
输出异常列表 `[{"type", "task_id", "detail"}]`。

边界：恰好等于阈值不触发，超过才触发；时间解析失败的卡跳过不报错。

1. **pending_low**：卡池 pending 数 < 2 时告急（`task_id=None`）
2. **claimed_stale**：claimed 卡超 30 分钟未动（`task_id=卡ID`）
3. **done_unverified**：done 状态卡超 5 分钟未被核验（协调者循环可能挂了）
4. **fbk_open_stale**：open FBK 超 10 分钟无人裁决（`task_id=FBK-ID`）

### 2.3 调度写入（TASK-0049，`run_once_dispatch`）

- 收集结构化快照 `build_anomaly_snapshot(service)`（taskboard / feedback 卡首行解析）；
- `_detect_anomalies` 检测；异常为空 → 返回 None，不调 LLM 不写；
- 异常非空 → 本地 LLM（`prefer=local` / `max_tokens=300`，think:false 由 `llm.chat` 护栏）
  组织 ≤100 字中文摘要；
- 写 `comm:dispatch`（`source=kb-dispatch`）；
- 异常检测失败 / LLM 不可用 / 摘要为空 → 记 WARNING 兜底返回 None，
  不崩溃、不影响监控主流程（`run_once_summary` 返回值不受本函数影响）。

### 2.4 接入点

- `run_once_summary` 新增 `dispatch_enabled` 参数（默认 True），写完 comm:monitor 后调 `run_once_dispatch`；
- `MonitorAgent.__init__` 新增 `dispatch_enabled`，`_run_once` 透传；
- `kb/config.py` 新增 `dispatch_enabled: bool = True`（环境变量 `KB_DISPATCH_ENABLED`）；
- `POST /api/v1/monitor/summary` 端点传 `dispatch_enabled=kb.settings.dispatch_enabled`；
- `create_app` 启动 `MonitorAgent` 时传 `dispatch_enabled`。

### 2.5 护栏与约束

- 复用 `llm.chat` 护栏（think:false / max_tokens 300 / prefer=local）；
- 无异常不写（避免 comm:dispatch 刷屏）；
- 异常检测失败只记 WARNING，不影响监控主流程；
- 纯函数 `_detect_anomalies` 不调 LLM 不发 HTTP，输入输出可单测；
- 提示词预算：system 固定 + user 壳（异常列表），远低于 1500 token 硬上限。
