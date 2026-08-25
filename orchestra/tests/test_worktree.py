"""board.py worktree 子命令（TASK-0025 治本分支串扰）：建/进/清/拒绝。

在临时 git 仓库上验证（--repo 注入），绝不污染真实 rag-kb 仓库。
用例：建（HEAD 正确）→ 已存在拒绝 → 脏目录拒绝 → enter 输出路径 → clean 无残留。
"""
import subprocess
from pathlib import Path

import pytest


def _init_repo(tmp_path: Path) -> Path:
    """在 tmp_path/repo 初始化 git 仓库，提交一个文件，并建 task/TASK-9999 分支。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    for args in (["init", "-b", "main"], ["add", "-A"],
                 ["commit", "-m", "base"], ["branch", "task/TASK-9999"]):
        subprocess.run(["git", "-C", str(repo)] + args,
                       capture_output=True, check=True)
    return repo


def test_setup_创建worktree且HEAD正确(tmp_path):
    import board
    repo = _init_repo(tmp_path)
    board.cmd_worktree_setup("TASK-9999", repo=repo)
    wt = repo / ".worktrees" / "TASK-9999"
    assert wt.is_dir()
    r = subprocess.run(["git", "-C", str(wt), "branch", "--show-current"],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "task/TASK-9999"


def test_setup_已存在拒绝(tmp_path):
    import board
    repo = _init_repo(tmp_path)
    board.cmd_worktree_setup("TASK-9999", repo=repo)
    with pytest.raises(ValueError, match="已存在"):
        board.cmd_worktree_setup("TASK-9999", repo=repo)


def test_setup_脏目录拒绝(tmp_path):
    import board
    repo = _init_repo(tmp_path)
    dirty = repo / ".worktrees" / "TASK-9998"
    dirty.mkdir(parents=True)
    (dirty / "junk.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="非空"):
        board.cmd_worktree_setup("TASK-9998", repo=repo)


def test_enter_输出worktree路径(tmp_path, capsys):
    import board
    repo = _init_repo(tmp_path)
    board.cmd_worktree_setup("TASK-9999", repo=repo)
    board.cmd_worktree_enter("TASK-9999", repo=repo)
    out = capsys.readouterr().out
    assert str(repo / ".worktrees" / "TASK-9999") in out


def test_enter_不存在报错(tmp_path):
    import board
    repo = _init_repo(tmp_path)
    with pytest.raises(ValueError, match="不存在"):
        board.cmd_worktree_enter("TASK-9999", repo=repo)


def test_clean_删除无残留(tmp_path):
    import board
    repo = _init_repo(tmp_path)
    board.cmd_worktree_setup("TASK-9999", repo=repo)
    board.cmd_worktree_clean("TASK-9999", repo=repo)
    assert not (repo / ".worktrees" / "TASK-9999").exists()
    r = subprocess.run(["git", "-C", str(repo), "worktree", "list"],
                       capture_output=True, text=True)
    assert "TASK-9999" not in r.stdout
    # clean 后再 setup 可重建（无残留）
    board.cmd_worktree_setup("TASK-9999", repo=repo)
    assert (repo / ".worktrees" / "TASK-9999").is_dir()


def test_setup_分支不存在报错(tmp_path):
    import board
    repo = _init_repo(tmp_path)
    with pytest.raises(ValueError, match="不存在"):
        board.cmd_worktree_setup("TASK-0000", repo=repo)
