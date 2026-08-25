"""本地监控 Agent（N18）：常驻 kb serve 进程内，周期收集协作快照并调用本地
LLM 生成中文摘要写入 comm:monitor（0015 设计书）。

- 零常驻：线程寄生 kb serve 进程（同 KBWatcher 模式），Event 控制停止；
- 强制本地：进程内直调 service.llm.chat(prefer="local")，绝不路由云端；
- 只读监控 + 结论写入：只读任务板/registry/交流窗，摘要写 comm:monitor 新频道；
- 异常兜底：LLM 不可用/快照异常记 WARNING 跳过本轮，不崩溃不影响服务主流程。
"""
import json
import logging
import threading
import webbrowser
from datetime import datetime

from kb.models import Record

logger = logging.getLogger("kb.monitor")

# 快照行数上限（YAGNI 不配置化，设计书第 5 节）
_MAX_TASK_PENDING = 4      # 待办最多 4 行
_MAX_TASK_CLAIMED = 4      # 进行中最多 4 行
_MAX_TASK_DONE = 1         # 已完成 1 行摘要
_MAX_TASK_VERIFIED = 1     # 已核验 1 行摘要
_MAX_WORKER = 8            # worker 最多 8 行
_MAX_COMM = 5              # 交流窗最近 5 条
_DEFAULT_INTERVAL = 10     # 轮询间隔（分钟）；非法配置回退默认

# 固定 system 提示：只陈述快照事实，≤100 字中文
_SYSTEM_PROMPT = ("你是 kb 系统的本地监控助理。基于协作快照，用 ≤100 字中文"
                  "总结当前协作状态：待办与进行中任务、各 worker 状态、近期"
                  "重要事件。只陈述快照中的事实，不评价、不编造。")


def _registry_line(rec) -> str | None:
    """registry 记录 content 为 JSON（worker/model/status/last_seen），解析失败返回 None。"""
    try:
        data = json.loads(rec.content)
    except (ValueError, TypeError):
        return None
    return (f"{data.get('worker', '?')} {data.get('model', '?')} "
            f"{data.get('status', '?')} {data.get('last_seen', '?')}")


def build_snapshot(service) -> str:
    """进程内收集紧凑快照文本（【任务板】/【worker】/【交流窗】三段，行数受限）。

    任务板：pending/claimed 各 ≤4 行 + done/verified 各 ≤1 行；
    worker：registry ≤8 行；交流窗：comm:*（排除 comm:monitor 自身）最近 ≤5 条。
    """
    # 任务板段
    pend, claimed, done, verified = [], [], [], []
    records, _ = service.list_records(tag="taskboard", limit=1000)
    for rec in records:
        first = rec.content.split("\n", 1)[0].strip()
        parts = first.split(" ", 2)
        if len(parts) < 3 or not parts[0].startswith("TASK-"):
            continue
        status = parts[1]
        if status == "pending" and len(pend) < _MAX_TASK_PENDING:
            pend.append(first)
        elif status == "claimed" and len(claimed) < _MAX_TASK_CLAIMED:
            claimed.append(first)
        elif status == "done" and len(done) < _MAX_TASK_DONE:
            done.append(first)
        elif status == "verified" and len(verified) < _MAX_TASK_VERIFIED:
            verified.append(first)
    task_lines = pend + claimed + done + verified
    task_section = "\n".join(task_lines) if task_lines else "（无）"
    # worker 段
    worker_lines = []
    records, _ = service.list_records(tag="registry", limit=1000)
    for rec in records:
        line = _registry_line(rec)
        if line is not None:
            worker_lines.append(line)
    worker_section = "\n".join(worker_lines[:_MAX_WORKER]) if worker_lines else "（无）"
    # 交流窗段（排除 comm:monitor 自身，按 updated_at 降序取最近 5 条）
    records, _ = service.list_records(limit=1000)
    comms = [r for r in records
             if any(t.startswith("comm:") and t != "comm:monitor" for t in r.tags)]
    comms.sort(key=lambda r: r.updated_at, reverse=True)
    comm_lines = [f"{r.source or '?'}: {r.content.replace(chr(10), ' ')[:60]}"
                  for r in comms[:_MAX_COMM]]
    comm_section = "\n".join(comm_lines) if comm_lines else "（无）"
    return (f"【任务板】\n{task_section}\n"
            f"【worker】\n{worker_section}\n"
            f"【交流窗】\n{comm_section}")


def build_messages(snapshot: str, time_str: str) -> list[dict]:
    """组装提示词（system 固定 + user 壳填快照）；预算 ≤700 token < 1500 硬上限。"""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",
         "content": f"协作快照（{time_str}）：\n{snapshot}\n请输出 ≤100 字中文摘要。"},
    ]


def maybe_open_dashboard(settings, opener=None) -> None:
    """KB_DASHBOARD_AUTOOPEN 开关：开启时 serve 启动自动打开看板一次。"""
    if settings.dashboard_autoopen:
        (opener or webbrowser.open)(settings.dashboard_url)


class MonitorAgent:
    """本地监控线程：周期收集快照 → 本地 LLM 摘要 → 写 comm:monitor。

    生命周期同 KBWatcher：start() 启动守护线程，stop() 置 Event 并 join。
    """

    def __init__(self, service, interval_minutes: int = _DEFAULT_INTERVAL,
                 startup_run: bool = True, max_tokens: int = 300):
        """service 为 KBService（需暴露 list_records/add_memory/llm）。"""
        self.service = service
        self.interval = interval_minutes if interval_minutes >= 1 else _DEFAULT_INTERVAL
        self.startup_run = startup_run
        self.max_tokens = max_tokens
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 生命周期 ----
    def start(self) -> None:
        """启动监控守护线程。"""
        self._thread = threading.Thread(target=self._run, name="kb-monitor",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """置停止 Event 并等待线程退出（至多 5s）。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ---- 主循环 ----
    def _run(self) -> None:
        """主循环：startup_run 时启动即跑一轮，此后按 interval 分钟轮询。"""
        if self.startup_run:
            self._run_once()
        while not self._stop.wait(self.interval * 60):
            self._run_once()

    def _run_once(self) -> None:
        """单轮：复用 run_once_summary 共享逻辑（TASK-0021 去常驻，不重写）。"""
        run_once_summary(self.service, max_tokens=self.max_tokens)


def run_once_summary(service, max_tokens: int = 300) -> Record | None:
    """单轮按需摘要（TASK-0021）：快照→本地 LLM→写 comm:monitor。

    返回写入的 Record（content=摘要，id 供端点回显）；LLM 不可用/摘要为空/
    任何异常记 WARNING 兜底并返回 None（不崩溃、不写空摘要）。MonitorAgent
    主循环与 POST /api/v1/monitor/summary 均复用本函数，保证单轮逻辑唯一。
    """
    try:
        snapshot = build_snapshot(service)
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        messages = build_messages(snapshot, time_str)
        summary = service.llm.chat(
            messages, max_tokens=max_tokens, prefer="local")
        summary = (summary or "").strip()
        if not summary:
            logger.warning("monitor 摘要为空，跳过本轮")
            return None
        record = service.add_memory(summary, tags=["comm:monitor"],
                                    source="kb-monitor")
        logger.info("monitor 已写入 comm:monitor 摘要%s",
                    f" id={record.id}" if record is not None else "")
        return record
    except Exception as exc:
        logger.warning("monitor 本轮失败（跳过不刷屏）: %s", exc)
        return None
