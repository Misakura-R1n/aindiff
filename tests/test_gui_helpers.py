import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aindiff.alice_tools import AliceError  # noqa: E402
from aindiff.gui import AinDiffApp, _reattach_target  # noqa: E402
from aindiff.model import AlignedRow, TextDump, TextEntry  # noqa: E402


class FakeVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeTree:
    """Small root-only Treeview fake, including detached-item semantics."""

    def __init__(self):
        self.items = {}
        self.attached = []
        self.focused = ""
        self.set_children_calls = 0

    def get_children(self, parent=""):
        return tuple(self.attached)

    def delete(self, *items):
        for item in items:
            del self.items[item]
            if item in self.attached:
                self.attached.remove(item)

    def detach(self, item):
        if item in self.attached:
            self.attached.remove(item)

    def reattach(self, item, parent, position):
        if position != "end" and not isinstance(position, int):
            raise ValueError("Treeview requires an integer position or 'end'")
        self.detach(item)
        self.attached.insert(len(self.attached) if position == "end" else position, item)

    def set_children(self, parent, *items):
        self.set_children_calls += 1
        self.attached = list(items)

    def index(self, item):
        return self.attached.index(item)

    def item(self, item, option=None, **values):
        self.items[item].update(values)
        return self.items[item].get(option) if option else self.items[item]

    def focus(self, item=None):
        if item is not None:
            self.focused = item
        return self.focused

    def see(self, item):
        pass

    def selection_set(self, item):
        pass


class HeadlessApp(AinDiffApp):
    """Exercise application logic without initializing Tk or opening windows."""

    def __init__(self):
        self.tree = FakeTree()
        self.structure_tree = FakeTree()
        self.rows = []
        self.rows_by_item = {}
        self.item_order = []
        self.row_main_item = {}
        self.section_rows = {}
        self.structure_item_to_row = {}
        self.main_to_structure_item = {}
        self.structure_children_loaded = set()
        self.section_maps = {"left": [], "right": []}
        self.search_hits = []
        self.search_index = -1
        self._search_query = None
        self._search_positions = {}
        self._editor = None
        self._last_changed_count = 0
        self.single_mode = False
        self.single_file_side = None
        self.current_view = "文本节（按函数）"
        self.left_var = FakeVariable("left.ain")
        self.right_var = FakeVariable("right.ain")
        self.left_enc_var = FakeVariable("CP932")
        self.right_enc_var = FakeVariable("CP936")
        self.search_var = FakeVariable()
        self.search_status_var = FakeVariable()
        self.status_var = FakeVariable()
        self.diff_only_var = FakeVariable(False)
        self.backup_var = FakeVariable(True)
        self.left_dump = TextDump("left.ain", "CP932")
        self.right_dump = TextDump("right.ain", "CP936")
        self.text_left_dump = self.left_dump
        self.text_right_dump = self.right_dump
        self.tools = Mock()
        self.tools.edit_text.return_value = ("left.ain", 100, 110)
        self.update_idletasks = Mock()
        self._update_status = Mock()

    def add_row(self, item, left="same", right="same", index=1):
        row = AlignedRow(
            left=TextEntry("s", index, 1, left),
            right=TextEntry("s", index, 1, right),
        )
        self.rows.append(row)
        self.rows_by_item[item] = row
        self.item_order.append(item)
        self.left_dump.entries.append(row.left)
        self.right_dump.entries.append(row.right)
        self.tree.items[item] = {
            "values": (1, "S", index, left, right),
            "tags": ("changed",) if row.changed else (),
        }
        self._last_changed_count += int(row.changed)
        self.tree.attached.append(item)
        return row

    def pending_edit(self, item, text):
        editor = Mock()

        def commit():
            self._editor = None
            entry = self.rows_by_item[item].left
            entry.text = text
            self.on_cell_edited(item, "left", entry)

        editor._commit.side_effect = commit
        self._editor = editor
        return editor


