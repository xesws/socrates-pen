"""写权限只允许一本手册的 original_path；读可放宽到同仓库白名单。"""

from __future__ import annotations

import re
from pathlib import Path

from pen.config import handbook_allow_roots

HANDBOOK_SUFFIXES = {".md", ".markdown"}

_WRAP = re.compile(r"^[`'\"](.*)[`'\"]$")
_LINE_SUFFIX = re.compile(r":\d+(-\d+)?$")


class SandboxError(PermissionError):
    """沙箱拒绝。带 i18n_key 时 app 层会按用户语言查表，否则回落这里的中文。"""

    def __init__(self, message: str, *, key: str | None = None, **args: object) -> None:
        super().__init__(message)
        self.i18n_key = key
        self.i18n_args = args


def _is_protected(got: Path) -> bool:
    """禁止 .git / .obsidian / .env*。部件比较不区分大小写（APFS 默认不敏感）。"""
    parts = [p.lower() for p in got.parts]
    if ".git" in parts or ".obsidian" in parts:
        return True
    name = got.name.lower()
    return name == ".env" or name.startswith(".env.")


def _unwrap(s: str) -> str:
    m = _WRAP.match(s)
    return m.group(1).strip() if m else s


def normalize_model_path(raw: str | Path) -> str:
    """剥模型常加的引号、反引号、:行号。空串表示「就读原文」。"""
    s = _unwrap(str(raw).strip())
    s = _LINE_SUFFIX.sub("", s)
    return _unwrap(s).strip()


def resolve_read_target(original_path: Path, target: str | Path) -> Path:
    """
    相对路径锚在手册所在目录，不看进程 cwd。
    空 / 空白 → 原文自己。
    """
    allowed = original_path.expanduser().resolve()
    raw = normalize_model_path(target)
    if not raw:
        return allowed
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = allowed.parent / p
    return p.resolve()


def resolve_existing_or_new(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def parse_vault_root(raw: str | None) -> list[Path]:
    """请求体带来的库根。空 → 不加。拒绝文件系统根与非目录。"""
    if raw is None or not str(raw).strip():
        return []
    p = Path(str(raw).strip()).expanduser().resolve()
    if p == p.parent:
        raise SandboxError("vault_root 不能是文件系统根", key="sandbox.vault_root_is_fs_root")
    if not p.is_dir():
        raise SandboxError(f"vault_root 不是目录：{p}", key="sandbox.vault_root_not_dir", got=str(p))
    return [p]


def assert_handbook_path(
    path: str | Path,
    extra_roots: list[Path] | None = None,
) -> Path:
    """登记和写回共用：必须是允许根下的 Markdown，且不是 .env / .git / .obsidian。"""
    got = Path(path).expanduser().resolve()
    if _is_protected(got):
        raise SandboxError(f"拒绝受保护路径：{got}", key="sandbox.protected", got=str(got))
    if got.suffix.lower() not in HANDBOOK_SUFFIXES:
        raise SandboxError(f"只接受 Markdown 教材（.md / .markdown）：{got}", key="sandbox.not_markdown", got=str(got))
    roots = handbook_allow_roots(extra_roots=extra_roots)
    if not any(got == root or root in got.parents for root in roots):
        raise SandboxError(f"教材不在允许的根内：{got}", key="sandbox.outside_roots", got=str(got))
    return got


def assert_write_target(original_path: Path, target: str | Path) -> Path:
    """写 / 回退 / commit 的目标必须就是登记的原文。"""
    allowed = original_path.expanduser().resolve()
    got = Path(target).expanduser().resolve()
    if got != allowed:
        raise SandboxError(f"拒绝写入 {got}：本手册只能改 {allowed}")
    if _is_protected(got):
        raise SandboxError(f"拒绝写入受保护路径：{got}")
    return got


def reading_roots(
    original_path: Path,
    extra_roots: list[Path] | None = None,
) -> list[Path]:
    """read_file 实际认的根。**书架的可见性必须用这个**，不能用全局的
    handbook_allow_roots()——那一套宽得多。

    印一条苏格拉底读不到的路径比不印更糟：它会照着去读，撞在「不在本手册允许的根内」
    上，读者看到的是一次白跑的工具调用加一句道歉。实测过：当前手册是仓库根那本
    swe-agent-v2（allow_root 为 None）时，全局根含 PEN_ALLOW_ROOTS 里的 vault，
    书架就会印出 vault 里那本，而它的沙箱根只有仓库根。
    """
    if extra_roots is not None:
        return [Path(r) for r in extra_roots]
    return [original_path.expanduser().resolve().parent]


def assert_readable(
    original_path: Path,
    target: str | Path,
    *,
    extra_roots: list[Path] | None = None,
) -> Path:
    """
    读：原文本身，或 extra_roots 之下（默认原文所在目录，用于对照 lab/）。
    相对路径相对手册目录解析。禁止 .env / .git / .obsidian。
    """
    allowed_file = original_path.expanduser().resolve()
    got = resolve_read_target(original_path, target)
    if _is_protected(got):
        raise SandboxError(f"拒绝读取受保护路径：{got}")
    if got == allowed_file:
        return got
    # 和 reading_roots 同源，别在这里另写一遍——两套根漂移正是上面那个 bug。
    for root in reading_roots(original_path, extra_roots):
        root_r = root.expanduser().resolve()
        if got == root_r or root_r in got.parents:
            return got
    raise SandboxError(f"拒绝读取 {got}：不在本手册允许的根内")
