"""芯片（chip）的意图层：固定芯片查表 + 读者自定义芯片的归一化与意图合成。

和另外两处的分工，先说清楚，别再长出第二张表：

- `pen/session.py` 的 `FIXED_CHIPS` —— 固定芯片的**花名册**（id / label / enabled / hint），
  它只管「侧栏底座上排几个按钮、叫什么名字」，一个字的 prompt 都不带。
- 这里的 `CHIP_INTENT` —— 那几枚按钮各自**注入什么指令**。v0.21.0 之前它住在
  `pen/tutor.py`，自定义芯片要用它拼写回纪律，留在 tutor 里就会和新模块循环 import。
  **搬走了就不留别名**：留一个 `CHIP_INTENT = chips.CHIP_INTENT` 就是第二个定义点，
  下次有人改这个而不是那个，两边就分家了（同 `tutor.py` 顶上 MAX_TOOL_ROUNDS 那段注释）。
- `pen/session.py` 的 `SYSTEM_PROMPT_TEMPLATE` 里那段「芯片意图：」—— 给模型的**总览**，
  和这里是一件事的两个高度：那边讲每枚芯片的行为轮廓，这里是本轮真正注进 packet 的那句。

自定义芯片住在**读者 vault 的 data.json 里**，后端一个字都不存；它随每一次
`POST /v1/chat` 的 `custom_chip` 字段上行。所以这里只有归一化和意图合成，没有存取。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# 写回纪律：**「先读后改」这条规矩的唯一定义点**。
#
# 固定的 writeback 芯片和读者自己勾了「会改写原文」的泡泡都用这一段，
# 下面 CHIP_INTENT["writeback"] 就是由它拼出来的。
#
# v0.21.0 之前这里是两份，而且互相打架：CHIP_INTENT["writeback"] 写着
# 「下一轮再单独 edit_file」，SYSTEM_PROMPT_TEMPLATE 写着「同一轮里做完」。
# 「下一轮」是我们自己写错的一句话——约束从来只是「拿到 read 的返回之后再改」，
# 模型逐字照做就会回一句「下一轮我就动手」然后停在那儿，读者白花一轮钱。
# 那次清理改了 SYSTEM_PROMPT 和 edit_file 的工具描述
# （pen/tests/test_agent.py 的 test_the_prompts_no_longer_say_next_round 盯着），
# **漏了这第三处**。v0.21.1 一并清掉，并把两份合成这一份。
#
# 「同一轮」不等于「同一批」：同批发出时模型还没看到原文，old_string 只能靠猜，
# 那条仍然被 pen/agent/permissions.py 的 read_first_block 硬闸拦着。措辞里
# 「看到它返回的带行号原文之后再 edit_file」说的正是这件事，别改松。
#
# 只放**机制**，不放「这是一次改写」的开场白——那句话两个调用方各说各的：
# 固定芯片说「把刚才的解答写进手册」，自定义泡泡说「上面这件事会改写手册原文」。
# 混进来的话，固定芯片那条会读成「把刚才的解答写进手册。这枚芯片会改写手册原文。」
WRITEBACK_DISCIPLINE = {
    "zh": (
        "先 read_file 同一路径，看到它返回的带行号原文之后再 edit_file，"
        "这两步在同一轮里接着做完，不要停下来等读者再说一遍。"
        "old_string 是去掉行号前缀后的纯原文，不要抄「12\\t」，且必须在文件里恰好出现一次。"
        "要写进手册的内容只放进 edit_file 的 new_string，聊天正文里不要再贴一遍。"
        "工具结果说「已编辑」之前，不要声称已经写盘。"
    ),
    "en": (
        "read_file the same path first, wait for the numbered "
        "original, then edit_file — finish both in the same turn, do not stop and wait for the "
        "reader to speak again. old_string is the raw original with the line-number prefix "
        'stripped (do not copy "12\\t"), and it must occur exactly once in the file. '
        "Put what goes into the handbook in edit_file's new_string only; do not paste it again "
        "in the chat reply. Do not claim it is on disk until the tool result says it was edited."
    ),
}


CHIP_INTENT = {
    "socratic": {
        "zh": "先别揭晓。只问一个问题，帮读者自己想。",
        "en": "Don't give it away. Ask one question and let the reader think.",
    },
    "explain_zero": {
        "zh": "假设读者零基础。按 TL;DR → (a)(b)(c) 讲完，再给两个可运行例子。",
        "en": "Assume the reader knows nothing. Teach TL;DR → (a)(b)(c), then two runnable examples.",
    },
    "examples": {
        "zh": "只举两个例子，紧贴本 Level 第七拍的名字。",
        "en": "Only two examples, using names that actually appear in this Level's seventh beat.",
    },
    "search": {
        "zh": "（未开放）不要假装检索。告诉读者 P2 才有联网。",
        "en": "(Not available.) Do not pretend you searched. Tell the reader web search is not on yet.",
    },
    # 「写什么」在前，「怎么写」直接取上面那一份，不再抄第二遍。
    "writeback": {
        "zh": "把刚才的解答写进手册。" + WRITEBACK_DISCIPLINE["zh"],
        "en": "Write the last answer into the handbook. " + WRITEBACK_DISCIPLINE["en"],
    },
    "free": {
        "zh": "按用户原话回答，仍守苏格拉底的人设。",
        "en": "Answer in the user's words, still in Socrates' voice.",
    },
}


def chip_intent(chip: str, lang: str) -> str:
    row = CHIP_INTENT.get(chip) or CHIP_INTENT["free"]
    return row["en"] if lang == "en" else row["zh"]


# ── 读者自定义的芯片 ──────────────────────────────────────────────────
#
# 它们住在读者 vault 的 `data.json` 里，后端一个字都不存，随每一次
# `POST /v1/chat` 的 `custom_chip` 字段上行。所以下面只有归一化和意图合成。

# id 的保留命名空间。前缀写死 "u."，撞不上 FIXED_CHIPS 的任何一个 id，
# 也撞不上 tutor 内部那个 "free"——`pen/app.py` 里 `chip == "search"` 的短路、
# `pen/probe.py` 的深挖门禁都按 id 精确匹配，命名空间隔开才不会误伤。
CUSTOM_ID_RE = re.compile(r"^u\.[A-Za-z0-9]{1,32}$")

# 三条长度闸。**前端 `src/customchips.ts` 有同名的第二副本**，由
# `scripts/check-chips.mjs` 机械地守着不许漂：前端那份只管 UX（输入框旁边的
# 字数提示），后端这份才是权威——和 `merge_limits` 的分工一模一样。
LABEL_MAX = 40
HINT_MAX = 80
# 4000 不是 2000：三段预置模板的英文版实测最长 1.6k，2000 只留一点余量，
# 读者照着改两句就顶线，而顶线的表现是**静默截掉尾巴**——被切掉的恰好是
# 写在最后的格式硬约束。宁可给足。
PROMPT_MAX = 4000

# 协议的**带内记号**。读者在自己的泡泡指令里写下它们，会被下游当成协议的一部分：
#   - `<!--pen:compact-->` 让 `compact.is_summary_message()` 把这一轮误判成滚动摘要
#     （`pen/compact.py:110-111`），这场会话的自动折叠从此错位；
#   - `<!--pen:chips` 让 `parse_dynamic_chips()` 从这里开始切（`pen/tutor.py:251`）。
# 剥掉。这不是防谁使坏——是别让读者随手写的一句话把协议顶穿。
_INBAND = ("<!--pen:compact-->", "<!--pen:chips")

# packet 的段头形状。读者的 prompt 原样拼进 `[意图]` 段，一行 `[框选]` 就能在
# 后面凭空开出第二个「框选」段，而 `pen/compact.py:369` 的 `_section_named`
# 是按段头找的。**只在行首前面塞一个空格**，不删字：读者写的还在，只是不再是段头。
# 行首段头。**方括号里不设长度上限**：下游 pen/compact.py 的 _section_named
# 认的是 `\[名字[^\]]*\]`，长度无限。这边原来写 {1,40}，于是括号里写满 41 字的
# `[用户补充xxxx…]` 绕过 defang、在 [意图] 段里开出一个真段头 —— 读者自己泡泡里
# 的话被当成「读者更正」写进滚动摘要，真正的 [用户补充] 段被遮蔽。两边必须同源。
_SECTION_HEAD = re.compile(r"^(\[[^\]\n]*\])", re.M)

# C0 控制符（保留 \n 和 \t）。TS 模板字符串里一个手滑的 `\b` 就是个退格符，
# 落进 data.json 谁都看不出来。
_LINE_SEP = re.compile(r"\r\n?|\u2028|\u2029")
_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 落单的代理项。Python 自己切不出这东西（str 按码点走），但**坏客户端发得出**：
# JS 那边一个 `.slice()` 劈开 emoji 就是半个，JSON.stringify 成 `\ud83d` 一路进来。
# 它在这儿看着人畜无害，等到按 UTF-8 写会话时才炸 UnicodeEncodeError，
# 把那一整场会话卡死——离现场很远的那种崩。前端已经不再造它（clampChars 按码点切），
# 这一道是给别的客户端兜的。
_LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")

_BLANKS = re.compile(r"\n{3,}")


def sanitize_prompt(raw: Any) -> str:
    """读者写的那段 prompt → 可以安全拼进 packet 的文本。**只夹紧，绝不抛。**"""
    if not isinstance(raw, str):
        return ""
    # 行分隔符全部归一成 \n，并剥掉 BOM。见 src/customchips.ts 同一处的注释：
    # JS 的 `^`（/m）认 U+2028 / U+2029 为行首、trim() 还吃 BOM，Python 都不认。
    # 不归一，「行首段头」这条规则在两边的判定就不一样。
    text = _LINE_SEP.sub("\n", raw).replace("\ufeff", "")
    text = _C0.sub("", text)
    text = _LONE_SURROGATE.sub("", text)
    for mark in _INBAND:
        text = text.replace(mark, "")
    text = _BLANKS.sub("\n\n", text).strip()
    # 夹紧排在 defang **之前**，defang 之后不再夹第二次。
    # 反过来写的话：defang 每遇到一个行首段头就塞一个空格，**把串撑长**，
    # 随后那一刀就从尾巴上多切掉同样多的字——而读者的格式硬约束恰恰写在最后。
    # 前端设置页那行「4000 / 4000」按夹紧后的真长度显示，两边这才对得上。
    # 代价是返回值可能比 PROMPT_MAX 多几个空格：无所谓，这个上限是拦
    # 「粘一整本书」的，不是精确配额，多出来的每个字节都不是读者写的内容。
    text = text[:PROMPT_MAX]
    # defang 排在 strip 之后：反过来写的话，prompt 第一行就是 `[框选]` 的那一格里，
    # 塞进去的那个空格正好在字符串开头，会被 strip 当空白清掉——
    # 于是最该防的那一格（读者从别处整段粘过来、第一行就是段头）恰好漏网。
    return _SECTION_HEAD.sub(r" \1", text)


def _one_line(raw: Any, cap: int) -> str:
    """label / hint 压成一行。它们要么进按钮，要么进 tooltip，换行只会撑破排版。"""
    if not isinstance(raw, str):
        return ""
    return " ".join(_C0.sub("", raw).split())[:cap]


@dataclass(frozen=True)
class CustomChipSpec:
    id: str
    label: str
    prompt: str
    writeback: bool
    # 下一版接格式校验器用。这一版**写而不读**：线上形状先定死，
    # 免得下次加校验时还要再改一遍前后端两侧的契约。
    format: str = ""


def normalize_custom_chip(raw: Any) -> CustomChipSpec | None:
    """线上那个 `custom_chip` 对象 → 可用的 spec。**只夹紧，绝不抛。**

    看不懂就返回 `None`，调用方当没给，退回按 chip id 查 `CHIP_INTENT`。
    这一条对齐 `merge_limits`：读者在设置页写了个奇怪的东西，该看到夹紧后的
    正常回复，不是一个红色 422。
    """
    if not isinstance(raw, dict):
        return None
    chip_id = raw.get("id")
    if not isinstance(chip_id, str) or not CUSTOM_ID_RE.match(chip_id):
        return None
    prompt = sanitize_prompt(raw.get("prompt"))
    if not prompt:
        # 没有 prompt 的自定义芯片和 `free` 一模一样，没有存在的理由。
        return None
    return CustomChipSpec(
        id=chip_id,
        label=_one_line(raw.get("label"), LABEL_MAX),
        prompt=prompt,
        writeback=raw.get("writeback") is True,
        format=_one_line(raw.get("format"), 32),
    )


def custom_intent(spec: CustomChipSpec, lang: str) -> str:
    """`[意图]` 段的正文 = 读者写的那段 +（勾了写回时）内置写回纪律。

    纪律**不在前端拼**：前端拼就等于把这段文案抄了第二份，下次改只会改到一边。
    前端只发一个布尔。
    """
    if not spec.writeback:
        return spec.prompt
    en = lang == "en"
    # 开场白是这一侧自己的：读者写的那段说的是「做什么」，得有一句把它和
    # 下面那套改写机制接上，否则纪律像是凭空冒出来的另一件事。
    lead = "上面这件事会改写手册原文。" if not en else "The task above rewrites the handbook. "
    return f"{spec.prompt}\n{lead}{WRITEBACK_DISCIPLINE['en' if en else 'zh']}"
