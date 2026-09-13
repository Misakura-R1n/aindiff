import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aindiff.alice_tools import AliceError
from aindiff.loading import load_pair, open_dump_with_fallback
from aindiff.model import count_changed


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.left = str(Path(self.tmp.name) / "left.ain")
        self.right = str(Path(self.tmp.name) / "right.ain")
        for path in (self.left, self.right):
            Path(path).write_bytes(b"synthetic")
        self.tools = Mock()

    def configure_tables(self, left_table, right_table):
        tables = {self.left: left_table, self.right: right_table}
        self.tools.decrypted_bytes.side_effect = tables.__getitem__
        self.tools.map_dump.side_effect = lambda path, enc: (
            f"{tables[path][:4].decode()}: 00000000 -> {len(tables[path]):08x}"
        )

    def test_matching_message_tables_can_have_different_tags(self):
        message = b"hello"
        left_table = b"MSG0" + struct.pack("<i", 1) + message + b"\0"
        encoded = bytes((byte + offset + 0x60) & 255 for offset, byte in enumerate(message))
        right_table = b"MSG1" + struct.pack("<iii", 1, 0, len(encoded)) + encoded
        self.configure_tables(left_table, right_table)
        with patch("aindiff.loading.open_dump", side_effect=AliceError("mixed")):
            for explicit_fallback in (False, True):
                with self.subTest(gui_policy=explicit_fallback):
                    left, right, rows, notes = load_pair(
                        self.tools, self.left, self.right, None, None,
                        allow_explicit_fallback=explicit_fallback,
                    )
                    self.assertEqual(count_changed(rows), 0)
                    self.assertEqual(notes, ["MSG0", "MSG1"])
                    self.assertEqual(left.entries[0].text, right.entries[0].text)

    def test_gui_explicit_fallback_preserves_encoding_and_cli_rejects(self):
        table = b"MSG0" + struct.pack("<i", 1) + "あ".encode("CP932") + b"\0"
        self.configure_tables(table, table)
        with patch("aindiff.loading.open_dump", side_effect=AliceError("mixed")):
            with self.assertRaises(AliceError):
                open_dump_with_fallback(self.tools, self.left, "CP932")
            dump, _ = open_dump_with_fallback(
                self.tools, self.left, "CP932", allow_explicit_fallback=True
            )
            self.assertEqual(dump.entries[0].text, "あ")
            self.assertEqual(dump.encoding, "CP932")
            with self.assertRaises(ValueError):
                open_dump_with_fallback(
                    self.tools, self.left, "UTF-8", allow_explicit_fallback=True
                )

    def test_nonmatching_tables_are_rejected(self):
        self.configure_tables(
            b"MSG0" + struct.pack("<i", 1) + b"hello\0",
            b"STR0" + struct.pack("<i", 1) + b"hello\0",
        )
        with patch("aindiff.loading.open_dump", side_effect=AliceError("mixed")):
            with self.assertRaisesRegex(AliceError, "缺少"):
                load_pair(self.tools, self.left, self.right, None, None)
