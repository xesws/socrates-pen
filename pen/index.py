"""确定性手册索引：行号 → Level + 拍 + 完整 Q 标题。不经 LLM。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

H1_RE = re.compile(r"^# (.+?)\s*$")
H2_RE = re.compile(r"^## (.+?)\s*$")
Q_RE = re.compile(r"^(\*\*Q\d+\. .+\*\*)\s*$")
HUITOU_RE = re.compile(r"^〔回读：")


@dataclass
class Section:
    kind: str  # q | teaching | other
    level: str
    beat: str | None
    q_title: str | None
    start_line: int
    end_line: int


@dataclass
class TocEntry:
    level: str
    beat: str | None
    start_line: int
    heading: str
    anchor_id: str


@dataclass
class HandbookIndex:
    original_path: str
    title: str
    sections: list[Section] = field(default_factory=list)
    toc: list[TocEntry] = field(default_factory=list)
    n_lines: int = 0

    def locate(self, line: int) -> Section:
        if line < 1 or line > max(self.n_lines, 1):
            exc = ValueError(f"行号越界：{line}（全书 {self.n_lines} 行）")
            exc.i18n_key = "index.line_out_of_range"  # type: ignore[attr-defined]
            exc.i18n_args = {"line": line, "n_lines": self.n_lines}  # type: ignore[attr-defined]
            raise exc
        qs = [
            s
            for s in self.sections
            if s.kind == "q" and s.start_line <= line <= s.end_line
        ]
        if len(qs) > 1:
            raise ValueError(f"行 {line} 落入多个 Q，索引损坏")
        if qs:
            return qs[0]
        others = [
            s
            for s in self.sections
            if s.kind != "q" and s.start_line <= line <= s.end_line
        ]
        if not others:
            raise ValueError(f"行 {line} 没有落入任何章节")
        others.sort(key=lambda s: (s.end_line - s.start_line, -s.start_line))
        return others[0]

    def find_q(self, level: str, q_title: str) -> Section:
        hits = [
            s
            for s in self.sections
            if s.kind == "q" and s.level == level and s.q_title == q_title
        ]
        if len(hits) != 1:
            raise ValueError(
                f"Q 定位失败：level={level!r} title={q_title!r} 命中 {len(hits)} 条"
            )
        return hits[0]

    def to_json(self) -> dict:
        return {
            "original_path": self.original_path,
            "title": self.title,
            "n_lines": self.n_lines,
            "sections": [asdict(s) for s in self.sections],
            "toc": [asdict(t) for t in self.toc],
        }

    @classmethod
    def from_json(cls, data: dict) -> HandbookIndex:
        return cls(
            original_path=data["original_path"],
            title=data["title"],
            n_lines=data["n_lines"],
            sections=[Section(**s) for s in data["sections"]],
            toc=[TocEntry(**t) for t in data["toc"]],
        )


def _classify_h1(text: str) -> str:
    if text.startswith("Level "):
        m = re.match(r"(Level \d+)", text)
        return m.group(1) if m else text
    if text.startswith("开篇"):
        return "开篇"
    if text.startswith("最终通关任务"):
        return "Capstone"
    if text.startswith("附录"):
        return "附录"
    return "封面"


def _anchor_id(level: str, beat: str | None, seq: int) -> str:
    slug_level = re.sub(r"[^\w]+", "-", level).strip("-").lower()
    if beat:
        return f"{slug_level}-beat-{seq}"
    return f"{slug_level}-{seq}"


def build_index(original_path: str | Path) -> HandbookIndex:
    path = Path(original_path).expanduser().resolve()
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    n = len(lines)

    title = path.name
    current_level = "封面"
    current_beat: str | None = None
    in_fence = False

    h1_marks: list[tuple[int, str, str]] = []  # line, display, level_key
    h2_marks: list[tuple[int, str, str]] = []  # line, beat, level_key
    q_marks: list[tuple[int, str, str, str | None]] = []  # line, title, level, beat
    huitou_marks: list[int] = []

    beat_seq = 0
    toc: list[TocEntry] = []

    for i, raw in enumerate(lines, start=1):
        stripped = raw.rstrip("\n")
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m1 = H1_RE.match(stripped)
        if m1:
            heading = m1.group(1)
            if i == 1 or heading.startswith("手搓"):
                title = heading
            current_level = _classify_h1(heading)
            current_beat = None
            h1_marks.append((i, heading, current_level))
            beat_seq = 0
            toc.append(
                TocEntry(
                    level=current_level,
                    beat=None,
                    start_line=i,
                    heading=heading,
                    anchor_id=_anchor_id(current_level, None, i),
                )
            )
            continue
        m2 = H2_RE.match(stripped)
        if m2:
            current_beat = m2.group(1)
            beat_seq += 1
            h2_marks.append((i, current_beat, current_level))
            toc.append(
                TocEntry(
                    level=current_level,
                    beat=current_beat,
                    start_line=i,
                    heading=current_beat,
                    anchor_id=_anchor_id(current_level, current_beat, beat_seq),
                )
            )
            continue
        mq = Q_RE.match(stripped)
        if mq:
            q_marks.append((i, mq.group(1), current_level, current_beat))
            continue
        if HUITOU_RE.match(stripped):
            huitou_marks.append(i)

    sections: list[Section] = []

    # Q blocks: each Q title to the next 回读 at or after it (before next Q)
    for qi, (q_line, q_title, q_level, q_beat) in enumerate(q_marks):
        next_q = q_marks[qi + 1][0] if qi + 1 < len(q_marks) else n + 1
        huitou = next((h for h in huitou_marks if q_line < h < next_q), None)
        end = huitou if huitou is not None else next_q - 1
        sections.append(
            Section(
                kind="q",
                level=q_level,
                beat=q_beat,
                q_title=q_title,
                start_line=q_line,
                end_line=end,
            )
        )

    q_ranges = [(s.start_line, s.end_line) for s in sections if s.kind == "q"]

    def covered_by_q(line: int) -> bool:
        return any(a <= line <= b for a, b in q_ranges)

    # Beat-sized teaching/other sections, punching out Q ranges
    beat_spans: list[tuple[int, int, str, str | None]] = []
    marks = [(ln, lv, None) for ln, _h, lv in h1_marks] + [
        (ln, lv, bt) for ln, bt, lv in h2_marks
    ]
    marks.sort(key=lambda x: x[0])
    for mi, (ln, lv, bt) in enumerate(marks):
        end = (marks[mi + 1][0] - 1) if mi + 1 < len(marks) else n
        beat_spans.append((ln, end, lv, bt))

    for start, end, lv, bt in beat_spans:
        cursor = start
        while cursor <= end:
            while cursor <= end and covered_by_q(cursor):
                cursor += 1
            if cursor > end:
                break
            run_start = cursor
            while cursor <= end and not covered_by_q(cursor):
                cursor += 1
            run_end = cursor - 1
            kind = "other" if lv in {"封面", "开篇", "Capstone", "附录"} else "teaching"
            if bt and "Meta Question" in bt:
                # leftover lines in 第五拍 that are not a Q (e.g. 门禁规则)
                kind = "teaching"
            sections.append(
                Section(
                    kind=kind,
                    level=lv,
                    beat=bt,
                    q_title=None,
                    start_line=run_start,
                    end_line=run_end,
                )
            )

    sections.sort(key=lambda s: (s.start_line, 0 if s.kind == "q" else 1))
    return HandbookIndex(
        original_path=str(path),
        title=title,
        sections=sections,
        toc=toc,
        n_lines=n,
    )


def check_index(idx: HandbookIndex) -> list[str]:
    """返回问题列表。空列表 = 通过。"""
    problems: list[str] = []
    q_titles: dict[tuple[str, str], int] = {}
    for s in idx.sections:
        if s.start_line < 1 or s.end_line < s.start_line:
            problems.append(f"坏区间 {s}")
        if s.kind == "q":
            if not s.q_title:
                problems.append(f"Q 缺标题 {s}")
            key = (s.level, s.q_title or "")
            q_titles[key] = q_titles.get(key, 0) + 1
    for key, c in q_titles.items():
        if c != 1:
            problems.append(f"Q 主键不唯一 {key} ×{c}")

    # every line has a locate()
    for line in range(1, idx.n_lines + 1):
        try:
            idx.locate(line)
        except ValueError as exc:
            problems.append(str(exc))
            if len(problems) > 20:
                problems.append("… 其余行定位错误已省略")
                break
    return problems


def save_index(idx: HandbookIndex, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(idx.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")


def neighborhood(path: Path, section: Section, selected_lines: tuple[int, int] | None) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(section.start_line, 1)
    end = min(section.end_line, len(lines))
    chunk = "\n".join(lines[start - 1 : end])
    from pen.config import NEIGHBORHOOD_CHARS

    if len(chunk) <= NEIGHBORHOOD_CHARS:
        return chunk
    if selected_lines:
        a, b = selected_lines
        pad = 40
        a2 = max(start, a - pad)
        b2 = min(end, b + pad)
        head = "\n".join(lines[start - 1 : min(start + 8, a2) - 1])
        mid = "\n".join(lines[a2 - 1 : b2])
        return (
            f"{head}\n…已截断，可用 read_file 按行翻…\n{mid}\n"
            f"（节选 {a2}-{b2} / 全节 {start}-{end}）"
        )
    return chunk[:NEIGHBORHOOD_CHARS] + "\n…已截断，可用 read_file 按行翻…"


def main() -> None:
    import argparse
    import sys

    from pen.config import DEFAULT_HANDBOOK

    parser = argparse.ArgumentParser(description="构建 / 检查手册索引")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_HANDBOOK))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    idx = build_index(args.path)
    qs = [s for s in idx.sections if s.kind == "q"]
    print(f"{idx.title}")
    print(f"path={idx.original_path}")
    print(f"lines={idx.n_lines} sections={len(idx.sections)} qs={len(qs)} toc={len(idx.toc)}")
    if args.check:
        problems = check_index(idx)
        if problems:
            print("CHECK FAIL")
            for p in problems[:40]:
                print(" -", p)
            sys.exit(1)
        print("CHECK OK")


if __name__ == "__main__":
    main()
