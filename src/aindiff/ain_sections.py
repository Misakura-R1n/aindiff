"""Read AIN binary section maps and decode text-bearing sections.

AliceSoft .ain files are made of tagged sections such as ``VERS``, ``CODE``,
``STR0``, ``MSG0`` or ``MSG1``.  The section map is produced by
``alice ain dump --map``.

This module intentionally reads only the requested section from disk and
returns the same :class:`~aindiff.model.TextDump` shape used by the normal
"text sections by function" view.  Therefore the raw message table in
MSG0/MSG1 and the raw string table in STR0 can be displayed and edited with
the exact same table/GUI code, and changes are written back through
``alice ain edit -t`` with ordinary ``m[id]`` / ``s[id]`` assignments.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .file_state import FileRevision
from .model import TextDump, TextEntry
from . import xsys35
from .alice_tools import AliceError, AliceRunError

__all__ = [
    "SectionInfo",
    "TEXT_SECTION_KINDS",
    "build_section_dump",
    "load_section_dump",
    "load_section_map",
    "load_text_section_with_fallback",
    "parse_section_map",
    "text_section_names",
]

# Sections whose payload is a string table usable for side-by-side editing.
TEXT_SECTION_KINDS = {"MSG0": "m", "MSG1": "m", "STR0": "s", "VARI": "s", "MSGI": "m"}
TEXT_SECTION_GROUPS = (("MSG0", "MSG1", "MSGI"), ("STR0", "VARI"))
SYSTEM4_ENCODINGS = ("CP932", "CP936", "UTF-8", "BIG5")

_MAP_RE = re.compile(r"^\s*([A-Z0-9]+):\s*([0-9a-fA-F]+)\s*->\s*([0-9a-fA-F]+)\s*$")
_MAX_ENTRIES = 10_000_000


@dataclass(frozen=True)
class SectionInfo:
    """One section from ``alice ain dump --map``."""

    name: str
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start

    def describe(self) -> str:
        return f"{self.name}: 0x{self.start:08x} -> 0x{self.end:08x}（{self.size:,} 字节）"


def parse_section_map(text: str) -> List[SectionInfo]:
    """Parse the ASCII output of ``alice ain dump --map``."""

    sections: List[SectionInfo] = []
    for raw_line in text.splitlines():
        match = _MAP_RE.match(raw_line)
        if not match:
            continue
        name, start_s, end_s = match.groups()
        sections.append(SectionInfo(name, int(start_s, 16), int(end_s, 16)))
    if not sections:
        raise ValueError("无法从 alice ain dump --map 的输出中解析到任何区段")
    previous_end = 0
    seen = set()
    for section in sorted(sections, key=lambda item: item.start):
        if section.name in seen or section.size <= 0 or section.start < previous_end:
            raise ValueError(f"AIN 区段重复或范围异常：{section.describe()}")
        seen.add(section.name)
        previous_end = section.end
    return sections


def _slice_section(decrypted: bytes, section: SectionInfo) -> bytes:
    size = section.size
    if size <= 0 or size > 2_000_000_000:
        raise ValueError(f"区段 {section.name} 大小异常：{size}")
    data = decrypted[section.start : section.end]
    if len(data) != size:
        raise ValueError(f"读取区段 {section.name} 不完整：期望 {size} 字节，实得 {len(data)} 字节")
    return data


def _decode_cstring_table(
    data: bytes, encoding: str, section_name: str, errors: str
) -> List[str]:
    # Section data starts with the 4-byte tag ("MSG0"/"STR0"), then int32 count.
    if len(data) < 8:
        raise ValueError(f"区段 {section_name} 数据不完整")
    count = struct.unpack_from("<i", data, 4)[0]
    if count < 0 or count > _MAX_ENTRIES:
        raise ValueError(f"区段 {section_name} 的条目数异常：{count}")

    result: List[str] = []
    pos = 8
    for index in range(count):
        end = data.find(b"\0", pos)
        if end < 0:
            raise ValueError(f"区段 {section_name} 在第 {index} 条处缺少字符串终止符")
        result.append(data[pos:end].decode(encoding, errors=errors))
        pos = end + 1
    return result


def _decode_msg1_table(data: bytes, encoding: str, errors: str) -> List[str]:
    # Section data starts with the 4-byte tag "MSG1", then int32 count and
    # int32 unknown field.
    if len(data) < 12:
        raise ValueError("区段 MSG1 数据不完整")
    count = struct.unpack_from("<i", data, 4)[0]
    if count < 0 or count > _MAX_ENTRIES:
        raise ValueError(f"区段 MSG1 的条目数异常：{count}")

    result: List[str] = []
    pos = 12  # tag(4) + count(4) + unknown(4)
    for index in range(count):
        if pos + 4 > len(data):
            raise ValueError(f"区段 MSG1 在第 {index} 条处截断")
        length = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        if length < 0 or pos + length > len(data):
            raise ValueError(f"区段 MSG1 在第 {index} 条处长度异常：{length}")
        raw = bytearray(data[pos : pos + length])
        pos += length
        # Mirror libsys4/src/ain.c::read_msg1_string().
        for offset, byte in enumerate(raw):
            raw[offset] = ((byte - offset) - 0x60) & 0xFF
        result.append(bytes(raw).decode(encoding, errors=errors))
    return result


def build_section_dump(
    path: str,
    section_name: str,
    encoding: str,
    data: bytes,
    *,
    errors: str = "strict",
) -> TextDump:
    """Build an editable TextDump without silently replacing invalid bytes."""

    kind = TEXT_SECTION_KINDS.get(section_name)
    if kind is None:
        raise ValueError(f"区段 {section_name} 不是可编辑的文本区段")
    if data[:4] != section_name.encode("ascii"):
        raise ValueError(f"区段标签和映射不一致：期望 {section_name}，实际 {data[:4]!r}")

    if section_name == "MSG1":
        texts = _decode_msg1_table(data, encoding, errors)
    else:
        texts = _decode_cstring_table(data, encoding, section_name, errors)

    entries = [
        TextEntry(
            kind=kind,
            index=index,
            occurrence=1,
            text=text,
            section=section_name,
            section_no=0,
            line_no=index + 1,
        )
        for index, text in enumerate(texts)
    ]
    return TextDump(path=str(path), encoding=encoding, sections=[section_name], entries=entries)


def read_system4_sections(
    tools: object,
    path: str,
    sections: Sequence[SectionInfo],
    encoding: str,
) -> list[TextDump]:
    """Read the specified System4 tables from one decrypted image."""
    decrypted = tools.decrypted_bytes(path)  # type: ignore[attr-defined]
    return [
        build_section_dump(path, section.name, encoding, _slice_section(decrypted, section))
        for section in sections
    ]


def load_section_map(
    tools: object, path: str, encodings: Sequence[str]
) -> List[SectionInfo]:
    last_error: Optional[Exception] = None
    for encoding in dict.fromkeys(encodings):
        try:
            map_text = tools.map_dump(path, encoding)  # type: ignore[attr-defined]
            return parse_section_map(map_text)
        except AliceError as exc:
            if not isinstance(exc, AliceRunError):
                raise
            last_error = exc
        except (OSError, RuntimeError, ValueError) as exc:
            last_error = exc
    raise ValueError(f"无法读取 AIN 区段表：{path}（{last_error}）") from last_error


def load_section_dump(
    tools: object,
    path: str,
    section_name: str,
    encoding: str,
    *,
    section_map_text: Optional[str] = None,
) -> TextDump:
    """Load one raw section as a TextDump.

    *tools* only needs a ``map_dump`` method returning the output of
    ``alice ain dump --map``; keeping it loose avoids a hard dependency on
    :mod:`aindiff.alice_tools`.
    """

    revision = FileRevision.capture(path)
    path = str(revision.path)
    if xsys35.is_xsys35_file(path):
        if section_name not in xsys35.SECTION_KINDS:
            raise ValueError(f"AINI 区段 {section_name} 不可编辑（支持 VARI/MSGI）")
        dump = xsys35.section_dump(path, section_name, encoding)
        revision.check()
        return dump

    if section_name not in TEXT_SECTION_KINDS:
        raise ValueError(f"区段 {section_name} 不是可编辑的文本区段（支持 MSG0/MSG1/STR0）")
    sections = (
        parse_section_map(section_map_text)
        if section_map_text is not None
        else load_section_map(tools, path, (encoding, "CP932", "CP936", "UTF-8"))
    )
    section = next((s for s in sections if s.name == section_name), None)
    if section is None:
        raise ValueError(f"该 AIN 中不存在区段 {section_name}")
    dump = read_system4_sections(tools, path, (section,), encoding)[0]
    revision.check()
    return dump


def text_section_names(
    tools: object,
    path: str,
    *,
    map_encodings: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return the text-bearing section names present in *ain_path*.

    The section map is ASCII, but ``alice ain dump --map`` still takes an
    input encoding and can reject one, so several are tried.
    """

    encodings = tuple(map_encodings or ("CP932", "CP936", "UTF-8"))
    return [
        s.name for s in load_section_map(tools, path, encodings) if s.name in TEXT_SECTION_KINDS
    ]


