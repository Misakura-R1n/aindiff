"""Read/write support for the old xsys35 AIN container (magic ``AINI``).

alice-tools handles System 4 AIN files (``VERS`` ... ``MSG0``).  Some old
AliceSoft games (e.g. System39/Rance 5D) ship an ``AINI`` container instead:

* 4-byte magic ``AINI`` (or ``AIN2``), then an unencrypted uint32 version;
* the remaining payload is obfuscated with a 2-bit rotate;
* sections: ``HEL0`` (DLL declarations), ``FUNC`` (functions),
  ``VARI`` (string table, exposed as ``s[]``) and ``MSGI``
  (message table, exposed as ``m[]``).

Format reference: https://github.com/kichikuou/xsys35c (compiler/ain.c and
decompiler/ain.c).
"""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .file_state import FileRevision, commit_edit
from .model import TextDump, TextEntry, encode_edit, escape_text, parse_edit_text

__all__ = [
    "MAGIC_AINI",
    "MAGIC_AIN2",
    "is_xsys35_file",
    "map_text",
    "section_dump",
    "text_dump",
    "edit_file",
    "detect_encoding",
]

MAGIC_AINI = b"AINI"
MAGIC_AIN2 = b"AIN2"
SECTION_KINDS = {"VARI": "s", "MSGI": "m"}
TEXT_ENCODINGS = ("UTF-8", "CP932", "CP936")
KNOWN_SECTIONS = ("HEL0", "FUNC", "VARI", "MSGI")
_DECRYPT_TABLE = bytes(((b << 2) | (b >> 6)) & 0xFF for b in range(256))
_ENCRYPT_TABLE = bytes(((b >> 2) | (b << 6)) & 0xFF for b in range(256))


def is_xsys35_file(path: str | os.PathLike[str]) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) in (MAGIC_AINI, MAGIC_AIN2)
    except OSError:
        return False


def _decrypt_payload(data: bytes) -> bytes:
    return data[8:].translate(_DECRYPT_TABLE)


def _encrypt_payload(payload: bytes) -> bytes:
    return payload.translate(_ENCRYPT_TABLE)


def _require_bytes(payload: bytes, pos: int, size: int) -> None:
    if pos < 0 or size < 0 or pos + size > len(payload):
        raise ValueError(f"AINI 区段数据不完整（偏移 0x{pos:x}）")


def _read_u16(payload: bytes, pos: int) -> int:
    _require_bytes(payload, pos, 2)
    return struct.unpack_from("<H", payload, pos)[0]


def _read_u32(payload: bytes, pos: int) -> int:
    _require_bytes(payload, pos, 4)
    return struct.unpack_from("<I", payload, pos)[0]


def _read_cstring(payload: bytes, pos: int) -> Tuple[bytes, int]:
    end = payload.find(b"\0", pos)
    if end < 0:
        raise ValueError("AINI 字符串缺少终止符")
    return payload[pos:end], end + 1


def _section_end(payload: bytes, start: int, name: str) -> int:
    """Return the end offset of a section whose header is at *start*."""
    pos = start + 8
    if name == "HEL0":
        dll_count = _read_u32(payload, pos)
        pos += 4
        for _ in range(dll_count):
            _dll_name, pos = _read_cstring(payload, pos)
            func_count = _read_u32(payload, pos)
            pos += 4
            for _ in range(func_count):
                _func_name, pos = _read_cstring(payload, pos)
                arg_count = _read_u32(payload, pos)
                pos += 4
                _require_bytes(payload, pos, 4 * arg_count)
                pos += 4 * arg_count
        return pos
    if name == "FUNC":
        func_count = _read_u32(payload, pos)
        pos += 4
        for _ in range(func_count):
            _func_name, pos = _read_cstring(payload, pos)
            _require_bytes(payload, pos, 6)
            pos += 6  # uint16 page + uint32 address
        return pos
    if name in SECTION_KINDS:
        count = _read_u32(payload, pos)
        pos += 4
        for _ in range(count):
            _text, pos = _read_cstring(payload, pos)
        return pos
    raise ValueError(f"未知 AINI 区段：{name}")


