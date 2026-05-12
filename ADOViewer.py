#!/usr/bin/env python3
"""
Azure DevOps Work Item CSV Viewer

Windows-friendly, standard-library-only Python script.

Usage:
    py -3 ado_work_items_viewer.py
    py -3 ado_work_items_viewer.py "C:\\Path\\To\\All_Work_Items.csv"

What it does:
    - Reads an Azure DevOps work item CSV export.
    - Displays work items in a Tkinter tree/table.
    - Tries to reconstruct hierarchy using:
        1. Parent ID-style columns, if present.
        2. Title 1 / Title 2 / Title 3 hierarchy columns, if present.
        3. Otherwise groups by Work Item Type.
    - Supports filtering, expand/collapse, and a details panel.
    - Double-click opens a URL column if present.
"""

import os
import sys
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from adoviewer.csv_io import (
    build_azure_tree_csv,
    build_round_trip_csv,
    CsvExportError,
    read_csv_file,
    render_csv_text,
    write_csv_rows,
)
from adoviewer.ado_client import AdoClient, AdoClientError, AdoConnectionSettings
from adoviewer.project_io import load_project_file, save_project_file
from adoviewer.publish import (
    build_field_map,
    build_publish_plan,
    run_dry_run,
    run_live_publish,
)
from adoviewer.tree_model import WorkItemModel

_ADO_SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".adoviewer_connection.json")


# ----------------------------
# Tkinter UI
# ----------------------------

_PALETTE = {
    "bg": "#ffffff",
    "surface": "#f7f8fa",
    "surface_alt": "#eef0f4",
    "border": "#e3e6ec",
    "border_strong": "#cfd3dc",
    "text": "#111827",
    "muted": "#6b7280",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_active": "#1e40af",
    "hover": "#eef2ff",
    "selected": "#dbeafe",
    "row_alt": "#fafbfc",
    "success": "#166534",
    "warning": "#92400e",
    "deleted": "#9ca3af",
    "error_bg": "#fee2e2",
    "warning_bg": "#fef3c7",
}

_FONT = ("Segoe UI", 10)
_FONT_SEMI = ("Segoe UI Semibold", 10)
_FONT_SMALL = ("Segoe UI", 9)
_FONT_HEAD = ("Segoe UI Semibold", 11)


