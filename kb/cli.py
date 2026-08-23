"""CLI 入口：add / search / info / serve。"""
import json

import typer
from rich.console import Console

from kb.config import Settings, get_settings
from kb.service import KBService

app = typer.Typer()
console = Console()


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


@app.command()
def add(content: str,
        tags: str = typer.Option("", "--tags", help="逗号分隔的标签"),
        source: str | None = typer.Option(None, "--source", help="来源标识"),
        namespace: str = typer.Option("default", "--namespace", help="命名空间")):
    """写入一条记忆。"""
    r = _service().add_memory(
        content, tags=[t for t in tags.split(",") if t] if tags else None,
        source=source, namespace=namespace)
    console.print(f"已写入 id={r.id}")


@app.command()
def search(query: str,
           top_k: int = typer.Option(5, "--top-k", help="返回条数"),
           mode: str = typer.Option("hybrid", "--mode", help="hybrid/vector/keyword")):
    """混合检索。"""
    hits = _service().search(query, top_k=top_k, mode=mode)
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
def serve():
    """启动常驻 REST 服务（python -m kb serve）。"""
    import uvicorn
    from kb.api import create_app
    s = get_settings()
    # 终端交互式设备检测（显式配置/runtime.json/询问），结果注入 settings
    s.device = resolve_device(s, interactive=True)
    app = create_app(s)
    uvicorn.run(app, host=s.api_host, port=s.api_port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()