class ReattachTargetTests(unittest.TestCase):
    def test_item_inserts_before_next_visible_item(self):
        order = ["a", "b", "c", "d"]
        attached = {"a", "c", "d"}
        self.assertEqual(_reattach_target(order, attached, "b"), "c")

    def test_item_inserts_at_end_when_no_later_item_is_visible(self):
        order = ["a", "b", "c"]
        attached = {"a", "b"}
        self.assertEqual(_reattach_target(order, attached, "c"), "")

    def test_unknown_item_goes_to_end(self):
        self.assertEqual(_reattach_target(["a"], set(), "x"), "")

    def test_already_attached_item_returns_next_but_caller_skips_it(self):
        # Caller only invokes this when main_item is detached; behavior here
        # is still deterministic if the helper is reused elsewhere.
        self.assertEqual(_reattach_target(["a", "b"], {"a", "b"}, "a"), "b")


class GuiStateTests(unittest.TestCase):
    def test_reveal_filtered_row_passes_integer_position(self):
        app = HeadlessApp()
        for item in ("a", "b", "c"):
            app.add_row(item)
        app.tree.detach("b")
        app._reveal_main_item("b")
        self.assertEqual(app.tree.attached, ["a", "b", "c"])
        self.assertEqual(app.tree.focus(), "b")

    def test_clear_rows_also_deletes_detached_items(self):
        app = HeadlessApp()
        app.add_row("a")
        app.add_row("b")
        app.tree.detach("b")
        app._clear_rows()
        self.assertEqual(app.tree.items, {})
        self.assertEqual(app.rows_by_item, {})

    def test_filter_restores_order_in_one_tree_call(self):
        app = HeadlessApp()
        app.add_row("a", left="match")
        app.add_row("b")
        app.add_row("c", right="match")
        app.search_var.set("match")
        app.apply_filter()
        self.assertEqual(app.tree.attached, ["a", "c"])
        self.assertEqual(app.tree.set_children_calls, 1)
        app.clear_filter()
        self.assertEqual(app.tree.attached, ["a", "b", "c"])
        self.assertEqual(app.tree.set_children_calls, 2)

    def test_find_next_reuses_hits_and_invalidates_after_edit(self):
        app = HeadlessApp()
        app.add_row("a", left="match")
        row = app.add_row("b", left="match")
        app.search_var.set("match")
        with patch.object(app, "_collect_search_hits", wraps=app._collect_search_hits) as collect:
            app.find_next()
            app.find_next()
            self.assertEqual(app.tree.focus(), "b")
            self.assertEqual(collect.call_count, 1)
            row.left.text = "removed"
            app.on_cell_edited("b", "left", row.left)
            app.find_next()
            self.assertEqual(app.tree.focus(), "a")
            self.assertEqual(collect.call_count, 2)

    def test_save_all_commits_active_editor_before_checking_dirty(self):
        app = HeadlessApp()
        row = app.add_row("a")
        editor = app.pending_edit("a", "edited")
        with patch("aindiff.gui.messagebox.askyesno", return_value=True):
            app.save_all()
        editor._commit.assert_called_once()
        self.assertIn('s[1] = "edited"', app.tools.edit_text.call_args.args[1])
        self.assertFalse(row.left.dirty)

    def test_discard_checks_active_editor(self):
        app = HeadlessApp()
        app.add_row("a")
        app.pending_edit("a", "edited")
        with patch("aindiff.gui.messagebox.askyesno", return_value=False) as confirm:
            self.assertFalse(app._confirm_discard_changes())
        confirm.assert_called_once()

    def test_reverting_one_side_keeps_other_sides_dirty_tag(self):
        app = HeadlessApp()
        row = app.add_row("a")
        row.right.text = "right edited"
        app.on_cell_edited("a", "right", row.right)
        row.left.text = "left edited"
        app.on_cell_edited("a", "left", row.left)
        row.left.text = row.left.original_text
        app.on_cell_edited("a", "left", row.left)
        self.assertIn("dirty", app.tree.items["a"]["tags"])

    def test_save_synchronizes_duplicate_ids_and_cached_view(self):
        app = HeadlessApp()
        first = app.add_row("a")
        second = app.add_row("b")
        cached = TextEntry("s", 1, 1, "same")
        app.text_left_dump = TextDump("left.ain", "CP932", entries=[cached])
        first.left.text = "saved"
        app.on_cell_edited("a", "left", first.left)
        app._search_query = "stale search"
        with patch("aindiff.gui.messagebox.askyesno", return_value=True):
            self.assertTrue(app.save_side("left"))
        self.assertEqual(second.left.text, "saved")
        self.assertFalse(second.left.dirty)
        self.assertEqual(cached.text, "saved")
        self.assertFalse(cached.dirty)
        self.assertEqual(app.tree.items["b"]["values"][3], "saved")
        self.assertIsNone(app._search_query)
        self.assertEqual(app._last_changed_count, 2)

    def test_failed_save_retains_dirty_model(self):
        app = HeadlessApp()
        row = app.add_row("a")
        row.left.text = "edited"
        app.tools.edit_text.side_effect = AliceError("failed")
        with patch("aindiff.gui.messagebox.askyesno", return_value=True), patch(
            "aindiff.gui.messagebox.showerror"
        ):
            self.assertFalse(app.save_side("left"))
        self.assertTrue(row.left.dirty)

    def test_failed_load_keeps_previous_rows_and_mode(self):
        app = HeadlessApp()
        row = app.add_row("old")
        previous_dump = app.left_dump
        app.right_var.set("")
        with patch("aindiff.gui.Path.is_file", return_value=True), patch.object(
            app, "_open_dump", side_effect=OSError("failed")
        ), patch("aindiff.gui.messagebox.showerror"):
            self.assertFalse(app.load_pair())
        self.assertFalse(app.single_mode)
        self.assertIs(app.left_dump, previous_dump)
        self.assertIs(app.rows_by_item["old"], row)

    def test_single_fallback_error_is_reported_once(self):
        app = HeadlessApp()
        app.right_var.set("")
        with patch("aindiff.gui.Path.is_file", return_value=True), patch.object(
            app, "_open_dump", side_effect=AliceError("dump failed")
        ), patch.object(
            app, "_open_dump_fallback", side_effect=AliceError("fallback failed")
        ) as fallback, patch("aindiff.gui.messagebox.showerror") as error:
            self.assertFalse(app.load_pair())
        fallback.assert_called_once()
        error.assert_called_once()

    def test_single_fallback_preserves_explicit_encoding(self):
        app = HeadlessApp()
        app.right_var.set("")
        payload = b"MSG0" + struct.pack("<i", 1) + "あ".encode("CP932") + b"\0"
        app.tools.map_dump.return_value = f"MSG0: 00000000 -> {len(payload):08x}"
        app.tools.decrypted_bytes.return_value = payload
        with patch("aindiff.gui.Path.is_file", return_value=True), patch.object(
            app, "_open_dump", side_effect=AliceError("dump failed")
        ), patch.object(app, "_load_section_maps"), patch.object(app, "_set_active_view"):
            self.assertTrue(app.load_pair())
        dump = app.text_left_dump
        self.assertEqual(dump.encoding, "CP932")
        self.assertEqual(dump.entries[0].text, "あ")

    def test_single_fallback_rejects_bytes_invalid_in_explicit_encoding(self):
        app = HeadlessApp()
        payload = b"MSG0" + struct.pack("<i", 1) + b"\x82\xa0\0"
        app.tools.map_dump.return_value = f"MSG0: 00000000 -> {len(payload):08x}"
        app.tools.decrypted_bytes.return_value = payload
        with self.assertRaises(ValueError):
            app._open_dump_fallback("missing.ain", encoding="UTF-8")

    def test_pair_fallback_preserves_each_selected_encoding(self):
        app = HeadlessApp()
        payloads = {
            "left.ain": b"MSG0" + struct.pack("<i", 1) + "あ".encode("CP932") + b"\0",
            "right.ain": b"MSG0" + struct.pack("<i", 1) + "你好".encode("CP936") + b"\0",
        }
        app.tools.map_dump.side_effect = lambda path, encoding: (
            f"MSG0: 00000000 -> {len(payloads[path]):08x}"
        )
        app.tools.decrypted_bytes.side_effect = payloads.__getitem__
        left, right, _ = app._open_pair_fallback("left.ain", "right.ain")
        self.assertEqual((left.encoding, right.encoding), ("CP932", "CP936"))
        self.assertEqual((left.entries[0].text, right.entries[0].text), ("あ", "你好"))

    def test_edit_hides_row_that_no_longer_matches_search(self):
        app = HeadlessApp()
        row = app.add_row("a", left="match", right="other")
        app.search_var.set("match")
        app.apply_filter()
        row.left.text = "removed"
        app.on_cell_edited("a", "left", row.left)
        self.assertEqual(app.tree.attached, [])

    def test_edit_hides_equal_row_with_both_filters_active(self):
        app = HeadlessApp()
        row = app.add_row("a", left="match", right="different")
        app.search_var.set("match")
        app.diff_only_var.set(True)
        app.apply_filter()
        row.right.text = "match"
        app.on_cell_edited("a", "right", row.right)
        self.assertEqual(app.tree.attached, [])

    def test_single_right_file_uses_its_selected_encoding(self):
        app = HeadlessApp()
        app.left_var.set("")
        loaded = TextDump("right.ain", "CP936")
        with patch("aindiff.gui.Path.is_file", return_value=True), patch.object(
            app, "_open_dump", return_value=loaded
        ) as open_dump, patch.object(app, "_load_section_maps"), patch.object(
            app, "_set_active_view"
        ):
            self.assertTrue(app.load_pair())
        open_dump.assert_called_once_with("right.ain", "CP936")
        self.assertEqual(app.left_enc_var.get(), "CP936")
        self.assertEqual(app.left_var.get(), "right.ain")
        self.assertEqual(app.right_var.get(), "")

    def test_switching_back_does_not_restore_discarded_cached_edits(self):
        app = HeadlessApp()
        row = app.add_row("a")
        row.left.text = "discard this"
        original_view = app.current_view
        raw_left = TextDump("left.ain", "CP932")
        raw_right = TextDump("right.ain", "CP936")

        def activate(left, right, view_name):
            app.left_dump, app.right_dump = left, right
            app.current_view = view_name

        with patch("aindiff.gui.messagebox.askyesno", return_value=True), patch(
            "aindiff.gui.load_section_dump", side_effect=[raw_left, raw_right]
        ), patch.object(app, "_set_active_view", side_effect=activate):
            app.switch_view("STR0")
            app.switch_view(original_view)
        self.assertEqual(app.left_dump.entries[0].text, "same")
        self.assertFalse(app.left_dump.entries[0].dirty)

    def test_failed_view_read_preserves_unsaved_edits(self):
        app = HeadlessApp()
        row = app.add_row("a")
        row.left.text = "keep this"
        previous_dump = app.left_dump
        with patch("aindiff.gui.messagebox.askyesno", return_value=True), patch(
            "aindiff.gui.load_section_dump", side_effect=AliceError("read failed")
        ), patch("aindiff.gui.messagebox.showerror"), patch.object(
            app, "_set_active_view"
        ) as activate:
            app.switch_view("STR0")
        activate.assert_not_called()
        self.assertIs(app.left_dump, previous_dump)
        self.assertEqual(row.left.text, "keep this")
        self.assertTrue(row.left.dirty)


if __name__ == "__main__":
    unittest.main()
