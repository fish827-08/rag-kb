"""CLI 入口：add / search / info / serve / mcp / forget / dedup / stats / ask / eval。

CLI entry: add / search / info / serve / mcp / forget / dedup / stats / ask / eval.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from kb.config import Settings, get_settings
from kb.governance import days_since
from kb.service import KBService

app = typer.Typer()
console = Console()


def _force_utf8_stdio() -> None:
    """强制本进程三个标准流以 UTF-8 编解码，Windows 下把控制台代码页切到 65001。

    背景：Windows 控制台默认代码页 936（GBK）；若 Python 进程按系统编码输出，
    而终端/管道按 UTF-8 解码（或反之），中文即显示为乱码——MCP stdio/SSE
    传输除协议本身外，错误信息、日志、CLI 输出都会踩中。本函数：
    1) reconfigure stdin/stdout/stderr 为 UTF-8（Python 3.7+）；
    2) 设置 PYTHONIOENCODING，令 uvicorn 等子进程继承 UTF-8；
    3) Windows 下 SetConsoleOutputCP/SetConsoleCP(65001)（失败静默）。
    幂等，可重复调用。
    English: Force the three standard streams of this process to UTF-8 and, on Windows, switch the
    console code page to 65001. Windows consoles default to 936 (GBK); when a Python process emits in the
    system encoding while the terminal/pipe decodes as UTF-8 (or vice versa), Chinese turns into mojibake —
    affecting MCP stdio/SSE, error messages, logs and CLI output alike. This: 1) reconfigures
    stdin/stdout/stderr to UTF-8 (Python 3.7+); 2) sets PYTHONIOENCODING so child processes (uvicorn) inherit
    UTF-8; 3) on Windows calls SetConsoleOutputCP/SetConsoleCP(65001) (fails silently). Idempotent."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass  # 非文本流或旧 Python，跳过
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass  # 非交互终端（管道/服务）无控制台，静默即可


# ---- N23a 维护 CLI 候选筛选纯函数（可单测，不依赖 CLI/服务）----

def find_stale_records(records, days: int, now: datetime | None = None):
    """筛选超 N 天未命中的记录（N23a forget --stale）。

    Find records not touched for more than N days (N23a forget --stale).

    last_accessed 为空时用 created_at 替代（governance.days_since 语义）。
    返回 [(record, days_float), ...]，按天数降序。
    """
    if now is None:
        now = datetime.now()
    stale = []
    for r in records:
        d = days_since(r.last_accessed, r.created_at, now=now)
        if d > days:
            stale.append((r, d))
    stale.sort(key=lambda x: x[1], reverse=True)
    return stale


def find_duplicate_pairs(records_with_embeddings, threshold: float = 0.85):
    """筛选相似度 > threshold 的记录对（N23a dedup）。

    records_with_embeddings: [(record, embedding_vector), ...]
    余弦相似度；返回 [(record_a, record_b, similarity), ...]，按相似度降序，无重复对。
    """
    import math
    pairs = []
    seen = set()
    n = len(records_with_embeddings)
    for i in range(n):
        r1, e1 = records_with_embeddings[i]
        for j in range(i + 1, n):
            r2, e2 = records_with_embeddings[j]
            dot = sum(a * b for a, b in zip(e1, e2))
            norm1 = math.sqrt(sum(a * a for a in e1))
            norm2 = math.sqrt(sum(b * b for b in e2))
            if norm1 == 0 or norm2 == 0:
                continue
            sim = dot / (norm1 * norm2)
            if sim > threshold:
                pair_key = tuple(sorted([r1.id, r2.id]))
                if pair_key not in seen:
                    seen.add(pair_key)
                    pairs.append((r1, r2, sim))
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs


def _truncate(text: str, n: int = 50) -> str:
    """内容摘要截断，避免 CLI 输出过长。"""
    return text[:n] + "…" if len(text) > n else text


# ---- N28 CLI 增强（A4 spec §2）：stats 纯函数 + 命令 ----

