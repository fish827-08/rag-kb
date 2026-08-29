"""worktree.py 隔离命令（TASK-0025 治本分支串扰；TASK-0031 迁至 worktree 模块）：建/进/清/拒绝。

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
    import worktree
    repo = _init_repo(tmp_path)
    worktree.cmd_worktree_setup("TASK-9999", repo=repo)
    wt = repo / ".worktrees" / "TASK-9999"
    assert wt.is_dir()
    r = subprocess.run(["git", "-C", str(wt), "branch", "--show-current"],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "task/TASK-9999"


def test_setup_已存在拒绝(tmp_path):
    import worktree
    repo = _init_repo(tmp_path)
    worktree.cmd_worktree_setup("TASK-9999", repo=repo)
    with pytest.raises(ValueError, match="已存在"):
        worktree.cmd_worktree_setup("TASK-9999", repo=repo)


def test_setup_脏目录拒绝(tmp_path):
    import worktree
    repo = _init_repo(tmp_path)
    dirty = repo / ".worktrees" / "TASK-9998"
    dirty.mkdir(parents=True)
    (dirty / "junk.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="非空"):
        worktree.cmd_worktree_setup("TASK-9998", repo=repo)


def test_enter_输出worktree路径(tmp_path, capsys):
    import worktree
    repo = _init_repo(tmp_path)
    worktree.cmd_worktree_setup("TASK-9999", repo=repo)
    worktree.cmd_worktree_enter("TASK-9999", repo=repo)
    out = capsys.readouterr().out
    assert str(repo / ".worktrees" / "TASK-9999") in out


def test_enter_不存在报错(tmp_path):
    import worktree
    repo = _init_repo(tmp_path)
    with pytest.raises(ValueError, match="不存在"):
        worktree.cmd_worktree_enter("TASK-9999", repo=repo)


def test_clean_删除无残留(tmp_path):
    import worktree
    repo = _init_repo(tmp_path)
    worktree.cmd_worktree_setup("TASK-9999", repo=repo)
    worktree.cmd_worktree_clean("TASK-9999", repo=repo)
    assert not (repo / ".worktrees" / "TASK-9999").exists()
    r = subprocess.run(["git", "-C", str(repo), "worktree", "list"],
                       capture_output=True, text=True)
    assert "TASK-9999" not in r.stdout
    # clean 后再 setup 可重建（无残留）
    worktree.cmd_worktree_setup("TASK-9999", repo=repo)
    assert (repo / ".worktrees" / "TASK-9999").is_dir()


def test_setup_分支不存在报错(tmp_path):
    import worktree
    repo = _init_repo(tmp_path)
    with pytest.raises(ValueError, match="不存在"):
        worktree.cmd_worktree_setup("TASK-0000", repo=repo)


def _init_multi_branch_repo(tmp_path: Path) -> Path:
    """初始化 git 仓库并建多个 task 分支（TASK-1001/1002/1003），供 --all 批量清理测试。"""
    repo = _init_repo(tmp_path)
    for branch in ("task/TASK-1001", "task/TASK-1002", "task/TASK-1003"):
        subprocess.run(["git", "-C", str(repo), "branch", branch],
                       capture_output=True, check=True)
    return repo


class TestCleanAll:
    """cmd_worktree_clean --all 批量清理（TASK-0042）。"""

    def test_clean_all_批量清理多个目录(self, tmp_path, capsys):
        import worktree
        repo = _init_multi_branch_repo(tmp_path)
        for tid in ("TASK-1001", "TASK-1002", "TASK-1003"):
            worktree.cmd_worktree_setup(tid, repo=repo)
        wt_root = repo / ".worktrees"
        assert len(list(wt_root.iterdir())) == 3
        capsys.readouterr()
        worktree.cmd_worktree_clean(repo=repo, all_=True)
        out = capsys.readouterr().out
        assert "将批量清理 3 个 worktree" in out
        assert "批量清理完成：成功 3，失败 0" in out
        assert not any(wt_root.iterdir())

    def test_clean_all_无目录时提示(self, tmp_path, capsys):
        import worktree
        repo = _init_repo(tmp_path)
        worktree.cmd_worktree_clean(repo=repo, all_=True)
        out = capsys.readouterr().out
        assert "无 worktree 目录可清理" in out

    def test_clean_all_空目录时提示(self, tmp_path, capsys):
        import worktree
        repo = _init_repo(tmp_path)
        (repo / ".worktrees").mkdir()
        worktree.cmd_worktree_clean(repo=repo, all_=True)
        out = capsys.readouterr().out
        assert "无 worktree 目录可清理" in out

    def test_clean_all_单卡模式不受影响(self, tmp_path):
        import worktree
        repo = _init_repo(tmp_path)
        with pytest.raises(ValueError, match="单卡清理需指定 task_id"):
            worktree.cmd_worktree_clean(repo=repo, all_=False)

    def test_clean_all_非原子一个失败不影响其他(self, tmp_path, capsys, monkeypatch):
        import worktree
        repo = _init_multi_branch_repo(tmp_path)
        for tid in ("TASK-1001", "TASK-1002"):
            worktree.cmd_worktree_setup(tid, repo=repo)
        capsys.readouterr()
        real_run = subprocess.run

        def fake_run(args, **kwargs):
            if "TASK-1001" in str(args):
                class FakeResult:
                    returncode = 1
                    stderr = "simulated failure"
                return FakeResult()
            return real_run(args, **kwargs)
        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        worktree.cmd_worktree_clean(repo=repo, all_=True)
        out = capsys.readouterr().out
        assert "清理失败（跳过）" in out
        assert "批量清理完成：成功 1，失败 1" in out
        remaining = [d.name for d in (repo / ".worktrees").iterdir() if d.is_dir()]
        assert "TASK-1001" in remaining
        assert "TASK-1002" not in remaining
