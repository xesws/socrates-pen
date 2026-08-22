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


DEFAULT_HANDBOOK_FIXTURE = Path(__file__).parent / "fixtures" / "default_handbook.md"


@pytest.fixture(autouse=True)
def _default_handbook_fixture(monkeypatch: pytest.MonkeyPatch):
    """把默认手册指到仓库自带的 fixture，不指实验室那本 13083 行的教材。

    `config.DEFAULT_HANDBOOK` 原本是 `REPO_ROOT / "SWE-Agent通关手册v2.md"`——
    那是实验室仓的教材，按 v0.14.0 的决定**不进公开仓**。于是公开仓干净 checkout 上
    `libraries.ensure_default()` 空操作返回 None，45 条拿默认手册当地基的测试全塌
    （test_app 36 / test_tutor 4 / test_retention 2 / test_i18n 2 / test_diagnose 1）。
    实验室仓因为那本书在，一直是绿的，把这条依赖盖住了。

    **无条件替换**，不写成「真书不在时才兜底」：条件分支会让两个仓跑的是不同的测试，
    同一份 `pen/` 却有两种行为，比这条病本身更难查。

    `DEFAULT_HANDBOOK_ID` 不动——测试只断言 id（`test_app.py:43`、`:591`），
    不断言书的内容。

    两个名字都得 patch：`pen/libraries.py:11` 是 `from pen.config import DEFAULT_HANDBOOK`，
    import 那一刻就冻住了，只 patch `config` 够不着它。

    fixture 落在 `pen/tests/fixtures/` 下，也就在 `config.REPO_ROOT` 里——
    `sandbox.handbook_allow_roots()` 靠这个放行（`config.py:345`）。
    """
    from pen import config, libraries

    monkeypatch.setattr(config, "DEFAULT_HANDBOOK", DEFAULT_HANDBOOK_FIXTURE)
    monkeypatch.setattr(libraries, "DEFAULT_HANDBOOK", DEFAULT_HANDBOOK_FIXTURE)
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
