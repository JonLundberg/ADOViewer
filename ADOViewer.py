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
        self.tree_item_to_node = {}

        self.create_widgets()
        self.create_menu()
        self.bind_keyboard_shortcuts()

        if initial_path:
            self.load_csv(initial_path)

    def create_menu(self):
        menu_bar = tk.Menu(self)

        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="Open CSV...", command=self.open_csv_dialog)
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

    def create_widgets(self):
        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)

        top_bar = ttk.Frame(outer)
        top_bar.pack(fill=tk.X, padx=8, pady=6)

        ttk.Button(top_bar, text="Open CSV...", command=self.open_csv_dialog).pack(side=tk.LEFT)
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

        details_label = ttk.Label(details_frame, text="Details")
        details_label.pack(anchor="w")

        self.details_text = tk.Text(details_frame, height=10, wrap=tk.NONE)
        self.details_text.pack(fill=tk.BOTH, expand=True)

        details_y_scroll = ttk.Scrollbar(
            details_frame,
            orient=tk.VERTICAL,
            command=self.details_text.yview,
        )
        details_y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.details_text.configure(yscrollcommand=details_y_scroll.set)

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

    def load_csv(self, path):
        try:
            fieldnames, rows = read_csv_file(path)
            self.model = WorkItemModel(fieldnames, rows)
            self.current_path = path
            self.populate_tree()
            self.title(f"Azure DevOps Work Items Viewer - {os.path.basename(path)}")
            self.update_status()

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
        self.select_local_id(select_local_id)

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
            return

        item = selection[0]
        node = self.tree_item_to_node.get(item)

        self.show_details(node)

    def show_details(self, node):
        self.details_text.delete("1.0", tk.END)

        if not node:
            return

        if node.synthetic:
            self.details_text.insert(tk.END, "Synthetic grouping node\n")
            self.details_text.insert(tk.END, f"Title: {node.row.get('Synthetic Title', '')}\n")
            return

        lines = []

        # Show commonly useful fields first.
        preferred_columns = [
            self.model.id_col,
            self.model.type_col,
            self.model.title_col,
            self.model.state_col,
            self.model.assigned_to_col,
            self.model.parent_col,
            self.model.effort_col,
            self.model.remaining_col,
            self.model.completed_col,
            self.model.area_col,
            self.model.iteration_col,
            self.model.tags_col,
            self.model.url_col,
        ]

        seen = set()

        for col in preferred_columns:
            if col and col in node.row and col not in seen:
                seen.add(col)
                lines.append(f"{col}: {node.row.get(col, '')}")

        # Then show the rest.
        for col in self.model.fieldnames:
            if col not in seen:
                lines.append(f"{col}: {node.row.get(col, '')}")

        self.details_text.insert(tk.END, "\n".join(lines))

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
                return

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

    def on_tree_double_click(self, event):
        selection = self.tree.selection()

        if not selection or not self.model:
            return

        item = selection[0]
        node = self.tree_item_to_node.get(item)

        if not node or node.synthetic:
            return

        url = self.model.row_url(node.row)

        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo(
                "No URL",
                "This CSV row does not appear to contain a URL column."
            )


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
