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

from adoviewer.csv_io import read_csv_file
from adoviewer.project_io import load_project_file, save_project_file
from adoviewer.tree_model import WorkItemModel


# ----------------------------
# Tkinter UI
# ----------------------------

class AdoWorkItemsViewer(tk.Tk):
    def __init__(self, initial_path=None):
        super().__init__()

        self.title("Azure DevOps Work Items Viewer")
        self.geometry("1450x850")
        self.minsize(1000, 600)

        self.model = None
        self.current_path = None
        self.project_path = None
        self.source_path = None
        self.tree_item_to_node = {}
        self.details_local_id = None
        self.common_field_vars = []
        self.raw_field_items = {}

        self.create_widgets()
        self.create_menu()
        self.bind_keyboard_shortcuts()

        if initial_path:
            self.load_initial_path(initial_path)

    def create_menu(self):
        menu_bar = tk.Menu(self)

        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="Open CSV...", command=self.open_csv_dialog)
        file_menu.add_command(label="Open Project...", command=self.open_project_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Save Project", command=self.save_project)
        file_menu.add_command(label="Save Project As...", command=self.save_project_as)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)

        work_item_menu = tk.Menu(menu_bar, tearoff=False)
        work_item_menu.add_command(label="Add Root", command=self.add_root_item)
        work_item_menu.add_command(label="Add Child", command=self.add_child_item)
        work_item_menu.add_command(label="Add Sibling", command=self.add_sibling_item)
        work_item_menu.add_separator()
        work_item_menu.add_command(label="Edit Title...", command=self.edit_selected_title)
        work_item_menu.add_command(label="Delete / Restore", command=self.toggle_delete_selected)
        work_item_menu.add_separator()
        work_item_menu.add_command(label="Move Up", command=self.move_selected_up)
        work_item_menu.add_command(label="Move Down", command=self.move_selected_down)
        work_item_menu.add_command(label="Indent", command=self.indent_selected)
        work_item_menu.add_command(label="Outdent", command=self.outdent_selected)
        work_item_menu.add_separator()
        work_item_menu.add_command(label="Validate", command=self.validate_model)

        view_menu = tk.Menu(menu_bar, tearoff=False)
        view_menu.add_command(label="Expand All", command=self.expand_all)
        view_menu.add_command(label="Collapse All", command=self.collapse_all)
        view_menu.add_separator()
        view_menu.add_command(label="Clear Filter", command=self.clear_filter)

        menu_bar.add_cascade(label="File", menu=file_menu)
        menu_bar.add_cascade(label="Work Item", menu=work_item_menu)
        menu_bar.add_cascade(label="View", menu=view_menu)

        self.config(menu=menu_bar)

    def bind_keyboard_shortcuts(self):
        def handled(command):
            def callback(_event):
                command()
                return "break"

            return callback

        self.tree.bind("<F2>", handled(self.edit_selected_title))
        self.tree.bind("<Delete>", handled(self.toggle_delete_selected))
        self.tree.bind("<Control-Up>", handled(self.move_selected_up))
        self.tree.bind("<Control-Down>", handled(self.move_selected_down))
        self.tree.bind("<Control-Right>", handled(self.indent_selected))
        self.tree.bind("<Control-Left>", handled(self.outdent_selected))
        self.bind_all("<Control-s>", handled(self.save_project))

    def create_widgets(self):
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)

        top_bar = ttk.Frame(outer)
        top_bar.pack(fill=tk.X, padx=8, pady=6)

        ttk.Button(top_bar, text="Open CSV...", command=self.open_csv_dialog).pack(side=tk.LEFT)
        ttk.Button(top_bar, text="Open Project...", command=self.open_project_dialog).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top_bar, text="Save", command=self.save_project).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(top_bar, text="Save As", command=self.save_project_as).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top_bar, text="Add Root", command=self.add_root_item).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(top_bar, text="Add Child", command=self.add_child_item).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top_bar, text="Add Sibling", command=self.add_sibling_item).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top_bar, text="Delete/Restore", command=self.toggle_delete_selected).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top_bar, text="Up", command=self.move_selected_up).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(top_bar, text="Down", command=self.move_selected_down).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top_bar, text="Indent", command=self.indent_selected).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top_bar, text="Outdent", command=self.outdent_selected).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top_bar, text="Validate", command=self.validate_model).pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(top_bar, text="  Filter:").pack(side=tk.LEFT)

        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(top_bar, textvariable=self.filter_var, width=45)
        self.filter_entry.pack(side=tk.LEFT, padx=4)
        self.filter_entry.bind("<Return>", lambda event: self.apply_filter())

        ttk.Button(top_bar, text="Apply", command=self.apply_filter).pack(side=tk.LEFT)
        ttk.Button(top_bar, text="Clear", command=self.clear_filter).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Button(top_bar, text="Expand All", command=self.expand_all).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Button(top_bar, text="Collapse All", command=self.collapse_all).pack(side=tk.LEFT, padx=(4, 0))

        self.status_var = tk.StringVar(value="Open an Azure DevOps CSV export to begin.")
        ttk.Label(top_bar, textvariable=self.status_var).pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        tree_frame = ttk.Frame(paned)
        details_frame = ttk.Frame(paned)

        paned.add(tree_frame, weight=4)
        paned.add(details_frame, weight=1)

        columns = (
            "id",
            "local_status",
            "type",
            "state",
            "assigned_to",
            "effort",
            "remaining",
            "completed",
            "area",
            "iteration",
            "tags",
        )

        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
        )

        self.tree.heading("#0", text="Title")
        self.tree.heading("id", text="ID")
        self.tree.heading("local_status", text="Status")
        self.tree.heading("type", text="Type")
        self.tree.heading("state", text="State")
        self.tree.heading("assigned_to", text="Assigned To")
        self.tree.heading("effort", text="Effort / Points / Estimate")
        self.tree.heading("remaining", text="Remaining")
        self.tree.heading("completed", text="Completed")
        self.tree.heading("area", text="Area")
        self.tree.heading("iteration", text="Iteration")
        self.tree.heading("tags", text="Tags")

        self.tree.column("#0", width=420, minwidth=250, stretch=True)
        self.tree.column("id", width=75, minwidth=60, stretch=False)
        self.tree.column("local_status", width=95, minwidth=80, stretch=False)
        self.tree.column("type", width=130, minwidth=90, stretch=False)
        self.tree.column("state", width=110, minwidth=80, stretch=False)
        self.tree.column("assigned_to", width=180, minwidth=100, stretch=False)
        self.tree.column("effort", width=140, minwidth=90, stretch=False)
        self.tree.column("remaining", width=90, minwidth=70, stretch=False)
        self.tree.column("completed", width=90, minwidth=70, stretch=False)
        self.tree.column("area", width=180, minwidth=100, stretch=False)
        self.tree.column("iteration", width=180, minwidth=100, stretch=False)
        self.tree.column("tags", width=220, minwidth=100, stretch=True)

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
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.show_tree_context_menu)
        self.tree.bind("<Button-2>", self.show_tree_context_menu)

        self.details_title_var = tk.StringVar(value="Details")
        details_label = ttk.Label(details_frame, textvariable=self.details_title_var)
        details_label.pack(anchor="w")

        self.details_notebook = ttk.Notebook(details_frame)
        self.details_notebook.pack(fill=tk.BOTH, expand=True)

        common_tab = ttk.Frame(self.details_notebook)
        raw_tab = ttk.Frame(self.details_notebook)
        validation_tab = ttk.Frame(self.details_notebook)

        self.details_notebook.add(common_tab, text="Common Fields")
        self.details_notebook.add(raw_tab, text="Raw Fields")
        self.details_notebook.add(validation_tab, text="Validation")

        self.common_form_frame = ttk.Frame(common_tab)
        self.common_form_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        common_button_bar = ttk.Frame(common_tab)
        common_button_bar.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(
            common_button_bar,
            text="Apply Common Fields",
            command=self.apply_common_field_edits,
        ).pack(side=tk.LEFT)

        raw_split = ttk.PanedWindow(raw_tab, orient=tk.HORIZONTAL)
        raw_split.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

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
        ttk.Label(raw_editor_frame, textvariable=self.raw_field_name_var).pack(anchor="w")
        self.raw_value_text = tk.Text(raw_editor_frame, height=6, wrap=tk.WORD)
        self.raw_value_text.pack(fill=tk.BOTH, expand=True, pady=(4, 4))
        ttk.Button(
            raw_editor_frame,
            text="Apply Raw Field",
            command=self.apply_raw_field_edit,
        ).pack(anchor="w")

        self.validation_text = tk.Text(validation_tab, height=8, wrap=tk.WORD)
        self.validation_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.validation_text.configure(state=tk.DISABLED)
        self.clear_details()

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
            self.populate_tree()
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
            self.populate_tree()
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


    def clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.tree_item_to_node.clear()

    def populate_tree(self, filter_text="", select_local_id=None):
        if select_local_id is None:
            select_local_id = self.selected_local_id()

        self.clear_tree()

        if not self.model:
            return

        filter_text = filter_text.strip().lower()

        for child in self.model.root.children:
            self.insert_node_if_matching("", child, filter_text)

        self.expand_all()
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

        item_id = self.insert_node(parent_tree_item, node)

        for child in node.children:
            self.insert_node_if_matching(item_id, child, filter_text)

        return item_id

    def insert_node(self, parent_tree_item, node):
        if node.synthetic:
            title = node.row.get("Synthetic Title", "Group")
            values = (
                "",
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
                self.node_local_status(node),
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

        tree_item = self.tree.insert(
            parent_tree_item,
            tk.END,
            text=title,
            values=values,
            tags=self.node_tags(node),
            open=True,
        )

        self.tree_item_to_node[tree_item] = node
        return tree_item

    def apply_filter(self):
        self.populate_tree(self.filter_var.get())

    def clear_filter(self):
        self.filter_var.set("")
        self.populate_tree("")

    def expand_all(self):
        for item in self.tree.get_children():
            self.set_open_recursive(item, True)

    def collapse_all(self):
        for item in self.tree.get_children():
            self.set_open_recursive(item, False)

    def set_open_recursive(self, item, open_value):
        self.tree.item(item, open=open_value)

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
            label="Add Root",
            command=self.add_root_item,
            state=state_for_add_root,
        )
        menu.add_command(
            label="Add Child",
            command=self.add_child_item,
            state=state_for_real,
        )
        menu.add_command(
            label="Add Sibling",
            command=self.add_sibling_item,
            state=state_for_real,
        )
        menu.add_separator()
        menu.add_command(
            label="Edit Title...",
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
            ttk.Label(self.common_form_frame, text=label).grid(
                row=row_index,
                column=0,
                sticky="w",
                padx=(0, 8),
                pady=2,
            )
            var = tk.StringVar(value=value)
            entry = ttk.Entry(self.common_form_frame, textvariable=var, width=80)
            entry.grid(row=row_index, column=1, sticky="ew", pady=2)
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

    def apply_common_field_edits(self):
        if not self.details_local_id or not self.model:
            messagebox.showinfo("Apply Common Fields", "Select a work item first.")
            return

        node = self.model.get_node(self.details_local_id)

        if not node or not node.item:
            messagebox.showinfo("Apply Common Fields", "The selected work item is no longer available.")
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
        else:
            self.status_var.set("No common field changes to apply.")

    def apply_raw_field_edit(self):
        if not self.details_local_id or not self.model:
            messagebox.showinfo("Apply Raw Field", "Select a work item first.")
            return

        selection = self.raw_fields_tree.selection()

        if not selection:
            messagebox.showinfo("Apply Raw Field", "Select a raw field first.")
            return

        field_name = self.raw_field_items.get(selection[0])

        if not field_name:
            return

        node = self.model.get_node(self.details_local_id)

        if not node or not node.item:
            messagebox.showinfo("Apply Raw Field", "The selected work item is no longer available.")
            return

        value = self.raw_value_text.get("1.0", "end-1c")

        if value == str(node.item.fields.get(field_name, "")):
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
        self.title("Azure DevOps Work Items Viewer - Untitled")
        self.populate_tree()
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
        self.populate_tree(self.filter_var.get())
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
        self.populate_tree(self.filter_var.get(), select_local_id=select_local_id)
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
        if not node.item:
            return ()

        if any(msg.severity == "error" for msg in node.item.validation):
            return ("validation_error",)

        if any(msg.severity == "warning" for msg in node.item.validation):
            return ("validation_warning",)

        if node.item.state == "new":
            return ("new",)

        if node.item.state == "modified":
            return ("modified",)

        if node.item.state == "deleted":
            return ("deleted",)

        return ()

    def update_status(self):
        if not self.model:
            self.status_var.set("Open an Azure DevOps CSV export to begin.")
            return

        source = os.path.basename(self.current_path) if self.current_path else "Untitled"
        item_count = len(self.model.flatten())
        counts = self.model.dirty_counts()
        errors = sum(1 for msg in self.model.validation_messages if msg.severity == "error")
        warnings = sum(1 for msg in self.model.validation_messages if msg.severity == "warning")
        dirty = counts["new"] + counts["modified"] + counts["deleted"]

        self.status_var.set(
            f"{source} - {item_count} items - "
            f"{dirty} dirty ({counts['new']} new, {counts['modified']} modified, {counts['deleted']} deleted) - "
            f"{errors} errors, {warnings} warnings"
        )

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
