"""Shared file loading for the GUI and command line; no Tk dependencies."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .ain_sections import load_section_dump, load_text_section_with_fallback, text_section_names
from .alice_tools import AliceError, AliceTools
from .model import AlignedRow, TextDump, align_dumps, parse_dump


def open_dump(
    tools: AliceTools,
    path: str,
    encoding: Optional[str],
) -> TextDump:
    path = str(Path(path).expanduser())
    encoding = encoding or tools.detect_encoding(path)
    text = tools.dump_text(path, encoding)
    return parse_dump(text, path=path, encoding=encoding)


def open_dump_with_fallback(
    tools: AliceTools,
    path: str,
    encoding: Optional[str],
    *,
    allow_explicit_fallback: bool = False,
) -> tuple[TextDump, Optional[str]]:
    """Open *path*, falling back to a raw text section when ``ain dump -t``
    cannot handle the file (mixed-encoding containers such as ランス０２).

    Returns ``(dump, fallback_note)``; *fallback_note* is ``None`` for a
    normal function-level dump, otherwise the raw section that was used.
    """

    resolved = str(Path(path).expanduser())
    if not Path(resolved).is_file():
        raise AliceError(f"文件不存在或不可读：{resolved}")
    try:
        return open_dump(tools, resolved, encoding), None
    except (AliceError, OSError, ValueError):
        if encoding and not allow_explicit_fallback:
            # An explicitly requested encoding must not be silently ignored.
            raise
        dump = load_text_section_with_fallback(
            tools, resolved, text_encodings=(encoding,) if encoding else None
        )
        return dump, dump.sections[0]


def load_pair(
    tools: AliceTools,
    left: str,
    right: str,
    left_encoding: Optional[str],
    right_encoding: Optional[str],
    *,
    allow_explicit_fallback: bool = False,
) -> tuple[TextDump, TextDump, list[AlignedRow], list[str]]:
    left_dump, left_note = open_dump_with_fallback(
        tools, left, left_encoding, allow_explicit_fallback=allow_explicit_fallback
    )
    right_dump, right_note = open_dump_with_fallback(
        tools, right, right_encoding, allow_explicit_fallback=allow_explicit_fallback
    )
    if left_note or right_note:
        # Raw tables and function-context dumps have different section ordinals.
        # Compare the same table on both sides whenever either side falls back.
        section = left_note or right_note
        aliases = (
            ("MSG0", "MSG1", "MSGI")
            if section in ("MSG0", "MSG1", "MSGI")
            else ("STR0", "VARI")
        )

        def raw_dump(
            dump: TextDump, note: Optional[str], explicit_encoding: Optional[str]
        ) -> TextDump:
            if note in aliases:
                return dump
            available = text_section_names(tools, dump.path)
            name = next((name for name in aliases if name in available), None)
            if name is None:
                raise AliceError(f"无法对照：{dump.path} 缺少与 {section} 对应的文本区段")
            if explicit_encoding:
                return load_section_dump(tools, dump.path, name, explicit_encoding)
            # Mixed files may use different encodings in each table.  Probe
            # the newly selected table instead of silently replacing bytes.
            encodings = tuple(dict.fromkeys((dump.encoding, "CP936", "CP932", "UTF-8")))
            return load_text_section_with_fallback(
                tools, dump.path, section_name=name, text_encodings=encodings
            )

        left_dump = raw_dump(left_dump, left_note, left_encoding)
        right_dump = raw_dump(right_dump, right_note, right_encoding)
        left_note, right_note = left_dump.sections[0], right_dump.sections[0]
    notes = list(dict.fromkeys(note for note in (left_note, right_note) if note))
    rows = align_dumps(left_dump, right_dump)
    return left_dump, right_dump, rows, notes
