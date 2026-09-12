"""Tkinter GUI: dual-pane comparison and editing for two .ain files."""

from __future__ import annotations

import ctypes
import os
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from . import ALICE_TOOLS_URL, APP_NAME, __version__
from .ain_sections import (
    SectionInfo,
    TEXT_SECTION_KINDS,
    load_section_dump,
    load_text_section_with_fallback,
    parse_section_map,
    text_section_names,
)
from .alice_tools import AliceError, AliceNotFound, AliceTools
from .model import (
    AlignedRow,
    TextDump,
    TextEntry,
    align_dumps,
    build_edit_text,
    display_text,
    normalize_search_text,
    parse_dump,
)

__all__ = ["run_gui"]

_ENCODINGS = ("自动识别", "CP932", "CP936", "GBK", "UTF-8", "BIG5", "EUC-JP")
_TEXT_VIEW = "文本节（按函数）"
_TEXT_ROOT = "root:text"
_AIN_ROOT = "root:ain"

_ROW_COLUMNS = ("no", "kind", "id", "left", "right")

_WINDOWS_DPI_AWARE = False


def _enable_windows_dpi_awareness() -> None:
    """Opt into Windows system DPI awareness before Tk creates a window.

    Without this, Windows renders the Tk window at 96 DPI and then scales
    the bitmap up on high-DPI/4K displays, which looks blurry.
    """
    global _WINDOWS_DPI_AWARE
    if _WINDOWS_DPI_AWARE or os.name != "nt":
        return
    _WINDOWS_DPI_AWARE = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _reattach_target(item_order: List[str], attached: set[str], main_item: str) -> str:
    """Return the iid to reattach *main_item* before ("" means end).

    Pure helper so the position-restoring logic can be unit tested without
    creating a Tk window.
    """
    try:
        target_index = item_order.index(main_item)
    except ValueError:
        return ""
    for later_item in item_order[target_index + 1 :]:
        if later_item in attached:
            return later_item
    return ""