def parse_sections(payload: bytes) -> List[Tuple[str, int, int]]:
    """Return [(name, start, end)] for the decrypted payload (after 8-byte header)."""
    sections: List[Tuple[str, int, int]] = []
    seen_text = set()
    pos = 0
    while pos + 4 <= len(payload):
        name_bytes = payload[pos : pos + 4]
        try:
            name = name_bytes.decode("ascii")
        except UnicodeDecodeError:
            break
        if name not in KNOWN_SECTIONS:
            break
        if name in SECTION_KINDS:
            if name in seen_text:
                raise ValueError(f"AINI 含有重复的文本区段：{name}")
            seen_text.add(name)
        end = _section_end(payload, pos, name)
        sections.append((name, pos, end))
        pos = end
    if not sections:
        raise ValueError("AINI 中没有可识别的区段")
    return sections


def _read_strings(payload: bytes, start: int, name: str) -> List[bytes]:
    count = _read_u32(payload, start + 8)
    pos = start + 12
    strings: List[bytes] = []
    for _ in range(count):
        text, pos = _read_cstring(payload, pos)
        strings.append(text)
    return strings


def _encode_string(text: str, encoding: str) -> bytes:
    try:
        return encode_edit(text, encoding)
    except (ValueError, LookupError) as exc:
        raise ValueError(
            f"文本无法用 {encoding} 编码写回 AINI：{text[:40]!r}（{exc}）"
        ) from exc


def _build_ain_section(header: bytes, raw_strings: List[bytes]) -> bytes:
    out = bytearray(header)  # Preserve the name and reserved bytes exactly.
    out += struct.pack("<I", len(raw_strings))
    for text in raw_strings:
        out += text + b"\0"
    return bytes(out)


def _load(path: str) -> Tuple[bytes, bytes]:
    data = Path(path).read_bytes()
    if data[:4] not in (MAGIC_AINI, MAGIC_AIN2):
        raise ValueError(f"不是 AINI 容器：{path}")
    if len(data) < 8:
        raise ValueError(f"AINI 文件不完整：{path}")
    version = struct.unpack_from("<I", data, 4)[0]
    if version not in (1, 2):
        raise ValueError(f"不支持的 AINI 版本：{version}")
    return data, _decrypt_payload(data)


def map_text(path: str) -> str:
    data, payload = _load(path)
    lines = []
    for name, start, end in parse_sections(payload):
        lines.append(f"{name}: {8 + start:08x} -> {8 + end:08x}")
    return "\n".join(lines) + "\n"


def _decode_tables(path: str, tables: dict[str, list[bytes]], encoding: str) -> TextDump:
    dump = TextDump(path=str(path), encoding=encoding)
    for section_no, (name, raw_strings) in enumerate(tables.items()):
        table = decode_text_table(path, name, raw_strings, encoding)
        dump.sections.append(name)
        for entry in table.entries:
            entry.section_no = section_no
        dump.entries.extend(table.entries)
    return dump


def text_dump(
    path: str, encoding: Optional[str] = None, *, candidates: Optional[Sequence[str]] = None
) -> TextDump:
    """Build a TextDump from VARI + MSGI (function-context view unavailable)."""
    tables = raw_text_tables(path)
    if encoding is not None:
        return _decode_tables(path, tables, encoding)
    last_error = None
    for candidate in candidates if candidates is not None else TEXT_ENCODINGS:
        try:
            return _decode_tables(path, tables, candidate)
        except (UnicodeError, LookupError) as exc:
            last_error = exc
    raise ValueError(f"无法识别 AINI 文本编码（{last_error}）") from last_error