def load_text_section_with_fallback(
    tools: object,
    path: str,
    *,
    section_name: Optional[str] = None,
    section_names: Optional[Sequence[str]] = None,
    map_encodings: Optional[Sequence[str]] = None,
    text_encodings: Optional[Sequence[str]] = None,
) -> TextDump:
    """Load a raw text section when ``ain dump -t`` is unusable.

    This is the fallback for files whose function names and string table use
    different encodings (for example ``ランス０２``: CP932 names plus CP936
    text).  ``alice-tools`` accepts only one global input encoding, so
    ``ain dump -t`` cannot convert such a file at all.  Reading one raw text
    section straight from the decrypted image still works, which keeps those
    files readable. Saving still depends on alice-tools accepting the encoding.

    Either pass an explicit *section_name* or let the *section_names*
    preference order decide.  The chosen section is reported through
    :attr:`TextDump.sections` so callers can show which view they got.
    """

    reader = TextTableReader(tools, path, map_encodings=map_encodings)
    preferred = tuple(section_names or reader.names)
    if section_name is not None:
        if section_name not in reader.names:
            raise ValueError(f"该 AIN 中不存在可回退的文本区段 {section_name}")
        preferred = (section_name,)
    candidates = [name for name in preferred if name in reader.names]
    if not candidates:
        raise ValueError("该 AIN 没有可用于回退的文本区段")
    encodings = text_encodings or reader.fallback_encodings
    last_error = None
    for name in candidates:
        try:
            return reader.read(name, encodings)
        except (ValueError, LookupError) as exc:
            last_error = exc
    raise ValueError(f"自动识别和区段回退均失败（{last_error}）") from last_error


