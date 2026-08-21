"""只对 original_path 做 git add -- <file>。禁止 git add ."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pen.sandbox import assert_write_target


class GitError(RuntimeError):
    pass


def git_root(start: Path) -> Path:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start if start.is_dir() else start.parent,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise GitError(r.stderr.strip() or "不在 git 仓库内")
    return Path(r.stdout.strip())


def commit_original(original_path: Path, message: str) -> str:
    target = assert_write_target(original_path, original_path)
    root = git_root(target)
    rel = target.relative_to(root)
    add = subprocess.run(
        ["git", "add", "--", str(rel)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if add.returncode != 0:
        raise GitError(add.stderr.strip())
    # 确认暂存区里没有别的路径被我们 add 进来（只检查这次 add 的文件）
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", str(rel)],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    if str(rel) not in staged.stdout and rel.as_posix() not in staged.stdout:
        raise GitError(f"暂存失败：{rel}")
    commit = subprocess.run(
        ["git", "commit", "-m", message, "--", str(rel)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        raise GitError(commit.stderr.strip() or commit.stdout.strip())
    return commit.stdout.strip()
