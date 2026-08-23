"""CLI 入口：add / search / info / serve（serve 在 N8 补全）。"""
import typer
from rich.console import Console

from kb.config import get_settings
from kb.service import KBService

app = typer.Typer()
console = Console()


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
    """启动常驻 REST 服务（设备检测在 N8 完善）。"""
    import uvicorn
    from kb.api import create_app
    s = get_settings()
    app = create_app(s)
    uvicorn.run(app, host=s.api_host, port=s.api_port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()