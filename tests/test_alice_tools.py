import hashlib
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aindiff.alice_tools import AliceError, AliceRunError, AliceTools, _byte_preserving_edit
from aindiff.model import escape_text, parse_dump


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
    def test_transport_preserves_encoded_bytes_and_comments(self):
        for encoding, text in (
            ("CP936", '中文あ乗\n\r\t\b\f"\\'),
            ("CP932", '日本語ソ表\n"\\'),
            ("UTF-8", '中文😀\u2028'),
        ):
            with self.subTest(encoding=encoding):
                patch_text = ';m[9] = "ignored"\n' + f'm[2] = "{escape_text(text)}"\n'
                transported = parse_dump(_byte_preserving_edit(patch_text, encoding))
                self.assertEqual(len(transported.entries), 1)
                self.assertEqual(transported.entries[0].index, 2)
                self.assertEqual(transported.entries[0].text.encode("latin1"), text.encode(encoding))

    def test_transport_rejects_invalid_or_unrepresentable_changes(self):
        for edit, encoding in (
            ('m[0] = "😀"\n', "CP936"),
            ('m[0] = "nul\0"\n', "UTF-8"),
            ('s["key"] = "value"\n', "CP936"),
            ('invalid\n', "CP936"),
            ('m[0] = "' + "a" * 65536 + '"\n', "CP936"),
        ):
            with self.subTest(encoding=encoding), self.assertRaises(AliceError):
                _byte_preserving_edit(edit, encoding)

    def test_iconv_failure_retries_using_byte_transport(self):
        text = '中文乗\n"\\'
        calls = []

        def run(args, **kwargs):
            calls.append(list(args))
            output = Path(args[args.index("-o") + 1])
            if len(calls) == 1:
                output.write_bytes(b"PARTIAL")
                raise AliceRunError("ERROR: iconv: Invalid argument")
            self.assertFalse(output.exists())
            self.assertEqual(args[args.index("--output-encoding") + 1], "ISO-8859-1")
            edit = Path(args[args.index("-t") + 1]).read_text(encoding="utf-8")
            entry = parse_dump(edit).entries[0]
            self.assertEqual(entry.text.encode("latin1"), text.encode("CP936"))
            output.write_bytes(b"SAVED")

        with patch.object(self.tools, "run", side_effect=run):
            with patch.object(self.tools, "_verify_edited_text") as verify:
                self.tools.edit_text(self.ain, f'm[0] = "{escape_text(text)}"\n', "CP936")
        self.assertEqual(verify.call_args.args[1], {("m", 0): text.encode("CP936")})
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.ain.read_bytes(), b"SAVED")
        self.assertEqual(self.ain.with_suffix(".ain.bak").read_bytes(), b"ORIGINAL")

    def test_failed_retry_preserves_original_and_backup(self):
        backup = self.ain.with_suffix(".ain.bak")
        backup.write_bytes(b"OLD BACKUP")
        with patch.object(self.tools, "run", side_effect=AliceRunError("iconv: failed")) as run:
            with self.assertRaises(AliceRunError):
                self.tools.edit_text(self.ain, 'm[0] = "text"\n', "CP936")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.ain.read_bytes(), b"ORIGINAL")
        self.assertEqual(backup.read_bytes(), b"OLD BACKUP")
        self.assertEqual(list(self.ain.parent.glob(".aindiff-edit-*")), [])

    def test_other_alice_errors_do_not_retry(self):
        with patch.object(self.tools, "run", side_effect=AliceRunError("Invalid index")) as run:
            with self.assertRaises(AliceRunError):
                self.tools.edit_text(self.ain, 'm[0] = "text"\n', "CP936")
        run.assert_called_once()

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

    def test_dump_text_auto_reuses_successful_dump(self):
        with patch.object(self.tools, "dump_text", return_value="text") as dump:
            self.assertEqual(self.tools.dump_text_auto(self.ain, ("CP932",)), ("text", "CP932"))
        dump.assert_called_once_with(self.ain, "CP932")

    def test_close_kills_active_child_and_prevents_new_commands(self):
        runner = AliceTools(sys.executable)
        # The Popen notification occurs under the same lock as child registration.
        started = threading.Event()
        errors = []

        def worker():
            try:
                runner.run(["-c", "import time; time.sleep(10)"], timeout=3)
            except AliceError as exc:
                errors.append(exc)

        import subprocess
        popen = subprocess.Popen

        def start(*args, **kwargs):
            child = popen(*args, **kwargs)
            started.set()
            return child

        with patch("aindiff.alice_tools.subprocess.Popen", side_effect=start):
            task = threading.Thread(target=worker)
            task.start()
            try:
                self.assertTrue(started.wait(2))
                runner.close()
                task.join(2)
                self.assertFalse(task.is_alive())
            finally:
                task.join(4)
        self.assertIsInstance(errors[0], AliceRunError)
        with self.assertRaisesRegex(AliceError, "已取消"):
            runner.run(["--version"])

    def test_dump_text_honors_output_encoding(self):
        text = self.tools.dump_text(self.ain, "CP936", output_encoding="CP936")
        self.assertIn("消息", text)

    def test_edit_text_writes_back_and_makes_backup(self):
        with patch.object(self.tools, "_verify_edited_text"):
            old, before, after = self.tools.edit_text(
                self.ain, 's[1] = "changed"\n', "CP936", backup=True
            )
        self.assertEqual(old, self.ain.resolve())
        self.assertEqual(before, len(b"ORIGINAL"))
        self.assertNotEqual(after, before)
        self.assertTrue(self.ain.with_name("input.ain.bak").exists())
        args = json.loads(self.log.read_text(encoding="utf-8"))
        self.assertEqual(args[args.index("--output-encoding") + 1], "CP936")
        self.assertEqual(Path(args[-1]).parent.parent, self.ain.parent.resolve())
        self.assertNotEqual(Path(args[-1]), self.ain.resolve())

    def test_save_preserves_existing_sibling_temporary_file(self):
        sibling = self.ain.with_name(self.ain.name + ".ain-new")
        sibling.write_bytes(b"UNRELATED")
        with patch.object(self.tools, "_verify_edited_text"):
            self.tools.edit_text(self.ain, 's[1] = "changed"\n', "CP936")
        self.assertEqual(sibling.read_bytes(), b"UNRELATED")
        args = json.loads(self.log.read_text(encoding="utf-8"))
        output = Path(args[args.index("-o") + 1])
        self.assertEqual(output.parent.parent, self.ain.parent.resolve())
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

    def test_invalid_system4_text_is_rejected_before_running_alice(self):
        for text, encoding in (("hello", "UTF-16LE"), ("a" * 65_536, "UTF-8")):
            with self.subTest(encoding=encoding), patch.object(self.tools, "run") as run:
                with self.assertRaises(AliceError):
                    self.tools.edit_text(self.ain, f'm[0] = "{text}"\n', encoding)
                run.assert_not_called()
                self.assertEqual(self.ain.read_bytes(), b"ORIGINAL")

    def test_nonempty_output_with_unapplied_edit_does_not_replace_source(self):
        data = b"MSG0" + struct.pack("<I", 1) + b"wrong\0"
        with (
            patch.object(self.tools, "map_dump", return_value=f"MSG0: 0 -> {len(data):x}"),
            patch.object(self.tools, "decrypted_bytes", return_value=data),
            self.assertRaisesRegex(AliceError, "与请求的修改不一致"),
        ):
            self.tools.edit_text(self.ain, 'm[0] = "expected"\n', "UTF-8")
        self.assertEqual(self.ain.read_bytes(), b"ORIGINAL")
        self.assertFalse(self.ain.with_suffix(".ain.bak").exists())

    def test_backup_failure_does_not_replace_source(self):
        with patch("aindiff.alice_tools.shutil.copy2", side_effect=OSError("backup failed")):
            with self.assertRaises(OSError):
                self.tools.edit_text(self.ain, 's[1] = "changed"\n', "CP936")
        self.assertEqual(self.ain.read_bytes(), b"ORIGINAL")
        self.assertEqual(list(self.ain.parent.glob(".aindiff-edit-*")), [])