def dump_text_syntax(path: str, encoding: str) -> str:
    """Return an ``ain dump -t``-style text for the AINI container."""
    dump = text_dump(path, encoding)
    lines: List[str] = []
    for section_name in dump.sections:
        lines.append(f"; {section_name}")
        for entry in dump.entries:
            if entry.section != section_name:
                continue
            escaped = escape_text(entry.text)
            lines.append(f';{entry.kind}[{entry.index}] = "{escaped}"')
    return "\n".join(lines) + "\n"


def section_dump(path: str, section_name: str, encoding: str) -> TextDump:
    tables = raw_text_tables(path)
    if section_name not in tables:
        raise ValueError(f"AINI 中不存在可编辑区段 {section_name}")
    return decode_text_table(path, section_name, tables[section_name], encoding)


def raw_text_tables(path: str) -> dict[str, list[bytes]]:
    _, payload = _load(path)
    return {
        name: _read_strings(payload, start, name)
        for name, start, _ in parse_sections(payload)
        if name in SECTION_KINDS
    }


def decode_text_table(path: str, name: str, raw_strings: list[bytes], encoding: str) -> TextDump:
    entries = [
        TextEntry(
            kind=SECTION_KINDS[name],
            index=index,
            occurrence=1,
            text=raw.decode(encoding),
            section=name,
            section_no=0,
            line_no=index + 1,
        )
        for index, raw in enumerate(raw_strings)
    ]
    return TextDump(path=str(path), encoding=encoding, sections=[name], entries=entries)


def detect_encoding(path: str, candidates: Sequence[str] = TEXT_ENCODINGS) -> str:
    return text_dump(path, candidates=candidates).encoding


def edit_file(
    path: str | os.PathLike[str],
    edit_text: str,
    output_encoding: str,
    *,
    backup: bool = True,
    backup_suffix: str = ".bak",
) -> Tuple[Path, int, int]:
    source = FileRevision.capture(path)
    written = write_file(source, edit_text, output_encoding, backup_suffix if backup else None)
    return written.path, source.size, written.size


def write_file(
    source: FileRevision, edit_text: str, output_encoding: str, backup_suffix: str | None
) -> FileRevision:
    with tempfile.TemporaryDirectory(prefix=".aindiff-edit-", dir=source.path.parent) as td:
        staging = Path(td)
        snapshot = source.snapshot(staging)
        edited = _edited_data(snapshot, edit_text, output_encoding)
        if edited is None:
            source.check()
            return source
        output = staging / "output.ain"
        output.write_bytes(edited)
        return commit_edit(source, snapshot, output, backup_suffix)


def _edited_data(path: Path, edit_text: str, output_encoding: str) -> Optional[bytes]:
    data, payload = _load(str(path))

    # Apply assignment statements (last one wins, like alice-tools).
    assignments = parse_edit_text(edit_text)

    sections = parse_sections(payload)
    if not assignments:
        return None
    counts = {
        SECTION_KINDS[name]: _read_u32(payload, start + 8)
        for name, start, _ in sections if name in SECTION_KINDS
    }
    for kind, index in assignments:
        if index >= counts.get(kind, 0):
            raise ValueError(f"AINI 中不存在文本 {kind}[{index}]")

    out = bytearray()
    for name, start, end in sections:
        if name in SECTION_KINDS:
            raw_strings = _read_strings(payload, start, name)
            kind = SECTION_KINDS[name]
            for index, raw in enumerate(raw_strings):
                new_text = assignments.get((kind, index))
                if new_text is not None:
                    raw_strings[index] = _encode_string(new_text, output_encoding)
            out += _build_ain_section(payload[start : start + 8], raw_strings)
        else:
            out += payload[start:end]
    # Future/unknown sections and trailing bytes must survive a text edit.
    out += payload[sections[-1][2] :]

    edited = data[:8] + _encrypt_payload(bytes(out))
    return edited if edited != data else None
