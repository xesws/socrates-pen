"""压缩策略层。

**这个文件里最重要的是 test_fast_window_never_touches_the_session。**
Fast Mode 的全部安全性建立在「它只给副本」这一条上：`compact_session` 会把
`session.compacted` 置真，而全仓没有任何地方把它改回 False——快模式只要走一次
破坏性压缩，这场会话之后连基座轮次都永远拿不到目录 / 邻域 / 书架。
那条断言红了就不是「测试挂了」，是整个设计塌了。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pen.compact import compact_session
from pen.compaction import (
    Budget,
    CompactionPlan,
    FastWindow,
    RollingSummary,
    STRATEGIES,
    est_tokens,
    fast_budget,
    strategy_for,
)
from pen.config import FAST_MAX_OUTPUT, FAST_WINDOW, default_limits, merge_limits
from pen.session import PenSession

# 实测系数：celeris-1-magnus 上中文 0.583 token/字，20K / 60K / 120K 三档一致。
# 估算器必须**高于**这个数，否则卡不住硬窗口。
MEASURED_TOK_PER_CJK_CHAR = 0.583


def _packet(path: Path, *, extra: str = "（无，按芯片意图行动）") -> str:
    return f"""[来源]
handbook_path: {path}
level: Level 3
beat: 第四拍
q_title: 决策①
kind: q
lines: 954-964

[工作目录里的其他教材]
- 《另一本》  path: {path.parent / "other.md"}
  大纲：开篇

[全书目录（不要整本背诵）]
{chr(10).join(f"- Level {i}  L{900 + i}  决策" for i in range(60))}

[框选]
为什么非要两个网络

[邻域]
{"邻域正文很长很长" * 200}

[意图]
chip = socratic
先别揭晓。

[用户补充]
{extra}

