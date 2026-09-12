"""Model: parsing and serializing ``alice ain dump -t`` text output.

The text dump produced by alice-tools has a very small syntax::

    ; <function/section name>            (section header, a comment)
    ;s[123] = "escaped string"           (commented-out assignment)
    ;m[456] = "escaped message"

Un-commenting an assignment (removing the leading ``;``) is exactly what
``alice ain edit -t`` accepts.  This module never talks to the ``alice``
binary; all binary work lives in :mod:`aindiff.alice_tools`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = [
    "AlignedRow",
    "EntryConflict",
    "TextDump",
    "TextEntry",
    "align_dumps",
    "build_edit_text",
    "display_text",
    "escape_text",
    "normalize_search_text",
    "parse_dump",
    "unescape_text",
]

_ASSIGN_RE = re.compile(r'^;?([sm])\[(\d+)\]\s*=\s*"(.*)"$')


class EntryConflict(ValueError):
    """Raised when two occurrences of the same s/m id are edited differently.

    In an .ain file the string table is indexed by id only; the last
    assignment in an edit file wins.  We refuse to guess in that situation.
    """


@dataclass(eq=False)
class TextEntry:
    """One ``s[...]``/``m[...]`` assignment seen in a text dump."""

    kind: str  # "s" (string) or "m" (message)
    index: int
    occurrence: int  # 1-based occurrence of (kind, index) inside its section
    text: str
    section: str = ""
    section_no: int = 0
    line_no: int = 0
    original_text: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        self.original_text = self.text

    @property
    def key(self) -> Tuple[str, int, int]:
        return (self.kind, self.index, self.occurrence)

    @property
    def id_key(self) -> Tuple[str, int]:
        return (self.kind, self.index)

    @property
    def dirty(self) -> bool:
        return self.text != self.original_text


@dataclass
class TextDump:
    """Parsed text dump of one .ain file."""

    path: str
    encoding: str
    sections: List[str] = field(default_factory=list)
    entries: List[TextEntry] = field(default_factory=list)

    @property
    def dirty_entries(self) -> List[TextEntry]:
        return [e for e in self.entries if e.dirty]

    @property
    def dirty_count(self) -> int:
        return sum(1 for e in self.entries if e.dirty)


@dataclass
class AlignedRow:
    """One visual row: a section header or a pair of left/right entries."""

    no: int = 0
    is_section: bool = False
    section_no: int = 0
    left_section: Optional[str] = None
    right_section: Optional[str] = None
    left: Optional[TextEntry] = None
    right: Optional[TextEntry] = None

    @property
    def changed(self) -> bool:
        if self.is_section:
            return False
        if self.left is None or self.right is None:
            return True
        return self.left.text != self.right.text


def unescape_text(text: str) -> str:
    r"""Decode the escaping produced by alice-tools' ``escape_string``.

    alice-tools escapes ``\``, ``"``, ``\n`` and ``\r``.  Its lexer also
    understands ``\t``, ``\b`` and ``\f`` when reading an edit file, so we
    decode those too.  An unknown escape such as ``\q`` becomes ``q``
    (mirroring the lexer rule ``\(.|\n)``).
    """

    out: List[str] = []
    i = 0
    simple = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(text):
            # A lone trailing backslash cannot come from alice-tools, but
            # be permissive when reading a hand-edited file.
            out.append("\\")
            break
        nxt = text[i]
        out.append(simple.get(nxt, nxt))
        i += 1
    return "".join(out)


def escape_text(text: str) -> str:
    """Escape text exactly like ``alice ain dump -t`` does."""

    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def normalize_search_text(text: str) -> str:
    """Normalize text for searching.

    NFKC folds fullwidth/halfwidth forms and compatibility characters;
    casefold handles Latin case.  Japanese kanji and Chinese hanzi that are
    genuinely different codepoints are intentionally kept different -- the
    search is row-based, so a query matching either the left or the right
    cell still finds the paired row.
    """

    return unicodedata.normalize("NFKC", text).casefold()


def display_text(text: str) -> str:
    """One-line representation for Treeview cells (same as dump escaping)."""

    return escape_text(text).replace("\t", "\\t")


def parse_dump(text: str, path: str = "", encoding: str = "UTF-8") -> TextDump:
    """Parse the UTF-8 output of ``alice ain dump -t``.

    Section headers are comments that do not look like assignments.  Entry
    occurrence numbers restart for every section, matching the duplicate-id
    semantics of the dump format.
    """

    dump = TextDump(path=str(path), encoding=encoding)
    section = ""
    section_no = -1
    occurrences: Dict[Tuple[str, int], int] = defaultdict(int)

    # splitlines() also treats form feeds and Unicode separators inside a
    # quoted string as line endings, silently dropping those assignments.
    for line_no, raw_line in enumerate(re.split(r"\r\n|\r|\n", text), start=1):
        line = raw_line.strip()
        if not line:
            continue

        match = _ASSIGN_RE.match(line)
        if match:
            if section_no < 0:
                # Entries before the first section header are accepted by
                # alice-tools; assign them to a synthetic section.
                section = ""
                section_no = 0
                if not dump.sections:
                    dump.sections.append(section)
            kind, index_s, body = match.groups()
            entry = TextEntry(
                kind=kind,
                index=int(index_s),
                occurrence=0,
                text=unescape_text(body),
                section=section,
                section_no=section_no,
                line_no=line_no,
            )
            key = entry.id_key
            occurrences[key] += 1
            entry.occurrence = occurrences[key]
            dump.entries.append(entry)
            continue

        # Anything else beginning with ";" is a section header comment.
        if line.startswith(";"):
            section = line[1:].strip()
            dump.sections.append(section)
            section_no = len(dump.sections) - 1
            occurrences.clear()

    return dump


def build_edit_text(entries: Sequence[TextEntry]) -> str:
    """Build an ``alice ain edit -t`` input file from entries.

    All produced lines are un-commented, so every entry listed here is
    written back.  Entries are emitted in dump order, grouped under their
    section header comments.
    """

    ordered = sorted(entries, key=lambda e: (e.line_no, e.kind, e.index, e.occurrence))
    by_id: Dict[Tuple[str, int], str] = {}
    for entry in ordered:
        if "\0" in entry.text:
            raise ValueError(
                f"文本包含无法写回的 NUL 字符：{entry.kind}[{entry.index}]"
            )
        previous = by_id.get(entry.id_key)
        if previous is not None and previous != entry.text:
            raise EntryConflict(
                f"同一 id 的文本被改成了不同内容：{entry.kind}[{entry.index}]"
            )
        by_id[entry.id_key] = entry.text

    lines: List[str] = []
    current_section: Optional[Tuple[int, str]] = None
    for entry in ordered:
        sec_key = (entry.section_no, entry.section)
        if sec_key != current_section:
            lines.append("")
            lines.append(f"; {entry.section}" if entry.section else "; (未命名节)")
            current_section = sec_key
        lines.append(f'{entry.kind}[{entry.index}] = "{escape_text(entry.text)}"')
    return "\n".join(lines) + "\n"


def align_dumps(left: TextDump, right: TextDump) -> List[AlignedRow]:
    """Align two text dumps into side-by-side rows.

    Alignment is structural: sections are paired by ordinal and entries
    inside a section are paired by ``(kind, index, occurrence)``.  This is
    exactly what is needed for a translation patch that preserves the
    scenario structure (e.g. a Japanese original and its Chinese build).
    Entries present on only one side become single-sided rows.
    """

    def by_section(dump: TextDump) -> Dict[int, List[TextEntry]]:
        buckets: Dict[int, List[TextEntry]] = defaultdict(list)
        for entry in dump.entries:
            buckets[entry.section_no].append(entry)
        return buckets

    left_sections = by_section(left)
    right_sections = by_section(right)
    all_section_nos = sorted(set(left_sections) | set(right_sections))

    rows: List[AlignedRow] = []
    row_no = 0
    for section_no in all_section_nos:
        left_name = left.sections[section_no] if section_no < len(left.sections) else None
        right_name = right.sections[section_no] if section_no < len(right.sections) else None
        rows.append(
            AlignedRow(
                no=0,
                is_section=True,
                section_no=section_no,
                left_section=left_name,
                right_section=right_name,
            )
        )

        right_by_key: Dict[Tuple[str, int, int], List[TextEntry]] = defaultdict(list)
        for entry in right_sections.get(section_no, []):
            right_by_key[entry.key].append(entry)

        left_entries = left_sections.get(section_no, [])
        for left_entry in left_entries:
            matches = right_by_key.get(left_entry.key)
            right_entry = matches.pop(0) if matches else None
            row_no += 1
            rows.append(
                AlignedRow(
                    no=row_no,
                    section_no=section_no,
                    left=left_entry,
                    right=right_entry,
                )
            )

        # Any right-side entries that had no left counterpart.
        for pending in right_by_key.values():
            for right_entry in pending:
                row_no += 1
                rows.append(
                    AlignedRow(
                        no=row_no,
                        section_no=section_no,
                        left=None,
                        right=right_entry,
                    )
                )

    return rows


def count_changed(rows: Sequence[AlignedRow]) -> int:
    return sum(1 for row in rows if not row.is_section and row.changed)
