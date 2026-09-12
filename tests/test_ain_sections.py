import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aindiff.ain_sections import (  # noqa: E402
    SectionInfo,
    build_section_dump,
    load_text_section_with_fallback,
    parse_section_map,
    text_section_names,
)

MAP_TEXT = """VERS: 00000000 -> 00000008
CODE: 00000008 -> 00000010
STR0: 00000010 -> 00000030
MSG0: 00000030 -> 00000050
MSG1: 00000050 -> 00000070
"""


def cstring_table(entries, encoding="utf-8"):
    return b"TAG" + b"\0" + struct.pack("<i", len(entries)) + b"\0".join(
        e.encode(encoding) for e in entries
    ) + b"\0"


class SectionMapTests(unittest.TestCase):
    def test_parse_map(self):
        sections = parse_section_map(MAP_TEXT)
        self.assertEqual([s.name for s in sections], ["VERS", "CODE", "STR0", "MSG0", "MSG1"])
        self.assertEqual(sections[3], SectionInfo("MSG0", 0x30, 0x50))
        self.assertEqual(sections[3].size, 0x20)

    def test_parse_map_requires_at_least_one_section(self):
        with self.assertRaises(ValueError):
            parse_section_map("not a map\n")


class RawSectionTests(unittest.TestCase):
    def test_msg0(self):
        dump = build_section_dump("x.ain", "MSG0", "UTF-8", cstring_table(["a", "b\nc"]))
        self.assertEqual(dump.sections, ["MSG0"])
        self.assertEqual([e.kind for e in dump.entries], ["m", "m"])
        self.assertEqual([e.text for e in dump.entries], ["a", "b\nc"])
        self.assertEqual(dump.entries[1].index, 1)

    def test_str0(self):
        dump = build_section_dump("x.ain", "STR0", "UTF-8", cstring_table(["s0", "s1"]))
        self.assertEqual([e.kind for e in dump.entries], ["s", "s"])

    def test_msg1_decoding(self):
        # Two bytes are encoded by libsys4 as: b = (raw - i - 0x60) & 0xff.
        def encode(text):
            raw = text.encode("utf-8")
            return bytes(((byte + 0x60 + i) & 0xFF) for i, byte in enumerate(raw))

        payload = b"MSG1" + struct.pack("<ii", 2, -559038737)  # 0xDEADBEEF as signed i32
        for text in ("ab", "你好"):
            data = encode(text)
            payload += struct.pack("<i", len(data)) + data
        dump = build_section_dump("x.ain", "MSG1", "UTF-8", payload)
        self.assertEqual([e.text for e in dump.entries], ["ab", "你好"])

    def test_bad_count_is_rejected(self):
        with self.assertRaises(ValueError):
            build_section_dump("x.ain", "MSG0", "UTF-8", b"MSG0" + struct.pack("<i", -1))

    def test_truncated_string_is_rejected(self):
        data = b"MSG0" + struct.pack("<i", 2) + b"abc"
        with self.assertRaises(ValueError):
            build_section_dump("x.ain", "MSG0", "UTF-8", data)

    def test_unsupported_section_is_rejected(self):
        with self.assertRaises(ValueError):
            build_section_dump("x.ain", "CODE", "UTF-8", b"")

    def test_strict_decoding_rejects_invalid_cstring_bytes(self):
        table = cstring_table(["ｱ"], encoding="cp932")
        with self.assertRaises(UnicodeDecodeError):
            build_section_dump("x.ain", "MSG0", "CP936", table, errors="strict")
        # A manually selected encoding remains permissive for mixed tables.
        dump = build_section_dump("x.ain", "MSG0", "CP936", table)
        self.assertEqual(dump.entries[0].text, "\ufffd")

    def test_strict_decoding_rejects_invalid_msg1_bytes(self):
        encoded = bytes([(0xB1 + 0x60) & 0xFF])
        table = b"MSG1" + struct.pack("<iii", 1, 0, len(encoded)) + encoded
        with self.assertRaises(UnicodeDecodeError):
            build_section_dump("x.ain", "MSG1", "CP936", table, errors="strict")
        dump = build_section_dump("x.ain", "MSG1", "CP932", table, errors="strict")
        self.assertEqual(dump.entries[0].text, "ｱ")


class _FakeTools:
    """Stands in for AliceTools in fallback tests (no alice binary needed)."""

    def __init__(self, map_text, sections, map_fails=()):
        self.map_text = map_text
        self.sections = sections
        self.map_fails = set(map_fails)
        self.map_encodings_tried = []
        self.decrypt_calls = 0

    def map_dump(self, _path, encoding):
        self.map_encodings_tried.append(encoding)
        if encoding in self.map_fails:
            raise RuntimeError(f"map dump failed for {encoding}")
        return self.map_text

    def decrypted_bytes(self, _path):
        self.decrypt_calls += 1
        return self.sections