def compute_type_distribution(records) -> dict[str, dict]:
    """类型分布：{type: {"count": 条数, "pct": 百分比}}（N28 stats）。

    空输入返回空 dict；百分比保留 1 位小数。
    """
    counts: dict[str, int] = {}
    for r in records:
        t = getattr(getattr(r, "type", None), "value", None) or str(
            getattr(r, "type", "unknown"))
        counts[t] = counts.get(t, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {t: {"count": c, "pct": round(c * 100.0 / total, 1)}
            for t, c in sorted(counts.items())}


def compute_hot_records(records, top: int = 5) -> list[tuple]:
    """访问热度 top N：[(record, access_count)] 按 access_count 降序（N28 stats）。

    零计数记录排除（从未访问无热度意义）。
    """
    items = [(r, getattr(r, "access_count", 0) or 0) for r in records]
    items = [(r, c) for r, c in items if c > 0]
    items.sort(key=lambda x: x[1], reverse=True)
    return items[:top]


def resolve_device(settings: Settings, interactive: bool, input_fn=input) -> str:
    """优先级：settings.device 显式非空值 > runtime.json 已存选择 > 交互询问（cuda 可用时）
    > 默认 cpu。interactive=False 时不询问直接 cpu。"""
    # 1. 显式配置最高优先，直接返回（不写文件）
    if settings.device:
        return settings.device
    # 2. runtime.json 已有持久化选择
    if settings.runtime_file.exists():
        try:
            saved = json.loads(settings.runtime_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            saved = {}
        if saved.get("device"):
            return saved["device"]
    # 3. 交互询问（仅 interactive 且 cuda 可用时）；仅此路径写入 runtime.json
    if interactive:
        try:
            import torch
            cuda_ok = torch.cuda.is_available()
        except ImportError:
            cuda_ok = False
        if cuda_ok:
            answer = input_fn("检测到独立显卡，是否启用 GPU 加速？[y/n]")
            device = "cuda" if answer.strip().lower() == "y" else "cpu"
            settings.runtime_file.parent.mkdir(parents=True, exist_ok=True)
            settings.runtime_file.write_text(
                json.dumps({"device": device}), encoding="utf-8")
            return device
    # 4. 默认 cpu（不写文件）
    return "cpu"


def _service() -> KBService:
    return KBService(get_settings())


# ---- serve 启动健壮性：端口预检与 kb.pid 自管理（任务 #2）----

def _pid_file() -> Path:
    """kb.pid 路径：当前工作目录（经 start_kb.bat 启动时即仓库根）。"""
    return Path.cwd() / "kb.pid"


def is_port_in_use(host: str, port: int) -> bool:
    """端口占用预检：getaddrinfo 解析 host 确定地址族（IPv4/IPv6 均支持），
    取首个结果试绑，绑定失败即视为已被占用。

    背景：pythonw 无控制台，端口被占时绑定失败完全静默，进程活着但不监听，
    形成“挂死空壳”。启动前先预检，被占立刻报错退出而非制造空壳。
    无法解析（如地址非法）时保守按“已占用”处理，宁可拒绝启动也不留空壳。
    注意：预检与真正绑定之间存在 TOCTOU 残余窗口（期间被别的进程抢占），
    由 start_kb.bat 的健康轮询兜底，此处只防典型的双实例竞跑。
    """
    import socket
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return True  # 无法解析，保守拒绝启动
    if not infos:
        return True
    family, _, _, _, addr = infos[0]
    with socket.socket(family, socket.SOCK_STREAM) as s:
        try:
            s.bind(addr)
        except OSError:
            return True
    return False


def write_pid_file() -> None:
    """由服务进程自身写入工作区根 kb.pid（当前进程 PID，供 stop_kb.bat 使用）。"""
    _pid_file().write_text(f"{os.getpid()}\n", encoding="utf-8")


def remove_pid_file() -> None:
    """退出时清理 kb.pid；仅当内容确为本进程 PID 时才删除，避免误删另一实例的记录。"""
    pid_path = _pid_file()
    try:
        if pid_path.exists() and \
                pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pid_path.unlink()
    except OSError:
        pass  # 清理失败不阻断退出（stop_kb.bat 另有端口/命令行兜底）


@app.command()
def add(content: str,
        tags: str = typer.Option("", "--tags", help="逗号分隔的标签"),
        source: str | None = typer.Option(None, "--source", help="来源标识"),
        namespace: str = typer.Option("default", "--namespace", help="命名空间"),
        agent: str = typer.Option("default", "--agent", help="Agent 身份（兼容参数，v2 不再参与隔离）"),
        client: str = typer.Option("CLI", "--client", help="来源客户端（默认 CLI）"),
        project: str | None = typer.Option(None, "--project", help="项目名；缺省自动取当前目录名（默认桶用 - 显式指定）")):
    """写入一条记忆（v3：全共享，client/project 仅审计归类，任何客户端/任务均可读）。"""
    svc = _service()
    project = project if project is not None else (Path.cwd().name or "-")
    r = svc.add_memory(
        content, tags=[t for t in tags.split(",") if t] if tags else None,
        source=source, namespace=namespace, agent_id=project or "default",
        client=client, project=project)
    console.print(f"已写入 id={r.id} client={client} project={project or '-'}")


@app.command()
def search(query: str,
           top_k: int = typer.Option(5, "--top-k", help="返回条数"),
           mode: str = typer.Option("hybrid", "--mode", help="hybrid/vector/keyword"),
           agent: str = typer.Option("default", "--agent", help="Agent 身份（兼容参数）"),
           client: str = typer.Option("CLI", "--client", help="来源客户端（默认 CLI）"),
           project: str | None = typer.Option(None, "--project", help="项目名；缺省自动取当前目录名")):
    """混合检索（v3：记忆与知识全共享，无 (client, project) 隔离）。"""
    project = project if project is not None else (Path.cwd().name or "-")
    hits = _service().search(query, top_k=top_k, mode=mode, agent_id=agent,
                             client=client, project=project)
    if not hits:
        console.print("无结果")
        return
    for h in hits:
        console.print(f"[{h['score']:.4f}] ({h['type']}) {h['source'] or '-'} :: {h['content']}")


@app.command()
def info():
    """运行信息。"""
    console.print(_service().stats())


@app.command()
def stats(
    stale_days: int = typer.Option(90, "--stale-days", help="陈旧阈值天数"),
    top: int = typer.Option(5, "--top", help="访问热度显示条数"),
):
    """记忆库统计：概览 / 类型分布 / 访问热度 / 陈旧分布（N28）。

    Memory stats: overview / type distribution / hot records / stale distribution (N28).

    用法：
      kb stats                      # 默认 90 天陈旧阈值，热度 top 5
      kb stats --stale-days 30 --top 10
    """
    svc = _service()
    base = svc.stats()
    records = list(svc.store.iter_all())

    console.print(f"[bold]记忆库统计 (Memory Stats)[/bold]　记录 {base['records']} 条 · "
                  f"device={base['device']} · llm={base['llm']}")

    # 类型分布 / Type distribution
    dist = compute_type_distribution(records)
    if dist:
        t1 = Table(title="类型分布 (By Type)")
        t1.add_column("类型 (Type)", style="cyan")
        t1.add_column("条数 (Count)", justify="right")
        t1.add_column("占比 (Pct)", justify="right")
        for t, d in dist.items():
            t1.add_row(t, str(d["count"]), f"{d['pct']}%")
        console.print(t1)
    else:
        console.print("[dim]类型分布：空库 (Type distribution: empty)[/dim]")

    # 访问热度 top N / Hot records top N
    hot = compute_hot_records(records, top=top)
    if hot:
        t2 = Table(title=f"访问热度 top {len(hot)} (Hot Records)")
        t2.add_column("内容摘要 (Summary)", style="white")
        t2.add_column("命中次数 (Hits)", justify="right", style="yellow")
        t2.add_column("最后命中 (Last accessed)", style="dim")
        for r, c in hot:
            t2.add_row(_truncate(r.content), str(c),
                       r.last_accessed or "-")
        console.print(t2)
    else:
        console.print("[dim]访问热度：暂无命中记录 (Hot records: none)[/dim]")

    # 陈旧分布 / Stale distribution
    stale = find_stale_records(records, stale_days)
    console.print(f"陈旧分布 (Stale)：超 {stale_days} 天未命中 "
                  f"[red]{len(stale)}[/red] 条"
                  + ("（kb forget --stale 可清理）" if stale else ""))


@app.command()
def ask(
    question: str,
    top_k: int = typer.Option(5, "--top-k", help="检索条数"),
    agent: str = typer.Option("default", "--agent", help="Agent 身份（推荐用任务名）"),
    client: str = typer.Option("CLI", "--client", help="来源客户端（默认 CLI）"),
    project: str | None = typer.Option(None, "--project", help="项目名（可选，用于审计文件名归类）"),
):
    """终端 RAG 问答：检索 + 生成，直连 KBService.ask（N28）。

    Terminal RAG Q&A: retrieval + generation, direct KBService.ask call (N28).

    LLM 不可用时仍输出检索命中（标注"仅检索未生成"），退出码 1。
    When the LLM is unavailable, still prints retrieval hits (marked "retrieval only"), exit code 1.
    A 节点 legacy 说明已在 v3（2026-08-31）移除：检索不再按 client/project 隔离。
    """
    from kb.service import LLMDisabledError
    svc = _service()
    console.print("[dim]正在检索与生成…（首次调用需加载模型，请稍候）[/dim]")
    try:
        result = svc.ask(question, agent_id=agent, client=client, project=project)
        console.print(f"\n[bold green]{result['answer']}[/bold green]\n")
        if result.get("sources"):
            t = Table(title=f"来源（{len(result['sources'])} 条 · "
                            f"llm={result.get('llm', '-')}）")
            t.add_column("ID", style="cyan", no_wrap=True)
            t.add_column("分数", justify="right")
            t.add_column("内容摘要", style="white")
            for s in result["sources"]:
                t.add_row(str(s["id"]), f"{s.get('score', 0):.4f}",
                          _truncate(s.get("content", ""), 40))
            console.print(t)
    except LLMDisabledError:
        # LLM 不可用：检索结果仍有价值，输出命中并给出配置指引
        hits = svc.search(question, top_k=top_k)
        console.print("[yellow]LLM 不可用（本地 Ollama 未响应且未配置云端 "
                      "Key），以下为检索结果（仅检索未生成）[/yellow]")
        console.print(f"配置指引：在 .env 设 KB_LLM_MODE=local 并配置 KB_LLM_MODEL=（先用 "
                      "ollama pull 选一个模型，名称以 ollama list 为准），或设"
                      " KB_LLM_API_KEY 启用云端（任意 OpenAI 兼容服务商）")
        console.print("[dim]Setup: set KB_LLM_MODE=local + KB_LLM_MODEL= (ollama pull first, "
                      "name per `ollama list`), or set KB_LLM_API_KEY for cloud "
                      "(any OpenAI-compatible provider).[/dim]")
        if hits:
            t = Table(title=f"检索命中（top {len(hits)}）")
            t.add_column("ID", style="cyan", no_wrap=True)
            t.add_column("分数", justify="right")
            t.add_column("内容摘要", style="white")
            for h in hits:
                t.add_row(h["id"], f"{h['score']:.4f}",
                          _truncate(h["content"], 40))
            console.print(t)
        else:
            console.print("[dim]无检索结果[/dim]")
        raise typer.Exit(code=1)


@app.command()
def serve():
    """启动常驻 REST 服务（python -m kb serve）。"""
    import uvicorn
    from kb.api import create_app, exposure_warning
    s = get_settings()
    # 端口占用预检：先于一切重初始化（设备检测/模型加载），被占立刻中文报错退出，
    # 杜绝“进程活着但不监听”的双实例竞跑空壳（任务 #2）
    if is_port_in_use(s.api_host, s.api_port):
        console.print(f"[red]端口 {s.api_port} 已被占用，可能已有实例在运行。"
                      f"请先停止已有实例（Windows：scripts\\windows\\stop_kb.bat；"
                      f"Linux：systemctl stop kb 或 kill 对应进程），"
                      f"确认端口释放后重试。[/red]")
        raise typer.Exit(code=1)
    # 终端交互式设备检测（显式配置/runtime.json/询问），结果注入 settings
    s.device = resolve_device(s, interactive=True)
    # N29 启动安全警告：非回环监听 + 未鉴权 → 终端醒目横幅（日志侧由 lifespan 记 warning）
    warn = exposure_warning(s.api_host, s.api_key)
    if warn:
        console.print(f"\n[bold red]{'!' * 68}[/bold red]")
        console.print(f"[bold red]{warn}[/bold red]")
        console.print(f"[bold red]{'!' * 68}[/bold red]\n")
    app = create_app(s, enable_watcher=True)
    # kb.pid 由服务进程自身管理：启动前写入（真实存活到此处才写，不再由
    # start_kb.bat 猜写）；无论正常退出还是异常，退出时清理，不留指向死进程的旧 pid。
    write_pid_file()
    try:
        uvicorn.run(app, host=s.api_host, port=s.api_port)
    finally:
        remove_pid_file()


@app.command(name="mcp")
def mcp_stdio():
    """以 stdio 模式运行 MCP 服务器（uvx / Claude Desktop 等客户端直接拉起，
    工具集与 REST 常驻服务完全一致；HTTP 挂载仍用 serve）。"""
    import anyio
    from kb.mcp import create_mcp_server
    from kb.service import KBService
    s = get_settings()
    # stdio 协议流上不可交互：设备跟随显式配置/runtime.json，否则 cpu
    s.device = resolve_device(s, interactive=False)
    svc = KBService(s)
    anyio.run(create_mcp_server(svc).run_stdio_async)


@app.command()
def forget(
    stale: bool = typer.Option(False, "--stale", help="扫描超 N 天未命中的陈旧记忆"),
    days: int = typer.Option(90, "--days", help="未命中天数阈值（默认90）"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run",
                                  help="仅输出候选不删除（默认开启，安全优先）"),
):
    """维护：扫描超 N 天未命中的记忆，dry-run 输出候选，非 dry-run 可删除。

    用法：
      kb forget --stale --days 90 --dry-run        # 预览超90天未命中的记录
      kb forget --stale --days 90 --no-dry-run     # 确认后删除
    """
    if not stale:
        console.print("[yellow]请指定 --stale（当前仅支持陈旧未命中模式）[/yellow]")
        raise typer.Exit(code=1)
    svc = _service()
    records = list(svc.store.iter_all())
    candidates = find_stale_records(records, days)
    if not candidates:
        console.print(f"[green]无超 {days} 天未命中的记录[/green]")
        return
    # 输出候选表
    table = Table(title=f"超 {days} 天未命中的记忆（{len(candidates)} 条）")
    table.add_column("记录ID", style="cyan", no_wrap=True)
    table.add_column("内容摘要", style="white")
    table.add_column("最后命中", style="yellow")
    table.add_column("天数", justify="right", style="red")
    for r, d in candidates:
        table.add_row(r.id, _truncate(r.content),
                      r.last_accessed or f"(创建:{r.created_at[:10]})",
                      f"{d:.1f}")
    console.print(table)
    if dry_run:
        console.print(f"[blue][dry-run] 共 {len(candidates)} 条候选，未删除。"
                      f"加 --no-dry-run 执行删除[/blue]")
        return
    # 非 dry-run：确认后删除
    confirm = typer.prompt(f"确认删除以上 {len(candidates)} 条记录？输入 yes 继续",
                            default="no")
    if confirm.strip().lower() != "yes":
        console.print("[yellow]已取消，未删除[/yellow]")
        return
    ids = [r.id for r, _ in candidates]
    svc.store.delete(ids)
    console.print(f"[green]已删除 {len(ids)} 条记录[/green]")


@app.command()
def dedup(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run",
                                  help="仅输出候选对不修改（默认开启，安全优先）"),
    threshold: float = typer.Option(0.85, "--threshold", help="相似度阈值（默认0.85）"),
):
    """维护：全库扫描相似度超阈值的重复对，dry-run 输出供人工审核。

    用法：
      kb dedup --dry-run                  # 预览相似度>0.85的重复对
      kb dedup --threshold 0.90 --dry-run # 自定义阈值
    """
    svc = _service()
    records = list(svc.store.iter_all())
    if len(records) < 2:
        console.print("[green]记录不足2条，无需去重扫描[/green]")
        return
    # 逐条计算 embedding（复用 embedder）
    console.print(f"[blue]正在扫描 {len(records)} 条记录的相似度…[/blue]")
    records_with_emb = []
    for r in records:
        vec = svc.embedder.embed_texts([r.content])[0]
        records_with_emb.append((r, vec))
    pairs = find_duplicate_pairs(records_with_emb, threshold=threshold)
    if not pairs:
        console.print(f"[green]无相似度 > {threshold} 的重复对[/green]")
        return
    # 输出候选对表
    table = Table(title=f"相似度 > {threshold} 的重复对（{len(pairs)} 对）")
    table.add_column("记录A", style="cyan", no_wrap=True)
    table.add_column("记录B", style="cyan", no_wrap=True)
    table.add_column("相似度", justify="right", style="red")
    table.add_column("A摘要", style="white")
    table.add_column("B摘要", style="white")
    for r1, r2, sim in pairs:
        table.add_row(r1.id, r2.id, f"{sim:.4f}",
                      _truncate(r1.content, 30), _truncate(r2.content, 30))
    console.print(table)
    if dry_run:
        console.print(f"[blue][dry-run] 共 {len(pairs)} 对候选，未修改数据。"
                      f"请人工审核后手动处理（自动合并待后续实现）[/blue]")
        return
    # 非 dry-run：自动合并暂未实现
    console.print("[yellow]自动合并功能暂未实现（N23c 智能层 consolidation）。"
                  "请人工审核候选对后手动删除/合并。[/yellow]")


@app.command()
def audit(
    client: str | None = typer.Option(None, "--client",
                                      help="来源客户端（如 TraeWork / Claude Code / Cursor；缺省=全部）"),
    project: str | None = typer.Option(None, "--project",
                                       help="项目名（如 kb；缺省自动取当前目录名，可显式覆盖）"),
    agent: str | None = typer.Option(None, "--agent",
                                     help="Agent 任务名（兼容旧三段式审计文件；一般不需要）"),
    action: str | None = typer.Option(None, "--action",
                                      help="过滤操作类型：write/search/read/update/delete/ask/ingest"),
    days: int | None = typer.Option(None, "--days", help="最近 N 天（默认全部）"),
    limit: int = typer.Option(100, "--limit", help="条数上限（默认100）"),
):
    """查询记忆存取审计（谁从哪个客户端/项目存了什么/读了什么）。

    读本机 agent-audit/（JSON 行），按 (client, project) 过滤，倒序输出最新在前。
    project 缺省自动取当前目录名（与 CLI 写入时一致）。
    用法：
      kb audit --client TraeWork           # 该客户端全部存取记录
      kb audit --client TraeWork --project kb   # 指定客户端+项目
      kb audit --client TraeWork --action write --days 7 --limit 20
    """
    svc = _service()
    if project is None:
        project = Path.cwd().name or "-"
    items = svc.query_access_audit(client=client, project=project,
                                   agent=agent, action=action,
                                   days=days, limit=limit)
    title = f"存取审计（{len(items)} 条）"
    if client:
        title += f" client={client}"
    if project:
        title += f"  project={project}"
    if not items:
        console.print(f"[yellow]无匹配的存取审计记录（client=`{client or '-'}` "
                      f"project=`{project or '-'}`；agent-audit 目录可能为空或未发生存取）[/yellow]")
        return
    table = Table(title=title)
    table.add_column("时间", style="cyan", no_wrap=True)
    table.add_column("客户端", style="yellow")
    table.add_column("项目", style="green")
    table.add_column("操作", style="magenta")
    table.add_column("内容/查询", style="white")
    table.add_column("命中", justify="right")
    for rec in items:
        ts = rec.get("timestamp", "")
        act = rec.get("action", "")
        body = rec.get("query") or rec.get("content") or rec.get("source") or ""
        hits = str(rec.get("hits", "")) if rec.get("hits") is not None else ""
        c = rec.get("client", "-")
        p = rec.get("project", "-")
        table.add_row(ts, c, p, act, _truncate(str(body), 40), hits)
    console.print(table)
    console.print("[dim]Tip: 内容/查询为前50字符摘要（敏感红线），完整内容不落日志。["
                  "/dim]")


@app.command("eval")
def eval_cmd(
    file: str = typer.Option("tests/eval_zh_50.jsonl", "--file",
                             help="评测数据集路径（JSONL）"),
    top_k: int = typer.Option(5, "--top-k", help="每条问题的检索条数"),
    mode: str = typer.Option("hybrid", "--mode", help="hybrid/vector/keyword"),
    rerank: bool = typer.Option(False, "--rerank",
                                 help="临时开启交叉重排（对比指标收益）"),
    sparse: bool = typer.Option(False, "--sparse",
                                 help="临时开启稀疏第三路（对比指标收益）"),
    json_out: str = typer.Option("", "--json", help="报告写入 JSON 文件路径"),
):
    """检索质量评测：Recall@1/@5 + MRR（独立临时库，不碰生产数据）。

    用法：
      kb eval                                # 双路基线
      kb eval --sparse --rerank              # 三路 + 精排（量化收益）
      kb eval --json report.json             # 报告落盘
    评测完成即成功（退出码 0），指标基线值记入 A3.5 设计文档。
    """
    import shutil
    import tempfile
    from pathlib import Path

    from kb.eval import EvalDatasetError, load_dataset, run_eval

    try:
        dataset = load_dataset(file)
    except EvalDatasetError as e:
        console.print(f"[red]数据集加载失败: {e}[/red]")
        raise typer.Exit(code=1)

    # 独立临时库：评测进程专用 KB_DATA_DIR，不碰生产库
    tmp_dir = tempfile.mkdtemp(prefix="kb_eval_")
    settings = Settings(data_dir=Path(tmp_dir),
                        rerank_enabled=rerank, sparse_enabled=sparse)
    try:
        svc = KBService(settings)
        console.print(f"[blue]评测中：{len(dataset)} 条 × mode={mode} "
                      f"top_k={top_k}（rerank={'on' if rerank else 'off'} "
                      f"sparse={'on' if sparse else 'off'}）[/blue]")
        report = run_eval(svc, dataset, top_k=top_k, mode=mode)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 终端表格：总体 + 分难度
    table = Table(title=f"kb eval 报告（n={report['count']} mode={mode}）")
    table.add_column("分组", style="cyan")
    table.add_column("Recall@1", justify="right")
    table.add_column("Recall@5", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("条数", justify="right")
    table.add_row("总体", f"{report['recall_at_1']:.3f}",
                  f"{report['recall_at_5']:.3f}", f"{report['mrr']:.3f}",
                  str(report["count"]))
    for tag in ("keyword", "semantic", "distractor"):
        sub = report["by_difficulty"].get(tag)
        if sub:
            table.add_row(tag, f"{sub['recall_at_1']:.3f}",
                          f"{sub['recall_at_5']:.3f}", f"{sub['mrr']:.3f}",
                          str(sub["count"]))
    console.print(table)
    console.print(f"平均检索延迟：{report['latency_ms_avg']:.1f} ms")
    if report["misses"]:
        miss_ids = ", ".join(str(m["qid"]) for m in report["misses"])
        console.print(f"[yellow]未命中 qid：{miss_ids}[/yellow]")
    else:
        console.print("[green]全部命中[/green]")

    if json_out:
        Path(json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"报告已写入 {json_out}")


def main() -> None:
    # 乱码修复：先强制三流 UTF-8 + Windows 控制台 65001，再进 typer 分发
    _force_utf8_stdio()
    app()


if __name__ == "__main__":
    main()