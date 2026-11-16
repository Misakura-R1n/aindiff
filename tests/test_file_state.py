import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aindiff.file_state import FileRevision, commit_edit, publish_files


class FilePublicationTests(unittest.TestCase):
    def test_late_change_with_restored_mtime_rejects_commit(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "file.ain"
            path.write_bytes(b"old")
            original_stat = path.stat()
            revision = FileRevision.capture(path)
            snapshot = revision.snapshot(Path(td))
            output = Path(td) / "output.ain"
            output.write_bytes(b"new")
            backup = path.with_suffix(".ain.bak")
            backup.write_bytes(b"previous backup")
            path.write_bytes(b"ext")
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            with self.assertRaisesRegex(OSError, "已被替换或修改"):
                commit_edit(revision, snapshot, output, ".bak")
            self.assertEqual(path.read_bytes(), b"ext")
            self.assertEqual(backup.read_bytes(), b"previous backup")

    def test_second_publish_failure_restores_both_previous_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "release.zip"
            digest = root / "release.zip.sha256"
            archive.write_bytes(b"old archive")
            digest.write_bytes(b"old checksum")
            staged_archive = root / "staged.zip"
            staged_digest = root / "staged.zip.sha256"
            staged_archive.write_bytes(b"new archive")
            staged_digest.write_bytes(b"new checksum")
            replace = os.replace

            def fail_on_checksum(source, target):
                if Path(source) == staged_digest:
                    raise OSError("checksum publication failed")
                return replace(source, target)

            with patch("aindiff.file_state.os.replace", side_effect=fail_on_checksum):
                with self.assertRaisesRegex(OSError, "checksum publication failed"):
                    publish_files(root, [(staged_archive, archive), (staged_digest, digest)])
            self.assertEqual(archive.read_bytes(), b"old archive")
            self.assertEqual(digest.read_bytes(), b"old checksum")
            self.assertFalse(list(root.glob(".aindiff-previous-*")))


if __name__ == "__main__":
    unittest.main()
