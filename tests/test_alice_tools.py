import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aindiff.alice_tools import AliceError, AliceTools


def _write_fake_alice(tmp: Path) -> Path:
    fake_py = tmp / "fake_alice.py"
    fake_py.write_text(
        """import json
import os
import sys
from pathlib import Path

log = Path(os.environ['FAKE_ALICE_LOG'])
log.write_text(json.dumps(sys.argv[1:], ensure_ascii=False), encoding='utf-8')

args = sys.argv[1:]

if args[:2] == ['ain', 'dump']:
    enc = args[args.index('--input-encoding') + 1]
    if enc == 'FAIL':
        print('iconv: Invalid argument', file=sys.stderr)
        raise SystemExit(2)
    out = Path(args[args.index('-o') + 1])
    output_encoding = args[args.index('--output-encoding') + 1]
    out.write_text('; main\\n;s[1] = "ok"\\n;m[2] = "消息"\\n', encoding=output_encoding)
    raise SystemExit(0)

if args[:2] == ['ain', 'edit']:
    edit_file = Path(args[args.index('-t') + 1])
    out = Path(args[args.index('-o') + 1])
    source = args[-1]
    payload = Path(source).read_bytes() + edit_file.read_bytes()
    out.write_bytes(payload)
    raise SystemExit(0)

print('unexpected args', args, file=sys.stderr)
raise SystemExit(2)
""",
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


class AliceToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.fake = _write_fake_alice(tmp)
        self.log = tmp / "args.json"
        env = patch.dict(os.environ, {"FAKE_ALICE_LOG": str(self.log)})
        env.start()
        self.addCleanup(env.stop)
        self.tools = AliceTools(self.fake)
        self.ain = tmp / "input.ain"
        self.ain.write_bytes(b"ORIGINAL")

    def test_dump_text_returns_parsed_output(self):
        text = self.tools.dump_text(self.ain, "CP932")
        self.assertIn("; main", text)
        self.assertIn('s[1] = "ok"', text)
        args = json.loads(self.log.read_text(encoding="utf-8"))
        self.assertIn("--input-encoding", args)
        self.assertEqual(args[args.index("--input-encoding") + 1], "CP932")

    def test_detect_encoding_uses_first_success(self):
        encoding = self.tools.detect_encoding(self.ain, candidates=("FAIL", "CP936"))
        self.assertEqual(encoding, "CP936")

    def test_dump_text_honors_output_encoding(self):
        text = self.tools.dump_text(self.ain, "CP936", output_encoding="CP936")
        self.assertIn("消息", text)

    def test_edit_text_writes_back_and_makes_backup(self):
        old, before, after = self.tools.edit_text(
            self.ain, 's[1] = "changed"\n', "CP936", backup=True
        )
        self.assertEqual(old, self.ain.resolve())
        self.assertEqual(before, len(b"ORIGINAL"))
        self.assertNotEqual(after, before)
        self.assertTrue(self.ain.with_name("input.ain.bak").exists())
        args = json.loads(self.log.read_text(encoding="utf-8"))
        self.assertEqual(args[args.index("--output-encoding") + 1], "CP936")
        self.assertIn(str(self.ain.resolve()), args)

    def test_save_preserves_existing_sibling_temporary_file(self):
        sibling = self.ain.with_name(self.ain.name + ".ain-new")
        sibling.write_bytes(b"UNRELATED")
        self.tools.edit_text(self.ain, 's[1] = "changed"\n', "CP936")
        self.assertEqual(sibling.read_bytes(), b"UNRELATED")
        args = json.loads(self.log.read_text(encoding="utf-8"))
        output = Path(args[args.index("-o") + 1])
        self.assertEqual(output.parent.parent, self.ain.parent)
        self.assertFalse(output.parent.exists())

    def test_failed_save_preserves_source_and_previous_backup(self):
        backup = self.ain.with_name(self.ain.name + ".bak")
        backup.write_bytes(b"PREVIOUS BACKUP")

        def fail(args, **kwargs):
            Path(args[args.index("-o") + 1]).write_bytes(b"PARTIAL")
            raise AliceError("failed")

        with patch.object(self.tools, "run", side_effect=fail):
            with self.assertRaises(AliceError):
                self.tools.edit_text(self.ain, 's[1] = "changed"\n', "CP936")
        self.assertEqual(self.ain.read_bytes(), b"ORIGINAL")
        self.assertEqual(backup.read_bytes(), b"PREVIOUS BACKUP")
        self.assertEqual(list(self.ain.parent.glob(".aindiff-edit-*")), [])

    def test_empty_output_does_not_replace_source(self):
        def empty(args, **kwargs):
            Path(args[args.index("-o") + 1]).write_bytes(b"")

        with patch.object(self.tools, "run", side_effect=empty):
            with self.assertRaisesRegex(AliceError, "空文件"):
                self.tools.edit_text(self.ain, 's[1] = "changed"\n', "CP936")
        self.assertEqual(self.ain.read_bytes(), b"ORIGINAL")
        self.assertFalse(self.ain.with_name(self.ain.name + ".bak").exists())

    def test_backup_failure_does_not_replace_source(self):
        with patch("aindiff.alice_tools.shutil.copy2", side_effect=OSError("backup failed")):
            with self.assertRaises(OSError):
                self.tools.edit_text(self.ain, 's[1] = "changed"\n', "CP936")
        self.assertEqual(self.ain.read_bytes(), b"ORIGINAL")
        self.assertEqual(list(self.ain.parent.glob(".aindiff-edit-*")), [])


if __name__ == "__main__":
    unittest.main()