class DownloadTests(unittest.TestCase):
    def test_valid_archive_installs_executable_and_keeps_zip(self):
        from aindiff import ALICE_RELEASE_TAG
        from aindiff.alice_tools import download_alice

        name = "alice.exe" if os.name == "nt" else "alice"

        def download(url, target, **kwargs):
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr(f"release/{name}", b"executable")
                archive.writestr("release/COPYING.txt", b"license")
                archive.writestr("../unexpected.txt", b"ignored")

        with tempfile.TemporaryDirectory() as td, patch(
            "aindiff.alice_tools._download", side_effect=download
        ):
            root = Path(td)
            exe = download_alice(root, expected_sha256=hashlib.sha256(b"executable").hexdigest())
            self.assertEqual(exe.read_bytes(), b"executable")
            self.assertEqual((exe.parent / "COPYING.txt").read_bytes(), b"license")
            self.assertTrue((root / f"alice-tools-{ALICE_RELEASE_TAG}.zip").is_file())
            self.assertEqual(len(list(root.iterdir())), 2)

    def test_invalid_archives_preserve_cached_executable(self):
        from aindiff import ALICE_RELEASE_TAG
        from aindiff.alice_tools import download_alice

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / f"alice-tools-{ALICE_RELEASE_TAG}"
            cache.mkdir()
            exe = cache / ("alice.exe" if os.name == "nt" else "alice")
            exe.write_bytes(b"previous executable")

            def download(url, target, **kwargs):
                if mode == "invalid":
                    target.write_bytes(b"not a zip")
                    return
                with zipfile.ZipFile(target, "w") as archive:
                    if mode == "missing":
                        archive.writestr("COPYING.txt", "license")
                    else:
                        archive.writestr(exe.name, b"" if mode == "empty" else b"new executable")
                if mode == "crc":
                    data = target.read_bytes().replace(b"new executable", b"bad executable")
                    target.write_bytes(data)

            for mode in ("invalid", "missing", "crc", "empty", "wrong_hash"):
                with self.subTest(mode=mode), patch(
                    "aindiff.alice_tools._download", side_effect=download
                ):
                    with self.assertRaises(AliceError):
                        download_alice(root)
                    self.assertEqual(exe.read_bytes(), b"previous executable")
                    self.assertEqual(list(root.iterdir()), [cache])


if __name__ == "__main__":
    unittest.main()