class AdoWorkItemsViewer(tk.Tk):
    def __init__(self, initial_path=None):
        super().__init__()

        self.title("Azure DevOps Work Items Viewer")
        self.geometry("1450x850")
        self.minsize(1000, 600)
        self.configure(background=_PALETTE["bg"])
        self._init_style()

        self.model = None
        self.current_path = None
        self.project_path = None
        self.source_path = None
        self.tree_item_to_node = {}
        self.details_local_id = None
        self.common_field_vars = []
        self.raw_field_items = {}
        self.expanded_node_keys = set()
        self.displayed_filter_text = ""
        self.updating_tree = False
        # The Status column is intentionally gone: state is shown as a small colored
        # dot in the title column. Type and State remain as text columns.
        self.tree_column_specs = [
            ("id", "ID", 75, 60, False),
            ("type", "Type", 130, 90, False),
            ("state", "State", 110, 80, False),
            ("assigned_to", "Assigned To", 180, 100, False),
            ("effort", "Effort / Points / Estimate", 140, 90, False),
            ("remaining", "Remaining", 90, 70, False),
            ("completed", "Completed", 90, 70, False),
            ("area", "Area", 180, 100, False),
            ("iteration", "Iteration", 180, 100, False),
            ("tags", "Tags", 220, 100, True),
        ]
        # Cache of small PhotoImage dots, keyed by state name.
        self._dot_images = {}
        self.visible_tree_columns = [column_id for column_id, *_rest in self.tree_column_specs]
        self.column_dialog = None
        self.column_chooser_vars = {}

        # Azure DevOps connection (org/project persisted; PAT is session-only)
        self.ado_connection: AdoConnectionSettings | None = None
        self._ado_pat: str | None = None  # never persisted
        self._load_connection_settings()

        self.create_widgets()
        self.create_menu()
        self.bind_keyboard_shortcuts()

        if initial_path:
            self.load_initial_path(initial_path)

    def _init_style(self):
        """Configure a modern, flat ttk theme for the whole app."""
        style = ttk.Style(self)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        p = _PALETTE

        # Frames and containers
        style.configure("TFrame", background=p["bg"])
        style.configure("Toolbar.TFrame", background=p["surface"])
        style.configure("Status.TFrame", background=p["surface"])
        style.configure("Card.TFrame", background=p["bg"])
        style.configure("TPanedwindow", background=p["bg"])

        # Labels
        style.configure("TLabel", background=p["bg"], foreground=p["text"], font=_FONT)
        style.configure(
            "Toolbar.TLabel",
            background=p["surface"],
            foreground=p["muted"],
            font=_FONT,
        )
        style.configure(
            "Status.TLabel",
            background=p["surface"],
            foreground=p["muted"],
            font=_FONT_SMALL,
        )
        style.configure(
            "Heading.TLabel",
            background=p["bg"],
            foreground=p["text"],
            font=_FONT_HEAD,
        )
        # Heading shown on the toolbar / header strip (matching surface bg).
        style.configure(
            "HeaderFile.TLabel",
            background=p["surface"],
            foreground=p["text"],
            font=_FONT_HEAD,
        )
        style.configure(
            "Muted.TLabel",
            background=p["bg"],
            foreground=p["muted"],
            font=_FONT_SMALL,
        )

        # Buttons - clean flat style
        style.configure(
            "TButton",
            background=p["bg"],
            foreground=p["text"],
            bordercolor=p["border"],
            lightcolor=p["bg"],
            darkcolor=p["bg"],
            focuscolor=p["accent"],
            relief="flat",
            padding=(12, 6),
            font=_FONT,
        )
        style.map(
            "TButton",
            background=[("active", p["hover"]), ("pressed", p["surface_alt"])],
            bordercolor=[("active", p["accent"]), ("focus", p["accent"])],
        )

        # Toolbar button - compact, blends into toolbar
        style.configure(
            "Toolbar.TButton",
            background=p["surface"],
            foreground=p["text"],
            bordercolor=p["surface"],
            lightcolor=p["surface"],
            darkcolor=p["surface"],
            focuscolor=p["accent"],
            relief="flat",
            padding=(10, 5),
            font=_FONT,
        )
        style.map(
            "Toolbar.TButton",
            background=[("active", p["hover"]), ("pressed", p["surface_alt"])],
            bordercolor=[("active", p["accent"])],
        )

        # Icon-style compact button (used for arrows etc.)
        style.configure(
            "Icon.TButton",
            background=p["surface"],
            foreground=p["text"],
            bordercolor=p["surface"],
            lightcolor=p["surface"],
            darkcolor=p["surface"],
            relief="flat",
            padding=(8, 5),
            font=_FONT_SEMI,
        )
        style.map(
            "Icon.TButton",
            background=[("active", p["hover"]), ("pressed", p["surface_alt"])],
        )

        # Accent / primary button
        style.configure(
            "Accent.TButton",
            background=p["accent"],
            foreground="#ffffff",
            bordercolor=p["accent"],
            lightcolor=p["accent"],
            darkcolor=p["accent"],
            focuscolor=p["accent_active"],
            relief="flat",
            padding=(14, 6),
            font=_FONT_SEMI,
        )
        style.map(
            "Accent.TButton",
            background=[("active", p["accent_hover"]), ("pressed", p["accent_active"])],
            bordercolor=[("active", p["accent_hover"]), ("pressed", p["accent_active"])],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )

        # Entry
        style.configure(
            "TEntry",
            fieldbackground="#ffffff",
            background="#ffffff",
            bordercolor=p["border"],
            lightcolor=p["border"],
            darkcolor=p["border"],
            foreground=p["text"],
            insertcolor=p["text"],
            padding=6,
            relief="flat",
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", p["accent"])],
            lightcolor=[("focus", p["accent"])],
            darkcolor=[("focus", p["accent"])],
        )

        # Treeview
        style.configure(
            "Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground=p["text"],
            rowheight=28,
            borderwidth=0,
            relief="flat",
            font=_FONT,
        )
        style.configure(
            "Treeview.Heading",
            background=p["surface"],
            foreground=p["muted"],
            relief="flat",
            font=_FONT_SEMI,
            padding=(10, 8),
            borderwidth=0,
        )
        style.map(
            "Treeview.Heading",
            background=[("active", p["surface_alt"])],
            foreground=[("active", p["text"])],
        )
        style.map(
            "Treeview",
            background=[("selected", p["selected"])],
            foreground=[("selected", p["text"])],
        )

        # Notebook tabs
        style.configure(
            "TNotebook",
            background=p["bg"],
            borderwidth=0,
            tabmargins=(0, 4, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=p["bg"],
            foreground=p["muted"],
            padding=(16, 8),
            borderwidth=0,
            font=_FONT,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", p["bg"]), ("active", p["surface"])],
            foreground=[("selected", p["accent"]), ("active", p["text"])],
            expand=[("selected", (0, 0, 0, 0))],
        )

        # Scrollbar - thinner, less obtrusive
        style.configure(
            "TScrollbar",
            background=p["surface"],
            bordercolor=p["surface"],
            arrowcolor=p["muted"],
            troughcolor=p["bg"],
            relief="flat",
            gripcount=0,
        )
        style.map(
            "TScrollbar",
            background=[("active", p["border_strong"])],
            arrowcolor=[("active", p["text"])],
        )

        # Separator
        style.configure("TSeparator", background=p["border"])

        # Checkbutton
        style.configure(
            "TCheckbutton",
            background=p["bg"],
            foreground=p["text"],
            font=_FONT,
        )
        style.map(
            "TCheckbutton",
            background=[("active", p["bg"])],
        )

        # Labelframe (just in case used elsewhere)
        style.configure(
            "TLabelframe",
            background=p["bg"],
            bordercolor=p["border"],
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=p["bg"],
            foreground=p["muted"],
            font=_FONT_SEMI,
        )

        # Apply default font to plain tk widgets too
        self.option_add("*Font", _FONT)
        self.option_add("*Menu.Font", _FONT)
        self.option_add("*TCombobox*Listbox.Font", _FONT)

    def _add_tb_button(self, parent, text, command, style_name="Toolbar.TButton", pad=(0, 2)):
        btn = ttk.Button(parent, text=text, command=command, style=style_name)
        btn.pack(side=tk.LEFT, padx=pad[0:1] + pad[1:])
        return btn

    def _toolbar_button(self, parent, text, command, accent=False, icon=False):
        if accent:
            style_name = "Accent.TButton"
        elif icon:
            style_name = "Icon.TButton"
        else:
            style_name = "Toolbar.TButton"
        btn = ttk.Button(parent, text=text, command=command, style=style_name)
        btn.pack(side=tk.LEFT, padx=(0, 2))
        return btn

    def _toolbar_separator(self, parent):
        wrap = ttk.Frame(parent, style="Toolbar.TFrame")
        wrap.pack(side=tk.LEFT, padx=8, fill=tk.Y)
        sep = ttk.Separator(wrap, orient=tk.VERTICAL)
        sep.pack(fill=tk.Y, pady=4)
        return sep

    def focus_search(self):
        """Move keyboard focus to the search field."""
        if hasattr(self, "filter_entry"):
            self.filter_entry.focus_set()
            self.filter_entry.select_range(0, tk.END)

    def _get_dot_image(self, color):
        """Return a small filled square PhotoImage of the given color, cached."""
        if color in self._dot_images:
            return self._dot_images[color]

        size = 10
        img = tk.PhotoImage(width=size, height=size)
        # Leave a 2px transparent border, fill the inner 6x6 with color.
        for y in range(2, size - 2):
            for x in range(2, size - 2):
                img.put(color, (x, y))
        self._dot_images[color] = img
        return img

    def _status_dot_for_node(self, node):
        """Return the dot image that represents this node's state, or None."""
        if not node.item:
            return None

        # Validation outranks edit state.
        if any(msg.severity == "error" for msg in node.item.validation):
            return self._get_dot_image("#dc2626")
        if any(msg.severity == "warning" for msg in node.item.validation):
            return self._get_dot_image("#d97706")

        if node.item.state == "new":
            return self._get_dot_image("#16a34a")
        if node.item.state == "modified":
            return self._get_dot_image("#2563eb")
        if node.item.state == "deleted":
            return self._get_dot_image("#9ca3af")
        return None

    def _auto_commit_common_fields(self, _event=None):
        """Silently commit any changed common field edits."""
        if not self.details_local_id or not self.model:
            return
        try:
            self.apply_common_field_edits(quiet=True)
        except Exception:
            pass

    def _auto_commit_raw_field(self, _event=None):
        """Silently commit any changed raw field edit."""
        if not self.details_local_id or not self.model:
            return
        try:
            self.apply_raw_field_edit(quiet=True)
        except Exception:
            pass

    def create_menu(self):
        """The menu bar is the single canonical command surface.

        Every action in the app appears here. Frequent actions also have
        keyboard shortcuts (shown next to the menu entry) and a duplicate
        in the row right-click context menu.
        """
        menu_bar = tk.Menu(self)

        # ---- File ----
        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(
            label="Open CSV...",
            command=self.open_csv_dialog,
            accelerator="Ctrl+O",
        )
        file_menu.add_command(
            label="Open Project...",
            command=self.open_project_dialog,
            accelerator="Ctrl+Shift+O",
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Save Project",
            command=self.save_project,
            accelerator="Ctrl+S",
        )
        file_menu.add_command(
            label="Save Project As...",
            command=self.save_project_as,
            accelerator="Ctrl+Shift+S",
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Export Azure Tree CSV...",
            command=self.export_azure_tree_csv_dialog,
        )
        file_menu.add_command(
            label="Export Round-Trip CSV...",
            command=self.export_round_trip_csv_dialog,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)

        # ---- Edit (work item operations) ----
        edit_menu = tk.Menu(menu_bar, tearoff=False)
        edit_menu.add_command(
            label="New Root Item",
            command=self.add_root_item,
            accelerator="Ctrl+N",
        )
        edit_menu.add_command(
            label="New Sibling",
            command=self.add_sibling_item,
            accelerator="Enter",
        )
        edit_menu.add_command(
            label="New Child",
            command=self.add_child_item,
            accelerator="Shift+Enter",
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Rename...",
            command=self.edit_selected_title,
            accelerator="F2",
        )
        edit_menu.add_command(
            label="Delete / Restore",
            command=self.toggle_delete_selected,
            accelerator="Del",
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Move Up",
            command=self.move_selected_up,
            accelerator="Ctrl+↑",
        )
        edit_menu.add_command(
            label="Move Down",
            command=self.move_selected_down,
            accelerator="Ctrl+↓",
        )
        edit_menu.add_command(
            label="Indent",
            command=self.indent_selected,
            accelerator="Tab",
        )
        edit_menu.add_command(
            label="Outdent",
            command=self.outdent_selected,
            accelerator="Shift+Tab",
        )
        edit_menu.add_command(
            label="Promote to Root",
            command=self.make_selected_root,
        )

        # ---- View ----
        view_menu = tk.Menu(menu_bar, tearoff=False)
        view_menu.add_command(
            label="Focus Search",
            command=self.focus_search,
            accelerator="Ctrl+F",
        )
        view_menu.add_command(
            label="Clear Filter",
            command=self.clear_filter,
            accelerator="Esc",
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="Expand All",
            command=self.expand_all,
        )
        view_menu.add_command(
            label="Collapse All",
            command=self.collapse_all,
        )
        view_menu.add_separator()
        view_menu.add_command(label="Columns...", command=self.show_column_chooser)
        view_menu.add_separator()
        view_menu.add_command(
            label="Validate Now",
            command=self.validate_model,
        )

        # ---- Azure DevOps ----
        ado_menu = tk.Menu(menu_bar, tearoff=False)
        ado_menu.add_command(
            label="Connection Settings...",
            command=self.show_connection_settings_dialog,
        )
        ado_menu.add_command(
            label="Test Connection",
            command=self.test_ado_connection,
        )
        ado_menu.add_separator()
        ado_menu.add_command(
            label="Publish Preview...",
            command=self.show_publish_preview_dialog,
        )
        ado_menu.add_command(
            label="Dry Run (Validate Only)...",
            command=self.run_publish_dry_run,
        )
        ado_menu.add_command(
            label="Publish to Azure DevOps...",
            command=self.run_live_publish,
        )
        ado_menu.add_separator()
        ado_menu.add_command(
            label="Fetch Work Item Types",
            command=self.fetch_work_item_types,
        )
        ado_menu.add_command(
            label="Fetch Fields",
            command=self.fetch_ado_fields,
        )

        menu_bar.add_cascade(label="File", menu=file_menu)
        menu_bar.add_cascade(label="Edit", menu=edit_menu)
        menu_bar.add_cascade(label="View", menu=view_menu)
        menu_bar.add_cascade(label="Azure DevOps", menu=ado_menu)

        self.config(menu=menu_bar)

    def bind_keyboard_shortcuts(self):
        def handled(command):
            def callback(_event):
                command()
                return "break"

            return callback

        # Tree-focused shortcuts (outliner contract)
        self.tree.bind("<F2>", handled(self.edit_selected_title))
        self.tree.bind("<Delete>", handled(self.toggle_delete_selected))
        self.tree.bind("<Return>", handled(self.add_sibling_item))
        self.tree.bind("<Shift-Return>", handled(self.add_child_item))
        self.tree.bind("<Tab>", handled(self.indent_selected))
        self.tree.bind("<Shift-Tab>", handled(self.outdent_selected))
        # Some platforms emit <Shift-Tab> as <ISO_Left_Tab>; bind both.
        self.tree.bind("<ISO_Left_Tab>", handled(self.outdent_selected))
        self.tree.bind("<Control-Up>", handled(self.move_selected_up))
        self.tree.bind("<Control-Down>", handled(self.move_selected_down))

        # Application-wide shortcuts
        self.bind_all("<Control-s>", handled(self.save_project))
        self.bind_all("<Control-S>", handled(self.save_project_as))
        self.bind_all("<Control-o>", handled(self.open_csv_dialog))
        self.bind_all("<Control-O>", handled(self.open_project_dialog))
        self.bind_all("<Control-n>", handled(self.add_root_item))
        self.bind_all("<Control-f>", handled(self.focus_search))
        self.bind_all("<Escape>", handled(self.clear_filter))

    def create_widgets(self):
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)

        # ---------- Status bar (packed first so it docks to the bottom) ----------
        self.status_var = tk.StringVar(value="Open an Azure DevOps CSV export to begin.")

        status_sep = ttk.Separator(outer, orient=tk.HORIZONTAL)
        status_sep.pack(side=tk.BOTTOM, fill=tk.X)

        status_bar = ttk.Frame(outer, style="Status.TFrame")
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(
            status_bar,
            textvariable=self.status_var,
            style="Status.TLabel",
        ).pack(side=tk.LEFT, padx=14, pady=6)

        # ---------- Header strip: filename + dirty indicator + search ----------
        header = ttk.Frame(outer, style="Toolbar.TFrame")
        header.pack(side=tk.TOP, fill=tk.X)

        header_inner = ttk.Frame(header, style="Toolbar.TFrame")
        header_inner.pack(fill=tk.X, padx=14, pady=10)

        self.file_label_var = tk.StringVar(value="No file open")
        ttk.Label(
            header_inner,
            textvariable=self.file_label_var,
            style="HeaderFile.TLabel",
        ).pack(side=tk.LEFT)

        self.dirty_indicator_var = tk.StringVar(value="")
        ttk.Label(
            header_inner,
            textvariable=self.dirty_indicator_var,
            style="Toolbar.TLabel",
        ).pack(side=tk.LEFT, padx=(8, 0))

        # Search on the right
        search_wrap = ttk.Frame(header_inner, style="Toolbar.TFrame")
        search_wrap.pack(side=tk.RIGHT)

        ttk.Label(
            search_wrap,
            text="Search",
            style="Toolbar.TLabel",
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(
            search_wrap, textvariable=self.filter_var, width=36, font=_FONT
        )
        self.filter_entry.pack(side=tk.LEFT, ipady=3)
        self.filter_entry.bind("<Return>", lambda event: self.apply_filter())
        self.filter_entry.bind("<Escape>", lambda event: self.clear_filter())
        # Live filtering as the user types
        self.filter_var.trace_add("write", self._on_filter_var_changed)

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(side=tk.TOP, fill=tk.X)

        # ---------- Main content: tree on the left, inspector on the right ----------
        content = ttk.Frame(outer)
        content.pack(fill=tk.BOTH, expand=True)

        paned = ttk.PanedWindow(content, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        tree_frame = ttk.Frame(paned)
        details_frame = ttk.Frame(paned)

        paned.add(tree_frame, weight=3)
        paned.add(details_frame, weight=2)

        columns = tuple(column_id for column_id, *_rest in self.tree_column_specs)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
        )

        self.tree.heading("#0", text="Title")

        self.tree.column("#0", width=420, minwidth=250, stretch=True)

        for column_id, heading, width, minwidth, stretch in self.tree_column_specs:
            self.tree.heading(column_id, text=heading)
            self.tree.column(
                column_id,
                width=width,
                minwidth=minwidth,
                stretch=stretch,
            )

        self.apply_visible_tree_columns()

        y_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        x_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)

        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.tag_configure("new", foreground="#166534")
        self.tree.tag_configure("modified", foreground="#92400e")
        self.tree.tag_configure("deleted", foreground="#6b7280")
        self.tree.tag_configure("validation_error", background="#fee2e2")
        self.tree.tag_configure("validation_warning", background="#fef3c7")

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<<TreeviewOpen>>", self.on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self.on_tree_close)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.show_tree_context_menu)
        self.tree.bind("<Button-2>", self.show_tree_context_menu)

        self.details_title_var = tk.StringVar(value="Details")
        details_header = ttk.Frame(details_frame)
        details_header.pack(fill=tk.X, pady=(8, 0))
        details_label = ttk.Label(
            details_header,
            textvariable=self.details_title_var,
            style="Heading.TLabel",
        )
        details_label.pack(side=tk.LEFT, anchor="w")

        self.details_notebook = ttk.Notebook(details_frame)
        self.details_notebook.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        common_tab = ttk.Frame(self.details_notebook)
        raw_tab = ttk.Frame(self.details_notebook)
        validation_tab = ttk.Frame(self.details_notebook)

        self.details_notebook.add(common_tab, text="Common Fields")
        self.details_notebook.add(raw_tab, text="Raw Fields")
        self.details_notebook.add(validation_tab, text="Validation")

        self.common_form_frame = ttk.Frame(common_tab)
        self.common_form_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # Common fields commit on focus loss; no Apply button needed.

        raw_split = ttk.PanedWindow(raw_tab, orient=tk.HORIZONTAL)
        raw_split.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        raw_list_frame = ttk.Frame(raw_split)
        raw_editor_frame = ttk.Frame(raw_split)
        raw_split.add(raw_list_frame, weight=2)
        raw_split.add(raw_editor_frame, weight=3)

        self.raw_fields_tree = ttk.Treeview(
            raw_list_frame,
            columns=("field", "value"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        self.raw_fields_tree.heading("field", text="Field")
        self.raw_fields_tree.heading("value", text="Value")
        self.raw_fields_tree.column("field", width=220, minwidth=140, stretch=False)
        self.raw_fields_tree.column("value", width=420, minwidth=180, stretch=True)
        self.raw_fields_tree.grid(row=0, column=0, sticky="nsew")
        self.raw_fields_tree.bind("<<TreeviewSelect>>", self.on_raw_field_select)

        raw_y_scroll = ttk.Scrollbar(
            raw_list_frame,
            orient=tk.VERTICAL,
            command=self.raw_fields_tree.yview,
        )
        raw_y_scroll.grid(row=0, column=1, sticky="ns")
        self.raw_fields_tree.configure(yscrollcommand=raw_y_scroll.set)
        raw_list_frame.rowconfigure(0, weight=1)
        raw_list_frame.columnconfigure(0, weight=1)

        self.raw_field_name_var = tk.StringVar(value="Select a field")
        ttk.Label(
            raw_editor_frame,
            textvariable=self.raw_field_name_var,
            style="Muted.TLabel",
        ).pack(anchor="w", padx=(12, 0))
        self.raw_value_text = tk.Text(
            raw_editor_frame,
            height=6,
            wrap=tk.WORD,
            font=_FONT,
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightcolor=_PALETTE["accent"],
            highlightbackground=_PALETTE["border"],
            background="#ffffff",
            foreground=_PALETTE["text"],
            insertbackground=_PALETTE["text"],
            padx=8,
            pady=6,
        )
        self.raw_value_text.pack(fill=tk.BOTH, expand=True, padx=(12, 0), pady=(6, 12))
        # Raw value commits on focus loss; no Apply button needed.
        self.raw_value_text.bind("<FocusOut>", lambda _e: self._auto_commit_raw_field())

        self.validation_text = tk.Text(
            validation_tab,
            height=8,
            wrap=tk.WORD,
            font=_FONT,
            relief="flat",
            borderwidth=0,
            background=_PALETTE["bg"],
            foreground=_PALETTE["text"],
            padx=12,
            pady=10,
        )
        self.validation_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.validation_text.configure(state=tk.DISABLED)
        self.clear_details()

    def apply_visible_tree_columns(self):
        if not hasattr(self, "tree"):
            return

        known_columns = {column_id for column_id, *_rest in self.tree_column_specs}
        self.visible_tree_columns = [
            column_id
            for column_id in self.visible_tree_columns
            if column_id in known_columns
        ]
        self.tree.configure(displaycolumns=tuple(self.visible_tree_columns))

    def show_column_chooser(self):
        if self.column_dialog and self.column_dialog.winfo_exists():
            self.column_dialog.lift()
            self.column_dialog.focus_set()
            return

        dialog = tk.Toplevel(self)
        dialog.title("Choose Columns")
        dialog.transient(self)
        dialog.resizable(False, True)
        dialog.protocol("WM_DELETE_WINDOW", self.close_column_chooser)

        self.column_dialog = dialog
        self.column_chooser_vars = {}

        content = ttk.Frame(dialog)
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        for row_index, (column_id, heading, _width, _minwidth, _stretch) in enumerate(self.tree_column_specs):
            var = tk.BooleanVar(value=column_id in self.visible_tree_columns)
            checkbutton = ttk.Checkbutton(content, text=heading, variable=var)
            checkbutton.grid(row=row_index, column=0, sticky="w", pady=2)
            self.column_chooser_vars[column_id] = var

        button_bar = ttk.Frame(content)
        button_bar.grid(
            row=len(self.tree_column_specs),
            column=0,
            sticky="ew",
            pady=(12, 0),
        )

        ttk.Button(
            button_bar,
            text="Show All",
            command=lambda: self.set_column_chooser_vars(True),
        ).pack(side=tk.LEFT)
        ttk.Button(
            button_bar,
            text="Hide All",
            command=lambda: self.set_column_chooser_vars(False),
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(
            button_bar,
            text="Reset",
            command=self.reset_column_chooser_vars,
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(
            button_bar,
            text="Apply",
            command=self.apply_column_chooser,
        ).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Button(
            button_bar,
            text="Close",
            command=self.close_column_chooser,
        ).pack(side=tk.LEFT, padx=(4, 0))

        dialog.update_idletasks()
        x = self.winfo_rootx() + max((self.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.winfo_rooty() + 80
        dialog.geometry(f"+{x}+{y}")
        dialog.focus_set()

    def set_column_chooser_vars(self, visible):
        for var in self.column_chooser_vars.values():
            var.set(visible)

    def reset_column_chooser_vars(self):
        default_columns = {column_id for column_id, *_rest in self.tree_column_specs}

        for column_id, var in self.column_chooser_vars.items():
            var.set(column_id in default_columns)

    def apply_column_chooser(self):
        selected_columns = [
            column_id
            for column_id, *_rest in self.tree_column_specs
            if self.column_chooser_vars.get(column_id)
            and self.column_chooser_vars[column_id].get()
        ]
        self.visible_tree_columns = selected_columns
        self.apply_visible_tree_columns()
        self.status_var.set(f"Showing {len(selected_columns)} tree data columns.")

    def close_column_chooser(self):
        if self.column_dialog and self.column_dialog.winfo_exists():
            self.column_dialog.destroy()

        self.column_dialog = None
        self.column_chooser_vars = {}

    def open_csv_dialog(self):
        path = filedialog.askopenfilename(
            title="Open Azure DevOps Work Items CSV",
            filetypes=[
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
        )

        if path:
            self.load_csv(path)

    def open_project_dialog(self):
        path = filedialog.askopenfilename(
            title="Open ADOViewer Project",
            filetypes=[
                ("ADOViewer projects", "*.adoviewer.json"),
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if path:
            self.load_project(path)

    def load_initial_path(self, path):
        if path.lower().endswith(".adoviewer.json"):
            self.load_project(path)
        else:
            self.load_csv(path)

    def load_csv(self, path):
        try:
            fieldnames, rows = read_csv_file(path)
            self.model = WorkItemModel(fieldnames, rows)
            self.current_path = path
            self.project_path = None
            self.source_path = path
            self.filter_var.set("")
            self.reset_expansion_state()
            self.populate_tree(capture_expansion=False)
            self.title(f"Azure DevOps Work Items Viewer - {os.path.basename(path)}")
            self.update_status()

        except Exception as ex:
            messagebox.showerror("Error", str(ex))

    def load_project(self, path):
        try:
            document = load_project_file(path)
            self.model = document.model
            self.current_path = path
            self.project_path = path
            self.source_path = document.source_path
            self.filter_var.set("")
            self.reset_expansion_state()
            self.populate_tree(capture_expansion=False)
            self.title(f"Azure DevOps Work Items Viewer - {os.path.basename(path)}")
            self.update_status()

        except Exception as ex:
            messagebox.showerror("Error", str(ex))

    def save_project(self):
        if not self.model:
            messagebox.showinfo("Save Project", "Open a CSV or add a root work item first.")
            return

        if not self.project_path:
            self.save_project_as()
            return

        self.write_project(self.project_path)

    def save_project_as(self):
        if not self.model:
            messagebox.showinfo("Save Project As", "Open a CSV or add a root work item first.")
            return

        path = filedialog.asksaveasfilename(
            title="Save ADOViewer Project",
            defaultextension=".adoviewer.json",
            initialfile=self.default_project_filename(),
            filetypes=[
                ("ADOViewer projects", "*.adoviewer.json"),
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if path:
            self.write_project(path)

    def default_project_filename(self):
        path = self.project_path or self.current_path

        if not path:
            return "work-items.adoviewer.json"

        base_name = os.path.basename(path)

        if base_name.lower().endswith(".adoviewer.json"):
            return base_name

        stem, _extension = os.path.splitext(base_name)
        return f"{stem}.adoviewer.json"

    def write_project(self, path):
        try:
            save_project_file(self.model, path, source_path=self.source_path)
            self.project_path = path
            self.current_path = path
            self.title(f"Azure DevOps Work Items Viewer - {os.path.basename(path)}")
            self.update_status()
            self.status_var.set(f"Saved project {os.path.basename(path)}.")

        except Exception as ex:
            messagebox.showerror("Error", str(ex))

    def export_azure_tree_csv_dialog(self):
        self.show_export_preview(
            "Export Azure Tree CSV",
            "azure-tree",
            build_azure_tree_csv,
        )

    def export_round_trip_csv_dialog(self):
        self.show_export_preview(
            "Export Round-Trip CSV",
            "round-trip",
            build_round_trip_csv,
        )

    def show_export_preview(self, title, suffix, build_export):
        if not self.model:
            messagebox.showinfo(title, "Open a CSV or add a root work item first.")
            return

        try:
            fieldnames, rows = build_export(self.model)

        except CsvExportError as ex:
            messagebox.showerror(title, str(ex))
            self.populate_tree(self._current_filter_text())
            self.update_status()
            return

        except Exception as ex:
            messagebox.showerror("Error", str(ex))
            return

        dialog = tk.Toplevel(self)
        dialog.title(f"{title} Preview")
        dialog.geometry("1000x650")
        dialog.minsize(720, 420)
        dialog.transient(self)

        summary = ttk.Label(
            dialog,
            text=f"{len(rows)} work items, {len(fieldnames)} columns",
        )
        summary.pack(anchor="w", padx=12, pady=(12, 4))

        text_frame = ttk.Frame(dialog)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        preview_text = tk.Text(text_frame, wrap=tk.NONE)
        preview_text.grid(row=0, column=0, sticky="nsew")
        preview_text.insert(tk.END, render_csv_text(fieldnames, rows))
        preview_text.configure(state=tk.DISABLED)

        y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=preview_text.yview)
        x_scroll = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=preview_text.xview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        preview_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        button_bar = ttk.Frame(dialog)
        button_bar.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(
            button_bar,
            text="Save...",
            command=lambda: self.save_preview_export(dialog, title, suffix, fieldnames, rows),
        ).pack(side=tk.LEFT)
        ttk.Button(
            button_bar,
            text="Close",
            command=dialog.destroy,
        ).pack(side=tk.LEFT, padx=(4, 0))

    def save_preview_export(self, dialog, title, suffix, fieldnames, rows):
        path = filedialog.asksaveasfilename(
            title=title,
            defaultextension=".csv",
            initialfile=self.default_export_filename(suffix),
            filetypes=[
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
        )

        if not path:
            return

        try:
            write_csv_rows(path, fieldnames, rows)
            self.status_var.set(
                f"Exported {len(rows)} work items and {len(fieldnames)} columns to {os.path.basename(path)}."
            )
            dialog.destroy()

        except Exception as ex:
            messagebox.showerror("Error", str(ex))

    def default_export_filename(self, suffix):
        path = self.project_path or self.current_path

        if not path:
            return f"work-items-{suffix}.csv"

        base_name = os.path.basename(path)

        if base_name.lower().endswith(".adoviewer.json"):
            base_name = base_name[:-len(".adoviewer.json")]
        else:
            base_name = os.path.splitext(base_name)[0]

        return f"{base_name}-{suffix}.csv"


    def clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.tree_item_to_node.clear()

    def expansion_key_for_node(self, node):
        if node.item:
            return f"item:{node.item.local_id}"

        return f"synthetic:{node.key}"

    def reset_expansion_state(self):
        self.expanded_node_keys = set()
        self.displayed_filter_text = ""

        if not self.model:
            return

        def visit(node):
            if node.children:
                self.expanded_node_keys.add(self.expansion_key_for_node(node))

            for child in node.children:
                visit(child)

        for child in self.model.root.children:
            visit(child)

    def capture_current_expansion(self):
        visible_keys = set()
        expanded_keys = set()

        def visit(tree_item):
            node = self.tree_item_to_node.get(tree_item)

            if node:
                key = self.expansion_key_for_node(node)
                visible_keys.add(key)
                if self.tree.item(tree_item, "open"):
                    expanded_keys.add(key)

            for child in self.tree.get_children(tree_item):
                visit(child)

        for item in self.tree.get_children():
            visit(item)

        self.expanded_node_keys.difference_update(visible_keys)
        self.expanded_node_keys.update(expanded_keys)

    def node_has_matching_descendant(self, node, filter_text):
        return any(
            self.node_matches_filter(child, filter_text)
            for child in node.children
        )

    def should_open_node(self, node, filter_text):
        key = self.expansion_key_for_node(node)

        if key in self.expanded_node_keys:
            return True

        return bool(filter_text and self.node_has_matching_descendant(node, filter_text))

    def remember_tree_item_expansion(self, tree_item, is_open):
        node = self.tree_item_to_node.get(tree_item)

        if not node:
            return

        key = self.expansion_key_for_node(node)

        if is_open:
            self.expanded_node_keys.add(key)
        else:
            self.expanded_node_keys.discard(key)

    def remember_open_ancestors(self, tree_item):
        parent = self.tree.parent(tree_item)

        while parent:
            self.remember_tree_item_expansion(parent, True)
            parent = self.tree.parent(parent)

    def populate_tree(self, filter_text="", select_local_id=None, capture_expansion=True):
        if select_local_id is None:
            select_local_id = self.selected_local_id()

        if capture_expansion and not self.displayed_filter_text:
            self.capture_current_expansion()

        filter_text = filter_text.strip().lower()
        self.updating_tree = True

        try:
            self.clear_tree()

            if not self.model:
                self.displayed_filter_text = filter_text
                return

            for child in self.model.root.children:
                self.insert_node_if_matching("", child, filter_text)

        finally:
            self.updating_tree = False
            self.displayed_filter_text = filter_text

        if select_local_id:
            if not self.select_local_id(select_local_id):
                self.clear_details()
        else:
            self.clear_details()

    def node_matches_filter(self, node, filter_text):
        if not filter_text:
            return True

        # Synthetic group nodes should match if any child matches.
        searchable_parts = []

        if node.synthetic:
            searchable_parts.append(str(node.row.get("Synthetic Title", "")))
            searchable_parts.append(str(node.row.get("Synthetic Type", "")))
        else:
            for value in node.row.values():
                searchable_parts.append(str(value))

        searchable_text = "\n".join(searchable_parts).lower()

        if filter_text in searchable_text:
            return True

        for child in node.children:
            if self.node_matches_filter(child, filter_text):
                return True

        return False

    def insert_node_if_matching(self, parent_tree_item, node, filter_text):
        if not self.node_matches_filter(node, filter_text):
            return None

        item_id = self.insert_node(parent_tree_item, node, filter_text)

        for child in node.children:
            self.insert_node_if_matching(item_id, child, filter_text)

        return item_id

    def insert_node(self, parent_tree_item, node, filter_text):
        if node.synthetic:
            title = node.row.get("Synthetic Title", "Group")
            values = (
                "",
                node.row.get("Synthetic Type", ""),
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            )
        else:
            title = self.model.row_title(node.row)
            values = (
                self.model.row_id(node.row),
                self.model.row_type(node.row),
                self.model.row_state(node.row),
                self.model.row_assigned_to(node.row),
                self.model.row_effort(node.row),
                self.model.row_remaining(node.row),
                self.model.row_completed(node.row),
                self.model.row_area(node.row),
                self.model.row_iteration(node.row),
                self.model.row_tags(node.row),
            )

        dot_image = self._status_dot_for_node(node) if not node.synthetic else None

        insert_kwargs = dict(
            text=title,
            values=values,
            tags=self.node_tags(node),
            open=self.should_open_node(node, filter_text),
        )
        if dot_image is not None:
            insert_kwargs["image"] = dot_image

        tree_item = self.tree.insert(
            parent_tree_item,
            tk.END,
            **insert_kwargs,
        )

        self.tree_item_to_node[tree_item] = node
        return tree_item

    def apply_filter(self):
        self.populate_tree(self._current_filter_text())

    def _on_filter_var_changed(self, *_args):
        """Live filter; debounced so we don't repopulate on every keystroke."""
        if getattr(self, "_filter_after_id", None):
            try:
                self.after_cancel(self._filter_after_id)
            except Exception:
                pass

        self._filter_after_id = self.after(180, self.apply_filter)

    def _current_filter_text(self):
        return self.filter_var.get()

    def clear_filter(self):
        self.filter_var.set("")
        self.populate_tree("")

    def expand_all(self):
        for item in self.tree.get_children():
            self.set_open_recursive(item, True)
        self.capture_current_expansion()

    def collapse_all(self):
        for item in self.tree.get_children():
            self.set_open_recursive(item, False)
        self.capture_current_expansion()

    def set_open_recursive(self, item, open_value):
        self.tree.item(item, open=open_value)
        self.remember_tree_item_expansion(item, open_value)

        for child in self.tree.get_children(item):
            self.set_open_recursive(child, open_value)

    def on_tree_select(self, event):
        selection = self.tree.selection()

        if not selection:
            self.clear_details()
            return

        item = selection[0]
        node = self.tree_item_to_node.get(item)

        self.show_details(node)

    def on_tree_open(self, _event):
        if self.updating_tree:
            return

        tree_item = self.tree.focus()

        if tree_item:
            self.remember_tree_item_expansion(tree_item, True)

    def on_tree_close(self, _event):
        if self.updating_tree:
            return

        tree_item = self.tree.focus()

        if tree_item:
            self.remember_tree_item_expansion(tree_item, False)

    def show_tree_context_menu(self, event):
        tree_item = self.tree.identify_row(event.y)

        if tree_item:
            self.tree.selection_set(tree_item)
            self.tree.focus(tree_item)
            node = self.tree_item_to_node.get(tree_item)
            self.show_details(node)
        else:
            self.tree.selection_remove(self.tree.selection())
            node = None
            self.clear_details()

        menu = tk.Menu(self, tearoff=False)
        real_item_selected = bool(node and node.item)
        synthetic_selected = bool(node and node.synthetic)
        can_add_root = bool(self.model) or not node
        has_url = bool(real_item_selected and self.model and self.model.row_url(node.row))
        state_for_real = tk.NORMAL if real_item_selected else tk.DISABLED
        state_for_add_root = tk.NORMAL if can_add_root else tk.DISABLED
        state_for_validate = tk.NORMAL if self.model else tk.DISABLED
        state_for_url = tk.NORMAL if has_url else tk.DISABLED

        menu.add_command(
            label="New Root Item",
            command=self.add_root_item,
            state=state_for_add_root,
        )
        menu.add_command(
            label="New Sibling",
            command=self.add_sibling_item,
            state=state_for_real,
        )
        menu.add_command(
            label="New Child",
            command=self.add_child_item,
            state=state_for_real,
        )
        menu.add_separator()
        menu.add_command(
            label="Rename...",
            command=self.edit_selected_title,
            state=state_for_real,
        )

        delete_label = "Delete / Restore"
        if real_item_selected and node.item.state == "deleted":
            delete_label = "Restore"
        elif real_item_selected:
            delete_label = "Delete"

        menu.add_command(
            label=delete_label,
            command=self.toggle_delete_selected,
            state=state_for_real,
        )
        menu.add_separator()
        menu.add_command(
            label="Move Up",
            command=self.move_selected_up,
            state=state_for_real,
        )
        menu.add_command(
            label="Move Down",
            command=self.move_selected_down,
            state=state_for_real,
        )
        menu.add_command(
            label="Indent",
            command=self.indent_selected,
            state=state_for_real,
        )
        menu.add_command(
            label="Outdent",
            command=self.outdent_selected,
            state=state_for_real,
        )
        menu.add_command(
            label="Make Root",
            command=self.make_selected_root,
            state=state_for_real,
        )
        menu.add_separator()
        menu.add_command(
            label="Open URL",
            command=self.open_selected_url,
            state=state_for_url,
        )
        menu.add_command(
            label="Validate",
            command=self.validate_model,
            state=state_for_validate,
        )

        if synthetic_selected:
            menu.add_separator()
            menu.add_command(label="Grouping node - select a work item to edit", state=tk.DISABLED)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def show_details(self, node):
        if not node:
            self.clear_details()
            return

        if node.synthetic:
            title = node.row.get("Synthetic Title", "Group")
            self.clear_details(f"Synthetic grouping node: {title}")
            return

        self.details_local_id = node.item.local_id
        self.details_title_var.set(f"Details - {node.item.title or '(untitled)'}")
        self.populate_common_fields(node)
        self.populate_raw_fields(node)
        self.populate_validation_details(node)

    def clear_details(self, message="Select a work item to edit its fields."):
        self.details_local_id = None
        self.details_title_var.set("Details")
        self.common_field_vars = []
        self.raw_field_items = {}

        for child in self.common_form_frame.winfo_children():
            child.destroy()

        ttk.Label(self.common_form_frame, text=message).grid(row=0, column=0, sticky="w")

        for item in self.raw_fields_tree.get_children():
            self.raw_fields_tree.delete(item)

        self.raw_field_name_var.set("Select a field")
        self.raw_value_text.delete("1.0", tk.END)
        self.set_validation_text(message)

    def common_field_definitions(self, node):
        item = node.item
        assert item is not None

        definitions = [
            ("Title", "__title__", item.title),
            ("Work Item Type", self.model.type_col or "Work Item Type", item.work_item_type),
            ("State", self.model.state_col or "State", self.model.row_state(item.fields)),
            ("Assigned To", self.model.assigned_to_col or "Assigned To", self.model.row_assigned_to(item.fields)),
            ("Area Path", self.model.area_col or "Area Path", self.model.row_area(item.fields)),
            ("Iteration Path", self.model.iteration_col or "Iteration Path", self.model.row_iteration(item.fields)),
            ("Tags", self.model.tags_col or "Tags", self.model.row_tags(item.fields)),
        ]

        optional_columns = [
            ("Effort / Points / Estimate", self.model.effort_col, self.model.row_effort(item.fields)),
            ("Remaining Work", self.model.remaining_col, self.model.row_remaining(item.fields)),
            ("Completed Work", self.model.completed_col, self.model.row_completed(item.fields)),
        ]

        for label, field_name, value in optional_columns:
            if field_name:
                definitions.append((label, field_name, value))

        return definitions

    def populate_common_fields(self, node):
        for child in self.common_form_frame.winfo_children():
            child.destroy()

        self.common_field_vars = []

        for row_index, (label, field_name, value) in enumerate(self.common_field_definitions(node)):
            ttk.Label(
                self.common_form_frame,
                text=label,
                style="Muted.TLabel",
            ).grid(
                row=row_index,
                column=0,
                sticky="w",
                padx=(0, 12),
                pady=4,
            )
            var = tk.StringVar(value=value)
            entry = ttk.Entry(self.common_form_frame, textvariable=var, width=40)
            entry.grid(row=row_index, column=1, sticky="ew", pady=4)
            # Commit silently when focus leaves the field.
            entry.bind("<FocusOut>", self._auto_commit_common_fields)
            entry.bind("<Return>", self._auto_commit_common_fields)
            self.common_field_vars.append((field_name, var))

        self.common_form_frame.columnconfigure(1, weight=1)

    def populate_raw_fields(self, node):
        for item in self.raw_fields_tree.get_children():
            self.raw_fields_tree.delete(item)

        self.raw_field_items = {}
        self.raw_field_name_var.set("Select a field")
        self.raw_value_text.delete("1.0", tk.END)

        for field_name in self.model.fieldnames:
            value = str(node.row.get(field_name, ""))
            preview = value.replace("\r", "").replace("\n", "\\n")
            if len(preview) > 160:
                preview = preview[:157] + "..."
            tree_item = self.raw_fields_tree.insert("", tk.END, values=(field_name, preview))
            self.raw_field_items[tree_item] = field_name

    def populate_validation_details(self, node):
        item = node.item
        assert item is not None

        if not item.validation:
            self.set_validation_text("No validation messages for this work item.")
            return

        lines = []

        for message in item.validation:
            location = ""
            if message.field:
                location = f" ({message.field})"
            lines.append(f"{message.severity.upper()}: {message.message}{location}")

        self.set_validation_text("\n".join(lines))

    def set_validation_text(self, text):
        self.validation_text.configure(state=tk.NORMAL)
        self.validation_text.delete("1.0", tk.END)
        self.validation_text.insert(tk.END, text)
        self.validation_text.configure(state=tk.DISABLED)

    def on_raw_field_select(self, _event):
        selection = self.raw_fields_tree.selection()

        if not selection or not self.details_local_id or not self.model:
            return

        field_name = self.raw_field_items.get(selection[0])

        if not field_name:
            return

        node = self.model.get_node(self.details_local_id)

        if not node or not node.item:
            return

        self.raw_field_name_var.set(field_name)
        self.raw_value_text.delete("1.0", tk.END)
        self.raw_value_text.insert(tk.END, str(node.item.fields.get(field_name, "")))

    def select_raw_field(self, field_name):
        for tree_item, item_field_name in self.raw_field_items.items():
            if item_field_name == field_name:
                self.raw_fields_tree.selection_set(tree_item)
                self.raw_fields_tree.focus(tree_item)
                self.raw_fields_tree.see(tree_item)
                self.on_raw_field_select(None)
                return True

        return False

    def apply_common_field_edits(self, quiet=False):
        if not self.details_local_id or not self.model:
            if not quiet:
                messagebox.showinfo("Apply Common Fields", "Select a work item first.")
            return

        node = self.model.get_node(self.details_local_id)

        if not node or not node.item:
            if not quiet:
                messagebox.showinfo(
                    "Apply Common Fields",
                    "The selected work item is no longer available.",
                )
            return

        changed = False

        for field_name, var in self.common_field_vars:
            value = var.get()

            if field_name == "__title__":
                if value != node.item.title:
                    self.model.edit_title(node.item.local_id, value)
                    changed = True
                continue

            current_value = str(node.item.fields.get(field_name, ""))

            if value != current_value:
                self.model.edit_field(node.item.local_id, field_name, value)
                changed = True

        if changed:
            self.refresh_after_model_change(node.item.local_id)
        elif not quiet:
            self.status_var.set("No common field changes to apply.")

    def apply_raw_field_edit(self, quiet=False):
        if not self.details_local_id or not self.model:
            if not quiet:
                messagebox.showinfo("Apply Raw Field", "Select a work item first.")
            return

        selection = self.raw_fields_tree.selection()

        if not selection:
            if not quiet:
                messagebox.showinfo("Apply Raw Field", "Select a raw field first.")
            return

        field_name = self.raw_field_items.get(selection[0])

        if not field_name:
            return

        node = self.model.get_node(self.details_local_id)

        if not node or not node.item:
            if not quiet:
                messagebox.showinfo(
                    "Apply Raw Field",
                    "The selected work item is no longer available.",
                )
            return

        value = self.raw_value_text.get("1.0", "end-1c")

        if value == str(node.item.fields.get(field_name, "")):
            if not quiet:
                self.status_var.set("No raw field changes to apply.")
            return

        self.model.edit_field(node.item.local_id, field_name, value)
        self.refresh_after_model_change(node.item.local_id)
        self.select_raw_field(field_name)

    def selected_local_id(self):
        selection = self.tree.selection()

        if not selection:
            return None

        node = self.tree_item_to_node.get(selection[0])

        if not node or not node.item:
            return None

        return node.item.local_id

    def select_local_id(self, local_id):
        if not local_id:
            return

        for tree_item, node in self.tree_item_to_node.items():
            if node.item and node.item.local_id == local_id:
                self.tree.selection_set(tree_item)
                self.tree.focus(tree_item)
                self.tree.see(tree_item)
                if not self.displayed_filter_text:
                    self.remember_open_ancestors(tree_item)
                self.show_details(node)
                return True

        return False

    def selected_real_node(self, action_name):
        if not self.model:
            messagebox.showinfo(action_name, "Open a CSV or add a root work item first.")
            return None

        selection = self.tree.selection()

        if not selection:
            messagebox.showinfo(action_name, "Select a work item first.")
            return None

        node = self.tree_item_to_node.get(selection[0])

        if not node or not node.item:
            messagebox.showinfo(action_name, "Select a real work item, not a grouping node.")
            return None

        return node

    def ensure_model_for_add(self):
        if self.model:
            return True

        self.model = WorkItemModel(["ID", "Work Item Type", "Title", "State"], [])
        self.current_path = None
        self.project_path = None
        self.source_path = None
        self.filter_var.set("")
        self.reset_expansion_state()
        self.title("Azure DevOps Work Items Viewer - Untitled")
        self.populate_tree(capture_expansion=False)
        self.update_status()
        return True

    def prompt_work_item_values(self, dialog_title, initial_title="New Work Item", initial_type="Task"):
        title = simpledialog.askstring(
            dialog_title,
            "Title:",
            initialvalue=initial_title,
            parent=self,
        )

        if title is None:
            return None

        work_item_type = simpledialog.askstring(
            dialog_title,
            "Work Item Type:",
            initialvalue=initial_type,
            parent=self,
        )

        if work_item_type is None:
            return None

        return title.strip(), work_item_type.strip()

    def default_work_item_type(self, selected_node=None):
        if selected_node and selected_node.item and selected_node.item.work_item_type:
            return selected_node.item.work_item_type

        if self.model:
            for item in self.model.flatten(include_deleted=False):
                if item.work_item_type:
                    return item.work_item_type

        return "Task"

    def add_root_item(self):
        if not self.ensure_model_for_add():
            return

        values = self.prompt_work_item_values(
            "Add Root Work Item",
            initial_type=self.default_work_item_type(),
        )

        if values is None:
            return

        title, work_item_type = values
        node = self.model.add_root(title, work_item_type)
        self.refresh_after_model_change(node.item.local_id)

    def add_child_item(self):
        parent = self.selected_real_node("Add Child")

        if not parent:
            return

        values = self.prompt_work_item_values(
            "Add Child Work Item",
            initial_type=self.default_work_item_type(parent),
        )

        if values is None:
            return

        title, work_item_type = values
        node = self.model.add_child(parent.item.local_id, title, work_item_type)
        self.refresh_after_model_change(node.item.local_id)

    def add_sibling_item(self):
        sibling = self.selected_real_node("Add Sibling")

        if not sibling:
            return

        values = self.prompt_work_item_values(
            "Add Sibling Work Item",
            initial_type=self.default_work_item_type(sibling),
        )

        if values is None:
            return

        title, work_item_type = values
        node = self.model.add_sibling(sibling.item.local_id, title, work_item_type)
        self.refresh_after_model_change(node.item.local_id)

    def edit_selected_title(self):
        node = self.selected_real_node("Edit Title")

        if not node:
            return

        title = simpledialog.askstring(
            "Edit Title",
            "Title:",
            initialvalue=node.item.title,
            parent=self,
        )

        if title is None:
            return

        self.model.edit_title(node.item.local_id, title.strip())
        self.refresh_after_model_change(node.item.local_id)

    def toggle_delete_selected(self):
        node = self.selected_real_node("Delete / Restore")

        if not node:
            return

        if node.item.state == "deleted":
            self.model.restore(node.item.local_id)
        else:
            self.model.soft_delete(node.item.local_id)

        self.refresh_after_model_change(node.item.local_id)

    def move_selected_up(self):
        node = self.selected_real_node("Move Up")

        if not node:
            return

        changed = self.model.move_up(node.item.local_id)
        self.refresh_after_model_change(node.item.local_id)

        if not changed:
            self.status_var.set("Selected work item is already first among its siblings.")

    def move_selected_down(self):
        node = self.selected_real_node("Move Down")

        if not node:
            return

        changed = self.model.move_down(node.item.local_id)
        self.refresh_after_model_change(node.item.local_id)

        if not changed:
            self.status_var.set("Selected work item is already last among its siblings.")

    def indent_selected(self):
        node = self.selected_real_node("Indent")

        if not node:
            return

        changed = self.model.indent(node.item.local_id)
        self.refresh_after_model_change(node.item.local_id)

        if not changed:
            self.status_var.set("Selected work item cannot be indented.")

    def outdent_selected(self):
        node = self.selected_real_node("Outdent")

        if not node:
            return

        changed = self.model.outdent(node.item.local_id)
        self.refresh_after_model_change(node.item.local_id)

        if not changed:
            self.status_var.set("Selected work item cannot be outdented.")

    def make_selected_root(self):
        node = self.selected_real_node("Make Root")

        if not node:
            return

        if node.parent is self.model.root:
            self.status_var.set("Selected work item is already a root item.")
            return

        self.model.reparent(node.item.local_id, None)
        self.refresh_after_model_change(node.item.local_id)

    def validate_model(self):
        if not self.model:
            messagebox.showinfo("Validate", "Open a CSV or add a root work item first.")
            return

        messages = self.model.validate()
        self.populate_tree(self._current_filter_text())
        self.update_status()

        errors = [msg for msg in messages if msg.severity == "error"]
        warnings = [msg for msg in messages if msg.severity == "warning"]
        summary = f"{len(errors)} errors, {len(warnings)} warnings."

        if messages:
            preview = "\n".join(f"- {msg.severity}: {msg.message}" for msg in messages[:10])
            if len(messages) > 10:
                preview += f"\n- ... {len(messages) - 10} more"
            messagebox.showwarning("Validation Results", f"{summary}\n\n{preview}")
        else:
            messagebox.showinfo("Validation Results", "No validation problems found.")

    def refresh_after_model_change(self, select_local_id=None):
        if not self.model:
            return

        self.model.validate()
        self.populate_tree(self._current_filter_text(), select_local_id=select_local_id)
        self.update_status()

    def node_local_status(self, node):
        if not node.item:
            return ""

        status_parts = []

        if any(msg.severity == "error" for msg in node.item.validation):
            status_parts.append("Error")
        elif any(msg.severity == "warning" for msg in node.item.validation):
            status_parts.append("Warning")

        if node.item.state != "unchanged":
            status_parts.append(node.item.state.capitalize())

        return ", ".join(status_parts)

    def node_tags(self, node):
        """Row-level tags.

        The colored status dot already encodes new/modified/deleted state, so
        the row text doesn't need to be colored too — that would double-encode.
        Only deleted items get a muted strike feel, and validation errors get
        a subtle row tint so problems stand out at a glance.
        """
        if not node.item:
            return ()

        if any(msg.severity == "error" for msg in node.item.validation):
            return ("validation_error",)

        if any(msg.severity == "warning" for msg in node.item.validation):
            return ("validation_warning",)

        if node.item.state == "deleted":
            return ("deleted",)

        return ()

    def update_status(self):
        if not self.model:
            self.status_var.set("Open an Azure DevOps CSV export to begin.")
            if hasattr(self, "file_label_var"):
                self.file_label_var.set("No file open")
            if hasattr(self, "dirty_indicator_var"):
                self.dirty_indicator_var.set("")
            return

        source = os.path.basename(self.current_path) if self.current_path else "Untitled"
        item_count = len(self.model.flatten())
        counts = self.model.dirty_counts()
        errors = sum(1 for msg in self.model.validation_messages if msg.severity == "error")
        warnings = sum(1 for msg in self.model.validation_messages if msg.severity == "warning")
        dirty = counts["new"] + counts["modified"] + counts["deleted"]

        # Header strip: filename + a small dot when there are unsaved changes
        if hasattr(self, "file_label_var"):
            self.file_label_var.set(source)
        if hasattr(self, "dirty_indicator_var"):
            self.dirty_indicator_var.set("•  unsaved" if dirty else "")

        # Status bar: compact summary
        parts = [f"{item_count} items"]
        if dirty:
            parts.append(
                f"{dirty} unsaved  ({counts['new']} new · {counts['modified']} modified · {counts['deleted']} deleted)"
            )
        if errors or warnings:
            issue_bits = []
            if errors:
                issue_bits.append(f"{errors} error{'s' if errors != 1 else ''}")
            if warnings:
                issue_bits.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
            parts.append(" · ".join(issue_bits))
        else:
            parts.append("no issues")

        self.status_var.set("    ·    ".join(parts))

    def open_selected_url(self):
        node = self.selected_real_node("Open URL")

        if not node:
            return

        url = self.model.row_url(node.row)

        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo(
                "No URL",
                "This CSV row does not appear to contain a URL column."
            )

    def on_tree_double_click(self, event):
        selection = self.tree.selection()

        if not selection or not self.model:
            return

        item = selection[0]
        node = self.tree_item_to_node.get(item)

        if not node or node.synthetic:
            return

        self.open_selected_url()

    # ------------------------------------------------------------------
    # Azure DevOps connection settings
    # ------------------------------------------------------------------

    def _load_connection_settings(self) -> None:
        try:
            import json as _json
            with open(_ADO_SETTINGS_FILE, encoding="utf-8") as fh:
                data = _json.load(fh)
            org_url = data.get("org_url", "").strip()
            project = data.get("project", "").strip()
            if org_url and project:
                self.ado_connection = AdoConnectionSettings(org_url=org_url, project=project)
        except (FileNotFoundError, Exception):
            self.ado_connection = None

    def _save_connection_settings(self) -> None:
        import json as _json
        if not self.ado_connection:
            return
        data = {
            "org_url": self.ado_connection.org_url,
            "project": self.ado_connection.project,
        }
        try:
            with open(_ADO_SETTINGS_FILE, "w", encoding="utf-8") as fh:
                _json.dump(data, fh, indent=2)
        except Exception as exc:
            messagebox.showwarning("Settings", f"Could not save connection settings:\n{exc}")

    def _require_client(self) -> AdoClient | None:
        """Return a ready AdoClient, prompting for PAT if not set this session."""
        if not self.ado_connection:
            messagebox.showinfo(
                "Azure DevOps",
                "Configure connection settings first (Azure DevOps > Connection Settings).",
            )
            return None

        if not self._ado_pat:
            pat = simpledialog.askstring(
                "Azure DevOps PAT",
                "Enter your Personal Access Token (not saved):",
                show="*",
                parent=self,
            )
            if not pat:
                return None
            self._ado_pat = pat.strip()

        return AdoClient(self.ado_connection, self._ado_pat)

    def show_connection_settings_dialog(self) -> None:
        """Modal dialog to configure org URL and project (PAT not stored)."""
        dialog = tk.Toplevel(self)
        dialog.title("Azure DevOps Connection Settings")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.configure(background=_PALETTE["bg"])

        pad = {"padx": 12, "pady": 6}

        ttk.Label(dialog, text="Organization URL:", style="TLabel").grid(row=0, column=0, sticky="w", **pad)
        org_var = tk.StringVar(value=self.ado_connection.org_url if self.ado_connection else "https://dev.azure.com/your-org")
        org_entry = ttk.Entry(dialog, textvariable=org_var, width=52)
        org_entry.grid(row=0, column=1, sticky="ew", **pad)

        ttk.Label(dialog, text="Project:", style="TLabel").grid(row=1, column=0, sticky="w", **pad)
        proj_var = tk.StringVar(value=self.ado_connection.project if self.ado_connection else "")
        proj_entry = ttk.Entry(dialog, textvariable=proj_var, width=52)
        proj_entry.grid(row=1, column=1, sticky="ew", **pad)

        ttk.Label(
            dialog,
            text="Personal Access Token (session only - not saved):",
            style="TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        pat_var = tk.StringVar(value="")
        pat_entry = ttk.Entry(dialog, textvariable=pat_var, show="*", width=52)
        pat_entry.grid(row=2, column=1, sticky="ew", **pad)

        note = ttk.Label(
            dialog,
            text="The PAT is only used this session and is never written to disk.",
            style="Muted.TLabel",
        )
        note.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

        def _save():
            org = org_var.get().strip()
            proj = proj_var.get().strip()
            if not org or not proj:
                messagebox.showwarning("Settings", "Organization URL and Project are required.", parent=dialog)
                return
            self.ado_connection = AdoConnectionSettings(org_url=org, project=proj)
            pat = pat_var.get().strip()
            if pat:
                self._ado_pat = pat
            else:
                self._ado_pat = None
            self._save_connection_settings()
            self.status_var.set(f"Azure DevOps connection configured: {org} / {proj}")
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(4, 12), padx=12, sticky="e")
        ttk.Button(btn_frame, text="Save", command=_save).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

        dialog.columnconfigure(1, weight=1)
        org_entry.focus_set()
        dialog.bind("<Return>", lambda _e: _save())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        self.wait_window(dialog)

    def test_ado_connection(self) -> None:
        """Attempt to reach the configured Azure DevOps org/project and report the result."""
        client = self._require_client()
        if not client:
            return
        try:
            project_info = client.test_connection()
            name = project_info.get("name", self.ado_connection.project)
            state = project_info.get("state", "")
            messagebox.showinfo(
                "Connection OK",
                f"Connected successfully.\n\nProject: {name}\nState: {state}",
            )
        except AdoClientError as exc:
            messagebox.showerror("Connection Failed", str(exc))
        except Exception as exc:
            messagebox.showerror("Connection Failed", f"Unexpected error:\n{exc}")

    def fetch_work_item_types(self) -> None:
        """Fetch work item type names from Azure DevOps and display them."""
        client = self._require_client()
        if not client:
            return
        try:
            types = client.get_work_item_types()
            names = [t.get("name", "") for t in types if t.get("name")]
            if not names:
                messagebox.showinfo("Work Item Types", "No work item types returned.")
                return
            messagebox.showinfo(
                "Work Item Types",
                f"Found {len(names)} type(s):\n\n" + "\n".join(f"  {n}" for n in sorted(names)),
            )
        except AdoClientError as exc:
            messagebox.showerror("Fetch Error", str(exc))
        except Exception as exc:
            messagebox.showerror("Fetch Error", f"Unexpected error:\n{exc}")

    def fetch_ado_fields(self) -> None:
        """Fetch field definitions from Azure DevOps and display a summary."""
        client = self._require_client()
        if not client:
            return
        try:
            fields = client.get_fields()
            if not fields:
                messagebox.showinfo("Fields", "No fields returned.")
                return
            lines = [f"  {f.get('name','')} ({f.get('referenceName','')})" for f in fields]
            messagebox.showinfo(
                "Azure DevOps Fields",
                f"Found {len(fields)} field(s):\n\n" + "\n".join(lines[:40])
                + ("\n  ... and more" if len(lines) > 40 else ""),
            )
        except AdoClientError as exc:
            messagebox.showerror("Fetch Error", str(exc))
        except Exception as exc:
            messagebox.showerror("Fetch Error", f"Unexpected error:\n{exc}")

    # ------------------------------------------------------------------
    # Publish preview and dry run
    # ------------------------------------------------------------------

    def _build_plan_or_warn(self):
        """Build a publish plan from the current model, or warn and return None."""
        if not self.model:
            messagebox.showinfo("Publish Preview", "Open a CSV or project first.")
            return None

        msgs = self.model.validate()
        errors = [m for m in msgs if m.severity == "error"]
        if errors:
            messagebox.showwarning(
                "Publish Preview",
                f"Fix {len(errors)} validation error(s) before publishing.\n\n"
                + "\n".join(m.message for m in errors[:10]),
            )
            return None

        dirty = self.model.dirty_counts()
        total_dirty = dirty["new"] + dirty["modified"] + dirty["deleted"]
        if total_dirty == 0:
            messagebox.showinfo("Publish Preview", "No unsaved changes to publish.")
            return None

        return build_publish_plan(self.model)

    def show_publish_preview_dialog(self) -> None:
        """Show a non-blocking preview of the publish plan."""
        plan = self._build_plan_or_warn()
        if not plan:
            return

        dialog = tk.Toplevel(self)
        dialog.title("Publish Preview")
        dialog.geometry("680x500")
        dialog.configure(background=_PALETTE["bg"])
        dialog.grab_set()

        ttk.Label(dialog, text="Publish Plan Summary", style="Heading.TLabel").pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        ttk.Separator(dialog, orient="horizontal").pack(fill="x", padx=16)

        # Summary text
        text_frame = ttk.Frame(dialog)
        text_frame.pack(fill="both", expand=True, padx=16, pady=8)

        sb = ttk.Scrollbar(text_frame, orient="vertical")
        sb.pack(side="right", fill="y")

        txt = tk.Text(
            text_frame,
            wrap="word",
            font=_FONT,
            relief="flat",
            background=_PALETTE["surface"],
            foreground=_PALETTE["text"],
            yscrollcommand=sb.set,
            state="normal",
        )
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)

        # Populate
        lines = plan.summary_lines()
        txt.insert("end", "\n".join(lines))

        # Per-operation detail
        if plan.operations:
            txt.insert("end", "\n\nOperations:\n")
            for op in plan.operations:
                pid = f"parent remote ID {op.parent_remote_id}" if op.parent_remote_id is not None else "root"
                tag = f"[{op.op_type.upper()}]"
                remote = f"ID={op.remote_id}" if op.remote_id is not None else "new"
                txt.insert("end", f"  {tag} depth={op.depth}  {remote}  {op.work_item_type}: {op.title[:60]!r}  ({pid})\n")
                if op.fields_excluded:
                    excluded_str = ", ".join(f"{n} ({r})" for n, r in op.fields_excluded[:5])
                    txt.insert("end", f"       excluded: {excluded_str}\n")

        txt.config(state="disabled")

        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", padx=16, pady=(4, 14))

        note = ttk.Label(
            btn_frame,
            text="Connection required for Dry Run. No items will be created or modified.",
            style="Muted.TLabel",
        )
        note.pack(side="left", anchor="w")

        ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side="right")

    def run_publish_dry_run(self) -> None:
        """Run a validateOnly dry run against Azure DevOps and show results."""
        plan = self._build_plan_or_warn()
        if not plan:
            return

        client = self._require_client()
        if not client:
            return

        # Optionally fetch live field metadata for better field resolution.
        field_metadata = None
        try:
            field_metadata = client.get_fields()
        except AdoClientError:
            pass  # fall back to built-in map

        fm = build_field_map(field_metadata)

        try:
            results = run_dry_run(plan, client, field_map=fm)
        except Exception as exc:
            messagebox.showerror("Dry Run Error", f"Unexpected error during dry run:\n{exc}")
            return

        # Show results dialog
        ok = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        dialog = tk.Toplevel(self)
        dialog.title("Dry Run Results")
        dialog.geometry("680x500")
        dialog.configure(background=_PALETTE["bg"])
        dialog.grab_set()

        summary = f"Dry run complete: {len(ok)} passed, {len(failed)} failed."
        ttk.Label(dialog, text=summary, style="Heading.TLabel").pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        ttk.Separator(dialog, orient="horizontal").pack(fill="x", padx=16)

        text_frame = ttk.Frame(dialog)
        text_frame.pack(fill="both", expand=True, padx=16, pady=8)

        sb = ttk.Scrollbar(text_frame, orient="vertical")
        sb.pack(side="right", fill="y")
        txt = tk.Text(
            text_frame,
            wrap="word",
            font=_FONT,
            relief="flat",
            background=_PALETTE["surface"],
            foreground=_PALETTE["text"],
            yscrollcommand=sb.set,
        )
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)

        for r in results:
            status = "PASS" if r.success else "FAIL"
            txt.insert("end", f"[{status}] {r.op.op_type.upper()} {r.op.title[:50]!r}")
            if r.error:
                txt.insert("end", f"\n       Error: {r.error}")
            for msg in r.server_messages:
                txt.insert("end", f"\n       Note: {msg}")
            txt.insert("end", "\n")

        txt.config(state="disabled")

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(
            side="right", padx=16, pady=(0, 14)
        )
        self.wait_window(dialog)

    def run_live_publish(self) -> None:
        """Confirm, then publish dirty items to Azure DevOps live."""
        plan = self._build_plan_or_warn()
        if not plan:
            return

        creates = len(plan.creates)
        updates = len(plan.updates)
        reparents = len(plan.reparents)
        warning_text = "\n".join(f"  * {w}" for w in plan.warnings) or "  (none)"
        confirm = messagebox.askyesno(
            "Publish to Azure DevOps",
            f"This will make live changes to Azure DevOps.\n\n"
            f"  Creates:   {creates}\n"
            f"  Updates:   {updates}\n"
            f"  Reparents: {reparents}\n\n"
            f"Warnings:\n{warning_text}\n\nContinue?",
        )
        if not confirm:
            return

        client = self._require_client()
        if not client:
            return

        field_metadata = None
        try:
            field_metadata = client.get_fields()
        except AdoClientError:
            pass

        fm = build_field_map(field_metadata)

        # Progress log dialog
        progress_dialog = tk.Toplevel(self)
        progress_dialog.title("Publishing to Azure DevOps...")
        progress_dialog.geometry("680x400")
        progress_dialog.configure(background=_PALETTE["bg"])

        prog_txt = tk.Text(
            progress_dialog,
            wrap="word",
            font=_FONT_SMALL,
            relief="flat",
            background=_PALETTE["surface"],
            foreground=_PALETTE["text"],
            state="normal",
        )
        prog_txt.pack(fill="both", expand=True, padx=12, pady=12)

        def _log(msg: str) -> None:
            prog_txt.insert("end", msg + "\n")
            prog_txt.see("end")
            progress_dialog.update_idletasks()

        _log("Starting publish...")

        try:
            report = run_live_publish(
                plan,
                client,
                model=self.model,
                field_map=fm,
                on_progress=_log,
            )
        except Exception as exc:
            progress_dialog.destroy()
            messagebox.showerror("Publish Error", f"Unexpected error:\n{exc}")
            return

        progress_dialog.destroy()

        # Show publish report dialog
        report_dialog = tk.Toplevel(self)
        report_dialog.title("Publish Report")
        report_dialog.geometry("680x500")
        report_dialog.configure(background=_PALETTE["bg"])
        report_dialog.grab_set()

        ttk.Label(
            report_dialog,
            text=report.summary(),
            style="Heading.TLabel",
        ).pack(anchor="w", padx=16, pady=(14, 4))
        ttk.Separator(report_dialog, orient="horizontal").pack(fill="x", padx=16)

        text_frame = ttk.Frame(report_dialog)
        text_frame.pack(fill="both", expand=True, padx=16, pady=8)

        sb = ttk.Scrollbar(text_frame, orient="vertical")
        sb.pack(side="right", fill="y")
        txt = tk.Text(
            text_frame,
            wrap="word",
            font=_FONT,
            relief="flat",
            background=_PALETTE["surface"],
            foreground=_PALETTE["text"],
            yscrollcommand=sb.set,
        )
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)

        for line in report.summary_lines():
            txt.insert("end", line + "\n")
        txt.config(state="disabled")

        ttk.Button(report_dialog, text="Close", command=report_dialog.destroy).pack(
            side="right", padx=16, pady=(0, 14)
        )

        # Refresh tree to reflect updated IDs and dirty state.
        self.refresh_after_model_change(None)
        self.update_status()

        self.wait_window(report_dialog)


# ----------------------------
# Main
# ----------------------------

def main():
    initial_path = None

    if len(sys.argv) >= 2:
        initial_path = sys.argv[1]

    app = AdoWorkItemsViewer(initial_path=initial_path)
    app.mainloop()


if __name__ == "__main__":
    main()
