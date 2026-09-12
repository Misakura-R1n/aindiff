import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aindiff import xsys35  # noqa: E402
from aindiff.model import parse_dump  # noqa: E402


def _encrypt(payload):
    return bytes(((b >> 2) | (b << 6)) & 0xFF for b in payload)


def _section(name, strings):
    out = name.encode("ascii") + struct.pack("<I", 0) + struct.pack("<I", len(strings))
    for text in strings:
        out += text.encode("utf-8") + b"\0"
    return out


def make_ain():
    payload = b"HEL0" + struct.pack("<I", 0) + struct.pack("<I", 0)
    payload += b"FUNC" + struct.pack("<I", 0) + struct.pack("<I", 0)
    payload += _section("VARI", ["VAR0", "你好"])
    payload += _section("MSGI", ["", "ランス"])
    return b"AINI" + struct.pack("<I", 1) + _encrypt(payload)


def _container(payload):
    return b"AINI" + struct.pack("<I", 1) + _encrypt(payload)


class Xsys35Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "System39.ain"
        self.path.write_bytes(make_ain())

    def test_detect_and_read(self):
        self.assertTrue(xsys35.is_xsys35_file(self.path))
        self.assertEqual(xsys35.detect_encoding(str(self.path)), "UTF-8")
        dump = xsys35.text_dump(str(self.path), "UTF-8")
        self.assertEqual(dump.sections, ["VARI", "MSGI"])
        self.assertEqual([e.kind for e in dump.entries[:2]], ["s", "s"])
        self.assertEqual(dump.entries[1].text, "你好")
        self.assertEqual(dump.entries[3].text, "ランス")

    def test_section_dump(self):
        dump = xsys35.section_dump(str(self.path), "MSGI", "UTF-8")
        self.assertEqual(dump.sections, ["MSGI"])
        self.assertEqual([e.text for e in dump.entries], ["", "ランス"])

    def test_map_text(self):
        text = xsys35.map_text(str(self.path))
        self.assertIn("VARI:", text)
        self.assertIn("MSGI:", text)

    def test_edit_roundtrip(self):
        original = self.path.read_bytes()
        edit = 'm[1] = "テスト"\n'
        path, old, new = xsys35.edit_file(str(self.path), edit, "UTF-8")
        self.assertEqual(path, self.path.resolve())
        self.assertTrue(Path(str(self.path) + ".bak").exists())
        self.assertEqual(Path(str(self.path) + ".bak").read_bytes(), original)
        self.assertEqual(old, len(original))
        self.assertEqual(new, self.path.stat().st_size)
        dump = xsys35.text_dump(str(self.path), "UTF-8")
        self.assertEqual(dump.entries[3].text, "テスト")

    def test_parse_dump_accepts_text_syntax(self):
        syntax = xsys35.dump_text_syntax(str(self.path), "UTF-8")
        dump = parse_dump(syntax, path=str(self.path), encoding="UTF-8")
        self.assertEqual(len(dump.entries), 4)

    def test_rotation_matches_format_for_every_byte(self):
        payload = bytes(range(256))
        self.assertEqual(xsys35._encrypt_payload(payload), _encrypt(payload))
        self.assertEqual(xsys35._decrypt_payload(_container(payload)), payload)

    def test_edit_preserves_unknown_tail_and_reserved_bytes(self):
        payload = bytearray(xsys35._decrypt_payload(make_ain()))
        start = payload.index(b"MSGI")
        payload[start + 4 : start + 8] = b"\x12\x34\x56\x78"
        for tail in (b"\xff", b"PAD", b"NEXT\x01\x02\x03\x04opaque\0"):
            with self.subTest(tail=tail):
                original_payload = bytes(payload) + tail
                original = b"AIN2" + struct.pack("<I", 2) + _encrypt(original_payload)
                self.path.write_bytes(original)
                xsys35.edit_file(self.path, 'm[1] = "test"', "UTF-8", backup=False)
                expected_payload = original_payload.replace("ランス".encode("utf-8"), b"test")
                self.assertEqual(self.path.read_bytes(), original[:8] + _encrypt(expected_payload))

    def test_commented_assignments_are_not_applied(self):
        edit = ';m[1] = "comment"\nm[1] = "first"\n  ;m[1] = "also a comment"\n'
        edit += 'm[1] = "last;value" ; trailing comment\n;m[1] = "ignored"'
        xsys35.edit_file(self.path, edit, "UTF-8", backup=False)
        dump = xsys35.section_dump(str(self.path), "MSGI", "UTF-8")
        self.assertEqual(dump.entries[1].text, "last;value")

    def test_dump_with_only_comments_does_not_reencode_text(self):
        original = self.path.read_bytes()
        edit = xsys35.dump_text_syntax(str(self.path), "CP932")
        xsys35.edit_file(self.path, edit, "CP932", backup=False)
        self.assertEqual(self.path.read_bytes(), original)

    def test_edit_supports_escaped_text_and_unicode_line_separator(self):
        edit = 'm[1] = "a\\\"b\\\\c\\n\\r\\t\u2028z"'
        xsys35.edit_file(self.path, edit, "UTF-8", backup=False)
        dump = xsys35.section_dump(str(self.path), "MSGI", "UTF-8")
        self.assertEqual(dump.entries[1].text, 'a"b\\c\n\r\t\u2028z')

    def test_invalid_edits_leave_original_and_backup_untouched(self):
        original = self.path.read_bytes()
        backup = Path(str(self.path) + ".bak")
        backup.write_bytes(b"existing backup")
        invalid = (
            ('m[999] = "x"', "UTF-8"),
            ('s[2] = "x"', "UTF-8"),
            ('m[1] = "unterminated', "UTF-8"),
            ('m[1] = "a"garbage"', "UTF-8"),
            ('m[-1] = "x"', "UTF-8"),
            ('m[1] = "nul\0byte"', "UTF-8"),
            ('m[1] = "x"', "UTF-16LE"),
            ('m[1] = "你好"', "ASCII"),
        )
        for edit, encoding in invalid:
            with self.subTest(edit=edit, encoding=encoding):
                with self.assertRaises(ValueError):
                    xsys35.edit_file(self.path, edit, encoding)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(backup.read_bytes(), b"existing backup")

    def test_truncated_sections_are_rejected_before_writing(self):
        func = b"FUNC" + struct.pack("<II", 0, 1) + b"name\0"
        hel = b"HEL0" + struct.pack("<II", 0, 1) + b"dll\0"
        hel += struct.pack("<I", 1) + b"func\0" + struct.pack("<I", 2)
        truncated = [func + bytes(size) for size in range(6)]
        truncated += [hel + bytes(size) for size in range(8)]
        truncated += [_section("MSGI", ["x"])[:size] for size in range(4, 12)]
        truncated += [_section("VARI", ["unterminated"])[:-1]]
        truncated += [b"VARI" + struct.pack("<II", 0, 0xFFFFFFFF)]
        for payload in truncated:
            with self.subTest(payload=payload):
                original = _container(payload)
                self.path.write_bytes(original)
                with self.assertRaises(ValueError):
                    xsys35.edit_file(self.path, "", "UTF-8")
                self.assertEqual(self.path.read_bytes(), original)
                self.assertFalse(Path(str(self.path) + ".bak").exists())

    def test_edit_rejects_unrecognized_and_ambiguous_payloads(self):
        payloads = (b"", b"NEXT" + bytes(8), _section("MSGI", ["a"]) * 2)
        for payload in payloads:
            with self.subTest(payload=payload):
                original = _container(payload)
                self.path.write_bytes(original)
                with self.assertRaises(ValueError):
                    xsys35.edit_file(self.path, 'm[0] = "x"', "UTF-8")
                self.assertEqual(self.path.read_bytes(), original)

    def test_edit_does_not_overwrite_existing_ain_new_file(self):
        neighbor = Path(str(self.path) + ".ain-new")
        neighbor.write_bytes(b"unrelated file")
        xsys35.edit_file(self.path, 'm[1] = "test"', "UTF-8", backup=False)
        self.assertEqual(neighbor.read_bytes(), b"unrelated file")
        self.assertEqual(
            sorted(p.name for p in self.path.parent.iterdir()), [self.path.name, neighbor.name]
        )

    def test_failed_replace_preserves_original_and_cleans_temporary_files(self):
        original = self.path.read_bytes()
        with mock.patch.object(xsys35.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                xsys35.edit_file(self.path, 'm[1] = "test"', "UTF-8", backup=False)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
