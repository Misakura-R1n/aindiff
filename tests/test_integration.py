"""Small integration fixtures for native Tk, bundled alice, and the portable ZIP."""
import hashlib
import io
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aindiff import cli, xsys35
from aindiff.alice_tools import AliceTools
from aindiff.gui import AinDiffApp

VENDOR = ROOT / "vendor" / "alice" / "alice.exe"
PACKAGE = os.environ.get("AINDIFF_TEST_PACKAGE")


def _aini(strings: bytes, message: bytes) -> bytes:
    payload = b"VARI" + struct.pack("<II", 0, 1) + strings + b"\0"
    payload += b"MSGI" + struct.pack("<II", 0, 1) + message + b"\0"
    return b"AINI" + struct.pack("<I", 1) + xsys35._encrypt_payload(payload)


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "Tk display unavailable")
class TkIntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "fixture.ain"
        self.app = AinDiffApp()
        self.app.withdraw()
        for name, result in (("askyesno", True), ("showerror", None), ("showinfo", None)):
            mock = patch(f"aindiff.gui.messagebox.{name}", return_value=result)
            self.addCleanup(mock.stop)
            setattr(self, name, mock.start())
        lookup = patch("aindiff.alice_tools.find_alice", side_effect=AssertionError("legacy called alice"))
        self.addCleanup(lookup.stop)
        lookup.start()
        self.addCleanup(self.app.destroy)

    def wait_for_view(self):
        deadline = time.monotonic() + 5
        while self.app._busy and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.005)
        self.app.update()
        self.assertFalse(self.app._busy)
        self.showerror.assert_not_called()

    def test_default_tree_and_async_write_refresh(self):
        app = self.app
        self.path.write_bytes(_aini(b"old", b"message"))
        app.expand_all_structure()
        app.left_var.set(str(self.path))
        app.diff_only_var.set(True)
        self.assertTrue(app.load_pair())
        self.wait_for_view()
        self.assertTrue(app.tree.get_children())
        self.assertFalse(app.diff_only_var.get())
        app.show_text_sections_var.set(True)
        app.on_text_sections_toggle()
        tree = app.structure_tree
        section = tree.get_children("root:text")[0]
        tree.focus(section)
        tree.tk.call("ttk::treeview::Toggle", tree._w, section)
        self.assertTrue(app.structure_children_loaded)
        offset_before = tree.item("ainsec:MSGI", "text")
        app.left_dump.entries[0].text = "a longer replacement"
        real_write = app.tools.write_text
        timer_ran = threading.Event()

        def delayed_write(*args, **kwargs):
            if not timer_ran.wait(3):
                raise RuntimeError("Tk timer stalled while writing")
            return real_write(*args, **kwargs)

        with patch.object(app.tools, "write_text", side_effect=delayed_write):
            app.after(1, timer_ran.set)
            self.assertTrue(app.save_side("left"))
            self.assertTrue(app._writing)
            self.wait_for_view()
        self.assertTrue(timer_ran.is_set())
        self.assertEqual(app.left_dump.dirty_count, 0)
        self.assertNotEqual(tree.item("ainsec:MSGI", "text"), offset_before)
        self.assertEqual(xsys35.section_dump(str(self.path), "VARI", "UTF-8").entries[0].text,
                         "a longer replacement")
        self.assertEqual(self.path.with_suffix(".ain.bak").read_bytes(), _aini(b"old", b"message"))

    def test_auto_mixed_aini_can_switch_to_other_encoding(self):
        self.path.write_bytes(_aini("你".encode("CP936"), "ｦ".encode("CP932")))
        self.app.left_var.set(str(self.path))
        self.assertTrue(self.app.load_pair())
        self.wait_for_view()
        self.assertEqual(self.app.current_view, "VARI")
        self.app.switch_view("MSGI")
        self.wait_for_view()
        self.assertEqual(self.app.left_dump.encoding, "CP932")
        self.assertEqual(self.app.left_dump.entries[0].text, "ｦ")


@unittest.skipUnless(os.name == "nt" and VENDOR.is_file(), "bundled Windows alice unavailable")
class System4IntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.left = self.root / "left.ain"
        self.right = self.root / "right.ain"
        tools = AliceTools(VENDOR)
        tools.run(["ain", "edit", "--ain-version", "4", "-o", self.left], timeout=10)
        self.right.write_bytes(self.left.read_bytes())
        tools.edit_text(self.left, 'm[0] = "left"\n', "CP932")
        tools.edit_text(self.right, 'm[0] = "right"\n', "CP932")

    def test_check_compares_unreferenced_messages(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["check", "--alice", str(VENDOR), str(self.left), str(self.right)]), 0)
        self.assertIn("差异：1", output.getvalue())

    @unittest.skipUnless(PACKAGE, "portable ZIP not supplied")
    def test_portable_zip_compares_unreferenced_messages(self):
        package = Path(PACKAGE)
        checksum = package.with_suffix(".zip.sha256").read_text(encoding="ascii").split()[0]
        self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), checksum)
        with zipfile.ZipFile(package) as archive:
            self.assertIsNone(archive.testzip())
            archive.extractall(self.root)
        exe = self.root / package.stem / "aindiff.exe"
        result = subprocess.run(
            [str(exe), "check", str(self.left), str(self.right)],
            cwd=self.root, capture_output=True, check=True, timeout=20,
            env=dict(os.environ, AINDIF_ALICE=""),
        )
        self.assertIn("差异：1", result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
