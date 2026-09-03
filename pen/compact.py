"""主对话滚动摘要：抽取式，不加第二次 LLM 调用。

折的是 `session.messages`（下次请求喂给模型的上下文），不是侧栏
`ui_messages`。旧气泡还在；摘要里必留行号锚点 / 书架 path / 读者纠正，
丢掉的 `read_file` 正文下次必须再读。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pen.clock import now_iso
from pen.config import SELECTED_TEXT_CHARS
from pen.session import PenSession
from pen.vision import IMAGE_PLACEHOLDER, content_text, has_image_parts

SUMMARY_MARK = "<!--pen:compact-->"
NOTE_KIND = "compact"

# 读者明确否定。宁可漏、不要把「为什么不对」这种提问收成纠正。
_NEG = re.compile(
    r"(?:^|[\s，。！？])(?:不对|不是这样|你说错|搞错了|说错了)"
    r"|(?:that's not|you'?re wrong|not true|no, that(?:'s| is) not)",
    re.I,
)
_NUMLINE = re.compile(r"^(\d+)\t", re.M)
_LINES_FIELD = re.compile(r"^lines:\s*(\d+)\s*-\s*(\d+)\s*$", re.M)
_FIELD = re.compile(r"^(level|beat|q_title|kind|handbook_path):\s*(.+)\s*$", re.M)
_LREF = re.compile(r"\bL(\d+)(?:-(\d+))?\b|第\s*(\d+)\s*(?:-|–|—)\s*(\d+)\s*行")


class CompactPending(Exception):
    """有待审批的编辑时拒绝 compact。messages 一个字都不能动。"""


@dataclass
class CompactResult:
    did: bool
    dropped_reads: int = 0
    kept_user_packets: int = 0


@dataclass
class Fold:
    """一次折叠的**结果**，纯数据，不含任何 session 状态的改动。

    这个类型存在的理由：「怎么压」和「压完写给谁」以前是焊死的——
    `compact_session` 一口气既算出新 messages 又就地改掉 session，于是
    想复用那套抽槽逻辑就必然连带把会话改坏。拆开之后：

      fold_messages()   纯函数，只读入参，算出新 messages       ← 唯一的「怎么压」
      compact_session() 破坏性外壳，写回 session               ← Base / 手动命令
      pen/compaction.py FastWindow 拿同一份结果当副本用         ← Fast Mode

    Fast 那一路**绝不能**走破坏性外壳：`session.compacted` 一旦为真就
    全仓无处置回 False，之后连基座轮次都永远拿不到目录 / 邻域 / 书架。
    """

    messages: list[dict[str, Any]]
    dropped_reads: int


@dataclass
class _Slots:
    book: str = ""
    shelf: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    cracks: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    writebacks: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def cap_selected_text(text: str, *, lang: str = "zh", start_line: int = 0, end_line: int = 0) -> tuple[str, bool]:
    """划选硬上限。返回 (写入 packet 的文本, 是否截过)。"""
    raw = text or ""
    if len(raw) <= SELECTED_TEXT_CHARS:
        return raw, False
    head = raw[:SELECTED_TEXT_CHARS]
    span = f"{start_line}-{end_line}" if start_line and end_line else "—"
    if lang == "en":
        note = (
            f"\n\n(Selection is {len(raw)} characters; only the first "
            f"{SELECTED_TEXT_CHARS} are in this packet. read_file the handbook "
            f"around lines {span} and expand as needed. Do not pretend you read the rest.)"
        )
    else:
        note = (
            f"\n\n（划选共 {len(raw)} 字，这里只留前 {SELECTED_TEXT_CHARS} 字。"
            f"先 read_file handbook_path 的第 {span} 行附近，再按需展开。"
            f"不要假装已经读过全文。）"
        )
    return head + note, True


def message_chars(messages: Sequence[dict[str, Any]]) -> int:
    """粗算上下文字符数。供应商不回 usage 时拿它 / 2 当 token 估。"""
    n = 0
    for m in messages:
        n += len(content_text(m.get("content")))
        # 思考正文是**真上线的字节**（thinking 模式下节点要求回传），所以它
        # 必须进估算——快模型那道硬窗口闸量的正是「这一枪有多大」。
        n += len(str(m.get("reasoning_content") or ""))
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            n += len(str(fn.get("arguments") or "")) + len(str(fn.get("name") or ""))
    return n


def should_auto_compact(session: PenSession, limits: Any) -> bool:
    """自动折的判据。pending 或阈值为 0 都不折。

    读 `limits.compact_chat_tokens`：这是 RuntimeLimits 上这个旋钮的活消费方，
    `test_every_limit_is_actually_read_somewhere` 盯着这个名字。
    """
    if session.pending:
        return False
    cap = int(getattr(limits, "compact_chat_tokens", 0) or 0)
    if cap <= 0:
        return False
    last = int(getattr(session, "last_context_tokens", 0) or 0)
    if last >= cap:
        return True
    if last == 0:
        return (message_chars(session.messages) / 2) >= cap
    return False


def is_summary_message(msg: dict[str, Any]) -> bool:
    return msg.get("role") == "user" and SUMMARY_MARK in content_text(msg.get("content"))


def _is_force_answer(content: str) -> bool:
    """收口枪塞进 messages 的假 user，不能当 last_turn 边界。"""
    from pen.tutor import FORCE_ANSWER, FORCE_ANSWER_BUDGET

    text = (content or "").strip()
    if not text:
        return False
    for table in (FORCE_ANSWER, FORCE_ANSWER_BUDGET):
        if text in {table["zh"].strip(), table["en"].strip()}:
            return True
    return False


def split_last_turn(
    messages: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]] | None:
    """切成 `(system, 更早的回合, 最后一轮)`。**「最后一轮从哪儿开始」的唯一定义点。**

    返回 None = 没什么可切：空表，或者往回找不到一个真的 user 包
    （只剩旧摘要和收口枪的假 user）。

    边界是「最后一个**真** user 消息」：滚动摘要自己是 user 角色，收口枪也往
    messages 里塞过一条假 user，两者都不能当边界——test_compact 的
    test_force_answer_user_is_not_last_turn 钉着这条。
    """
    msgs = list(messages or [])
    if not msgs:
        return None

    system: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for m in msgs:
        if not rest and m.get("role") == "system" and not system:
            system.append(m)
        else:
            rest.append(m)

    last_user = -1
    for i, m in enumerate(rest):
        if m.get("role") != "user":
            continue
        if is_summary_message(m) or _is_force_answer(content_text(m.get("content"))):
            continue
        last_user = i
    if last_user < 0:
        return None
    return system, rest[:last_user], rest[last_user:]


def fold_messages(
    messages: Sequence[dict[str, Any]],
    *,
    allowed: Sequence[Path],
    lang: str = "zh",
    book_title: str = "",
    last_chips: Sequence[dict[str, Any]] = (),
    last_anchor: dict[str, Any] | None = None,
) -> Fold | None:
    """把 messages 折成 `[system, 摘要, 最后一轮]`。**只读入参，一个字都不写回。**

    没什么可折时返回 `None`——空表，或只剩一份旧摘要 / 收口话术。
    调用方拿到 None 就当没折（`compact_session` 回 `did=False`）。

    `allowed` 是**已经解析过的**白名单（`_allowed_files` 的产物）。这里不自己
    去算，是因为它要读 `session.last_anchor`，而这个函数的立身之本就是不碰
    session——白名单由调用方备好，两条路（破坏性 / 副本）各自决定怎么备。
    """
    cut = split_last_turn(messages)
    if cut is None:
        return None
    system, middle, last_turn = cut

    slots = _Slots()
    _eat_system_book(slots, system)
    if (book_title or "").strip() and not slots.book:
        slots.book = book_title.strip()
    all_rest = middle + last_turn
    calls = _tool_calls_by_id(all_rest)
    for m in all_rest:
        _eat_message(slots, m, allowed, calls)
    for chip in last_chips or []:
        text = str(chip.get("text") or "").strip()
        if text and text not in slots.cracks:
            slots.cracks.append(text)
    _eat_anchor(slots, last_anchor)

    stubbed_last, dropped_n = _stub_tools(last_turn, allowed, lang)
    slimmed_last = [_slim_if_user(m, lang) for m in stubbed_last]
    dropped_n += sum(
        1
        for m in middle
        if m.get("role") == "tool" and str((calls.get(str(m.get("tool_call_id") or "")) or {}).get("name") or "") == "read_file"
    )

    summary = _render_summary(slots, lang)
    return Fold(
        messages=[*system, {"role": "user", "content": summary}, *slimmed_last],
        dropped_reads=dropped_n,
    )


def compact_session(
    session: PenSession,
    *,
    allow_paths: Sequence[Path],
    original_path: Path | None = None,
) -> CompactResult:
    """就地改 `session.messages` / `read_ok_paths` / `ui_messages` 的 note。

    待审批时抛 `CompactPending`，调用方不得再 save。

    **这是破坏性的那一条路**：`compacted` 置真之后全仓没有任何地方改回来，
    之后每个 packet 都永久走精简路径。要一份不污染会话的压缩，走
    `fold_messages()` 或 `pen/compaction.py` 的 FastWindow。
    """
    if session.pending:
        raise CompactPending()
    allowed = _allowed_files(allow_paths, original_path, session)
    fold = fold_messages(
        session.messages,
        allowed=allowed,
        lang=session.lang,
        book_title=session.book_title,
        last_chips=session.last_chips,
        last_anchor=session.last_anchor,
    )
    if fold is None:
        return CompactResult(did=False)

    session.messages = fold.messages
    session.read_ok_paths = []
    session.compacted = True
    # 折完之后上一枪的 prompt_tokens 不再描述当前窗口。清零让下次
    # should_auto_compact 走 message_chars/2，避免供应商不回 usage 时每轮都折。
    session.last_context_tokens = 0
    _upsert_note(session)
    if isinstance(session.last_anchor, dict):
        raw = str(session.last_anchor.get("selected_text") or "")
        if len(raw) > SELECTED_TEXT_CHARS:
            capped, _ = cap_selected_text(
                raw,
                lang=session.lang,
                start_line=int(session.last_anchor.get("start_line") or 0),
                end_line=int(session.last_anchor.get("end_line") or 0),
            )
            session.last_anchor["selected_text"] = capped
            session.last_anchor["selection_capped"] = True
    return CompactResult(did=True, dropped_reads=fold.dropped_reads, kept_user_packets=1)


def allow_paths_for(original_path: Path) -> list[Path]:
    """摘要里**允许指名道姓**的文件：当前笔记 + 已登记书架。不把 vault 当白名单。

    **这条规则的唯一定义点。** 手动 /compact、自动折叠、Fast Mode 的窗口闸
    三处共用它——分家就会出现「同一段历史在不同入口下，路径一会儿露出来
    一会儿变成 (path omitted)」。

    libraries 读不出来就只给当前笔记：路径少露几条只是摘要里少几个可点的
    线索，不该把一次压缩弄挂。
    """
    paths = [original_path]
    try:
        from pen import libraries

        paths.extend(Path(m.original_path) for m in libraries.list_handbooks())
    except Exception:
        pass
    return paths


def _allowed_files(
    allow_paths: Sequence[Path],
    original_path: Path | None,
    session: PenSession,
) -> list[Path]:
    """只认当前笔记和书架上的文件，不把父目录当成白名单。"""
    out: list[Path] = []
    for r in allow_paths:
        try:
            out.append(Path(r).expanduser().resolve())
        except Exception:
            continue
    if original_path is not None:
        try:
            out.append(Path(original_path).expanduser().resolve())
        except Exception:
            pass
    if isinstance(session.last_anchor, dict):
        raw = str(session.last_anchor.get("path") or "")
        if raw:
            try:
                out.append(Path(raw).expanduser().resolve())
            except Exception:
                pass
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _allowed(raw: str, allowed_files: Sequence[Path]) -> str | None:
    if not raw or not allowed_files:
        return None
    try:
        p = Path(raw).expanduser().resolve()
    except Exception:
        return None
    for a in allowed_files:
        try:
            if p == a:
                return str(p)
            if p.exists() and a.exists() and p.samefile(a):
                return str(p)
        except Exception:
            continue
    return None


def _eat_system_book(slots: _Slots, system: Sequence[dict[str, Any]]) -> None:
    if slots.book or not system:
        return
    text = str(system[0].get("content") or "")
    m = re.search(r"《([^》]+)》", text)
    if m:
        slots.book = m.group(1).strip()
        return
    m = re.search(r'a handbook called "([^"]+)"', text)
    if m:
        slots.book = m.group(1).strip()


def _eat_anchor(slots: _Slots, a: dict[str, Any] | None) -> None:
    """把 last_anchor 抽成一行锚点。收的是那个 dict 本身，不是 session——
    fold_messages 的纯度全靠这一条。"""
    if not isinstance(a, dict):
        return
    start = int(a.get("start_line") or 0)
    end = int(a.get("end_line") or 0)
    if not (start and end):
        return
    snippet = " ".join(str(a.get("selected_text") or "").split())[:80]
    line = f"L{start}-{end} {a.get('level') or '—'} / {a.get('beat') or '—'} / {a.get('q_title') or '—'}"
    if snippet:
        line += f" 「{snippet}」"
    if line not in slots.anchors:
        slots.anchors.insert(0, line)


def _eat_message(
    slots: _Slots,
    msg: dict[str, Any],
    roots: Sequence[Path],
    calls: dict[str, dict[str, Any]],
) -> None:
    role = str(msg.get("role") or "")
    if role == "user":
        content = content_text(msg.get("content"))
        if is_summary_message(msg):
            _eat_summary(slots, content, roots)
            return
        _eat_user_packet(slots, content, roots)
        if has_image_parts(msg.get("content")):
            note = "pasted image (pixels dropped; paste again to look)"
            if note not in slots.dropped:
                slots.dropped.append(note)
        return
    if role == "assistant":
        _eat_assistant(slots, content_text(msg.get("content")))
        return
    if role == "tool":
        _eat_tool_result(slots, msg, roots, calls)


def _eat_summary(slots: _Slots, content: str, roots: Sequence[Path]) -> None:
    book = _section(content, "compact.book")
    if book and not slots.book:
        slots.book = book.splitlines()[0].strip().strip("《》").strip('"')
    for line in _section(content, "compact.shelf").splitlines():
        raw = line.strip().lstrip("- ").strip()
        hit = _allowed(raw, roots)
        if hit and hit not in slots.shelf:
            slots.shelf.append(hit)
    _extend_unique(slots.anchors, _section(content, "compact.anchors"))
    _extend_unique(slots.cracks, _section(content, "compact.cracks"))
    _extend_unique(slots.corrections, _section(content, "compact.corrections"))
    _extend_unique(slots.writebacks, _section(content, "compact.writebacks"))
    for line in _section(content, "compact.dropped").splitlines():
        item = line.strip().lstrip("- ").strip()
        if not item or item in ("（无）", "(none)"):
            continue
        m = re.match(r"^(.*?)\s+L(\d+)-(\d+)$", item)
        if m:
            hit = _allowed(m.group(1).strip(), roots)
            if hit:
                rebuilt = f"{hit} L{m.group(2)}-{m.group(3)}"
                if rebuilt not in slots.dropped:
                    slots.dropped.append(rebuilt)
            continue
        hit = _allowed(item, roots)
        if hit and hit not in slots.dropped:
            slots.dropped.append(hit)


def _extend_unique(dest: list[str], block: str) -> None:
    for line in block.splitlines():
        item = line.strip().lstrip("- ").strip()
        if not item or item in ("（无）", "(none)"):
            continue
        if item not in dest:
            dest.append(item)


def _section(content: str, name: str) -> str:
    # `^` + re.M：段头只认**行首**。真实 packet 里段头永远在第 0 列（都是
    # build_user_packet 拼的），所以这是纯硬化，合法输入一个字都不受影响。
    # 不锚的话，正文里随手一句「见 [框选] 那段」就能凭空开出第二个段。
    # v0.21.0 起 [意图] 段里会原样带上**读者自己写的**那段 prompt，这条从
    # 「理论上」变成了「一定会遇到」——chips.sanitize_prompt 把行首段头前面
    # 塞一个空格来 defang，而那个 defang 只有在这里锚了行首之后才真的生效。
    m = re.search(
        rf"^\[{re.escape(name)}\]\n(.*?)(?=\n\[compact\.|\Z)",
        content,
        re.S | re.M,
    )
    return (m.group(1) if m else "").strip()


def _eat_user_packet(slots: _Slots, content: str, roots: Sequence[Path]) -> None:
    fields = {k: v.strip() for k, v in _FIELD.findall(content)}
    start = end = 0
    lm = _LINES_FIELD.search(content)
    if lm:
        start, end = int(lm.group(1)), int(lm.group(2))
    sel = _section_named(content, ("框选", "Selection"))
    snippet = " ".join((sel or "").split())[:80]
    level = fields.get("level") or "—"
    beat = fields.get("beat") or "—"
    q_title = fields.get("q_title") or "—"
    if start and end:
        line = f"L{start}-{end} {level} / {beat} / {q_title}"
        if snippet:
            line += f" 「{snippet}」"
        if line not in slots.anchors:
            slots.anchors.append(line)
    for raw_line in _section_named(content, ("工作目录里的其他教材", "Other handbooks in the workspace")).splitlines():
        m = re.search(r"path:\s*(.+?)\s*$", raw_line)
        if not m:
            continue
        hit = _allowed(m.group(1).strip(), roots)
        if hit and hit not in slots.shelf:
            slots.shelf.append(hit)
    extra = _section_named(content, ("用户补充", "Reader's note"))
    if extra and _NEG.search(extra):
        clip = " ".join(extra.split())[:160]
        if clip not in slots.corrections:
            slots.corrections.append(clip)


def _section_named(content: str, names: tuple[str, ...]) -> str:
    for name in names:
        # 同 _section：段头只认行首。注意结束的那个 lookahead `\n\[` 本来就
        # 是锚在行首的，起头这边不锚就是两头不同源——一句话既开不了真的段，
        # 又能把真的段提前掐断。
        m = re.search(
            rf"^\[{re.escape(name)}[^\]]*\]\n(.*?)(?=\n\[|\Z)",
            content,
            re.S | re.M,
        )
        if m:
            return m.group(1).strip()
    return ""


def _eat_assistant(slots: _Slots, content: str) -> None:
    if not content:
        return
    for raw in content.splitlines():
        line = raw.strip().lstrip("-* ").strip()
        if not line:
            continue
        if _LREF.search(line) or ("？" in line or "?" in line):
            if len(line) > 200:
                line = line[:200] + "…"
            if line not in slots.cracks and ("？" in line or "?" in line):
                slots.cracks.append(line)


def _tool_calls_by_id(messages: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            tid = str(tc.get("id") or "")
            fn = tc.get("function") or {}
            args: dict[str, Any] = {}
            raw = fn.get("arguments") or "{}"
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        args = parsed
                except json.JSONDecodeError:
                    args = {}
            elif isinstance(raw, dict):
                args = raw
            out[tid] = {"name": str(fn.get("name") or ""), "args": args}
    return out


def _eat_tool_result(
    slots: _Slots,
    msg: dict[str, Any],
    roots: Sequence[Path],
    calls: dict[str, dict[str, Any]],
) -> None:
    tid = str(msg.get("tool_call_id") or "")
    meta = calls.get(tid) or {}
    name = str(meta.get("name") or "")
    args = dict(meta.get("args") or {})
    content = content_text(msg.get("content"))
    if name == "read_file":
        path = _allowed(str(args.get("path") or ""), roots)
        span = _line_span(content)
        if path:
            item = f"{path} L{span[0]}-{span[1]}" if span else path
            if item not in slots.dropped:
                slots.dropped.append(item)
    elif name == "edit_file" and "已编辑" in content:
        line = content.split("\n", 1)[0].strip()[:200]
        if line and line not in slots.writebacks:
            slots.writebacks.append(line)


def _stub_tools(
    messages: list[dict[str, Any]], roots: Sequence[Path], lang: str = "zh"
) -> tuple[list[dict[str, Any]], int]:
    calls = _tool_calls_by_id(messages)
    dropped = 0
    out: list[dict[str, Any]] = []
    en = lang == "en"
    drop_note = (
        "Body dropped from context. To quote or edit_file this passage, read_file again. Old reads do not count."
        if en
        else "正文已从上下文拿掉。要引用或 edit_file 这段，必须再 read_file。旧读不算数。"
    )
    other_note = " (result folded into the summary)" if en else "  （结果已折进摘要）"
    for m in messages:
        if m.get("role") != "tool":
            out.append(m)
            continue
        tid = str(m.get("tool_call_id") or "")
        meta = calls.get(tid) or {}
        name = str(meta.get("name") or "tool")
        args = dict(meta.get("args") or {})
        raw_path = str(args.get("path") or "")
        content = content_text(m.get("content"))
        span = _line_span(content)
        if name == "read_file":
            dropped += 1
            hit = _allowed(raw_path, roots)
            path_s = hit or "(path omitted)"
            if span:
                body = f"read_file  path={path_s}  L{span[0]}-{span[1]}\n{drop_note}"
            else:
                body = f"read_file  path={path_s}\n{drop_note}"
            out.append({**m, "content": body})
        elif name == "edit_file":
            out.append({**m, "content": content[:240] if len(content) > 240 else content})
        else:
            out.append({**m, "content": f"{name}{other_note}"})
    return out, dropped


def _line_span(content: str) -> tuple[int, int] | None:
    nums = [int(m.group(1)) for m in _NUMLINE.finditer(content)]
    if not nums:
        return None
    return nums[0], nums[-1]


def _slim_if_user(msg: dict[str, Any], lang: str) -> dict[str, Any]:
    if msg.get("role") != "user" or is_summary_message(msg):
        return msg
    content = msg.get("content")
    text = content_text(content)
    if "[来源]" not in text and "[Source]" not in text:
        return msg
    if isinstance(content, list):
        out: list[Any] = []
        for part in content:
            if isinstance(part, dict) and str(part.get("type") or "") == "text":
                slim = _slim_packet(str(part.get("text") or ""), lang)
                out.append({**part, "text": slim})
            elif isinstance(part, dict) and str(part.get("type") or "") in ("image_url", "image"):
                out.append({"type": "text", "text": IMAGE_PLACEHOLDER})
            else:
                out.append(part)
        return {**msg, "content": out}
    slim = _slim_packet(text, lang)
    if slim == text:
        return msg
    return {**msg, "content": slim}


def _slim_packet(content: str, lang: str) -> str:
    """已经 compact 过：历史 user 包里的目录 / 邻域 / 书架整段换成引用。"""
    if lang == "en":
        toc = "[Table of contents (do not recite the book)]\nSee the rolling summary. read_file a passage if you need it.\n"
        nb = "[Neighborhood]\nSee the rolling summary. read_file by line if you need the body.\n"
        shelf = (
            "[Other handbooks in the workspace]\n"
            "Paths are in the rolling summary. read_file a path to talk about that book.\n"
        )
    else:
        toc = "[全书目录（不要整本背诵）]\n见滚动摘要；缺哪段 read_file。\n"
        nb = "[邻域]\n见滚动摘要；缺正文就按行 read_file。\n"
        shelf = "[工作目录里的其他教材]\n路径在滚动摘要里。要说那本讲什么，先 read_file 那个 path。\n"
    text = _replace_section(content, "全书目录（不要整本背诵）", toc)
    text = _replace_section(text, "Table of contents (do not recite the book)", toc)
    text = _replace_section(text, "邻域", nb)
    text = _replace_section(text, "Neighborhood", nb)
    text = _replace_section(text, "工作目录里的其他教材", shelf)
    text = _replace_section(text, "Other handbooks in the workspace", shelf)
    text = _cap_selection_section(text, lang)
    return text


def _replace_section(content: str, header: str, replacement: str) -> str:
    pat = re.compile(
        rf"\[{re.escape(header)}[^\]]*\]\n.*?(?=\n\[|\Z)",
        re.S,
    )
    if not pat.search(content):
        return content
    return pat.sub(replacement.rstrip() + "\n", content, count=1)


def _cap_selection_section(content: str, lang: str) -> str:
    for header in ("框选", "Selection"):
        m = re.search(
            rf"(\[{re.escape(header)}\]\n)(.*?)(?=\n\[|\Z)",
            content,
            re.S,
        )
        if not m:
            continue
        body = m.group(2)
        # 已经带过封顶说明的不再切
        if "只留前" in body or "only the first" in body:
            return content
        lm = _LINES_FIELD.search(content)
        start = int(lm.group(1)) if lm else 0
        end = int(lm.group(2)) if lm else 0
        capped, hit = cap_selected_text(body.rstrip("\n"), lang=lang, start_line=start, end_line=end)
        if not hit:
            return content
        return content[: m.start()] + m.group(1) + capped + "\n" + content[m.end() :]
    return content


def _render_summary(slots: _Slots, lang: str) -> str:
    none = "(none)" if lang == "en" else "（无）"
    intro = (
        "Earlier turns are folded here. If a passage is missing, read_file it. "
        "Old reads do not count for edit_file."
        if lang == "en"
        else "更早的回合已折进这里。缺正文就 read_file。旧读不算数，edit_file 之前必须再读。"
    )
    book = slots.book or (none)
    def bullets(items: list[str]) -> str:
        if not items:
            return none
        return "\n".join(f"- {x}" for x in items)

    return (
        f"{SUMMARY_MARK}\n{intro}\n\n"
        f"[compact.book]\n{book}\n\n"
        f"[compact.shelf]\n{bullets(slots.shelf)}\n\n"
        f"[compact.anchors]\n{bullets(slots.anchors)}\n\n"
        f"[compact.cracks]\n{bullets(slots.cracks)}\n\n"
        f"[compact.corrections]\n{bullets(slots.corrections)}\n\n"
        f"[compact.writebacks]\n{bullets(slots.writebacks)}\n\n"
        f"[compact.dropped]\n{bullets(slots.dropped)}\n"
    )


def _upsert_note(session: PenSession) -> None:
    rows = list(session.ui_messages or [])
    kept = [r for r in rows if not (r.get("role") == "note" and r.get("kind") == NOTE_KIND)]
    # 什么时候折的也要记：回看时「这之前的气泡是折叠前的」得有个钟。
    kept.append({"role": "note", "kind": NOTE_KIND, "text": "", "ts": now_iso()})
    session.ui_messages = kept


def compact_fed_packet_suffix(lang: str) -> str:
    """已经 compact 过的会话，新 user 包不再整段重喂目录 / 邻域 / 书架。"""
    if lang == "en":
        return (
            "[Map already in the rolling summary]\n"
            "Table of contents, neighborhood and other-handbook sketches were folded. "
            "read_file by path and line if you need a passage. Old reads do not count.\n\n"
        )
    return (
        "[地图已在滚动摘要里]\n"
        "目录、邻域、书架前 400 行已经折过。缺哪段就按 path 和行号 read_file。"
        "旧读不算数。\n\n"
    )
