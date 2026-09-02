"""路径与环境。默认 DeepSeek（OPENAI_* 或 DEEPSEEK_API_KEY）。不猜 Kimi。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, fields, replace
from pathlib import Path
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def is_source_tree(root: Path | None = None) -> bool:
    """git 检出或带 pyproject 的源码树。pip 装进 site-packages 为假。"""
    base = root if root is not None else REPO_ROOT
    if not (base / "pen" / "app.py").is_file():
        return False
    return (base / ".git").is_dir() or (base / "pyproject.toml").is_file()


def default_pen_dir(root: Path | None = None) -> Path:
    """源码树用仓库里的 `.pen`；安装后的包用 `~/.socrates-pen`。"""
    base = root if root is not None else REPO_ROOT
    if is_source_tree(base):
        return base / ".pen"
    return Path.home() / ".socrates-pen"


PEN_DIR: Path = default_pen_dir()
LIBRARIES_DIR: Path = PEN_DIR / "libraries"
DEFAULT_HANDBOOK_ID = "swe-agent-v2"
DEFAULT_HANDBOOK: Path = REPO_ROOT / "SWE-Agent通关手册v2.md"

MAX_OUTPUT = 5000
# 一轮对话里读**当前手册以外**的文件的总字符预算（别的教材、lab/ 对照文件）。
# 读当前手册不计：写回要先 read_file 看原文、翻本册别的 Level 都是本职，
# 一次都不该受影响——这是这道闸零回归的关键，别改成按次数或按总量。
# v0.8.7 把书架接上之后，实测一句「另一本讲什么」触发 21 次 read_file、
# 46912 字符，全部进 session.messages 并落盘，本会话之后每一轮都重发
# （末轮 prompt 27k token）。读者只看到「在翻手册…」，和 probe.py 开头写死的
# 那条「后台任务一旦能自主循环调工具，读者看不见它在烧钱」是同一件事。
# 单次 read_file 已被 MAX_OUTPUT 截到 5000，所以这里约等于 5 次满窗跨书阅读。
CROSS_BOOK_CHARS = 24000
# 光封字节封不住轮数：模型每次只读一行，字节预算永远用不完，
# 而 MAX_TOOL_ROUNDS=100 一次不少，每轮 prompt 还要把整段 messages 重发一遍。
# 实测跑满 50 轮时总 prompt 仍有 240 万字符。次数是第二道闸，两道任一触顶就收敛。
CROSS_BOOK_READS = 8
NEIGHBORHOOD_CHARS = 4000
# 主对话划选硬上限。超过的第一包不带全文，改成「先读标题附近，再按需展开」。
# 不上设置页——和 TOC_CHARS 同类，不是读者旋钮。
SELECTED_TEXT_CHARS = 4000
# 苏格拉底回答一轮里最多翻多少次工具。**从 tutor.py 搬过来的，那边不留别名**——
# 留一个 `MAX_TOOL_ROUNDS = config.MAX_TOOL_ROUNDS` 就是第二个定义点，
# 下次有人改 tutor 那个而不是这个，两边就分家了。
# 读者要的就是宽：接上书架之后，一个跨教材的问题本来就要多翻几次才答得像样。
# 成本那头由 CROSS_BOOK_CHARS / CROSS_BOOK_READS 两道闸兜着。
MAX_TOOL_ROUNDS = 100
SNAPSHOT_KEEP = 20
# 会话文件的保留期。实测读者的 .pen/sessions/ 攒到了 3389 个文件 / 10.4 MB，
# 其中 3371 个是**空的**（只有 system prompt）——每划一次词就建一场，
# 大部分没等到第一句话就被下一次划词换掉了。
#
# 分两档是因为**纯 7 天规则一个都删不掉**：那 3371 个空会话最老的也才几天，
# `-mtime +7` 零命中。空的那档才是真正管用的那把扫帚。
#
# 空的留 1 天而不是 1 小时：读者刚打开一篇新笔记 → 建会话 → 还没提问，
# 这时它就是「空」的（实测最新的空会话建于 6.9 分钟前）。留足一整天的余量。
SESSION_KEEP_DAYS_EMPTY = 1
SESSION_KEEP_DAYS_CHATTED = 7
# 挂着**真**审批（聊过 + `pending.id`）的那一场：删掉读者点「同意」就撞 404，
# 手里已生成的提案当场作废，所以给它一档长得多的保护。
#
# 但**不能是无限**（v0.12.6）：读者在审批面板开着的时候关掉 Obsidian、
# 之后点「新开会话」，那一场就再也没人碰它了——一条没有天花板的豁免
# 就是一条谁都清不掉的泄漏，正是这次要治的病本身。30 天足够任何人回来点那一下。
SESSION_KEEP_DAYS_PENDING = 30
# 内存里同时留几场会话。`SessionStore` 此前**完全没有淘汰**——进程活多久涨多久，
# 而 sidecar 是常驻的，读者一天划几百次词就是几百个 PenSession 挂在那儿。
#
# 32 是按实测最坏那场定的：24 条 messages / 66817 字符，**深估 364 KB**，
# 32 场约 11.4 MB。（v0.12.5 这里写的是「116 KB / 约 3 MB」，那是按 2 字节/字符
# 估的——59539 × 2 = 116.3 KB，正好对上——既漏了 Python 每个 str/dict 对象的头，
# 也低估了中文在 UTF-8 里的三字节，差了 3 倍多。上限 32 不改：11.4 MB 是
# **最坏**情形，而真实平均一场只有几 KB。）
# 淘汰只是把它从内存里放掉，磁盘上的快照一个字没动，下次 get() 现读回来。
MAX_LIVE_SESSIONS = 32

ENV_BASE_URL = "OPENAI_BASE_URL"
ENV_API_KEY = "OPENAI_API_KEY"
ENV_MODEL = "MODEL_NAME"
ENV_ALLOW_ROOTS = "PEN_ALLOW_ROOTS"
ENV_PEN_HOME = "PEN_HOME"

# 钥匙只认 lab 三件套，以及 DeepSeek 自己的名字。不把 KIMI_API_KEY 当成默认供应商。
_KEY_ALIASES = ("OPENAI_API_KEY", "DEEPSEEK_API_KEY")
DEEPSEEK_BASE = "https://api.deepseek.com"
# 与 lab/level2 README、Socrates-agent 注释一致
DEEPSEEK_MODEL = "deepseek-v4-flash"

# ── 托管密钥（v0.18.0）。设置页的钥匙只落这儿，vault 的 data.json 永远拿不到。 ──
# 之前 apiKey 明文存在 .obsidian/plugins/socrates-pen/data.json，跟着
# Sync / iCloud / git 走。现在唯一归宿是 PEN_DIR/llm.json（0600），
# resolve_llm 的优先级：托管文件 > 环境变量。env 路径保持原样，伺候无 UI 场景。
MANAGED_KEY_FILE = "llm.json"
MANAGED_KEY_SOURCE = "sidecar"


THINKING_LEVELS = ("off", "low", "medium", "high")

# ── 异步深挖（v0.8.1）──────────────────────────────────────────
# 总开关。PEN_PROBE=off 一刀关掉后台探索。
ENV_PROBE = "PEN_PROBE"
# 「实质回复」的门槛，和 tutor._finish_text 里 has_substantive 的判据保持一致。
PROBE_MIN_REPLY_CHARS = 80
# 每次探索的硬上限。不给模型 tools，读取由 Python 执行——
# 广度必须由代码封死，后台任务自主循环烧钱是看不见的。
# 调用次数不设常量：explore() 的结构就是「一次，需要正文时再一次」，
# 摆一个 PROBE_MAX_CALLS 在这里而代码不读它，改的人会以为改了有用。
# 上限由 test_probe.py 的两条断言锁住。
PROBE_MAX_READS = 2
PROBE_READ_LINES = 80
# 目录和 Q 清单进 system 的字符预算。build_user_packet 早就有 TOC_CHARS，
# 这边一直没有——换一本五倍厚的手册，system 会从 5.7k 涨到 23k token。
PROBE_TOC_CHARS = 6000
PROBE_Q_CHARS = 6000
# 后台任务，没人在等它，超时给宽一点。30 秒实测不够：单次输入约 6k token，
# 模型还要吐一段 JSON，真跑时直接 APITimeoutError，探索会静默失败，
# 读者永远看不到深题。主对话那条是 120 秒。
# 跨书那一轮的 prompt 要多背 3600 字符正文，90 秒偏紧。而 OpenAI SDK 默认
# max_retries=2——超时不是失败，是**再花一次输入 token 重来**，读者看不见。
# 实测一次跨书探索总耗时 351 秒，正好落在「90×3 + 退避」这个区间里。
PROBE_TIMEOUT = 150.0
# 配额。读者选的是「每轮实质回复都探」，所以不设轮次冷却，
# 这几个只是失控保护，不是降频手段。
PROBE_MAX_PER_SESSION = 8
# 窗口是**小时**，不是天。读者选的就是「每小时 40 次」，
# 先前实现成每天等于严了 24 倍。
PROBE_MAX_PER_WINDOW = 40
PROBE_CONCURRENCY = 2
# 手里还有这么多没抛出去的好问题时就别再探了——省的不是频率，是浪费。
PROBE_PENDING_CAP = 3
# 两次深挖之间至少隔几轮。**0 = 每轮实质回复都探**，就是读者当初选的行为。
# 默认 0，所以不改设置的读者一点感觉都没有。
PROBE_EVERY_N_ROUNDS = 0
# 一次深挖最多**入池**几条（过完全部校验之后）。
PROBE_KEEP_PER_RUN = 2
# 解析层最多**收**几条候选。和上面那个不是一回事：中间隔着一个损耗极大的
# 漏斗（depth<4 丢、validate_slots 丢、clean_candidates 丢、相似度 0.72 丢）。
# **不上设置页**，由 keep 推导 max(3, keep+1)：调大它只会让模型多吐几条废题，
# 多花输出 token 而入池数不变，纯亏。
PROBE_PARSE_CAP = 3
# 一次里最多几条 open 题（手册里没出处、凭模型记忆答、答时要在开头挑明）。
# **不上设置页**：它不是预算，是诚实策略。默认 keep=2 时若允许 2 条 open，
# 整整一轮的深挖可以全是无出处的——这条产品承诺不该做成旋钮让读者自己调没。
# 给它一个名字只是为了让它不再是一个匿名的 1。
PROBE_OPEN_PER_RUN = 1

# ── token 上限（v0.10.6）──────────────────────────────────────
# **全部默认 0 = 不限**，要读者自己填才生效。0 不是保守，是回归保证：
# 不填任何东西时 meter.over() 恒返回 False，四条代码路径的行为与 v0.10.5
# 逐字节一致。
#
# 分类不分总：不会因为后台深挖超支，把读者正在读的那轮主对话也掐了。
#
# ⚠️ 主对话那个**不是硬上限**：到线之后苏格拉底还会用手上的材料出一次答案，
# 那一枪不受限——不打它就等于把已经花掉的钱全扔了。设置页文案必须直说。
MAX_TOKENS_CHAT = 0
MAX_TOKENS_PROBE = 0
MAX_TOKENS_CROSS_BOOK = 0
# 主对话自动 compact 的窗口阈值。单位是上一枪的 prompt_tokens（此刻上下文占多大），
# 不是累计花销。0 = 不自动折（手动命令仍可用）。默认 32000：远低于常见 128k
# 窗口，长会话会真的折，而不是默默关掉。
COMPACT_CHAT_TOKENS = 32000

# ── Fast Mode（v0.22.0）──────────────────────────────────────
# 只读轮走的快模型。默认落点是 celeris-1-magnus，实测 249 tok/s。
# 和基座一样**全部可在设置页改**，这里只是不填时的回落。
FAST_BASE = "https://inference.celeris.ai/celeris-1-magnus/v1"
FAST_MODEL = "celeris-1-magnus"
# ⚠️ 快模型的窗口是 **input + max_tokens 合计**，而且供应商在**请求时**就硬拒，
# 不是生成到一半才截断。实测报文把两个数字直接念出来：
#   "you requested 16384 output tokens and your prompt contains at least
#    114689 input tokens, for a total of at least 131073 tokens"
# 所以输入的真上限是 FAST_WINDOW − FAST_MAX_OUTPUT = 114688，不是 131072。
# Budget.input_cap() 就是这条算式的唯一定义点，别在别处再算一遍。
FAST_WINDOW = 131072
FAST_MAX_OUTPUT = 16384
# 送进快模型的上下文目标。默认 90000 给硬顶留了 24688 的余量，那不是保守是必需：
# 路由和压缩都发生在 build_user_packet **之前**，本轮的 packet（最坏 ~16k 字符）
# 还没进去，而 _agent_loop 里每次 read_file 还能再加 5000 字符。
FAST_CONTEXT_TOKENS = 90000


def probe_enabled(env_file: Path | None = None) -> bool:
    raw = (os.environ.get(ENV_PROBE) or parse_dotenv(env_file).get(ENV_PROBE) or "").strip().lower()
    return raw not in ("off", "0", "false", "no")


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    key_source: str
    thinking: str = "off"
    vision: bool = False


@dataclass(frozen=True)
class RuntimeLimits:
    """一次请求认下来的全部上限。构造完不再变——后台探索线程拿的是它的引用。

    **为什么必须每请求透传，不能用全局可变槽**：sidecar 是多会话共享进程，
    设置页在 Obsidian 端，同一台 sidecar 可能同时伺候两个 vault。写全局槽 =
    A 库的设置串到 B 库的会话上，而且改设置的那一刻正在跑的会话会中途换闸。

    主对话线和 probe 线**共用这一个类，不拆两个**。前缀分区：
      probe_*  只有探索那条线读
      其余     只有主对话那条线读
    拆开的代价是两张 clamp 表 / 两个 merge / 两个请求体字段 / 两组词表键，
    收益只有「probe 线程手里少几个它不读的 int」。本仓「两个闸不同源」踩过三次。

    **没有字段默认值**，只能经 default_limits() 构造，或
    `dataclasses.replace(default_limits(), x=…)`。这样每个数字在全仓只出现
    一次（上面那些模块常量），default_limits() 是唯一的映射表。

    **只装已经有活消费方的旋钮。** 还没人读的字段一个都不加——那正是本文件
    上面写过的禁令（「摆一个常量而代码不读它，改的人会以为改了有用」）的
    翻版，而且做成界面上能填的旋钮比常量更糟。
    test_config.test_every_limit_is_actually_read_somewhere 机械地守着这条。
    """

    max_tool_rounds: int
    cross_book_chars: int
    cross_book_reads: int
    probe_max_per_session: int
    probe_max_per_window: int
    probe_pending_cap: int
    probe_max_reads: int
    probe_read_lines: int
    probe_timeout_s: float
    probe_min_reply_chars: int
    probe_concurrency: int
    # 两次深挖之间至少隔几轮。0 = 每轮都探（今天的行为）。
    probe_every_n_rounds: int
    # 一次深挖最多入池几条
    probe_keep_per_run: int
    # 解析层最多收几条候选。由 keep 推导，不上设置页。
    probe_parse_cap: int
    # 一次里最多几条 open 题。诚实策略，不上设置页。
    probe_open_per_run: int
    # 三个 token 上限。0 = 不限。
    max_tokens_chat: int
    max_tokens_probe: int
    max_tokens_cross_book: int
    # 自动把旧回合折进滚动摘要的窗口阈值。0 = 不自动折。
    compact_chat_tokens: int
    # 送进快模型的上下文目标（token）。0 = 不压，等于把 Fast Mode 的窗口守卫关掉。
    # 活消费方是 pen/compaction.py 的 FastWindow。
    fast_context_tokens: int


def default_limits() -> RuntimeLimits:
    """进程默认。**每次调用现读模块属性，不做模块级单例。**

    理由：`monkeypatch.setattr(config, "PROBE_MAX_PER_WINDOW", 3)` 是本仓
    唯一一处限流常量的测试打法，它能生效正是因为消费方走属性访问。
    做成 `DEFAULT_LIMITS = RuntimeLimits(...)` 就是把晚绑定又冻回导入期——
    等于在修 probe.py 那个信号量的同时新开一个一模一样的坑。
    """
    return RuntimeLimits(
        max_tool_rounds=MAX_TOOL_ROUNDS,
        cross_book_chars=CROSS_BOOK_CHARS,
        cross_book_reads=CROSS_BOOK_READS,
        probe_max_per_session=PROBE_MAX_PER_SESSION,
        probe_max_per_window=PROBE_MAX_PER_WINDOW,
        probe_pending_cap=PROBE_PENDING_CAP,
        probe_max_reads=PROBE_MAX_READS,
        probe_read_lines=PROBE_READ_LINES,
        probe_timeout_s=PROBE_TIMEOUT,
        probe_min_reply_chars=PROBE_MIN_REPLY_CHARS,
        probe_concurrency=PROBE_CONCURRENCY,
        probe_every_n_rounds=PROBE_EVERY_N_ROUNDS,
        probe_keep_per_run=PROBE_KEEP_PER_RUN,
        probe_parse_cap=PROBE_PARSE_CAP,
        probe_open_per_run=PROBE_OPEN_PER_RUN,
        max_tokens_chat=MAX_TOKENS_CHAT,
        max_tokens_probe=MAX_TOKENS_PROBE,
        max_tokens_cross_book=MAX_TOKENS_CROSS_BOOK,
        compact_chat_tokens=COMPACT_CHAT_TOKENS,
        fast_context_tokens=FAST_CONTEXT_TOKENS,
    )


# 字段 → (下限, 上限)。**只夹紧，不报错**：一个设置项绝不能把读者这一轮
# 对话弄挂。下限不是 0 的那几个都写了理由——0 在那里是「看起来像 bug 的关闭方式」。
LIMIT_RANGE: dict[str, tuple[float, float]] = {
    "max_tool_rounds": (1, 200),
    "cross_book_chars": (0, 400_000),  # 0 = 一本别的书都不翻，是合法选择
    "cross_book_reads": (0, 100),  # 同上
    "probe_max_per_session": (0, 200),  # 0 = 本场不探
    "probe_max_per_window": (0, 1000),  # 0 = 本小时不探
    # 0 会永久阻塞（pending_pool >= 0 恒真），那叫关掉，请用总开关
    "probe_pending_cap": (1, 50),
    "probe_max_reads": (0, 8),
    "probe_read_lines": (10, 400),  # 一行的摘录没有信息量
    "probe_timeout_s": (30.0, 300.0),
    "probe_min_reply_chars": (0, 2000),
    "probe_concurrency": (1, 8),  # 0 = 永不探，同 pending_cap 的理由
    "probe_every_n_rounds": (0, 20),  # 0 = 每轮都探 = 今天
    "probe_keep_per_run": (1, 5),
    "probe_parse_cap": (1, 10),
    "probe_open_per_run": (0, 3),
    # 0 = 不限；下限给到 1000 以下没意义（一次 system prompt 就 5.7k token）
    "max_tokens_chat": (0, 4_000_000),
    "max_tokens_probe": (0, 1_000_000),
    "max_tokens_cross_book": (0, 4_000_000),
    "compact_chat_tokens": (0, 500_000),
    # 上限就是 FAST_WINDOW - FAST_MAX_OUTPUT：填得再大也过不了供应商那关，
    # 与其让读者在对话里吃 400，不如在这里夹住。0 = 不压。
    "fast_context_tokens": (0, 114_688),
}


def merge_limits(raw: Mapping[str, Any] | None) -> RuntimeLimits:
    """请求体的 limits → 本次生效的上限。

    和 merge_llm 同一条路：请求体给了就用，没给的回落进程默认。差别在于
    **看不懂的一律当没给**而不是当 0——设置页填错一个字符不该把这一轮打挂，
    也不该把预算静默归零（那比报错更难查）。
    """
    base = default_limits()
    if not raw:
        return base
    clean: dict[str, Any] = {}
    for f in fields(RuntimeLimits):
        if f.name not in raw:
            continue
        got = raw[f.name]
        # bool 要先挡：isinstance(True, int) 为真、float(True) == 1.0，
        # 一个 JSON 里的 true 会静默变成上限 1。
        if got is None or isinstance(got, bool):
            continue
        try:
            val = float(got)
        except (TypeError, ValueError, OverflowError):
            # OverflowError 不能漏：JSON 允许任意精度整数，float(10**400) 抛的
            # 就是它。漏了的话读者拿到的是「对话中途出了意外错误」——正是
            # 请求体用 dict 而不是子模型时承诺要避免的那个形状。
            continue
        if val != val or val in (float("inf"), float("-inf")):
            continue
        lo, hi = LIMIT_RANGE[f.name]
        val = min(max(val, lo), hi)
        cur = getattr(base, f.name)
        clean[f.name] = float(val) if isinstance(cur, float) else int(val)

    # 漏斗不能被自己掐死：解析上限必须**大于**入池上限。读者把「每次产出」
    # 调到 4，而模型只被允许提 3 条，那个 4 就是个谎。
    # 默认档 keep=2 / parse=3 时 3 < 3 为假，一个字节都不动。
    keep = clean.get("probe_keep_per_run", base.probe_keep_per_run)
    parse = clean.get("probe_parse_cap", base.probe_parse_cap)
    if parse < keep + 1:
        clean["probe_parse_cap"] = int(min(LIMIT_RANGE["probe_parse_cap"][1], keep + 1))
    # open 题不能多过入池总数，否则「最多一条无出处」这条承诺就没意义了。
    if clean.get("probe_open_per_run", base.probe_open_per_run) > keep:
        clean["probe_open_per_run"] = int(keep)
    return replace(base, **clean)


def apply_pen_home(env_file: Path | None = None) -> Path:
    """若设置 PEN_HOME，把 PEN_DIR / LIBRARIES_DIR 挪到该目录。测试仍可 monkeypatch PEN_DIR。"""
    global PEN_DIR, LIBRARIES_DIR
    file_vals = parse_dotenv(env_file)
    raw = (os.environ.get(ENV_PEN_HOME) or file_vals.get(ENV_PEN_HOME) or "").strip()
    if raw:
        PEN_DIR = Path(raw).expanduser().resolve()
        LIBRARIES_DIR = PEN_DIR / "libraries"
    return PEN_DIR


def handbook_allow_roots(
    env_file: Path | None = None,
    extra_roots: list[Path] | None = None,
) -> list[Path]:
    """登记/写回教材必须落在这些根之下。默认仓库根；env 与请求体 extra_roots 追加。"""
    roots = [REPO_ROOT.resolve()]
    file_vals = parse_dotenv(env_file)
    raw = (os.environ.get(ENV_ALLOW_ROOTS) or file_vals.get(ENV_ALLOW_ROOTS) or "").strip()
    for part in raw.split(os.pathsep):
        piece = part.strip()
        if not piece:
            continue
        roots.append(Path(piece).expanduser().resolve())
    if extra_roots:
        for piece in extra_roots:
            roots.append(Path(piece).expanduser().resolve())
    return roots


def ensure_pen_dirs() -> None:
    LIBRARIES_DIR.mkdir(parents=True, exist_ok=True)
    (PEN_DIR / "trajectories").mkdir(parents=True, exist_ok=True)
    (PEN_DIR / "sessions").mkdir(parents=True, exist_ok=True)
    (PEN_DIR / "proposals").mkdir(parents=True, exist_ok=True)


def parse_dotenv(path: Path | None = None) -> dict[str, str]:
    dest = path or (REPO_ROOT / ".env")
    out: dict[str, str] = {}
    if not dest.is_file():
        return out
    for raw in dest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        else:
            value = value.split("#", 1)[0].strip()
        if key:
            out[key] = value
    return out


def _get(name: str, file_vals: dict[str, str]) -> str:
    return (os.environ.get(name) or file_vals.get(name) or "").strip()


def managed_key_path() -> Path:
    """每次现读 PEN_DIR。import 时冻结的话，测试 monkeypatch 根就够不着了。"""
    return PEN_DIR / MANAGED_KEY_FILE


# 托管文件里的两个**槽**。基座和快模型各占一对键，住同一个 0600 文件里。
# 一个槽 = (api_key 的键名, base_url 的键名)。这张表是「文件里有哪些键」的
# 唯一定义点——加第三个 provider 就在这儿加一行，别再开第三个文件。
_KEY_SLOTS: dict[str, tuple[str, str]] = {
    "base": ("api_key", "base_url"),
    "fast": ("fast_api_key", "fast_base_url"),
}


def _read_key_file() -> dict[str, Any]:
    """整个托管文件。打不开 / 坏 JSON / 不是对象一律当空——不炸设置页。"""
    try:
        data = json.loads(managed_key_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_key_file(data: dict[str, Any]) -> None:
    """0600 落盘。先写临时文件再 rename：半截写不在正主位置留明文。

    **全仓唯一的密钥落盘点。** 两个槽都从这儿走。
    """
    path = managed_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass  # Windows / 受限目录：尽力而为，文件本身仍然 0600
    # 临时文件用 mkstemp 起唯一名：固定名的话两个 vault 同时 PUT 会互删对方的
    # 半成品（unlink 清理是按名字找的）。mkstemp 生来 0600，同目录保证 rename 原子。
    fd, tmpname = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        # O_CREAT 的 mode 会被 umask 削，显式补一刀；文件已存在时 mode 根本不吃 O_CREAT。
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        # 半截写不带出去：失败路径也不许留一个装着明文的临时文件。
        tmp.unlink(missing_ok=True)
        raise


def read_key_slot(slot: str) -> dict[str, str] | None:
    """读一个槽。空 key 当没有。"""
    names = _KEY_SLOTS.get(slot)
    if names is None:
        return None
    k_key, k_url = names
    data = _read_key_file()
    key = str(data.get(k_key) or "").strip()
    if not key:
        return None
    return {"api_key": key, "base_url": str(data.get(k_url) or "").strip()}


def write_key_slot(slot: str, api_key: str, base_url: str = "") -> None:
    """写一个槽，**保住另一个槽**。

    读-改-写而不是整份覆盖：两个槽住同一个文件，直接覆盖等于填了快模型的
    钥匙就把基座的抹了。
    """
    names = _KEY_SLOTS.get(slot)
    if names is None:
        return
    k_key, k_url = names
    data = _read_key_file()
    data[k_key] = api_key
    data[k_url] = base_url
    _write_key_file(data)


def clear_key_slot(slot: str) -> None:
    """清一个槽。**只清自己那两键**；两个槽都空了才把文件删掉。

    直接 unlink 会把另一个槽一起带走——读者在设置页删掉快模型的钥匙，
    基座的也没了，而他完全不知道自己按了什么。
    """
    names = _KEY_SLOTS.get(slot)
    if names is None:
        return
    data = _read_key_file()
    for name in names:
        data.pop(name, None)
    # 剩下的键里还有哪个槽是活的？都空了就整份删掉，别在盘上留个空壳。
    alive = any(str(data.get(k) or "").strip() for k, _u in _KEY_SLOTS.values())
    if alive:
        _write_key_file(data)
    else:
        managed_key_path().unlink(missing_ok=True)


def read_managed_key() -> dict[str, str] | None:
    """基座那个槽。"""
    return read_key_slot("base")


def write_managed_key(api_key: str, base_url: str = "") -> None:
    return write_key_slot("base", api_key, base_url)


def clear_managed_key() -> None:
    clear_key_slot("base")


def read_fast_key() -> dict[str, str] | None:
    """快模型那个槽。"""
    return read_key_slot("fast")


def write_fast_key(api_key: str, base_url: str = "") -> None:
    return write_key_slot("fast", api_key, base_url)


def clear_fast_key() -> None:
    clear_key_slot("fast")


def resolve_llm(env_file: Path | None = None) -> LLMConfig | None:
    file_vals = parse_dotenv(env_file)
    managed = read_managed_key()
    if managed is not None:
        # 托管 key 优先于环境：设置页是普通读者唯一的入口，env 是无 UI 场景的后备。
        base = managed["base_url"] or DEEPSEEK_BASE
        return LLMConfig(
            base_url=base,
            api_key=managed["api_key"],
            model=_get(ENV_MODEL, file_vals) or DEEPSEEK_MODEL,
            key_source=MANAGED_KEY_SOURCE,
            thinking="off",
        )
    key = ""
    source = ""
    for name in _KEY_ALIASES:
        val = _get(name, file_vals)
        if val:
            key = val
            source = name
            break
    if not key:
        return None

    base = _get(ENV_BASE_URL, file_vals)
    model = _get(ENV_MODEL, file_vals)
    if not base:
        base = DEEPSEEK_BASE
    if not model:
        model = DEEPSEEK_MODEL
    return LLMConfig(base_url=base, api_key=key, model=model, key_source=source, thinking="off")


def normalize_thinking(raw: str | None) -> str:
    got = (raw or "").strip().lower()
    return got if got in THINKING_LEVELS else "off"


def _host_of(url: str) -> str:
    """小写 netloc，剥掉 userinfo。钥匙不跨主机挪用。"""
    return urlparse(url).netloc.lower().rsplit("@", 1)[-1]


def _merge_over(
    resolved: LLMConfig | None,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    thinking: str | None,
    vision: bool | None,
    default_base: str,
    default_model: str,
) -> LLMConfig | None:
    """请求体覆盖 + 跨主机钥匙保护。**这条规则的唯一实现。**

    基座和快模型各有自己的托管槽和默认值，但「钥匙不跨主机挪用」这一条对两者
    是同一条。写两份的话，下次有人加固基座那份，快模型这份就是个洞。
    """
    req_key = (api_key or "").strip()
    req_url = (base_url or "").strip()
    if req_key:
        key = req_key
        source = "settings"
    elif resolved and req_url and _host_of(req_url) != _host_of(resolved.base_url):
        # 换主机却没自带 key → 不挪用已解析的钥匙，让设置页自己填那台的。
        return None
    elif resolved:
        key = resolved.api_key
        source = resolved.key_source
    else:
        return None
    url = req_url or (resolved.base_url if resolved else default_base)
    name = (model or "").strip() or (resolved.model if resolved else default_model)
    return LLMConfig(
        base_url=url,
        api_key=key,
        model=name,
        key_source=source,
        thinking=normalize_thinking(thinking),
        vision=bool(vision),
    )


def merge_llm(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    vision: bool | None = None,
    env_file: Path | None = None,
) -> LLMConfig | None:
    """请求体非空字段优先，缺的回退 resolve_llm()（托管文件或 env）。两边都没有
    key → None。请求把 base_url 换到别的主机却没自带 key → 不挪用已解析的钥匙，
    返回 None，让设置页自己填那台主机的 key。"""
    return _merge_over(
        resolve_llm(env_file),
        api_key=api_key,
        base_url=base_url,
        model=model,
        thinking=thinking,
        vision=vision,
        default_base=DEEPSEEK_BASE,
        default_model=DEEPSEEK_MODEL,
    )


def resolve_fast_llm() -> LLMConfig | None:
    """快模型的托管配置。**只认托管槽，不吃 env。**

    基座那边留 env 后备是为了无 UI 场景（CI、脚本）。快模式是个界面开关，
    没有界面的场景压根不会打开它——再开一组 FAST_* 环境变量只是多两个
    没人读的名字。要它就在设置页填。
    """
    slot = read_fast_key()
    if slot is None:
        return None
    return LLMConfig(
        base_url=slot["base_url"] or FAST_BASE,
        api_key=slot["api_key"],
        model=FAST_MODEL,
        key_source=MANAGED_KEY_SOURCE,
        thinking="off",
    )


def merge_fast_llm(
    *,
    base_url: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    vision: bool | None = None,
) -> LLMConfig | None:
    """快模型的请求体覆盖。**没有 api_key 参数**——钥匙只走托管槽。

    对齐 v0.18.0 那条：密钥永远不随请求体上行（scripts/check-key.mjs 机械守着）。
    基座那边的 merge_llm 还留着 api_key 入参是历史兼容，新的这条不再开这个口。
    """
    return _merge_over(
        resolve_fast_llm(),
        api_key=None,
        base_url=base_url,
        model=model,
        thinking=thinking,
        vision=vision,
        default_base=FAST_BASE,
        default_model=FAST_MODEL,
    )


def openai_config() -> tuple[str | None, str | None, str | None]:
    cfg = resolve_llm()
    if cfg is None:
        return None, None, None
    return cfg.base_url, cfg.api_key, cfg.model


def fast_public_status() -> dict[str, str | bool]:
    """快模型的配置摘要，不含密钥。形状和 llm_public_status 一致，前端好复用。"""
    cfg = resolve_fast_llm()
    if cfg is None:
        return {"ok": False, "base_url": "", "model": "", "key_source": "", "key_tail": ""}
    return {
        "ok": True,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "key_source": cfg.key_source,
        "key_tail": cfg.api_key[-4:] if len(cfg.api_key) >= 12 else "",
    }


def llm_public_status() -> dict[str, str | bool]:
    """给前端看的配置摘要，不含密钥。key_tail 只露末 4 位，短钥匙干脆不露。"""
    cfg = resolve_llm()
    if cfg is None:
        return {
            "ok": False,
            "base_url": "",
            "model": "",
            "key_source": "",
            "thinking": "off",
            "key_tail": "",
        }
    return {
        "ok": True,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "key_source": cfg.key_source,
        "thinking": cfg.thinking,
        "key_tail": cfg.api_key[-4:] if len(cfg.api_key) >= 12 else "",
    }


apply_pen_home()
