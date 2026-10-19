"""N16 收尾与清理：全模块导入冒烟 + 归档完整性 + README 挂载示例验收。"""
from pathlib import Path


def test_全部模块可导入():
    import kb.config, kb.models, kb.embedder, kb.storage, kb.bm25
    import kb.retriever, kb.service, kb.llm, kb.ingest, kb.watcher
    import kb.api, kb.mcp, kb.cli  # noqa: F401


def test_旧代码已归档且根目录无残留():
    from pathlib import Path
    # 根目录不得残留旧路径（已提前归档，2026-08-23；
    # 注意：根目录 ROADMAP.md 是 2026-08-24 新建的 Bus 总线设计书，与归档内旧 ROADMAP.md 无关，不在禁止之列）
    for p in ("rag_kb", "app", "demo.py", "test_py", "notes", "step_doc"):
        assert not Path(p).exists(), f"根目录残留旧文件: {p}"
    # 归档目录仅作者本机存在（.gitignore 排除 _archive/），clone 后必然缺失，
    # 因此只在它有出现时校验完整性，不能要求所有克隆者都必须有（第二轮报告 P1）。
    if Path("_archive").exists():
        for p in ("_archive/rag_kb", "_archive/app", "_archive/demo.py",
                  "_archive/test_py", "_archive/step_doc", "_archive/notes",
                  "_archive/README.md", "_archive/ROADMAP.md"):
            assert Path(p).exists(), f"归档缺失: {p}"


def test_README含挂载示例():
    content = Path("README.md").read_text(encoding="utf-8")
    assert "python -m kb serve" in content
    assert "/mcp" in content
