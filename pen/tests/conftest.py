from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_pen_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """每个测试都指向自己的临时 .pen，绝不写读者真实的那个。

    此前跑一次 pytest 会往真实 `.pen/sessions/` 写 16 个文件（test_app.py 15 +
    test_i18n.py 1）。那些文件和读者自己的会话混在一起，谁也分不出来——
    「会话按时间清理」的验证数据也就永远是脏的。

    四个名字就是全部：其余路径（`session.sessions_dir` / `trajectory` /
    `proposals` / `probe_store.probes_dir` / `_quota_path`）都是
    `config.PEN_DIR / ...` **每次现算**，patch 根就够；只有 `libraries` 和
    `snapshots` 是 `from pen.config import LIBRARIES_DIR` 在 import 时冻结的，
    要单独补。

    两条不能碰：
    - **一个目录都不建**。`test_app.py` / `test_libraries.py` /
      `test_snapshots.py` 里约 35 处是裸 `lib.mkdir()`（无 `exist_ok`），
      这里抢先建好它们会集体 `FileExistsError`。交给 `ensure_pen_dirs()` 懒建。
    - **不 patch `config.REPO_ROOT`**。`sandbox.assert_handbook_path` 靠它放行
      默认手册，patch 掉约 45 个测试全红。
    """
    from pen import config, libraries, snapshots

    pen_dir = tmp_path / "pen-home"
    lib_dir = pen_dir / "libraries"
    monkeypatch.setattr(config, "PEN_DIR", pen_dir)
    monkeypatch.setattr(config, "LIBRARIES_DIR", lib_dir)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib_dir)
    monkeypatch.setattr(snapshots, "LIBRARIES_DIR", lib_dir)
    yield


@pytest.fixture(autouse=True)
def _reset_module_state():
    """清掉跨测试残留的模块级状态。

    现有测试里已经有十几处在手动 `library_scan._CACHE.clear()`（那个缓存 TTL
    60 秒，一个测试写的文件会被下一个测试读到旧内容）。收进这里之后不再依赖
    每个作者记得写。`STORE` 的两个字典也必须清——临时 `.pen` 换了，内存里
    留着上一个测试的会话对象就等于绕过了隔离。
    """
    from pen import app as appmod, library_scan, probe as probemod
    from pen.session import STORE

    library_scan._CACHE.clear()
    appmod._proposals.clear()
    STORE._items.clear()
    STORE._locks.clear()
    probemod._inflight = 0
    yield
    library_scan._CACHE.clear()
    appmod._proposals.clear()
    STORE._items.clear()
    STORE._locks.clear()
    probemod._inflight = 0
