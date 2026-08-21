from __future__ import annotations

from pathlib import Path

from pen import config
from pen.config import merge_llm, parse_dotenv, resolve_llm


def test_parse_strips_inline_comment(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("DEEPSEEK_API_KEY=sk-test # comment\n", encoding="utf-8")
    assert parse_dotenv(p)["DEEPSEEK_API_KEY"] == "sk-test"


def test_deepseek_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    p = tmp_path / ".env"
    p.write_text("DEEPSEEK_API_KEY=sk-ds-demo\n", encoding="utf-8")
    cfg = resolve_llm(p)
    assert cfg is not None
    assert cfg.key_source == "DEEPSEEK_API_KEY"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.api_key == "sk-ds-demo"


def test_openai_triplet_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setenv("MODEL_NAME", "deepseek-v4-flash")
    p = tmp_path / ".env"
    p.write_text("DEEPSEEK_API_KEY=sk-ignored\n", encoding="utf-8")
    cfg = resolve_llm(p)
    assert cfg is not None
    assert cfg.api_key == "sk-from-env"
    assert cfg.key_source == "OPENAI_API_KEY"
    assert cfg.model == "deepseek-v4-flash"


def test_kimi_key_alone_is_not_used(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    p = tmp_path / ".env"
    p.write_text("KIMI_API_KEY=sk-kimi-not-this\n", encoding="utf-8")
    assert resolve_llm(p) is None


def test_pen_home_moves_pen_dir(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "pen-home"
    dest.mkdir()
    monkeypatch.setenv("PEN_HOME", str(dest))
    got = config.apply_pen_home(tmp_path / "missing.env")
    assert got == dest.resolve()
    assert config.PEN_DIR == dest.resolve()
    assert config.LIBRARIES_DIR == dest.resolve() / "libraries"
    monkeypatch.delenv("PEN_HOME", raising=False)
    config.PEN_DIR = config.default_pen_dir()
    config.LIBRARIES_DIR = config.PEN_DIR / "libraries"


def test_default_pen_dir_source_tree() -> None:
    assert config.default_pen_dir(config.REPO_ROOT) == config.REPO_ROOT / ".pen"


def test_default_pen_dir_installed_package(tmp_path: Path) -> None:
    pkg = tmp_path / "pen"
    pkg.mkdir()
    (pkg / "app.py").write_text("#", encoding="utf-8")
    assert config.default_pen_dir(tmp_path) == Path.home() / ".socrates-pen"


def test_merge_llm_request_wins_and_key_alone_works(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    assert merge_llm(env_file=empty) is None
    only_req = merge_llm(
        api_key="sk-from-settings",
        base_url="https://api.openai.com/v1",
        model="gpt-4.1-mini",
        thinking="high",
        env_file=empty,
    )
    assert only_req is not None
    assert only_req.api_key == "sk-from-settings"
    assert only_req.base_url == "https://api.openai.com/v1"
    assert only_req.model == "gpt-4.1-mini"
    assert only_req.thinking == "high"
    assert only_req.key_source == "settings"

    envf = tmp_path / ".env"
    envf.write_text("DEEPSEEK_API_KEY=sk-env\n", encoding="utf-8")
    mixed = merge_llm(model="override-model", thinking="nope", env_file=envf)
    assert mixed is not None
    assert mixed.api_key == "sk-env"
    assert mixed.model == "override-model"
    assert mixed.thinking == "off"
    assert mixed.key_source == "DEEPSEEK_API_KEY"
    assert mixed.base_url == "https://api.deepseek.com"


def _clear_llm_env(monkeypatch) -> None:
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)


def test_merge_llm_same_host_model_override_keeps_env_key(tmp_path: Path, monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text("DEEPSEEK_API_KEY=sk-env\n", encoding="utf-8")
    cfg = merge_llm(
        base_url="https://api.deepseek.com",
        model="deepseek-reasoner",
        env_file=envf,
    )
    assert cfg is not None
    assert cfg.api_key == "sk-env"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.model == "deepseek-reasoner"
    assert cfg.key_source == "DEEPSEEK_API_KEY"
    with_userinfo = merge_llm(
        base_url="https://user:pass@api.deepseek.com/v1",
        env_file=envf,
    )
    assert with_userinfo is not None
    assert with_userinfo.api_key == "sk-env"


def test_merge_llm_different_host_without_request_key_returns_none(tmp_path: Path, monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text("DEEPSEEK_API_KEY=sk-env\n", encoding="utf-8")
    assert merge_llm(base_url="https://api.openai.com/v1", env_file=envf) is None


def test_merge_llm_different_host_with_request_key_uses_request_key(tmp_path: Path, monkeypatch) -> None:
    _clear_llm_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text("DEEPSEEK_API_KEY=sk-env\n", encoding="utf-8")
    cfg = merge_llm(
        api_key="sk-from-page",
        base_url="https://api.openai.com/v1",
        env_file=envf,
    )
    assert cfg is not None
    assert cfg.api_key == "sk-from-page"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.key_source == "settings"


# ── v0.10.1 RuntimeLimits ───────────────────────────────────────


def test_defaults_are_exactly_the_v0_9_0_numbers() -> None:
    """上线当天必须和改造前逐字节一致。**这张表是唯一的证明。**

    故意写死字面量而不是 `== config.MAX_TOOL_ROUNDS`：自指断言什么都证明不了。
    改任何一个数都要在这里改一次，改的人会看见自己在改行为。
    """
    lim = config.default_limits()
    assert lim.max_tool_rounds == 100
    assert (lim.cross_book_chars, lim.cross_book_reads) == (24000, 8)
    assert (lim.probe_max_per_session, lim.probe_max_per_window) == (8, 40)
    assert lim.probe_pending_cap == 3
    assert (lim.probe_max_reads, lim.probe_read_lines) == (2, 80)
    assert lim.probe_timeout_s == 150.0
    assert lim.probe_min_reply_chars == 80
    assert lim.probe_concurrency == 2


def test_absent_and_garbage_limits_all_fall_back_to_defaults() -> None:
    """填错一个字符不该把这一轮打挂，也不该把预算静默归零。"""
    base = config.default_limits()
    assert config.merge_limits(None) == base
    assert config.merge_limits({}) == base
    assert config.merge_limits({"没这个字段": 1}) == base
    assert config.merge_limits({"max_tool_rounds": "abc"}) == base
    assert config.merge_limits({"max_tool_rounds": None}) == base
    assert config.merge_limits({"max_tool_rounds": float("nan")}) == base
    assert config.merge_limits({"max_tool_rounds": float("inf")}) == base


def test_a_json_true_must_not_become_a_limit_of_one() -> None:
    """isinstance(True, int) 是真、float(True) == 1.0。不显式挡 bool 的话，
    JSON 里一个 true 会静默把上限设成 1——比报错难查得多。"""
    assert config.merge_limits({"max_tool_rounds": True}) == config.default_limits()
    assert config.merge_limits({"probe_concurrency": False}) == config.default_limits()


def test_limits_are_clamped_not_rejected() -> None:
    got = config.merge_limits({"max_tool_rounds": 99999, "probe_concurrency": 0})
    assert got.max_tool_rounds == 200, "夹到上限"
    assert got.probe_concurrency == 1, "0 会让深挖永远起不来，那叫关掉，用总开关"
    assert config.merge_limits({"max_tool_rounds": -5}).max_tool_rounds == 1


def test_partial_limits_keep_the_rest_at_default() -> None:
    got = config.merge_limits({"max_tool_rounds": 7})
    assert got.max_tool_rounds == 7
    assert got.cross_book_chars == config.default_limits().cross_book_chars


def test_float_field_stays_float_and_int_field_stays_int() -> None:
    got = config.merge_limits({"probe_timeout_s": 90, "probe_max_reads": 3.7})
    assert isinstance(got.probe_timeout_s, float) and got.probe_timeout_s == 90.0
    assert isinstance(got.probe_max_reads, int) and got.probe_max_reads == 3


def test_default_limits_reads_the_module_attribute_every_time(monkeypatch) -> None:
    """不能做成模块级单例。`monkeypatch.setattr(config, "PROBE_MAX_PER_WINDOW", 3)`
    是本仓唯一一处限流常量的测试打法，它能生效正是靠属性访问。
    冻成单例 = 在修 probe 那个信号量的同时新开一个一模一样的坑。"""
    monkeypatch.setattr(config, "PROBE_MAX_PER_WINDOW", 3)
    assert config.default_limits().probe_max_per_window == 3


def test_every_limit_range_key_matches_a_field() -> None:
    from dataclasses import fields

    names = {f.name for f in fields(config.RuntimeLimits)}
    assert set(config.LIMIT_RANGE) == names, "夹紧表和字段表必须一一对应"


def test_every_limit_is_actually_read_somewhere() -> None:
    """本仓的老规矩（config.py 里那段「摆一个常量而代码不读它，改的人会以为
    改了有用」）。在这里做成机械检查——新加字段却忘了接线的话，这条会红。

    这比注释强的地方在于：把一个还没有消费方的旋钮放上设置页，比留一个
    没人读的常量更糟——常量至少读者看不见。
    """
    from dataclasses import fields
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in root.rglob("*.py")
        if p.name != "config.py" and "tests" not in p.parts
    )
    for f in fields(config.RuntimeLimits):
        assert f.name in src, f"RuntimeLimits.{f.name} 全仓没人读——要么接上，要么删掉"


def test_merge_limits_survives_an_arbitrary_precision_integer() -> None:
    """JSON 允许任意精度整数，float(10**400) 抛的是 OverflowError——
    不挡的话读者拿到「对话中途出了意外错误」，正是请求体用 dict 而不是
    子模型时承诺要避免的那个形状。插件自己发不出，手写 curl 能。"""
    base = config.default_limits()
    assert config.merge_limits({"max_tool_rounds": 10**400}) == base
    assert config.merge_limits({"cross_book_chars": -(10**400)}) == base
    assert config.merge_limits({"probe_timeout_s": 10**309}) == base


# ── v0.12.3：测试不许写读者真实的 .pen ─────────────────────────────


def test_tests_never_write_the_real_pen_dir() -> None:
    """conftest 那个 autouse 隔离掉了就红。

    此前跑一次 pytest 会往真实 `.pen/sessions/` 里塞 16 个文件，和读者自己的
    会话混在一起。这条守着「四个名字」全都指向临时目录——少 patch 一个，
    对应那条断言就会指回 REPO_ROOT/.pen。
    """
    from pen import config, libraries, snapshots

    real = (config.REPO_ROOT / ".pen").resolve()
    assert config.PEN_DIR.resolve() != real
    assert config.LIBRARIES_DIR.resolve() != (real / "libraries")
    # libraries / snapshots 是 import 时冻结的副本，要单独盯。
    assert libraries.LIBRARIES_DIR.resolve() == config.LIBRARIES_DIR.resolve()
    assert snapshots.LIBRARIES_DIR.resolve() == config.LIBRARIES_DIR.resolve()
    # 实验室检出里默认手册还得能读到；公开仓 / pip 安装没有这份文件。
    if (config.REPO_ROOT / "SWE-Agent通关手册v2.md").is_file():
        assert config.DEFAULT_HANDBOOK.is_file()


def test_isolated_pen_dir_is_not_pre_created() -> None:
    """autouse 里一旦手滑 mkdir，约 35 处裸 `lib.mkdir()`（无 exist_ok）会集体炸。"""
    from pen import config

    assert not config.PEN_DIR.exists()
    assert not config.LIBRARIES_DIR.exists()
