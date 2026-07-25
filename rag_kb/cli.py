# -*- coding: utf-8 -*-
"""
@Time ： 2026/7/25 下午 12:11
@Auth ： Yu
@File ：cli.py
@IDE ：PyCharm
@Intro : 用 Typer 构建一个命令行工具，让用户通过命令操作知识库：导入文档、提问、交互问答、清空、查看配置。
"""

import os
from pathlib import Path

import typer
from rag_kb import rag_chain,config
from rich.console import Console
from rich.table import Table

console = Console()
chain = rag_chain.RAGChain()

app = typer.Typer()


@app.command()
def hello(name: str):
    """返回hello xxx"""
    print(f"hello {name}")


@app.command()
def add(file_path: str):
    """
    导入单个文件
    支持 .pdf / .txt / .md
    """
    path = config.Config.DATA_DIR / f"{file_path}"  # 如果该文件在data目录下，就使用他
    if path.exists():
        file_path = path
    chunk_nums = chain.add_document(file_path)  # 不在就使用用户传入的其他路径的文件
    console.print("导入文件成功！"
                  f"被切分为{chunk_nums}个区块", style="green")


@app.command()
def add_dir(directory: str):
    """
    批量导入目录
    自动扫描 .pdf/.txt/.md 文件
    """
    files = os.listdir(directory)  # 获取一个目录下有哪些文件，返回list[str]
    fmt = chain.document_processor.supported_formats
    all_chunk_nums = 0
    for file in files:
        suf = Path(file).suffix.lower()
        if suf in fmt:
            all_chunk_nums += chain.add_document(str(Path(directory) / f"{file}"))
    console.print("批量导入文件成功！"
                  f"总共被切分为{all_chunk_nums}个区块", style="green")


@app.command()
def ask(question: str = None):
    """
    单次提问
    """
    if question is None:
        question = typer.prompt("请输入你的问题")
    # print(f"问题是: {question}")
    response = chain.ask_with_sources(question)
    answer = response.get("answer", "文档中没有相关信息")
    sources = response.get("sources", "文档中没有相关信息")
    console.print(f"答案：{answer}", style="green")
    console.print(f"来源：{sources}", style="green")


@app.command()
def chat():
    """交互式连续问答"""
    console.print("进入交互式问答..."
                  "help 展示命令"
                  "输入 quit 退出", style="yellow")
    while True:
        text = str(typer.prompt("> ")).strip()
        if text is None:
            continue
        if should_quit(text):
            console.print("再见！", style="cyan")
            break
        ask(text)


@app.command()
def clear():
    """用于清除向量库数据"""
    if typer.confirm("确定清除向量数据库吗？"):
        chain.vector_store.clear()
    else:
        console.print("取消操作！", style="cyan")


@app.command()
def info():
    """以表格的方式显示当前配置"""
    table = Table(title="⚙️ 当前配置", show_header=True, header_style="bold magenta")
    table.add_column("配置项", style="bold yellow")
    table.add_column("值", style="white")

    for key, value in config.Config.get_all().items():
        table.add_row(str(key), str(value))

    console.print(table)


def should_quit(text: str) -> bool:
    """接收离开命令返回True"""
    # strip() 用来「剥掉」字符串首尾的指定字符，默认剥掉空白符（空格、制表符、换行符等），常用于清理用户输入中不小心多敲的前后空格。
    if text.strip().lower() in ("quit", "exit", "q", "bye"):
        return True


if __name__ == "__main__":
    app()
