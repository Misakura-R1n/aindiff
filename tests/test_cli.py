"""CLI-level tests for `aindiff check` / `aindiff export`.

These use a fake ``alice`` executable, so they run without alice-tools and
without a GUI.
"""

import csv
import io
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aindiff import cli  # noqa: E402
from aindiff.model import count_changed, parse_dump

# MSG0 occupies 0x30..0x3e (14 bytes): tag(4) + count(4) + "hi\0"(3) + "yo\0"(3).
MAP_TEXT = "VERS: 00000000 -> 00000008\nMSG0: 00000030 -> 0000003e\n"


def _msg0_payload():
    table = b"MSG0" + struct.pack("<i", 2) + b"hi\0" + b"yo\0"
    return b"\0" * 0x30 + table


def _write_fake_alice(tmp: Path) -> Path:
    """A fake alice that dumps normally, but fails on `ain dump -t` for a file
    named ``*.mixed.ain`` (mirroring ランス０２'s mixed-encoding failure)."""

    fake_py = tmp / "fake_alice.py"
    fake_py.write_text(
        f'''import sys
from pathlib import Path

args = sys.argv[1:]
map_text = {MAP_TEXT!r}
payload = {_msg0_payload()!r}

if args[:2] == ["ain", "dump"]:
    source = args[-1]
    mixed = Path(source).name == "mixed.ain"
    if "--map" in args:
        out = sys.stdout
        out.write(map_text)
        raise SystemExit(0)
    if "-d" in args:
        Path(args[args.index("-o") + 1]).write_bytes(payload)
        raise SystemExit(0)
    if "-t" in args and mixed:
        print("iconv: Invalid argument", file=sys.stderr)
        raise SystemExit(1)
    Path(args[args.index("-o") + 1]).write_text(
        '; main\\n;s[1] = "a"\\n;m[2] = "b"\\n', encoding="utf-8"
    )
    raise SystemExit(0)

print("unexpected args", args, file=sys.stderr)
raise SystemExit(2)
''',
        encoding="utf-8",
    )
    if os.name != "nt":
        fake_py.write_text(f"#!{sys.executable}\n" + fake_py.read_text(encoding="utf-8"), encoding="utf-8")
        fake_py.chmod(0o755)
        return fake_py
    cmd = tmp / "fake_alice.cmd"
    cmd.write_text(
        f'@echo off\r\n"{sys.executable}" "%~dp0fake_alice.py" %*\r\n',
        encoding="utf-8",
    )
    return cmd


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.alice = _write_fake_alice(tmp)
        self.normal = tmp / "normal.ain"
        self.normal.write_bytes(b"ORIGINAL")
        self.mixed = tmp / "mixed.ain"
        self.mixed.write_bytes(b"ORIGINAL")

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = cli.main([argv[0], "--alice", str(self.alice), *argv[1:]])
        return code, buf.getvalue()

    def test_check_same_file_reports_no_differences(self):
        code, out = self._run(["check", str(self.normal), str(self.normal)])
        self.assertEqual(code, 0)
        self.assertIn("差异：0", out)
        self.assertNotIn("回退", out)

    def test_check_falls_back_for_mixed_encoding_file(self):
        code, out = self._run(["check", str(self.mixed), str(self.mixed)])
        self.assertEqual(code, 0)
        self.assertIn("回退到原始区段 MSG0", out)
        self.assertIn("2 条文本", out)
        self.assertIn("差异：0", out)

    def test_check_rejects_missing_file_with_clear_message(self):
        code, out = self._run(
            ["check", str(self.normal.parent / "nope.ain"), str(self.normal)]
        )
        self.assertEqual(code, 1)
        self.assertIn("文件不存在或不可读", out)

    def test_export_writes_tsv_rows(self):
        out_path = Path(self.tmp.name) / "out.tsv"
        code, out = self._run(
            ["export", str(self.normal), str(self.normal), "-o", str(out_path)]
        )
        self.assertEqual(code, 0)
        self.assertIn("已导出", out)
        lines = out_path.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(lines[0].split("\t")[:3], ["no", "section_left", "section_right"])
        self.assertEqual(len(lines), 4)  # header + section + s[1] + m[2]
        self.assertIn("same", lines[2])

    def test_export_falls_back_for_mixed_encoding_file(self):
        out_path = Path(self.tmp.name) / "out.tsv"
        code, out = self._run(
            ["export", str(self.mixed), str(self.mixed), "-o", str(out_path)]
        )
        self.assertEqual(code, 0)
        self.assertIn("回退到原始区段 MSG0", out)
        body = out_path.read_text(encoding="utf-8-sig")
        self.assertIn("MSG0", body)
        self.assertIn("hi", body)

    def test_explicit_encoding_is_not_silently_overridden(self):
        code, out = self._run(
            [
                "check",
                str(self.mixed),
                str(self.mixed),
                "--left-encoding", "CP932",
                "--right-encoding", "CP932",
            ]
        )
        self.assertEqual(code, 1)
        self.assertNotIn("回退", out)

    def test_empty_arguments_dispatch_to_gui_without_starting_a_window(self):
        with patch.object(cli, "_run_gui", return_value=0) as run_gui:
            self.assertEqual(cli.main([]), 0)
        run_gui.assert_called_once()
        self.assertIsNone(run_gui.call_args.args[0].left)

    def test_help_does_not_launch_gui(self):
        buf = io.StringIO()
        with redirect_stdout(buf), patch.object(cli, "_run_gui") as run_gui:
            with self.assertRaises(SystemExit) as exc:
                cli.main(["--help"])
        self.assertEqual(exc.exception.code, 0)
        self.assertIn("usage:", buf.getvalue())
        run_gui.assert_not_called()

    def test_one_sided_fallback_compares_matching_raw_tables(self):
        out_path = Path(self.tmp.name) / "out.tsv"
        code, _ = self._run(["export", str(self.mixed), str(self.normal), "-o", str(out_path)])
        self.assertEqual(code, 0)
        with out_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        self.assertEqual([row["state"] for row in rows[1:]], ["same", "same"])
        self.assertEqual(rows[0]["section_right"], "MSG0")

    def test_switching_raw_tables_rechecks_the_table_encoding(self):
        left = parse_dump('; STR0\n;s[0] = "ｦ"\n', path=str(self.normal), encoding="CP932")
        right = parse_dump('; MSG0\n;m[0] = "hello"\n', path=str(self.mixed), encoding="CP936")
        payload = b"STR0" + struct.pack("<i", 1) + "ｦ".encode("CP932") + b"\0"
        tools = Mock()
        tools.map_dump.return_value = f"STR0: 00000000 -> {len(payload):08x}\n"
        tools.decrypted_bytes.return_value = payload
        with patch.object(cli, "_open_dump_with_fallback", side_effect=[(left, "STR0"), (right, "MSG0")]):
            _, right_dump, rows, notes = cli._load_pair(
                tools, str(self.normal), str(self.mixed), None, None
            )
        self.assertEqual(right_dump.encoding, "CP932")
        self.assertEqual(right_dump.entries[0].text, "ｦ")
        self.assertEqual(count_changed(rows), 0)
        self.assertEqual(notes, ["STR0"])

    def test_export_rejects_input_as_output(self):
        before = self.normal.read_bytes()
        code, out = self._run(["export", str(self.normal), str(self.mixed), "-o", str(self.normal)])
        self.assertEqual(code, 1)
        self.assertIn("不能与输入", out)
        self.assertEqual(self.normal.read_bytes(), before)

    def test_failed_export_preserves_previous_output(self):
        output = Path(self.tmp.name) / "out.tsv"
        output.write_bytes(b"PREVIOUS")
        with patch.object(cli, "_write_tsv", side_effect=OSError("disk full")):
            code, out = self._run(["export", str(self.normal), str(self.normal), "-o", str(output)])
        self.assertEqual(code, 1)
        self.assertIn("disk full", out)
        self.assertEqual(output.read_bytes(), b"PREVIOUS")
        self.assertEqual(list(output.parent.glob(".aindiff-export-*")), [])

    def test_invalid_container_reports_error_without_traceback(self):
        self.normal.write_bytes(b"AINI")
        code, out = self._run(["check", str(self.normal), str(self.normal), "--left-encoding", "UTF-8"])
        self.assertEqual(code, 1)
        self.assertIn("不完整", out)


if __name__ == "__main__":
    unittest.main()
