"""Shared file loading for the GUI and command line; no Tk dependencies."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .ain_sections import TEXT_SECTION_GROUPS, TextTableReader, load_text_section_with_fallback
from .alice_tools import AliceError, AliceNotFound, AliceTools
from .model import AlignedRow, TextDump, align_dumps, parse_dump
from . import xsys35


def open_dump(
    tools: AliceTools,
    path: str,
    encoding: Optional[str],
) -> TextDump:
    path = str(Path(path).expanduser().resolve())
    if xsys35.is_xsys35_file(path):
        return xsys35.text_dump(path, encoding)
    if encoding:
        text = tools.dump_text(path, encoding)
    else:
        text, encoding = tools.dump_text_auto(path)
    return parse_dump(text, path=path, encoding=encoding)


def _input_path(path: str) -> str:
    target = Path(path).expanduser()
    if not target.is_file():
        raise AliceError(f"文件不存在或不可读：{target}")
    return str(target.resolve(strict=True))


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

    resolved = _input_path(path)
    try:
        return open_dump(tools, resolved, encoding), None
    except AliceNotFound:
        raise
    except (AliceError, OSError, ValueError):
        if encoding and not allow_explicit_fallback:
            # An explicitly requested encoding must not be silently ignored.
            raise
        dump = load_text_section_with_fallback(
            tools, resolved,
            text_encodings=(encoding,) if encoding else None,
            map_encodings=(encoding, "CP932", "CP936", "UTF-8", "ISO-8859-1") if encoding else None,
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
        left_dump, right_dump = _match_fallback_tables(
            tools, left_dump, right_dump,
            left_note, right_note, left_encoding, right_encoding,
        )
        left_note, right_note = left_dump.sections[0], right_dump.sections[0]
    notes = list(dict.fromkeys(note for note in (left_note, right_note) if note))
    return left_dump, right_dump, align_dumps(left_dump, right_dump), notes


def _match_fallback_tables(
    tools: AliceTools,
    left: TextDump,
    right: TextDump,
    left_note: Optional[str],
    right_note: Optional[str],
    left_encoding: Optional[str],
    right_encoding: Optional[str],
) -> tuple[TextDump, TextDump]:
    first = left_note or right_note
    groups = sorted(TEXT_SECTION_GROUPS, key=lambda names: first not in names)
    readers: dict[str, TextTableReader] = {}

    def matching(dump, note, encoding, names):
        if note in names:
            return dump
        if dump.path not in readers:
            readers[dump.path] = TextTableReader(
                tools, dump.path, map_encodings=(dump.encoding, "CP932", "CP936", "UTF-8", "ISO-8859-1"),
            )
        reader = readers[dump.path]
        name = next((candidate for candidate in names if candidate in reader.names), None)
        if name is None:
            raise ValueError(f"{dump.path} 缺少对应区段（{'/'.join(names)}）")
        encodings = (encoding,) if encoding else (dump.encoding, *reader.fallback_encodings)
        return reader.read(name, encodings)

    last_error = None
    for group in groups:
        try:
            return (
                matching(left, left_note, left_encoding, group),
                matching(right, right_note, right_encoding, group),
            )
        except AliceNotFound:
            raise
        except (AliceError, OSError, ValueError) as exc:
            last_error = exc
    raise AliceError(f"无法对照：两侧缺少共同可解码的文本区段（{last_error}）") from last_error


def load_table_pair(
    tools: AliceTools,
    left: str,
    right: str,
    left_encoding: Optional[str],
    right_encoding: Optional[str],
) -> tuple[TextDump, TextDump, list[AlignedRow], list[str]]:
    """Compare complete raw tables by kind and ID, including unused entries."""
    def combined(path, encoding):
        path = _input_path(path)
        map_encodings = ((encoding,) if encoding else ()) + ("CP932", "CP936", "UTF-8", "ISO-8859-1")
        reader = TextTableReader(tools, path, map_encodings=map_encodings)
        dump = TextDump(path, "", sections=[""] * len(TEXT_SECTION_GROUPS))
        used_encodings = []
        for section_no, group in enumerate(TEXT_SECTION_GROUPS):
            names = [name for name in group if name in reader.names]
            if len(names) > 1:
                raise ValueError(f"{path} 包含多个同类文本区段：{'/'.join(names)}")
            if not names:
                continue
            name = names[0]
            table = reader.read(name, (encoding,) if encoding else reader.encodings)
            dump.sections[section_no] = name
            for entry in table.entries:
                entry.section_no = section_no
            dump.entries.extend(table.entries)
            used_encodings.append(f"{name} {table.encoding}")
        dump.encoding = " / ".join(used_encodings) or "无文本区段"
        return dump

    left_dump, right_dump = combined(left, left_encoding), combined(right, right_encoding)
    return left_dump, right_dump, align_dumps(left_dump, right_dump), []