class TextTableReader:
    """A stable image and map, shared by the requested raw-table decodes."""

    def __init__(self, tools: object, path: str, *, map_encodings: Optional[Sequence[str]] = None):
        revision = FileRevision.capture(path)
        self.path = path = str(revision.path)
        self._legacy = xsys35.is_xsys35_file(path)
        if self._legacy:
            self._legacy_tables = xsys35.raw_text_tables(path)
            self.names = tuple(name for name in ("VARI", "MSGI") if name in self._legacy_tables)
            self.encodings = xsys35.TEXT_ENCODINGS
            self.fallback_encodings = xsys35.TEXT_ENCODINGS
        else:
            sections = load_section_map(tools, path, map_encodings or SYSTEM4_ENCODINGS)
            self._sections = {section.name: section for section in sections}
            self.names = tuple(name for name in ("MSG0", "MSG1", "STR0") if name in self._sections)
            self._image = tools.decrypted_bytes(path) if self.names else b""  # type: ignore[attr-defined]
            self.encodings = SYSTEM4_ENCODINGS
            self.fallback_encodings = ("CP936", "CP932", "UTF-8", "BIG5")
        revision.check()

    def read(self, name: str, encodings: Sequence[str]) -> TextDump:
        if name not in self.names:
            raise ValueError(f"该 AIN 中不存在区段 {name}：{self.path}")
        data = None if self._legacy else _slice_section(self._image, self._sections[name])
        last_error = None
        for encoding in dict.fromkeys(encodings):
            try:
                if data is None:
                    return xsys35.decode_text_table(self.path, name, self._legacy_tables[name], encoding)
                return build_section_dump(self.path, name, encoding, data)
            except (UnicodeError, LookupError) as exc:
                last_error = exc
        raise ValueError(f"{self.path} 的区段 {name} 无法用指定编码解码（{last_error}）") from last_error