class _CellEditor:
    """Transient Text widget used for in-place Treeview editing."""

    def __init__(self, app: AinDiffApp, item: str, column: str) -> None:
        self.app = app
        self.item = item
        self.column = column
        row = app.rows_by_item.get(item)
        if row is None or row.is_section:
            self.cancelled = True
            return
        entry = row.left if column == "left" else row.right
        if entry is None:
            self.cancelled = True
            return
        self.entry: TextEntry = entry
        self.cancelled = False
        self._closed = False
        self.original_value = entry.text

        tree = app.tree
        bbox = tree.bbox(item, column)
        if not bbox:
            self.cancelled = True
            return
        x, y, width, height = bbox

        lines = self.original_value.count("\n") + 1
        lines = min(max(lines, 1), 8)
        self.widget = tk.Text(
            tree,
            width=max(20, width // 8),
            height=lines,
            wrap="none",
            undo=True,
            font=("TkDefaultFont", 9),
            relief="solid",
            borderwidth=1,
        )
        self.widget.insert("1.0", self.original_value)
        self.widget.place(x=x, y=y, width=width, height=max(height, lines * 18))
        self.widget.focus_set()
        self.widget.bind("<Escape>", self._cancel)
        self.widget.bind("<Control-Return>", self._commit)
        self.widget.bind("<FocusOut>", self._commit)
        self.widget.bind("<Tab>", self._tab)
        self.app._editor = self

    def _cancel(self, _event: object = None) -> str:
        self.cancelled = True
        self._close()
        return "break"

    def _commit(self, _event: object = None) -> str:
        if self._closed:
            return "break"
        if self.widget.winfo_exists():
            self.entry.text = self.widget.get("1.0", "end-1c")
        self._close()
        return "break"

    def _tab(self, _event: object = None) -> str:
        # Treat Tab as normal text input; leaving is done by click/focus.
        self.widget.insert(tk.INSERT, "\t")
        return "break"

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.widget.winfo_exists():
            self.widget.destroy()
        if getattr(self.app, "_editor", None) is self:
            self.app._editor = None
        if not self.cancelled:
            self.app.on_cell_edited(self.item, self.column, self.entry)


class AinDiffApp(tk.Tk):
    """Main application window."""

    def __init__(
        self,
        left: Optional[str] = None,
        right: Optional[str] = None,
        left_encoding: Optional[str] = None,
        right_encoding: Optional[str] = None,
        alice: Optional[str] = None,
    ) -> None:
        _enable_windows_dpi_awareness()
        super().__init__()
        self.title(f"{APP_NAME}  v{__version__}")
        self.geometry("1280x800")
        self.minsize(900, 520)

        self.alice_path = alice
        self.tools: Optional[AliceTools] = None
        self.left_dump: Optional[TextDump] = None
        self.right_dump: Optional[TextDump] = None
        self.text_left_dump: Optional[TextDump] = None
        self.text_right_dump: Optional[TextDump] = None
        self.current_view = _TEXT_VIEW
        self.single_mode = False
        self.single_file_side: Optional[str] = None
        self.section_maps: Dict[str, List[SectionInfo]] = {"left": [], "right": []}
        self.rows: List[AlignedRow] = []
        self.rows_by_item: Dict[str, AlignedRow] = {}
        self.item_order: List[str] = []
        self.section_rows: Dict[int, List[tuple[str, AlignedRow]]] = {}
        self.row_main_item: Dict[int, str] = {}
        self.structure_item_to_row: Dict[str, tuple[str, AlignedRow]] = {}
        self.main_to_structure_item: Dict[str, str] = {}
        self.structure_children_loaded: set[str] = set()
        self.search_hits: List[str] = []
        self.search_index = -1
        self._search_query: Optional[str] = None
        self._search_positions: Dict[str, int] = {}
        self._editor: Optional[_CellEditor] = None

        self.left_var = tk.StringVar(value=left or "")
        self.right_var = tk.StringVar(value=right or "")
        self.left_enc_var = tk.StringVar(value=left_encoding or _ENCODINGS[0])
        self.right_enc_var = tk.StringVar(value=right_encoding or _ENCODINGS[0])
        self.backup_var = tk.BooleanVar(value=True)
        self.show_text_sections_var = tk.BooleanVar(value=False)
        self.diff_only_var = tk.BooleanVar(value=False)
        self.search_var = tk.StringVar()
        self.search_status_var = tk.StringVar()
        self.status_var = tk.StringVar(value="正在初始化 alice-tools ...")

        self._build_menu()
        self._build_toolbar()
        self._build_searchbar()
        self._build_main_pane()
        self._build_statusbar()
        self._bind_shortcuts()

        try:
            self.tools = AliceTools(self.alice_path) if self.alice_path else AliceTools()
        except AliceNotFound as exc:
            self.status_var.set("未找到 alice-tools")
            messagebox.showerror("缺少 alice-tools", str(exc), parent=self)
        else:
            self.status_var.set(f"alice-tools：{self.tools.executable}")

        if left or right:
            self.after(150, self.load_pair)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="打开左侧 AIN ...", command=lambda: self.choose_file("left"))
        file_menu.add_command(label="打开右侧 AIN ...", command=lambda: self.choose_file("right"))
        file_menu.add_command(label="加载 / 刷新", accelerator="F5", command=self.load_pair)
        file_menu.add_separator()
        file_menu.add_command(label="保存左侧（写回原文件）", accelerator="Ctrl+Shift+L", command=lambda: self.save_side("left"))
        file_menu.add_command(label="保存右侧（写回原文件）", accelerator="Ctrl+Shift+R", command=lambda: self.save_side("right"))
        file_menu.add_command(label="两侧全部保存", accelerator="Ctrl+S", command=self.save_all)
        file_menu.add_checkbutton(label="写回前备份 .bak", variable=self.backup_var)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.destroy)
        menubar.add_cascade(label="文件", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="查找栏", accelerator="Ctrl+F", command=self.toggle_searchbar)
        view_menu.add_command(label="查找下一个", accelerator="F3", command=lambda: self.find_next(1))
        view_menu.add_command(label="查找上一个", accelerator="Shift+F3", command=lambda: self.find_next(-1))
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="左侧显示文本节（按函数）",
            variable=self.show_text_sections_var,
            command=self.on_text_sections_toggle,
        )
        view_menu.add_command(label="显示 / 隐藏结构栏", command=self.toggle_structure)
        view_menu.add_command(label="展开全部结构", command=self.expand_all_structure)
        view_menu.add_separator()
        view_menu.add_checkbutton(label="仅显示差异行", variable=self.diff_only_var, command=self.apply_filter)
        view_menu.add_command(label="显示全部行", command=lambda: (self.diff_only_var.set(False), self.apply_filter()))
        menubar.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="alice-tools 项目主页", command=lambda: webbrowser.open(ALICE_TOOLS_URL))
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(fill="x")
        bar.columnconfigure(1, weight=1)

        ttk.Button(bar, text="打开左侧", width=9, command=lambda: self.choose_file("left")).grid(row=0, column=0, padx=(0, 4), pady=2)
        ttk.Entry(bar, textvariable=self.left_var).grid(row=0, column=1, sticky="ew", padx=(0, 4))
        ttk.Label(bar, text="编码").grid(row=0, column=2, padx=(0, 2))
        self.left_enc_combo = ttk.Combobox(bar, textvariable=self.left_enc_var, values=_ENCODINGS, width=9, state="readonly")
        self.left_enc_combo.grid(row=0, column=3, padx=(0, 8))
        ttk.Button(bar, text="加载 / 刷新", command=self.load_pair).grid(row=0, column=4, padx=(0, 4))
        ttk.Button(bar, text="保存左侧", command=lambda: self.save_side("left")).grid(row=0, column=5, padx=(0, 4))
        ttk.Button(bar, text="保存右侧", command=lambda: self.save_side("right")).grid(row=0, column=6)

        ttk.Button(bar, text="打开右侧", width=9, command=lambda: self.choose_file("right")).grid(row=1, column=0, padx=(0, 4), pady=(2, 0))
        ttk.Entry(bar, textvariable=self.right_var).grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=(2, 0))
        ttk.Label(bar, text="编码").grid(row=1, column=2, padx=(0, 2), pady=(2, 0))
        self.right_enc_combo = ttk.Combobox(bar, textvariable=self.right_enc_var, values=_ENCODINGS, width=9, state="readonly")
        self.right_enc_combo.grid(row=1, column=3, padx=(0, 8), pady=(2, 0))
        ttk.Button(bar, text="交换左右", command=self.swap_sides).grid(row=1, column=4, padx=(0, 4), pady=(2, 0))
        ttk.Button(bar, text="查找 ...", command=self.focus_search).grid(row=1, column=5, padx=(0, 4), pady=(2, 0))
        ttk.Button(bar, text="两侧保存", command=self.save_all).grid(row=1, column=6, pady=(2, 0))

    def _build_searchbar(self) -> None:
        self.search_frame = ttk.Frame(self, padding=(6, 2))
        ttk.Label(self.search_frame, text="查找").pack(side="left")
        self.search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var, width=34)
        self.search_entry.pack(side="left", padx=(4, 4))
        self.search_entry.bind("<Return>", lambda _e: self.find_next(1))
        self.search_entry.bind("<F3>", lambda _e: self.find_next(1))
        self.search_entry.bind("<Shift-F3>", lambda _e: self.find_next(-1))
        ttk.Button(self.search_frame, text="上一个", width=6, command=lambda: self.find_next(-1)).pack(side="left", padx=2)
        ttk.Button(self.search_frame, text="下一个", width=6, command=lambda: self.find_next(1)).pack(side="left", padx=2)
        ttk.Button(self.search_frame, text="仅显示匹配", command=self.apply_filter).pack(side="left", padx=(6, 2))
        ttk.Button(self.search_frame, text="清除", command=self.clear_filter).pack(side="left", padx=2)
        ttk.Button(self.search_frame, text="关闭", command=lambda: self.toggle_searchbar(False)).pack(side="left", padx=(4, 0))
        ttk.Label(self.search_frame, textvariable=self.search_status_var, anchor="w").pack(side="left", padx=(8, 0))

    def toggle_searchbar(self, show: Optional[bool] = None) -> None:
        visible = self.search_frame.winfo_manager() != ""
        show = (not visible) if show is None else show
        if show:
            self.search_frame.pack(fill="x", before=self.main_pane)
            self.search_entry.focus_set()
        else:
            self.search_frame.pack_forget()

    def toggle_structure(self) -> None:
        if self.sidebar_frame in self.main_pane.panes():
            self.main_pane.forget(self.sidebar_frame)
        else:
            self.main_pane.insert("end", self.sidebar_frame, weight=0)

    def _build_main_pane(self) -> None:
        self.main_pane = ttk.Panedwindow(self, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True)

        self.sidebar_frame = ttk.Frame(self.main_pane, padding=(4, 4), width=340)
        self.sidebar_frame.pack_propagate(False)
        sidebar = self.sidebar_frame
        sidebar.rowconfigure(2, weight=1)
        sidebar.columnconfigure(0, weight=1)
        ttk.Label(sidebar, text="AIN 结构", anchor="w").grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            sidebar,
            text="文本节",
            variable=self.show_text_sections_var,
            command=self.on_text_sections_toggle,
        ).grid(row=0, column=1, sticky="e")
        buttons = ttk.Frame(sidebar)
        buttons.grid(row=1, column=0, sticky="ew", pady=(2, 2))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="编辑左侧", command=lambda: self.edit_selected_item("left")).grid(row=0, column=0, sticky="ew", padx=(0, 2))
        ttk.Button(buttons, text="编辑右侧", command=lambda: self.edit_selected_item("right")).grid(row=0, column=1, sticky="ew", padx=(2, 0))

        structure_container = ttk.Frame(sidebar)
        structure_container.grid(row=2, column=0, sticky="nsew")
        structure_container.rowconfigure(0, weight=1)
        structure_container.columnconfigure(0, weight=1)
        self.structure_tree = ttk.Treeview(structure_container, show="tree", selectmode="browse")
        self.structure_tree.tag_configure("struct_changed", foreground="#a06000")
        self.structure_tree.tag_configure("struct_dirty", foreground="#1c7c1c")
        s_vsb = ttk.Scrollbar(structure_container, orient="vertical", command=self.structure_tree.yview)
        self.structure_tree.configure(yscrollcommand=s_vsb.set)
        self.structure_tree.grid(row=0, column=0, sticky="nsew")
        s_vsb.grid(row=0, column=1, sticky="ns")
        self.structure_tree.bind("<<TreeviewOpen>>", self.on_structure_open)
        self.structure_tree.bind("<<TreeviewSelect>>", self.on_structure_select)

        self.main_pane.add(sidebar, weight=0)
        self._build_tree(self.main_pane)
        self.main_pane.add(self.tree_container, weight=1)

    def _build_tree(self, parent: tk.Widget) -> None:
        self.tree_container = ttk.Frame(parent)
        self.tree_container.rowconfigure(0, weight=1)
        self.tree_container.columnconfigure(0, weight=1)

        headings = {
            "no": "行",
            "kind": "类型",
            "id": "ID",
            "left": "左侧文本（双击编辑）",
            "right": "右侧文本（双击编辑）",
        }
        widths = {"no": 70, "kind": 50, "id": 75, "left": 430, "right": 430}
        stretch = {"no": False, "kind": False, "id": False, "left": True, "right": True}

        self.tree = ttk.Treeview(self.tree_container, columns=_ROW_COLUMNS, show="headings", selectmode="browse")
        for col in _ROW_COLUMNS:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=40, stretch=stretch[col])
        self.tree.tag_configure("section", background="#e8eef7", foreground="#1f3864")
        self.tree.tag_configure("changed", background="#fff1cc")
        self.tree.tag_configure("missing", background="#ffd9d9")
        self.tree.tag_configure("dirty", background="#d9f2d9")

        vsb = ttk.Scrollbar(self.tree_container, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self.tree_container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Left>", lambda _e: self.tree.xview_scroll(-2, "units"))
        self.tree.bind("<Right>", lambda _e: self.tree.xview_scroll(2, "units"))

    def _build_statusbar(self) -> None:
        status = ttk.Frame(self, padding=(6, 2))
        status.pack(fill="x", side="bottom")
        ttk.Label(status, textvariable=self.status_var, anchor="w").pack(fill="x")

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-s>", lambda _e: self.save_all())
        self.bind("<Control-f>", lambda _e: self.focus_search())
        self.bind("<F3>", lambda _e: self.find_next(1))
        self.bind("<Control-Shift-L>", lambda _e: self.save_side("left"))
        self.bind("<Control-Shift-R>", lambda _e: self.save_side("right"))
        self.bind("<Control-o>", lambda _e: self.choose_file("left"))
        self.bind("<Control-Shift-O>", lambda _e: self.choose_file("right"))

    # ------------------------------------------------------------------
    # loading and display
    # ------------------------------------------------------------------
    def choose_file(self, side: str) -> None:
        initial = self.left_var.get() if side == "left" else self.right_var.get()
        initial_dir = str(Path(initial).parent) if initial else None
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 AIN 文件",
            initialdir=initial_dir,
            filetypes=[("AliceSoft scenario", "*.ain"), ("All files", "*.*")],
        )
        if not path:
            return
        if not Path(path).is_file():
            messagebox.showerror("文件无效", f"文件不存在或不可读：{path}", parent=self)
            return
        var = self.left_var if side == "left" else self.right_var
        var.set(str(Path(path)))
        other = self.right_var.get().strip() if side == "left" else self.left_var.get().strip()
        if other:
            self.status_var.set("两个文件路径已就绪，点击“加载 / 刷新”开始对照。")
        else:
            self.status_var.set("已选择一个 AIN，点击“加载 / 刷新”可直接单文件打开。")

    def _encoding_for(self, side: str) -> Optional[str]:
        value = (self.left_enc_var if side == "left" else self.right_enc_var).get()
        return None if value == _ENCODINGS[0] else value

    def load_pair(self, *, force: bool = False) -> bool:
        left = self.left_var.get().strip()
        right = self.right_var.get().strip()
        if not left and not right:
            messagebox.showinfo("提示", "请至少选择一个 .ain 文件。", parent=self)
            return False
        if self.tools is None:
            return False
        if not force and not self._confirm_discard_changes():
            return False

        present: List[tuple[str, str]] = []
        for side, raw_path in (("left", left), ("right", right)):
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if not path.is_file():
                messagebox.showerror("文件无效", f"文件不存在或不可读：{path}", parent=self)
                return False
            normalized = str(path)
            if side == "left":
                self.left_var.set(normalized)
            else:
                self.right_var.set(normalized)
            present.append((side, normalized))

        single_mode = len(present) == 1
        if single_mode:
            side, path = present[0]
            single_encoding = self._encoding_for(side)

        self.status_var.set("正在读取并解析 AIN 文本节 ...")
        self.update_idletasks()
        initial_view = _TEXT_VIEW
        try:
            if single_mode:
                side, path = present[0]
                try:
                    dump = self._open_dump(path, single_encoding)
                except AliceError as exc:
                    try:
                        dump, initial_view = self._open_dump_fallback(path, encoding=single_encoding)
                    except (AliceError, OSError, ValueError) as fallback_exc:
                        raise AliceError(f"{exc}\n\n区段回退也失败：{fallback_exc}") from fallback_exc
                self.status_var.set(f"已读取：{Path(path).name}（{dump.encoding}）")
                self.update_idletasks()
                left_dump = dump
                right_dump = self._empty_dump()
                self._load_section_maps([("left", path)], {"left": dump.encoding})
            else:
                left_path = next(path for side, path in present if side == "left")
                right_path = next(path for side, path in present if side == "right")
                try:
                    left_dump = self._open_dump(left_path, self._encoding_for("left"))
                    self.status_var.set(f"左侧完成：{Path(left_path).name}（{left_dump.encoding}）")
                    self.update_idletasks()
                    right_dump = self._open_dump(right_path, self._encoding_for("right"))
                except AliceError as exc:
                    # Keep both panes on the same raw section if either
                    # file has mixed encodings and cannot be dumped.
                    try:
                        left_dump, right_dump, initial_view = self._open_pair_fallback(
                            left_path, right_path
                        )
                    except (AliceError, OSError, ValueError) as fallback_exc:
                        raise AliceError(f"{exc}\n\n区段回退也失败：{fallback_exc}") from fallback_exc
                self._load_section_maps(
                    present,
                    {"left": left_dump.encoding, "right": right_dump.encoding},
                )
        except (AliceError, OSError, ValueError) as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)
            self.status_var.set("读取失败")
            return False

        # Keep the previous model and its rows intact until every read has
        # succeeded.  A failed refresh must not strand unsaved edits.
        self.single_mode = single_mode
        self.single_file_side = "left" if single_mode else None
        if single_mode:
            if present[0][0] == "right":
                self.left_enc_var.set(self.right_enc_var.get())
            self.left_var.set(left_dump.path)
            self.right_var.set("")
        self.text_left_dump = left_dump
        self.text_right_dump = None if single_mode else right_dump
        self._set_active_view(left_dump, right_dump, initial_view)
        return True

    @staticmethod
    def _empty_dump() -> TextDump:
        return TextDump(path="", encoding="", sections=[], entries=[])

    def _load_section_maps(
        self,
        present: list[tuple[str, str]],
        encoding_by_side: Dict[str, str],
    ) -> None:
        assert self.tools is not None
        maps: Dict[str, List[SectionInfo]] = {"left": [], "right": []}
        for side, path in present:
            encoding = encoding_by_side.get(side, "CP932")
            map_text = ""
            for candidate in dict.fromkeys((encoding, "CP932", "CP936", "UTF-8")):
                try:
                    map_text = self.tools.map_dump(path, candidate)
                    break
                except AliceError:
                    continue
            if not map_text:
                raise AliceError(f"无法读取 AIN 区段表：{path}")
            maps[side] = parse_section_map(map_text)
        self.section_maps = maps


    def _set_active_view(
        self,
        left_dump: TextDump,
        right_dump: TextDump,
        view_name: str,
    ) -> None:
        self.left_dump = left_dump
        self.right_dump = right_dump
        self.current_view = view_name
        self._clear_rows()
        self.rows = align_dumps(left_dump, right_dump)
        self._populate_tree()
        self.apply_filter()
        self._update_status()

    def switch_view(self, view_name: str) -> None:
        if view_name == self.current_view:
            return
        if view_name == _TEXT_VIEW:
            if not self._confirm_discard_changes():
                return
            assert self.text_left_dump is not None
            right_dump = self.text_right_dump or self._empty_dump()
            self._discard_active_edits()
            self._set_active_view(self.text_left_dump, right_dump, _TEXT_VIEW)
            return

        if view_name not in TEXT_SECTION_KINDS:
            self._show_section_info(view_name)
            return

        if not self._confirm_discard_changes():
            return
        assert self.tools is not None and self.text_left_dump is not None
        try:
            left_raw = load_section_dump(
                self.tools,
                self.text_left_dump.path,
                view_name,
                self.text_left_dump.encoding,
            )
            right_raw = (
                load_section_dump(
                    self.tools,
                    self.text_right_dump.path,
                    view_name,
                    self.text_right_dump.encoding,
                )
                if self.text_right_dump is not None
                else self._empty_dump()
            )
        except (AliceError, OSError, ValueError) as exc:
            messagebox.showerror("区段读取失败", str(exc), parent=self)
            return

        largest = max(len(left_raw.entries), len(right_raw.entries))
        if largest > 50_000:
            ok = messagebox.askyesno(
                "区段较大",
                f"{view_name} 有 {largest:,} 条记录，载入表格可能占用较多内存。" + chr(10) + "是否继续？",

                parent=self,
            )
            if not ok:
                return
        self._discard_active_edits()
        self._set_active_view(left_raw, right_raw, view_name)

    def _discard_active_edits(self) -> None:
        """Reset outgoing edits only after a view switch can proceed."""
        for dump in (self.left_dump, self.right_dump):
            if dump is not None:
                for entry in dump.entries:
                    if entry.dirty:
                        entry.text = entry.original_text

    def _show_section_info(self, view_name: str) -> None:
        lines: List[str] = [f"{view_name} 不是可直接双栏编辑的文本区段。"]
        for side, label in (("left", "左侧"), ("right", "右侧")):
            section = next((s for s in self.section_maps.get(side, []) if s.name == view_name), None)
            if section is not None:
                lines.append(f"{label}：{section.describe()}")
        lines.append("")
        lines.append("可直接编辑的文本区段：MSG0、MSG1、STR0。")
        messagebox.showinfo("AIN 区段", chr(10).join(lines), parent=self)

    def _open_dump(self, path: str, encoding: Optional[str]) -> TextDump:
        assert self.tools is not None
        encoding = encoding or self.tools.detect_encoding(path)
        text = self.tools.dump_text(path, encoding)
        return parse_dump(text, path=path, encoding=encoding)

    def _open_dump_fallback(
        self,
        path: str,
        section_name: Optional[str] = None,
        *,
        encoding: Optional[str] = None,
    ) -> tuple[TextDump, str]:
        """Fallback for files whose function names and strings use different
        encodings (e.g. ランス０２: CP932 names + CP936 strings).

        alice-tools has a single global input encoding, so ``ain dump -t``
        cannot convert such files.  We instead read one raw text section
        directly from the decrypted AIN image.
        """
        assert self.tools is not None
        dump = load_text_section_with_fallback(
            self.tools,
            path,
            section_name=section_name,
            text_encodings=(encoding,) if encoding else None,
        )
        return dump, dump.sections[0]

    def _open_pair_fallback(
        self,
        left_path: str,
        right_path: str,
    ) -> tuple[TextDump, TextDump, str]:
        """Raw-section fallback for a pair where one side cannot be dumped.

        Both sides must fall back to the *same* section name, otherwise the
        two panes would show unrelated tables and alignment would be
        meaningless.
        """
        assert self.tools is not None
        left_sections = text_section_names(self.tools, left_path)
        right_sections = text_section_names(self.tools, right_path)
        common = [name for name in ("MSG0", "MSG1", "STR0") if name in left_sections and name in right_sections]
        if not common:
            raise AliceError(
                "两侧没有共同的文本区段，无法回退对照"
                f"（左：{', '.join(left_sections) or '无'}；"
                f"右：{', '.join(right_sections) or '无'}）"
            )
        section = common[0]
        left_dump, _ = self._open_dump_fallback(
            left_path, section, encoding=self._encoding_for("left")
        )
        right_dump, _ = self._open_dump_fallback(
            right_path, section, encoding=self._encoding_for("right")
        )
        return left_dump, right_dump, section

    def _clear_rows(self) -> None:
        # Detached (filtered-out) items are absent from get_children(), but
        # still occupy memory inside Tk.  Delete every tracked item in batches.
        for start in range(0, len(self.item_order), 1000):
            self.tree.delete(*self.item_order[start : start + 1000])
        for item in self.structure_tree.get_children():
            self.structure_tree.delete(item)
        self.rows_by_item.clear()
        self.rows.clear()
        self.item_order.clear()
        self.row_main_item.clear()
        self.section_rows.clear()
        self.structure_item_to_row.clear()
        self.main_to_structure_item.clear()
        self.structure_children_loaded.clear()
        self.search_hits.clear()
        self.search_index = -1
        self._search_query = None
        self._search_positions.clear()
        self.search_status_var.set("")

    def _populate_tree(self) -> None:
        entries_seen = 0
        changed = 0
        for row in self.rows:
            if row.is_section:
                iid = self.tree.insert(
                    "",
                    "end",
                    values=("", "▼", "", row.left_section or "", row.right_section or ""),
                    tags=("section",),
                )
            else:
                if self.single_mode:
                    left_text = row.left.text if row.left else ""
                    right_text = ""
                else:
                    left_text = row.left.text if row.left else "<此行仅右侧存在>"
                    right_text = row.right.text if row.right else "<此行仅左侧存在>"
                tags: List[str] = []
                if (row.left is None or row.right is None) and not self.single_mode:
                    tags.append("missing")
                elif self._row_changed(row):
                    tags.append("changed")
                if self._row_changed(row):
                    changed += 1
                if (row.left and row.left.dirty) or (row.right and row.right.dirty):
                    tags.append("dirty")
                iid = self.tree.insert(
                    "",
                    "end",
                    values=(
                        row.no,
                        (row.left or row.right).kind.upper(),
                        (row.left or row.right).index,
                        display_text(left_text),
                        display_text(right_text),
                    ),
                    tags=tuple(tags) if tags else (),
                )
            self.rows_by_item[iid] = row
            self.row_main_item[id(row)] = iid
            self.item_order.append(iid)
            if not row.is_section:
                entries_seen += 1
        self._last_changed_count = changed
        self._populate_structure_tree()

    def _populate_structure_tree(self) -> None:
        """Build a lazy tree: function sections and the real AIN section map."""
        if self.current_view == _TEXT_VIEW and self.show_text_sections_var.get():
            self.structure_tree.insert("", "end", iid=_TEXT_ROOT, text="文本节（按函数）", open=True)
            for row in self.rows:
                if row.is_section:
                    left_name = row.left_section or ""
                    right_name = row.right_section or ""
                    if left_name == right_name:
                        label = f"{row.section_no}. {left_name}"
                    else:
                        label = f"{row.section_no}. L:{left_name} | R:{right_name}"
                    iid = f"sec:{row.section_no}"
                    self.structure_tree.insert(_TEXT_ROOT, "end", iid=iid, text=label, open=False)
                else:
                    self.section_rows.setdefault(row.section_no, []).append((str(len(self.section_rows.get(row.section_no, []))), row))

        self.structure_tree.insert("", "end", iid=_AIN_ROOT, text="AIN 区段", open=True)
        for section in self._all_ain_sections():
            editable = section.name in TEXT_SECTION_KINDS
            label = f"{section.name}{'（可编辑）' if editable else ''}  0x{section.start:08x}  {section.size:,}B"
            self.structure_tree.insert(
                _AIN_ROOT,
                "end",
                iid=f"ainsec:{section.name}",
                text=label,
                tags=("struct_dirty",) if editable else (),
            )
        if self.current_view != _TEXT_VIEW:
            iid = f"ainsec:{self.current_view}"
            if self.structure_tree.exists(iid):
                self.structure_tree.selection_set(iid)
                self.structure_tree.see(iid)

    def _all_ain_sections(self) -> List[SectionInfo]:
        names: List[str] = []
        sections: List[SectionInfo] = []
        for side in ("left", "right"):
            for section in self.section_maps.get(side, []):
                if section.name not in names:
                    names.append(section.name)
                    sections.append(section)
        return sections

    def _structure_preview(self, text: str, limit: int = 36) -> str:
        one_line = display_text(text)
        return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"

    def _ensure_structure_children(self, section_iid: str) -> None:
        if section_iid in self.structure_children_loaded:
            return
        sec_no = int(section_iid.split(":", 1)[1])
        for _dummy, row in self.section_rows.get(sec_no, []):
            # main item id for this row
            main_item = self.row_main_item.get(id(row), "")
            if not main_item:
                continue
            entry = row.left or row.right
            left_preview = self._structure_preview(row.left.text) if row.left else "<无>"
            right_preview = self._structure_preview(row.right.text) if row.right else "<无>"
            label = (
                f"{entry.kind.upper()}[{entry.index}] 第{entry.occurrence}次  "
                f"L:{left_preview} | R:{right_preview}"
            )
            child = self.structure_tree.insert(
                section_iid, "end", text=label, tags=("struct_changed",) if self._row_changed(row) else ()
            )
            self.structure_item_to_row[child] = (main_item, row)
            self.main_to_structure_item[main_item] = child
        self.structure_children_loaded.add(section_iid)

    def on_structure_open(self, _event: object = None) -> None:
        selected = self.structure_tree.selection()
        if selected and selected[0].startswith("sec:"):
            self._ensure_structure_children(selected[0])

    def on_structure_select(self, _event: object = None) -> None:
        selected = self.structure_tree.selection()
        if not selected:
            return
        iid = selected[0]
        if iid.startswith("ainsec:"):
            self.switch_view(iid.split(":", 1)[1])
            return
        pair = self.structure_item_to_row.get(iid)
        if not pair:
            return
        self._reveal_main_item(pair[0])

    def edit_selected_item(self, side: str) -> None:
        if self.current_view != _TEXT_VIEW:
            messagebox.showinfo(
                "提示",
                "当前是 AIN 区段视图，条目直接显示在主表中。"
                "请双击主表左/右单元格编辑，或用“查找定位”跳到目标 ID。",
                parent=self,
            )
            return
        selected = self.structure_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先在左侧结构树中选择一个 s/m 条目。", parent=self)
            return
        pair = self.structure_item_to_row.get(selected[0])
        if not pair:
            messagebox.showinfo("提示", "请选择具体的 s/m 条目，而不是节标题。", parent=self)
            return
        main_item, row = pair
        if (side == "left" and row.left is None) or (side == "right" and row.right is None):
            messagebox.showinfo("提示", "该侧没有这个条目。", parent=self)
            return
        self._reveal_main_item(main_item)
        self._begin_edit(main_item, side)

    def on_text_sections_toggle(self) -> None:
        """Show/hide the function-section tree, switching view if needed."""
        if self.show_text_sections_var.get() and self.current_view != _TEXT_VIEW:
            self.switch_view(_TEXT_VIEW)
            if self.current_view != _TEXT_VIEW:
                self.show_text_sections_var.set(False)
            return
        self.refresh_structure_tree()

    def refresh_structure_tree(self) -> None:
        """Rebuild only the left structure tree (keeps the main table)."""
        for item in self.structure_tree.get_children(""):
            self.structure_tree.delete(item)
        self.section_rows.clear()
        self.structure_item_to_row.clear()
        self.main_to_structure_item.clear()
        self.structure_children_loaded.clear()
        self._populate_structure_tree()

    def expand_all_structure(self) -> None:
        if self.structure_tree.exists(_TEXT_ROOT):
            self.structure_tree.item(_TEXT_ROOT, open=True)
        if self.structure_tree.exists(_AIN_ROOT):
            self.structure_tree.item(_AIN_ROOT, open=True)
        if self.current_view == _TEXT_VIEW:
            for section_iid in self.structure_tree.get_children(_TEXT_ROOT):
                self._ensure_structure_children(section_iid)
                self.structure_tree.item(section_iid, open=True)

    def _reveal_main_item(self, main_item: str) -> None:
        """Reattach a filtered-out row at its original position, then scroll.

        Reattaching with ``"end"`` would move the row to the bottom of the
        table, which is the bug fixed here: we look for the next item in the
        original insertion order that is still visible and insert before it.
        """
        attached = set(self.tree.get_children(""))
        if main_item not in attached:
            next_item = _reattach_target(self.item_order, attached, main_item)
            position = self.tree.index(next_item) if next_item else "end"
            self.tree.reattach(main_item, "", position)
        self.tree.see(main_item)
        self.tree.selection_set(main_item)
        self.tree.focus(main_item)

    def _update_structure_node(self, main_item: str) -> None:
        structure_item = self.main_to_structure_item.get(main_item)
        if not structure_item:
            return
        _dummy, row = self.structure_item_to_row[structure_item]
        entry = row.left or row.right
        left_preview = self._structure_preview(row.left.text) if row.left else "<无>"
        right_preview = self._structure_preview(row.right.text) if row.right else "<无>"
        label = (
            f"{entry.kind.upper()}[{entry.index}] 第{entry.occurrence}次  "
            f"L:{left_preview} | R:{right_preview}"
        )
        tags = []
        if self._row_changed(row):
            tags.append("struct_changed")
        if (row.left and row.left.dirty) or (row.right and row.right.dirty):
            tags.append("struct_dirty")
        self.structure_tree.item(structure_item, text=label, tags=tuple(tags))

    def _row_changed(self, row: AlignedRow) -> bool:
        return not self.single_mode and row.changed

    def _update_status(self) -> None:
        if self.left_dump is None:
            return
        left_dirty = self.left_dump.dirty_count
        visible = len(self.tree.get_children())
        if self.right_dump is not None and self.right_dump.path:
            right_info = (
                f"右 {Path(self.right_dump.path).name}"
                f"（{self.right_dump.encoding}，{len(self.right_dump.entries)} 条）"
            )
            right_dirty = self.right_dump.dirty_count
            dirty_info = f"未保存：左 {left_dirty} / 右 {right_dirty}"
        else:
            right_info = "右（未加载）"
            dirty_info = f"未保存：左 {left_dirty}"
        self.status_var.set(
            f"左 {Path(self.left_dump.path).name}（{self.left_dump.encoding}，{len(self.left_dump.entries)} 条）"
            f"  |  {right_info}"
            f"  |  差异 {self._last_changed_count}  |  显示 {visible} 行"
            f"  |  视图 {self.current_view}"
            f"  |  {dirty_info}"
            f"  |  alice：{self.tools.executable if self.tools else '未找到'}"
        )

    def swap_sides(self) -> None:
        if not self._confirm_discard_changes():
            return
        left = self.left_var.get()
        right = self.right_var.get()
        self.left_var.set(right)
        self.right_var.set(left)
        left_enc = self.left_enc_var.get()
        right_enc = self.right_enc_var.get()
        self.left_enc_var.set(right_enc)
        self.right_enc_var.set(left_enc)
        self.load_pair(force=True)

    def _confirm_discard_changes(self) -> bool:
        self._commit_editor()
        if (self.left_dump is None or not self.left_dump.dirty_count) and (
            self.right_dump is None or not self.right_dump.dirty_count
        ):
            return True
        return messagebox.askyesno(
            "放弃未保存的修改？",
            "当前有未保存的编辑，重新加载/打开文件会丢弃这些修改。" + chr(10) + "确定继续吗？",
            parent=self,
        )

    # ------------------------------------------------------------------
    # filtering
    # ------------------------------------------------------------------
    def apply_filter(self) -> None:
        self._commit_editor()
        query = self.search_var.get().strip().lower()
        diff_only = self.diff_only_var.get()
        visible = []
        for item, row in self.rows_by_item.items():
            if row.is_section:
                visible.append(item)
                continue
            show = True
            if diff_only and not self._row_changed(row):
                show = False
            if show and query:
                show = self._row_matches(row, query)
            if show:
                visible.append(item)
        # One Tk call replaces thousands of detach/reattach crossings, while
        # set_children keeps omitted items available for later searches.
        if tuple(visible) != self.tree.get_children(""):
            self.tree.set_children("", *visible)
        self._update_status()

    def _row_matches(self, row: AlignedRow, query: str) -> bool:
        """Match either side of the paired row after Unicode normalization.

        This is deliberately row-based: a query written in Japanese matches
        the Japanese cell, and a query written in Chinese matches the Chinese
        cell; either match locates the same paired row.
        """
        normalized_query = normalize_search_text(query)
        needles = (
            (row.left.text if row.left else ""),
            (row.right.text if row.right else ""),
            (row.left.section if row.left else ""),
            (row.right.section if row.right else ""),
            str((row.left or row.right).index),
            str((row.left or row.right).kind),
        )
        return any(normalized_query in normalize_search_text(str(n)) for n in needles)

    # ------------------------------------------------------------------
    # search / locate
    # ------------------------------------------------------------------
    def _collect_search_hits(self, query: str) -> List[str]:
        query = normalize_search_text(query)
        return [
            item
            for item, row in self.rows_by_item.items()
            if not row.is_section and self._row_matches(row, query)
        ]

    def find_next(self, direction: int = 1) -> None:
        self._commit_editor()
        query = normalize_search_text(self.search_var.get().strip())
        if not query:
            self.search_status_var.set("请输入查找内容")
            return
        if query != self._search_query:
            self.search_hits = self._collect_search_hits(query)
            self._search_positions = {
                item: index for index, item in enumerate(self.search_hits)
            }
            self._search_query = query
        hits = self.search_hits
        if not hits:
            self.search_index = -1
            self.search_status_var.set("未找到匹配项")
            return

        current = self.tree.focus()
        current_index = self._search_positions.get(
            current, -1 if direction > 0 else len(hits)
        )
        self.search_index = (current_index + direction) % len(hits)
        item = hits[self.search_index]
        self._reveal_main_item(item)
        self.search_status_var.set(
            f"第 {self.search_index + 1} / {len(hits)} 个匹配"
        )

    def focus_search(self) -> None:
        self.toggle_searchbar(True)
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, tk.END)

    def clear_filter(self) -> None:
        self.search_var.set("")
        self.diff_only_var.set(False)
        self.search_hits.clear()
        self.search_index = -1
        self._search_query = None
        self._search_positions.clear()
        self.search_status_var.set("")
        self.apply_filter()

    # ------------------------------------------------------------------
    # editing
    # ------------------------------------------------------------------
    def _commit_editor(self) -> None:
        if self._editor is not None:
            self._editor._commit()

    def on_double_click(self, event: tk.Event) -> None:
        if self._editor is not None:
            return
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        column = _ROW_COLUMNS[int(column[1:]) - 1]
        if column not in ("left", "right"):
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self._begin_edit(item, column)

    def _begin_edit(self, item: str, column: str) -> bool:
        row = self.rows_by_item.get(item)
        if row is None or row.is_section:
            return False
        if (column == "left" and row.left is None) or (column == "right" and row.right is None):
            return False
        _CellEditor(self, item, column)
        return True

    def on_cell_edited(self, item: str, column: str, entry: TextEntry) -> None:
        self._search_query = None
        row = self.rows_by_item.get(item)
        if row is None:
            return
        values = list(self.tree.item(item, "values"))
        values[_ROW_COLUMNS.index(column)] = display_text(entry.text)
        self.tree.item(item, values=values)

        tags = list(self.tree.item(item, "tags"))
        self._last_changed_count += int(self._row_changed(row)) - int(
            "changed" in tags or "missing" in tags
        )
        if (row.left and row.left.dirty) or (row.right and row.right.dirty):
            if "dirty" not in tags:
                tags.append("dirty")
        elif "dirty" in tags:
            tags.remove("dirty")
        if not row.is_section:
            if self._row_changed(row):
                if "changed" not in tags and "missing" not in tags:
                    tags.append("changed")
            else:
                if "changed" in tags:
                    tags.remove("changed")
        self.tree.item(item, tags=tags)
        query = self.search_var.get().strip()
        if (self.diff_only_var.get() and not self._row_changed(row)) or (
            query and not self._row_matches(row, query)
        ):
            self.tree.detach(item)
        self._update_structure_node(item)
        self._update_status()

    # ------------------------------------------------------------------
    # saving back to the original files
    # ------------------------------------------------------------------
    def save_side(self, side: str) -> bool:
        self._commit_editor()
        if self.tools is None:
            messagebox.showerror("错误", "alice-tools 不可用。", parent=self)
            return False
        if self.single_mode and side == "right":
            messagebox.showinfo("提示", "当前是单文件模式，请使用“保存左侧”。", parent=self)
            return False
        dump = self.left_dump if side == "left" else self.right_dump
        if dump is None:
            messagebox.showinfo("提示", "请先加载文件。", parent=self)
            return False
        dirty = dump.dirty_entries
        if not dirty:
            messagebox.showinfo("提示", f"「{Path(dump.path).name}」没有未保存的修改。", parent=self)
            return False
        try:
            edit_text = build_edit_text(dirty)
        except ValueError as exc:
            messagebox.showerror("无法保存", str(exc), parent=self)
            return False

        backup_note = "将先备份为 .bak。" if self.backup_var.get() else "不会创建备份。"
        ok = messagebox.askyesno(
            "确认写回原文件",
            f"即将把 {len(dirty)} 处修改写回：\n{dump.path}\n\n{backup_note}\n是否继续？",
            parent=self,
        )
        if not ok:
            return False

        self.status_var.set(f"正在写回 {Path(dump.path).name} ...")
        self.update_idletasks()
        try:
            _, old_size, new_size = self.tools.edit_text(
                dump.path,
                edit_text,
                dump.encoding,
                backup=self.backup_var.get(),
            )
        except AliceError as exc:
            messagebox.showerror("写回失败", str(exc), parent=self)
            self.status_var.set("写回失败")
            return False
        except OSError as exc:
            messagebox.showerror("写回失败", str(exc), parent=self)
            self.status_var.set("写回失败")
            return False

        # AIN stores each s/m id once; every occurrence (and cached text
        # view) must reflect the value that was actually written.
        saved_values = {entry.id_key: entry.text for entry in dirty}
        cached = self.text_left_dump if side == "left" else self.text_right_dump
        for saved_dump in (dump, cached):
            if saved_dump is None:
                continue
            for entry in saved_dump.entries:
                if entry.id_key in saved_values:
                    entry.text = saved_values[entry.id_key]
                    entry.original_text = entry.text
        self._refresh_after_save()
        self.status_var.set(
            f"已写回 {Path(dump.path).name}：{len(dirty)} 处修改，"
            f"{old_size} -> {new_size} 字节。"
        )
        return True

    def save_all(self) -> None:
        self._commit_editor()
        saved = 0
        for side in ("left", "right"):
            dump = self.left_dump if side == "left" else self.right_dump
            if dump is None:
                continue
            if dump.dirty_count:
                saved += 1 if self.save_side(side) else 0
        if saved:
            self._update_status()

    def _refresh_after_save(self) -> None:
        """Rebuild every row's tags from the current model state."""
        self._search_query = None
        self._last_changed_count = 0
        for item, row in self.rows_by_item.items():
            if row.is_section:
                self.tree.item(item, tags=("section",))
                continue
            tags: List[str] = []
            if (row.left is None or row.right is None) and not self.single_mode:
                tags.append("missing")
            elif self._row_changed(row):
                tags.append("changed")
            if self._row_changed(row):
                self._last_changed_count += 1
            if (row.left is not None and row.left.dirty) or (
                row.right is not None and row.right.dirty
            ):
                tags.append("dirty")
            values = list(self.tree.item(item, "values"))
            for side, entry in (("left", row.left), ("right", row.right)):
                if entry is not None:
                    values[_ROW_COLUMNS.index(side)] = display_text(entry.text)
            self.tree.item(item, values=values, tags=tuple(tags))
            self._update_structure_node(item)
        self.apply_filter()

    # ------------------------------------------------------------------
    # misc
    # ------------------------------------------------------------------
    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"{APP_NAME} v{__version__}\n\n"
            "并排打开两个 AliceSoft .ain 文件，对照并编辑全部文本节，"
            "通过官方 alice-tools 写回原文件。\n\n"
            f"alice-tools：{ALICE_TOOLS_URL}\n"
            "本项目代码许可：MIT",
            parent=self,
        )

    def destroy(self) -> None:
        self._commit_editor()
        if (
            (self.left_dump is not None and self.left_dump.dirty_count)
            or (self.right_dump is not None and self.right_dump.dirty_count)
        ):
            if not messagebox.askyesno("退出", "存在未保存的修改，确定退出吗？", parent=self):
                return
        super().destroy()


def run_gui(
    left: Optional[str] = None,
    right: Optional[str] = None,
    left_encoding: Optional[str] = None,
    right_encoding: Optional[str] = None,
    alice: Optional[str] = None,
) -> int:
    """Create the Tk application and enter its main loop."""

    _enable_windows_dpi_awareness()
    app = AinDiffApp(
        left=left,
        right=right,
        left_encoding=left_encoding,
        right_encoding=right_encoding,
        alice=alice,
    )
    app.mainloop()
    return 0
