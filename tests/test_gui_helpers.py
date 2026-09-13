import struct
import sys
import threading
import time
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

    def insert(self, parent, position, **values):
        item = f"item{len(self.items)}"
        self.items[item] = values
        self.attached.append(item)
        return item

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
        self._busy = False
        self._closing = False
        self._filter_key = None
        self._visible_items = set()
        self._item_section = {}
        self._section_visible_counts = {}
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

    def _start_read(self, work, finish, error_title="读取失败"):
        try:
            result = work()
        except Exception as exc:
            from aindiff.gui import messagebox
            messagebox.showerror(error_title, str(exc), parent=self)
            self.status_var.set(error_title)
            return False
        finish(result)
        return True

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
    def large_app(self):
        app = HeadlessApp()
        for number in range(10001):
            app.add_row(f"r{number}", left="match" if number in (0, 10000) else "other")
        callbacks = []
        app.after = lambda delay, callback: callbacks.append(callback)
        app._set_busy = lambda value: setattr(app, "_busy", value)
        return app, callbacks

    def test_large_search_yields_and_preserves_reverse_navigation_and_cache(self):
        app, callbacks = self.large_app()
        app.search_var.set("ＭＡＴＣＨ")
        with patch.object(app, "_row_matches", wraps=app._row_matches) as matches:
            app.find_next(-1)
            self.assertTrue(app._busy)
            self.assertEqual(matches.call_count, 0)
            callbacks.pop(0)()
            self.assertLessEqual(matches.call_count, 5000)
            self.assertEqual(app.tree.focus(), "")
            while callbacks:
                callbacks.pop(0)()
            self.assertFalse(app._busy)
            self.assertEqual(app.tree.focus(), "r10000")
            self.assertEqual(matches.call_count, 10001)
            app.find_next()
            self.assertEqual(app.tree.focus(), "r0")
            self.assertEqual(matches.call_count, 10001)

    def test_large_filter_commits_only_when_complete_and_clear_restores_order(self):
        app, callbacks = self.large_app()
        original = list(app.tree.attached)
        app.search_var.set("match")
        app.apply_filter()
        callbacks.pop(0)()
        self.assertEqual(app.tree.attached, original)
        self.assertFalse(app.save_side("left"))
        while callbacks:
            callbacks.pop(0)()
        self.assertEqual(app.tree.attached, ["r0", "r10000"])
        app.clear_filter()
        while callbacks:
            callbacks.pop(0)()
        self.assertEqual(app.tree.attached, original)

    def test_large_search_after_edit_and_changed_query_has_no_stale_hits(self):
        app, callbacks = self.large_app()
        app.search_var.set("match")
        app.find_next()
        while callbacks:
            callbacks.pop(0)()
        row = app.rows_by_item["r0"]
        row.left.text = "new phrase"
        app.on_cell_edited("r0", "left", row.left)
        app.find_next()
        while callbacks:
            callbacks.pop(0)()
        self.assertEqual(app.search_hits, ["r10000"])
        app.search_var.set("new phrase")
        app.find_next()
        while callbacks:
            callbacks.pop(0)()
        self.assertEqual(app.search_hits, ["r0"])
        self.assertEqual(app.tree.focus(), "r0")

    def test_closed_scan_does_not_commit_partial_filter(self):
        app, callbacks = self.large_app()
        original = list(app.tree.attached)
        app.search_var.set("match")
        app.apply_filter()
        callbacks.pop(0)()
        app._closing = True
        callbacks.pop(0)()
        self.assertEqual(app.tree.attached, original)
        self.assertEqual(callbacks, [])

    def test_filter_reuses_complete_search_results_until_edit(self):
        app, callbacks = self.large_app()
        app.search_var.set("match")
        app.find_next()
        while callbacks:
            callbacks.pop(0)()
        with patch.object(app, "_row_matches", wraps=app._row_matches) as matches:
            app.apply_filter()
            while callbacks:
                callbacks.pop(0)()
            matches.assert_not_called()
        self.assertEqual(app.tree.attached, ["r0", "r10000"])
        row = app.rows_by_item["r0"]
        row.left.text = "other"
        app.on_cell_edited("r0", "left", row.left)
        app.apply_filter()
        while callbacks:
            callbacks.pop(0)()
        self.assertEqual(app.tree.attached, ["r10000"])

    def test_large_save_all_still_saves_both_sides(self):
        app, callbacks = self.large_app()
        row = app.rows_by_item["r0"]
        row.left.text = "left saved"
        row.right.text = "right saved"
        with patch("aindiff.gui.messagebox.askyesno", return_value=True):
            app.save_all()
        self.assertEqual(app.tools.edit_text.call_count, 2)
        self.assertFalse(app.left_dump.dirty_count)
        self.assertFalse(app.right_dump.dirty_count)
        self.assertEqual(callbacks, [])

    def test_filtered_edit_only_checks_the_changed_row(self):
        app = HeadlessApp()
        for number in range(1000):
            app.add_row(f"r{number}", left="match")
        app.search_var.set("ＭＡＴＣＨ")
        app.apply_filter()
        row = app.rows_by_item["r0"]
        row.left.text = "removed"
        with patch.object(app, "_row_matches", wraps=app._row_matches) as matches:
            app.on_cell_edited("r0", "left", row.left)
        self.assertEqual(matches.call_count, 1)
        self.assertEqual(app.tree.set_children_calls, 0)  # all matched before the edit
        self.assertNotIn("r0", app.tree.attached)
        self.assertEqual(len(app.tree.attached), 999)

    def test_revealed_section_updates_incremental_visibility_counts(self):
        app = HeadlessApp()
        app.rows_by_item["section"] = AlignedRow(is_section=True)
        app.tree.items["section"] = {}
        app.tree.attached.append("section")
        app.item_order.append("section")
        row = app.add_row("a", left="other")
        app.search_var.set("match")
        app.apply_filter()
        app._reveal_main_item("a")
        self.assertEqual(app.tree.attached, ["section", "a"])
        row.left.text = "still other"
        app.on_cell_edited("a", "left", row.left)
        self.assertEqual(app.tree.attached, [])

    def test_render_yields_between_batches_and_blocks_writes(self):
        app = HeadlessApp()
        app.rows = [AlignedRow(left=TextEntry("s", n, 1, "text")) for n in range(601)]
        callbacks = []
        app.after = lambda delay, callback: callbacks.append(callback)
        app._set_busy = lambda value: setattr(app, "_busy", value)
        app._populate_structure_tree = Mock()
        app._populate_tree()
        self.assertTrue(app._busy)
        self.assertEqual(len(app.tree.items), 0)
        callbacks.pop(0)()
        self.assertEqual(len(app.tree.items), 300)
        self.assertFalse(app.load_pair())
        self.assertFalse(app.save_side("left"))
        self.assertFalse(app._begin_edit("item0", "left"))
        app.tools.edit_text.assert_not_called()
        while callbacks:
            callbacks.pop(0)()
        self.assertFalse(app._busy)
        self.assertEqual(len(app.tree.items), 601)
        self.assertEqual(app.item_order, list(app.tree.items))
        app._populate_structure_tree.assert_called_once()

    def test_background_read_never_delivers_from_worker_thread(self):
        app = HeadlessApp()
        callbacks = []
        app.after = lambda delay, callback: callbacks.append(callback)
        app._set_busy = lambda value: setattr(app, "_busy", value)
        release = threading.Event()
        started = threading.Event()
        worker_ids = []
        delivered = []

        def work():
            worker_ids.append(threading.get_ident())
            started.set()
            release.wait(2)
            return "loaded"

        AinDiffApp._start_read(app, work, lambda value: delivered.append((value, threading.get_ident())))
        try:
            self.assertTrue(started.wait(1))
            self.assertTrue(app._busy)
            callbacks.pop(0)()
            self.assertEqual(delivered, [])
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while not delivered and time.monotonic() < deadline:
            callbacks.pop(0)()
            time.sleep(0.001)
        self.assertEqual(delivered, [("loaded", threading.get_ident())])
        self.assertNotEqual(worker_ids[0], threading.get_ident())
        self.assertFalse(app._busy)

    def test_close_ignores_pending_background_result(self):
        app = HeadlessApp()
        callbacks = []
        app.after = lambda delay, callback: callbacks.append(callback)
        app._set_busy = lambda value: setattr(app, "_busy", value)
        finish = Mock()
        AinDiffApp._start_read(app, lambda: "result", finish)
        app._closing = True
        callbacks.pop(0)()
        finish.assert_not_called()

    def test_edit_removes_only_empty_filtered_section_and_clear_restores_order(self):
        for query, diff_only in (("match", False), ("", True), ("match", True)):
            with self.subTest(query=query, diff_only=diff_only):
                app = HeadlessApp()
                for number in range(2):
                    item = f"section{number}"
                    app.rows_by_item[item] = AlignedRow(is_section=True)
                    app.tree.items[item] = {}
                    app.tree.attached.append(item)
                    app.add_row(f"row{number}", left="match", right="other")
                original = list(app.tree.attached)
                app.search_var.set(query)
                app.diff_only_var.set(diff_only)
                app.apply_filter()
                row = app.rows_by_item["row0"]
                row.left.text = "other"
                app.on_cell_edited("row0", "left", row.left)
                self.assertEqual(app.tree.attached, ["section1", "row1"])
                self.assertTrue(row.left.dirty)
                app.clear_filter()
                self.assertEqual(app.tree.attached, original)

    def test_filter_hides_empty_sections_and_restores_them(self):
        app = HeadlessApp()
        for number in range(3):
            item = f"section{number}"
            app.rows_by_item[item] = AlignedRow(is_section=True)
            app.tree.items[item] = {}
            app.tree.attached.append(item)
            app.add_row(f"row{number}", left="match" if number == 1 else "same")
        original = list(app.tree.attached)
        app.search_var.set("match")
        app.apply_filter()
        self.assertEqual(app.tree.attached, ["section1", "row1"])
        app.search_var.set("absent")
        app.apply_filter()
        self.assertEqual(app.tree.attached, [])
        app.clear_filter()
        self.assertEqual(app.tree.attached, original)
        app.diff_only_var.set(True)
        app.apply_filter()
        self.assertEqual(app.tree.attached, ["section1", "row1"])

    def test_structure_toggle_restores_sidebar_on_left(self):
        app = HeadlessApp()
        app.sidebar_frame = Mock()
        app.main_pane = Mock()
        app.main_pane.panes.return_value = (str(app.sidebar_frame), "tree")
        app.toggle_structure()
        app.main_pane.forget.assert_called_once_with(app.sidebar_frame)
        app.main_pane.panes.return_value = ("tree",)
        app.toggle_structure()
        app.main_pane.insert.assert_called_once_with(0, app.sidebar_frame, weight=0)

    def test_search_shortcuts_navigate_once_and_refresh_is_bound(self):
        app = HeadlessApp()
        app.bind = Mock()
        app.find_next = Mock()
        app.load_pair = Mock()
        app._bind_shortcuts()
        bindings = dict(call.args for call in app.bind.call_args_list)
        bindings["<F3>"](None)
        app.find_next.assert_called_once_with(1)
        app.find_next.reset_mock()
        bindings["<Shift-F3>"](None)
        app.find_next.assert_called_once_with(-1)
        bindings["<F5>"](None)
        app.load_pair.assert_called_once_with()

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
        with patch("aindiff.gui.Path.is_file", return_value=True), patch(
            "aindiff.loading.open_dump", side_effect=OSError("failed")
        ), patch("aindiff.gui.messagebox.showerror"):
            self.assertFalse(app.load_pair())
        self.assertFalse(app.single_mode)
        self.assertIs(app.left_dump, previous_dump)
        self.assertIs(app.rows_by_item["old"], row)

    def test_single_fallback_error_is_reported_once(self):
        app = HeadlessApp()
        app.right_var.set("")
        with patch("aindiff.gui.Path.is_file", return_value=True), patch(
            "aindiff.loading.open_dump", side_effect=AliceError("dump failed")
        ), patch(
            "aindiff.loading.load_text_section_with_fallback", side_effect=AliceError("fallback failed")
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
        with patch("aindiff.gui.Path.is_file", return_value=True), patch(
            "aindiff.loading.open_dump", side_effect=AliceError("dump failed")
        ), patch("aindiff.gui.load_section_map", return_value=[]), patch.object(app, "_set_active_view"):
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
            from aindiff.ain_sections import load_text_section_with_fallback
            load_text_section_with_fallback(app.tools, "missing.ain", text_encodings=("UTF-8",))

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
        from aindiff.loading import load_pair
        with patch("aindiff.loading.Path.is_file", return_value=True), patch(
            "aindiff.loading.open_dump", side_effect=AliceError("mixed")
        ):
            left, right, _, _ = load_pair(app.tools, "left.ain", "right.ain", "CP932", "CP936", allow_explicit_fallback=True)
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
        with patch("aindiff.gui.Path.is_file", return_value=True), patch(
            "aindiff.loading.open_dump", return_value=loaded
        ) as open_dump, patch("aindiff.gui.load_section_map", return_value=[]), patch.object(
            app, "_set_active_view"
        ):
            self.assertTrue(app.load_pair())
        open_dump.assert_called_once_with(app.tools, "right.ain", "CP936")
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

        def activate(left, right, view_name, **kwargs):
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

    def test_invalid_raw_encoding_keeps_view_and_pending_edits(self):
        app = HeadlessApp()
        row = app.add_row("a")
        app.pending_edit("a", "keep this")
        previous_dump = app.left_dump
        previous_view = app.current_view
        app.text_left_dump.encoding = "CP936"
        payload = b"STR0" + struct.pack("<i", 1) + "ｱ".encode("CP932") + b"\0"
        app.tools.map_dump.return_value = f"STR0: 00000000 -> {len(payload):08x}"
        app.tools.decrypted_bytes.return_value = payload
        with patch("aindiff.gui.messagebox.askyesno", return_value=True), patch(
            "aindiff.gui.messagebox.showerror"
        ) as error, patch.object(app, "_set_active_view") as activate:
            app.switch_view("STR0")
        error.assert_called_once()
        activate.assert_not_called()
        self.assertIs(app.left_dump, previous_dump)
        self.assertEqual(app.current_view, previous_view)
        self.assertEqual(row.left.text, "keep this")
        self.assertTrue(row.left.dirty)


if __name__ == "__main__":
    unittest.main()