class TextSectionNamesTests(unittest.TestCase):
    def test_lists_only_text_sections(self):
        tools = _FakeTools(MAP_TEXT, b"")
        self.assertEqual(text_section_names(tools, "x.ain"), ["STR0", "MSG0", "MSG1"])

    def test_falls_through_failing_encodings(self):
        tools = _FakeTools(MAP_TEXT, b"", map_fails=("CP932",))
        self.assertEqual(text_section_names(tools, "x.ain"), ["STR0", "MSG0", "MSG1"])
        self.assertEqual(tools.map_encodings_tried, ["CP932", "CP936"])

    def test_raises_when_no_encoding_works(self):
        tools = _FakeTools(MAP_TEXT, b"", map_fails=("CP932", "CP936", "UTF-8"))
        with self.assertRaises(ValueError):
            text_section_names(tools, "x.ain")

    def test_retries_when_map_output_cannot_be_parsed(self):
        tools = _FakeTools(MAP_TEXT, b"")
        original_map_dump = tools.map_dump

        def map_dump(path, encoding):
            result = original_map_dump(path, encoding)
            return "invalid map" if encoding == "CP932" else result

        tools.map_dump = map_dump
        self.assertEqual(text_section_names(tools, "x.ain"), ["STR0", "MSG0", "MSG1"])
        self.assertEqual(tools.map_encodings_tried, ["CP932", "CP936"])


class FallbackLoadTests(unittest.TestCase):
    def _tools(self, text="hello", encoding="utf-8"):
        table = cstring_table([text], encoding=encoding)
        # MSG0 must span exactly the encoded table, so derive its end offset.
        start, end = 0x30, 0x30 + len(table)
        map_text = f"VERS: 00000000 -> 00000008\nMSG0: {start:08x} -> {end:08x}\n"
        return _FakeTools(map_text, b"\0" * start + table)

    def test_prefers_msg0_and_decodes_with_fallback_encoding(self):
        tools = self._tools("你好", encoding="cp936")
        dump = load_text_section_with_fallback(tools, "x.ain")
        self.assertEqual(dump.sections, ["MSG0"])
        self.assertEqual(dump.encoding, "CP936")
        self.assertEqual([e.text for e in dump.entries], ["你好"])

    def test_explicit_section_is_honoured(self):
        dump = load_text_section_with_fallback(
            self._tools(), "x.ain", section_name="MSG0"
        )
        self.assertEqual(dump.sections, ["MSG0"])

    def test_retries_invalid_text_encoding_without_reloading_data(self):
        tools = self._tools("ｱ", encoding="cp932")
        dump = load_text_section_with_fallback(tools, "x.ain")
        self.assertEqual(dump.encoding, "CP932")
        self.assertEqual([entry.text for entry in dump.entries], ["ｱ"])
        self.assertEqual(tools.map_encodings_tried, ["CP932"])
        self.assertEqual(tools.decrypt_calls, 1)

    def test_tries_all_encodings_for_preferred_section_first(self):
        messages = cstring_table(["ｱ"], encoding="cp932")
        strings = cstring_table(["internal string"])
        map_text = (
            f"MSG0: 00000000 -> {len(messages):08x}\n"
            f"STR0: {len(messages):08x} -> {len(messages) + len(strings):08x}\n"
        )
        tools = _FakeTools(map_text, messages + strings)
        dump = load_text_section_with_fallback(tools, "x.ain")
        self.assertEqual(dump.sections, ["MSG0"])
        self.assertEqual(dump.encoding, "CP932")
        self.assertEqual([entry.text for entry in dump.entries], ["ｱ"])

    def test_rejects_text_invalid_in_every_candidate_encoding(self):
        tools = self._tools("ｱ", encoding="cp932")
        with self.assertRaisesRegex(ValueError, "区段回退均失败"):
            load_text_section_with_fallback(
                tools, "x.ain", text_encodings=("CP936", "UTF-8")
            )
        self.assertEqual(tools.decrypt_calls, 1)

    def test_missing_explicit_section_is_rejected(self):
        with self.assertRaises(ValueError):
            load_text_section_with_fallback(
                self._tools(), "x.ain", section_name="STR0"
            )

    def test_reports_all_failures(self):
        tools = _FakeTools("VERS: 00000000 -> 00000008\n", b"\0" * 0x30)
        with self.assertRaises(ValueError):
            load_text_section_with_fallback(tools, "x.ain")


if __name__ == "__main__":
    unittest.main()