[已经抛过的追问（别再重复这些）]
（还没抛过）
"""


def _session(book: Path, *, turns: int = 3) -> PenSession:
    """造一个「已经聊了几轮、翻过几次手册」的会话。

    turns 调大就能把上下文撑到任意体量——阶梯测试靠它制造超预算。
    """
    sess = PenSession(session_id="c" * 32, handbook_id="demo")
    sess.book_title = "从零手写DQN"
    sess.lang = "zh"
    sess.last_anchor = {
        "path": str(book),
        "start_line": 954,
        "end_line": 964,
        "level": "Level 3",
        "beat": "第四拍",
        "q_title": "决策①",
        "selected_text": "为什么非要两个网络",
    }
    body = "\n".join(f"{i}\t这一行是手册正文，内容不重要但要足够长才撑得起窗口" for i in range(10, 60))
    msgs: list[dict] = [
        {"role": "system", "content": "你是苏格拉底，正在带人读《从零手写DQN》。"}
    ]
    for n in range(turns):
        msgs.append({"role": "user", "content": _packet(book, extra=f"第 {n} 轮的补充")})
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{n}",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": str(book), "offset": 10, "limit": 50}),
                        },
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{n}", "content": body})
        msgs.append({"role": "assistant", "content": f"第 {n} 轮：那我问你，标签是 ground truth 吗？"})
    sess.messages = msgs
    sess.ui_messages = [{"role": "user", "text": "先别揭晓"}]
    sess.last_chips = [{"text": "监督回归的标签是自己估的？"}]
    sess.read_ok_paths = [str(book.resolve())]
    return sess


def _book(tmp_path: Path) -> Path:
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    (tmp_path / "other.md").write_text("# y\n", encoding="utf-8")
    return book


def _roots(tmp_path: Path) -> list[Path]:
    return [tmp_path / "book.md", tmp_path / "other.md"]


# ── 立身之本 ────────────────────────────────────────────────────────


def test_fast_window_never_touches_the_session(tmp_path: Path) -> None:
    """FastWindow 一个 session 字段都不许改。

    这是整个 Fast Mode 的地基。红了说明快模式又开始污染会话了——
    那意味着关掉开关之后，基座轮次也再拿不到目录 / 邻域 / 书架。
    """
    book = _book(tmp_path)
    sess = _session(book, turns=6)
    before = {
        "messages": copy.deepcopy(sess.messages),
        "read_ok_paths": list(sess.read_ok_paths),
        "compacted": sess.compacted,
        "last_context_tokens": sess.last_context_tokens,
        "last_anchor": copy.deepcopy(sess.last_anchor),
        "ui_messages": copy.deepcopy(sess.ui_messages),
    }
    plan = FastWindow().plan(
        sess, budget=Budget(target=200), allow_paths=_roots(tmp_path), original_path=book
    )
    assert plan.changed is True, "预算给到 200 还没动手，夹具没撑起来"
    assert sess.messages == before["messages"], "session.messages 被改了"
    assert sess.read_ok_paths == before["read_ok_paths"], "read_ok_paths 被清了"
    assert sess.compacted is before["compacted"] is False, "compacted 被置真——这就是那个不可逆的坑"
    assert sess.last_context_tokens == before["last_context_tokens"]
    assert sess.last_anchor == before["last_anchor"], "last_anchor 被就地改写了"
    assert sess.ui_messages == before["ui_messages"], "往侧栏插了 note"


def test_rolling_summary_also_only_plans(tmp_path: Path) -> None:
    """两个策略共用一条纪律：plan() 谁都不写回。

    破坏性只有 compact_session 一个入口。
    """
    book = _book(tmp_path)
    sess = _session(book)
    snapshot = copy.deepcopy(sess.messages)
    plan = RollingSummary().plan(
        sess, budget=Budget(target=0), allow_paths=_roots(tmp_path), original_path=book
    )
    assert plan.changed is True
    assert sess.messages == snapshot
    assert sess.compacted is False


def test_rolling_plan_matches_the_destructive_path(tmp_path: Path) -> None:
    """RollingSummary 和 compact_session 必须产出同一份 messages。

    两条路同源的证明。分家了就是「手动折」和「策略折」给出两种上下文。
    """
    book = _book(tmp_path)
    planned = RollingSummary().plan(
        _session(book), budget=Budget(target=0), allow_paths=_roots(tmp_path), original_path=book
    )
    sess = _session(book)
    compact_session(sess, allow_paths=_roots(tmp_path), original_path=book)
    assert planned.messages == sess.messages


# ── 够用就不动 ──────────────────────────────────────────────────────


def test_enough_room_returns_the_original_untouched(tmp_path: Path) -> None:
    book = _book(tmp_path)
    sess = _session(book)
    plan = FastWindow().plan(
        sess, budget=Budget(target=90000), allow_paths=_roots(tmp_path), original_path=book
    )
    assert plan.changed is False
    assert plan.steps == []
    assert plan.fits is True
    assert plan.messages == sess.messages, "预算够的时候必须逐字节原样返回"


def test_zero_target_means_no_squeeze(tmp_path: Path) -> None:
    """target=0 且窗口也不限 → 不压。旋钮的关闭档。"""
    book = _book(tmp_path)
    sess = _session(book, turns=8)
    plan = FastWindow().plan(
        sess,
        budget=Budget(target=0, window=0),
        allow_paths=_roots(tmp_path),
        original_path=book,
    )
    assert plan.changed is False
    assert plan.messages == sess.messages


# ── 阶梯 ────────────────────────────────────────────────────────────


def test_ladder_climbs_in_order_and_shrinks_every_step(tmp_path: Path) -> None:
    """预算越紧走得越深，且每多下一档 est_tokens 必须真的更小。

    只断言「压过了」不够：一档不起作用而下一档兜住的话，前一档就是死代码。

    **不写死「哪个预算走到哪一档」**——那会被环境晃到。夹具里的 packet 嵌了
    绝对路径，pytest 的 tmp_path 比手写临时目录长得多，光是这点差异就足以
    让同一个预算落到不同的档上（第一版就是这么误判过的）。这里只断言
    「更紧的预算 ⇒ 档位是前一档的超集，且估算不增」这条不变量。
    """
    book = _book(tmp_path)
    full = est_tokens(_session(book, turns=6).messages)
    # 从「够用」一路收到「怎么压都不够」，覆盖整条阶梯
    targets = [full * 2, full // 2, full // 6, full // 20, full // 60, 1]
    seen: list[tuple[list[str], int]] = []
    for target in targets:
        sess = _session(book, turns=6)
        plan = FastWindow().plan(
            sess, budget=Budget(target=target), allow_paths=_roots(tmp_path), original_path=book
        )
        seen.append((plan.steps, plan.est_tokens))

    assert seen[0][0] == [], "预算给到两倍还动手了"
    assert seen[-1][0] == ["stub-history", "slim-history", "fold"], (
        f"最紧的预算没走完整条阶梯：{seen[-1][0]}"
    )
    # 每一档都必须是前一档的前缀（阶梯只会往下走，不会跳档或换序）
    for (prev_steps, prev_est), (steps, est) in zip(seen, seen[1:]):
        assert steps[: len(prev_steps)] == prev_steps, f"档位跳了：{prev_steps} → {steps}"
        assert est <= prev_est, f"往下走一档反而变大了：{prev_est} → {est}"
    # 三档必须都真的出现过，否则中间那档是死代码
    assert {s for steps, _ in seen for s in steps} == {"stub-history", "slim-history", "fold"}


def test_last_turn_survives_until_the_fold_rung(tmp_path: Path) -> None:
    """没走到第 3 档时，最后一轮必须原封不动。

    那是模型这会儿正要用的材料，先扔它等于为了省窗口把这一轮问废。
    同样不写死预算，改成扫一串预算、对每个「没 fold」的结果验这条不变量。
    """
    book = _book(tmp_path)
    full = est_tokens(_session(book, turns=6).messages)
    checked_a_squeezed_one = False
    for target in (full * 2, full, full // 2, full // 3, full // 4, full // 6, full // 10):
        sess = _session(book, turns=6)
        tail = sess.messages[-1]
        plan = FastWindow().plan(
            sess, budget=Budget(target=target), allow_paths=_roots(tmp_path), original_path=book
        )
        if "fold" in plan.steps:
            continue
        assert plan.messages[-1] == tail, f"target={target} 没 fold 却动了最后一轮"
        # 最后一轮那次 read_file 的正文还在
        assert "这一行是手册正文" in json.dumps(plan.messages, ensure_ascii=False)
        if plan.changed:
            checked_a_squeezed_one = True
    assert checked_a_squeezed_one, "没有一个预算落在「压过但还没 fold」那一格，这条测试是空的"


def test_cannot_fit_says_so_instead_of_pretending(tmp_path: Path) -> None:
    """压到底还是超 → fits=False，让调用方退回基座。

    把上下文压成废墟去迁就窗口，还不如让大窗口的模型跑。
    """
    book = _book(tmp_path)
    sess = _session(book, turns=6)
    plan = FastWindow().plan(
        sess, budget=Budget(target=1), allow_paths=_roots(tmp_path), original_path=book
    )
    assert plan.fits is False
    assert plan.est_tokens > 1


def test_nothing_to_fold_is_honest(tmp_path: Path) -> None:
    """连一个真 user 包都没有时不假装压过。"""
    sess = PenSession(session_id="c" * 32, handbook_id="demo")
    sess.messages = [{"role": "system", "content": "系" * 5000}]
    plan = FastWindow().plan(sess, budget=Budget(target=10))
    assert plan.changed is False
    assert plan.fits is False


# ── 估算器 ──────────────────────────────────────────────────────────


def test_estimate_is_above_the_measured_truth_for_chinese() -> None:
    """中文的估值必须**高于**实测，否则卡不住硬窗口。

    估低了不是「差一点」，是对话中途吃供应商的 400。
    """
    text = "在深度 Q 网络里，经验回放缓冲区把转移四元组存起来，训练时均匀随机采样。" * 300
    msgs = [{"role": "user", "content": text}]
    real = len(text) * MEASURED_TOK_PER_CJK_CHAR
    got = est_tokens(msgs)
    assert got > real, f"估算 {got} 低于实测 {real:.0f}——会撑破窗口"
    assert got < real * 1.5, f"估算 {got} 比实测 {real:.0f} 高太多，会白折"


def test_estimate_counts_tool_call_arguments() -> None:
    """tool_calls 的 arguments 也占窗口。漏算它就会低估。"""
    bare = [{"role": "assistant", "content": ""}]
    with_args = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "x",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "p" * 900})},
                }
            ],
        }
    ]
    assert est_tokens(with_args) > est_tokens(bare)


def test_empty_is_zero_not_a_crash() -> None:
    assert est_tokens([]) == 0


# ── 预算 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "budget,want",
    [
        (Budget(target=90000), 90000),
        # 填得比物理上限还大 → 按物理上限走，不报错（对齐 merge_limits 只夹紧）
        (Budget(target=999_999), FAST_WINDOW - FAST_MAX_OUTPUT),
        # target=0 是「不限」，但窗口还在
        (Budget(target=0), FAST_WINDOW - FAST_MAX_OUTPUT),
        # 两个都不限
        (Budget(target=0, window=0), 0),
        # 窗口不限、目标给了 → 听目标的
        (Budget(target=5000, window=0), 5000),
    ],
)
def test_input_cap_reserves_the_output_half(budget: Budget, want: int) -> None:
    """窗口是 input + output 合计，输出那一半必须先扣掉。

    实测报文：prompt 114689 + max_tokens 16384 → 400。
    """
    assert budget.input_cap() == want


def test_fast_budget_reads_the_knob() -> None:
    assert fast_budget(default_limits()).target == default_limits().fast_context_tokens
    got = merge_limits({"fast_context_tokens": 12345})
    assert fast_budget(got).target == 12345


def test_knob_is_clamped_to_what_the_provider_will_take() -> None:
    """填 100 万也过不了供应商那关，在这儿就夹住。"""
    assert merge_limits({"fast_context_tokens": 1_000_000}).fast_context_tokens == 114_688
    assert merge_limits({"fast_context_tokens": -5}).fast_context_tokens == 0


# ── 注册表 ──────────────────────────────────────────────────────────


def test_strategy_lookup_falls_back_instead_of_raising() -> None:
    """认不出的路由回落 rolling（= 今天的行为），不抛。

    形状同 chips.chip_intent：未命中回落到一个明确的默认。
    """
    assert strategy_for("fast").name == "window"
    assert strategy_for("base").name == "rolling"
    assert strategy_for("").name == "rolling"
    assert strategy_for("没这个").name == "rolling"


def test_every_registered_strategy_answers_the_same_shape(tmp_path: Path) -> None:
    book = _book(tmp_path)
    for name, strat in STRATEGIES.items():
        plan = strat.plan(
            _session(book),
            budget=Budget(target=90000),
            allow_paths=_roots(tmp_path),
            original_path=book,
        )
        assert isinstance(plan, CompactionPlan), name
        assert isinstance(plan.messages, list) and plan.messages, name
        assert isinstance(plan.est_tokens, int), name
