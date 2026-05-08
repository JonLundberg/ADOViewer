"""Tree construction and model-level work item operations."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable

from .csv_io import (
    ColumnMap,
    detect_columns,
    get_populated_title_levels,
    get_row_level,
    get_row_title,
)
from .models import ValidationMessage, WorkItem, WorkItemNode


def _new_local_id() -> str:
    return f"wi-{uuid.uuid4()}"


def _parse_int(value: object) -> int | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        return int(text)
    except ValueError:
        return None


def _optional_int(value: object) -> int | None:
    return _parse_int(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    return {
        str(key): "" if dict_value is None else str(dict_value)
        for key, dict_value in value.items()
    }


class WorkItemModel:
    def __init__(
        self,
        fieldnames: Iterable[str],
        rows: Iterable[dict[str, str]],
        local_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.fieldnames = list(fieldnames or [])
        self.rows = [dict(row) for row in rows]
        self.local_id_factory = local_id_factory or _new_local_id

        self._refresh_columns()

        self.root = WorkItemNode("__root__", synthetic=True)
        self.all_nodes: list[WorkItemNode] = []
        self.nodes_by_local_id: dict[str, WorkItemNode] = {}
        self.validation_messages: list[ValidationMessage] = []
        self._initializing = True

        self.build_tree()
        self._snapshot_original_hierarchy()
        self._initializing = False
        self.validate()

    @classmethod
    def from_project_items(
        cls,
        fieldnames: Iterable[str],
        item_records: Iterable[dict[str, object]],
        root_order: Iterable[str] | None = None,
        local_id_factory: Callable[[], str] | None = None,
    ) -> WorkItemModel:
        model = cls.__new__(cls)
        model.fieldnames = list(fieldnames or [])
        model.rows = []
        model.local_id_factory = local_id_factory or _new_local_id

        item_records = list(item_records or [])

        if not model.fieldnames:
            for record in item_records:
                fields = record.get("fields")
                if not isinstance(fields, dict):
                    continue
                for field_name in fields:
                    field_name = str(field_name)
                    if field_name not in model.fieldnames:
                        model.fieldnames.append(field_name)

        model._refresh_columns()

        model.root = WorkItemNode("__root__", synthetic=True)
        model.all_nodes = []
        model.nodes_by_local_id = {}
        model.validation_messages = []
        model._initializing = True

        for record in item_records:
            if not isinstance(record, dict):
                raise ValueError("Project work item entries must be objects.")

            local_id = str(record.get("local_id") or model._allocate_local_id()).strip()
            if not local_id:
                local_id = model._allocate_local_id()

            if local_id in model.nodes_by_local_id:
                raise ValueError(f"Duplicate local ID {local_id!r} in project file.")

            fields = _string_dict(record.get("fields") if isinstance(record.get("fields"), dict) else {})
            original_fields = _string_dict(
                record.get("original_fields") if isinstance(record.get("original_fields"), dict) else {}
            )

            for field_name in model.fieldnames:
                fields.setdefault(field_name, "")

            state = str(record.get("state") or "unchanged")
            if state not in {"unchanged", "new", "modified", "deleted"}:
                raise ValueError(f"Unsupported work item state {state!r} in project file.")

            parent_local_id = _optional_string(record.get("parent_local_id"))
            item = WorkItem(
                local_id=local_id,
                remote_id=_optional_int(record.get("remote_id")),
                rev=_optional_int(record.get("rev")),
                work_item_type=str(record.get("work_item_type") or model.row_type(fields)),
                title=str(record.get("title") or model.row_title(fields)),
                fields=fields,
                original_fields=original_fields,
                parent_local_id=parent_local_id,
                original_parent_local_id=_optional_string(
                    record.get("original_parent_local_id")
                ) if "original_parent_local_id" in record else parent_local_id,
                children=[
                    str(child_id)
                    for child_id in record.get("children", [])
                    if str(child_id).strip()
                ],
                source_row_index=_optional_int(record.get("source_row_index")),
                state=state,  # type: ignore[arg-type]
            )
            node = WorkItemNode(local_id, item=item)
            model.rows.append(fields)
            model.all_nodes.append(node)
            model.nodes_by_local_id[local_id] = node

        visited: set[str] = set()

        def attach(parent: WorkItemNode, child_id: str) -> None:
            if child_id in visited:
                return

            child = model.nodes_by_local_id.get(child_id)
            if not child:
                return

            parent.add_child(child)
            visited.add(child_id)

            assert child.item is not None
            for grandchild_id in child.item.children:
                attach(child, grandchild_id)

        root_ids = [
            str(local_id)
            for local_id in (root_order or [])
            if str(local_id).strip()
        ]

        if not root_ids:
            root_ids = [
                node.item.local_id
                for node in model.all_nodes
                if node.item and node.item.parent_local_id is None
            ]

        for local_id in root_ids:
            attach(model.root, local_id)

        for node in model.all_nodes:
            assert node.item is not None

            if node.item.local_id in visited:
                continue

            parent = None
            if node.item.parent_local_id:
                parent = model.nodes_by_local_id.get(node.item.parent_local_id)

            attach(parent or model.root, node.item.local_id)

        model._sync_item_hierarchy_from_tree()
        model._initializing = False
        model.validate()
        return model

    def row_id(self, row: dict[str, str]) -> str:
        if self.id_col:
            return str(row.get(self.id_col, "")).strip()
        return ""

    def row_type(self, row: dict[str, str]) -> str:
        if self.type_col:
            return str(row.get(self.type_col, "")).strip()
        return ""

    def row_title(self, row: dict[str, str]) -> str:
        return get_row_title(row, self.title_col, self.title_level_columns)

    def row_state(self, row: dict[str, str]) -> str:
        if self.state_col:
            return str(row.get(self.state_col, "")).strip()
        return ""

    def row_assigned_to(self, row: dict[str, str]) -> str:
        if self.assigned_to_col:
            return str(row.get(self.assigned_to_col, "")).strip()
        return ""

    def row_tags(self, row: dict[str, str]) -> str:
        if self.tags_col:
            return str(row.get(self.tags_col, "")).strip()
        return ""

    def row_url(self, row: dict[str, str]) -> str:
        if self.url_col:
            return str(row.get(self.url_col, "")).strip()
        return ""

    def row_area(self, row: dict[str, str]) -> str:
        if self.area_col:
            return str(row.get(self.area_col, "")).strip()
        return ""

    def row_iteration(self, row: dict[str, str]) -> str:
        if self.iteration_col:
            return str(row.get(self.iteration_col, "")).strip()
        return ""

    def row_effort(self, row: dict[str, str]) -> str:
        if self.effort_col:
            return str(row.get(self.effort_col, "")).strip()
        return ""

    def row_remaining(self, row: dict[str, str]) -> str:
        if self.remaining_col:
            return str(row.get(self.remaining_col, "")).strip()
        return ""

    def row_completed(self, row: dict[str, str]) -> str:
        if self.completed_col:
            return str(row.get(self.completed_col, "")).strip()
        return ""

    def build_tree(self) -> None:
        if self.parent_col and self.id_col:
            self.build_tree_from_parent_column()
        elif self.title_level_columns:
            self.build_tree_from_title_levels()
        else:
            self.build_grouped_tree()

    def build_tree_from_parent_column(self) -> None:
        """Build a tree using ID + Parent ID."""
        by_remote_id: dict[str, WorkItemNode] = {}
        duplicate_ids: set[str] = set()

        for index, row in enumerate(self.rows):
            node = self._node_from_row(index, row)
            item_id = self.row_id(row)

            if item_id:
                if item_id in by_remote_id:
                    duplicate_ids.add(item_id)
                    self._add_validation(
                        "error",
                        f"Duplicate work item ID {item_id}.",
                        node,
                        row_index=index,
                        field=self.id_col,
                    )
                else:
                    by_remote_id[item_id] = node

        for duplicate_id in duplicate_ids:
            first = by_remote_id.get(duplicate_id)
            if first:
                self._add_validation(
                    "error",
                    f"Duplicate work item ID {duplicate_id}.",
                    first,
                    row_index=first.item.source_row_index if first.item else None,
                    field=self.id_col,
                )

        for node in self.all_nodes:
            parent_id = str(node.row.get(self.parent_col, "")).strip() if self.parent_col else ""
            node_id = self.row_id(node.row)

            if parent_id and parent_id == node_id:
                self._add_validation(
                    "error",
                    f"Work item {node_id} cannot be its own parent.",
                    node,
                    row_index=node.item.source_row_index if node.item else None,
                    field=self.parent_col,
                )
                self.root.add_child(node)
            elif parent_id and parent_id in by_remote_id:
                parent = by_remote_id[parent_id]
                if self._is_descendant(parent, node):
                    label = node_id
                    if not label and node.item:
                        label = node.item.local_id
                    self._add_validation(
                        "error",
                        f"Work item {label} would create a parent-child cycle.",
                        node,
                        row_index=node.item.source_row_index if node.item else None,
                        field=self.parent_col,
                    )
                    self.root.add_child(node)
                else:
                    self._attach_existing_node(parent, node)
            else:
                if parent_id:
                    self._add_validation(
                        "warning",
                        f"Parent ID {parent_id} was not found in this file.",
                        node,
                        row_index=node.item.source_row_index if node.item else None,
                        field=self.parent_col,
                    )
                self.root.add_child(node)

    def build_tree_from_title_levels(self) -> None:
        """Build tree from Title 1, Title 2, Title 3..."""
        stack_by_level: dict[int, WorkItemNode] = {}

        for index, row in enumerate(self.rows):
            node = self._node_from_row(index, row)
            populated = get_populated_title_levels(row, self.title_level_columns)
            level = get_row_level(row, self.title_level_columns)

            if len(populated) > 1:
                self._add_validation(
                    "error",
                    "Only one Title N column should be populated per row.",
                    node,
                    row_index=index,
                )

            if level is None:
                level = 1

            if level > 1 and (level - 1) not in stack_by_level:
                self._add_validation(
                    "warning",
                    f"Title level {level} has no previous level {level - 1} parent.",
                    node,
                    row_index=index,
                )

            if level <= 1:
                self.root.add_child(node)
            else:
                parent = None

                for parent_level in range(level - 1, 0, -1):
                    if parent_level in stack_by_level:
                        parent = stack_by_level[parent_level]
                        break

                if parent:
                    self._attach_existing_node(parent, node)
                else:
                    self.root.add_child(node)

            stack_by_level[level] = node

            for existing_level in list(stack_by_level.keys()):
                if existing_level > level:
                    del stack_by_level[existing_level]

    def build_grouped_tree(self) -> None:
        """No hierarchy data found. Group by Work Item Type."""
        groups: dict[str, WorkItemNode] = {}

        for index, row in enumerate(self.rows):
            item_type = self.row_type(row) or "Unknown Type"

            if item_type not in groups:
                group_node = WorkItemNode(
                    key=f"group-{item_type}",
                    row={
                        "Synthetic Title": item_type,
                        "Synthetic Type": "Group",
                    },
                    synthetic=True,
                )
                groups[item_type] = group_node
                self.root.add_child(group_node)

            node = self._node_from_row(index, row)
            groups[item_type].add_child(node)

    def add_root(
        self,
        title: str = "New Work Item",
        work_item_type: str = "",
        fields: dict[str, str] | None = None,
        index: int | None = None,
    ) -> WorkItemNode:
        node = self._new_node(title, work_item_type, fields)
        self.root.add_child(node, index)
        return node

    def add_child(
        self,
        parent_local_id: str,
        title: str = "New Work Item",
        work_item_type: str = "",
        fields: dict[str, str] | None = None,
        index: int | None = None,
    ) -> WorkItemNode:
        parent = self.get_node(parent_local_id)
        if not parent:
            raise KeyError(f"No work item with local ID {parent_local_id!r}.")

        node = self._new_node(title, work_item_type, fields)
        self._attach_existing_node(parent, node, index)
        return node

    def add_sibling(
        self,
        sibling_local_id: str,
        title: str = "New Work Item",
        work_item_type: str = "",
        fields: dict[str, str] | None = None,
        after: bool = True,
    ) -> WorkItemNode:
        sibling = self.get_node(sibling_local_id)
        if not sibling or not sibling.parent:
            raise KeyError(f"No work item with local ID {sibling_local_id!r}.")

        parent = sibling.parent
        index = parent.children.index(sibling) + (1 if after else 0)
        node = self._new_node(title, work_item_type, fields)

        if parent is self.root:
            self.root.add_child(node, index)
        elif parent.item:
            self._attach_existing_node(parent, node, index)
        else:
            parent.add_child(node, index)

        return node

    def edit_field(self, local_id: str, field_name: str, value: str) -> None:
        node = self._require_real_node(local_id)
        item = node.item
        assert item is not None

        if field_name not in self.fieldnames:
            self.fieldnames.append(field_name)
            self._refresh_columns()

        item.fields[field_name] = value
        self._refresh_item_core_fields(item)
        self._refresh_dirty_state(item)

    def edit_title(self, local_id: str, title: str) -> None:
        node = self._require_real_node(local_id)
        title_col = self._title_field_for_node(node)
        self.edit_field(local_id, title_col, title)

    def soft_delete(self, local_id: str) -> None:
        node = self._require_real_node(local_id)
        assert node.item is not None
        node.item.state = "deleted"

    def restore(self, local_id: str) -> None:
        node = self._require_real_node(local_id)
        item = node.item
        assert item is not None

        if item.original_fields:
            item.state = "unchanged"
            self._refresh_dirty_state(item)
        else:
            item.state = "new"

    def reparent(
        self,
        local_id: str,
        new_parent_local_id: str | None,
        index: int | None = None,
    ) -> None:
        node = self._require_real_node(local_id)
        new_parent = self.root if new_parent_local_id is None else self._require_real_node(new_parent_local_id)

        if new_parent is node:
            raise ValueError("A work item cannot be its own parent.")

        if self._is_descendant(new_parent, node):
            raise ValueError("Cannot reparent a work item under one of its descendants.")

        self._detach_node(node)

        if new_parent is self.root:
            self.root.add_child(node, index)
            assert node.item is not None
            node.item.parent_local_id = None
            self._update_parent_field(node)
            self._refresh_dirty_state(node.item)
        else:
            self._attach_existing_node(new_parent, node, index)

    def move_up(self, local_id: str) -> bool:
        return self._move_sibling(local_id, -1)

    def move_down(self, local_id: str) -> bool:
        return self._move_sibling(local_id, 1)

    def indent(self, local_id: str) -> bool:
        """Move an item under its previous real sibling."""
        node = self._require_real_node(local_id)

        if not node.parent:
            return False

        siblings = node.parent.children
        index = siblings.index(node)

        if index == 0:
            return False

        new_parent = siblings[index - 1]

        if not new_parent.item:
            return False

        self.reparent(local_id, new_parent.item.local_id)
        return True

    def outdent(self, local_id: str) -> bool:
        """Move an item to become the next sibling of its current parent."""
        node = self._require_real_node(local_id)
        parent = node.parent

        if not parent or parent is self.root:
            return False

        grandparent = parent.parent

        if not grandparent:
            return False

        index = grandparent.children.index(parent) + 1
        new_parent_local_id = grandparent.item.local_id if grandparent.item else None
        self.reparent(local_id, new_parent_local_id, index)
        return True

    def flatten(self, include_deleted: bool = True) -> list[WorkItem]:
        items: list[WorkItem] = []

        def visit(node: WorkItemNode) -> None:
            if node.item and (include_deleted or node.item.state != "deleted"):
                items.append(node.item)

            for child in node.children:
                visit(child)

        for child in self.root.children:
            visit(child)

        return items

    def get_node(self, local_id: str) -> WorkItemNode | None:
        return self.nodes_by_local_id.get(local_id)

    def dirty_counts(self) -> dict[str, int]:
        counts = {
            "new": 0,
            "modified": 0,
            "deleted": 0,
            "unchanged": 0,
        }

        for node in self.all_nodes:
            if not node.item:
                continue
            counts[node.item.state] += 1

        return counts

    def validate(self) -> list[ValidationMessage]:
        """Run model-level validation while preserving import-time findings."""
        self._reset_validation_messages()
        self._validate_title_level_shape()
        self._validate_parent_id_fields()
        self._validate_required_fields()
        self._validate_remote_ids()
        self._validate_parent_link_consistency()
        self._validate_cycles()
        self._validate_deleted_parents()
        return list(self.validation_messages)

    def _node_from_row(self, index: int, row: dict[str, str]) -> WorkItemNode:
        local_id = self._allocate_local_id()
        item_id = self.row_id(row)
        remote_id = _parse_int(item_id)
        state = "new" if self.id_col and not item_id else "unchanged"
        item = WorkItem(
            local_id=local_id,
            remote_id=remote_id,
            rev=_parse_int(row.get(self.rev_col)) if self.rev_col else None,
            work_item_type=self.row_type(row),
            title=self.row_title(row),
            fields=row,
            original_fields=dict(row),
            parent_local_id=None,
            source_row_index=index,
            state=state,
        )
        node = WorkItemNode(local_id, item=item)
        self.all_nodes.append(node)
        self.nodes_by_local_id[local_id] = node
        return node

    def _allocate_local_id(self) -> str:
        for _attempt in range(10000):
            local_id = self.local_id_factory()
            if local_id not in self.nodes_by_local_id:
                return local_id

        raise RuntimeError("Could not allocate a unique local work item ID.")

    def _refresh_columns(self) -> None:
        self.columns: ColumnMap = detect_columns(self.fieldnames)
        self.id_col = self.columns.id_col
        self.rev_col = self.columns.rev_col
        self.type_col = self.columns.type_col
        self.title_col = self.columns.title_col
        self.state_col = self.columns.state_col
        self.assigned_to_col = self.columns.assigned_to_col
        self.tags_col = self.columns.tags_col
        self.url_col = self.columns.url_col
        self.parent_col = self.columns.parent_col
        self.area_col = self.columns.area_col
        self.iteration_col = self.columns.iteration_col
        self.effort_col = self.columns.effort_col
        self.remaining_col = self.columns.remaining_col
        self.completed_col = self.columns.completed_col
        self.title_level_columns = self.columns.title_level_columns

    def _new_node(
        self,
        title: str,
        work_item_type: str,
        fields: dict[str, str] | None = None,
    ) -> WorkItemNode:
        title_col = self._ensure_title_column()
        type_col = self._ensure_type_column()
        row = {fieldname: "" for fieldname in self.fieldnames}

        if fields:
            for field_name, value in fields.items():
                if field_name not in self.fieldnames:
                    self.fieldnames.append(field_name)
                row[field_name] = value

        row[title_col] = title
        row[type_col] = work_item_type

        if self.id_col:
            row[self.id_col] = ""

        item = WorkItem(
            local_id=self._allocate_local_id(),
            remote_id=None,
            rev=None,
            work_item_type=work_item_type,
            title=title,
            fields=row,
            original_fields={},
            parent_local_id=None,
            source_row_index=None,
            state="new",
        )
        node = WorkItemNode(item.local_id, item=item)
        self.rows.append(row)
        self.all_nodes.append(node)
        self.nodes_by_local_id[item.local_id] = node
        return node

    def _ensure_title_column(self) -> str:
        if self.title_col:
            return self.title_col

        self.title_col = "Title"
        if self.title_col not in self.fieldnames:
            self.fieldnames.append(self.title_col)
            self._refresh_columns()

        return self.title_col

    def _ensure_type_column(self) -> str:
        if self.type_col:
            return self.type_col

        self.type_col = "Work Item Type"
        if self.type_col not in self.fieldnames:
            self.fieldnames.append(self.type_col)
            self._refresh_columns()

        return self.type_col

    def _title_field_for_node(self, node: WorkItemNode) -> str:
        for _level, col, _value in get_populated_title_levels(node.row, self.title_level_columns):
            return col

        return self._ensure_title_column()

    def _attach_existing_node(
        self,
        parent: WorkItemNode,
        child: WorkItemNode,
        index: int | None = None,
    ) -> None:
        parent.add_child(child, index)

        if parent.item and child.item:
            child.item.parent_local_id = parent.item.local_id
            if child.item.local_id not in parent.item.children:
                if index is None or index >= len(parent.item.children):
                    parent.item.children.append(child.item.local_id)
                else:
                    parent.item.children.insert(max(index, 0), child.item.local_id)
            self._update_parent_field(child)
            self._refresh_dirty_state(child.item)

    def _detach_node(self, node: WorkItemNode) -> None:
        old_parent = node.parent

        if old_parent:
            old_parent.remove_child(node)

        if node.item and old_parent and old_parent.item:
            if node.item.local_id in old_parent.item.children:
                old_parent.item.children.remove(node.item.local_id)

    def _update_parent_field(self, node: WorkItemNode) -> None:
        if not node.item or not self.parent_col:
            return

        parent = node.parent
        parent_remote_id = parent.item.remote_id if parent and parent.item else None
        node.item.fields[self.parent_col] = str(parent_remote_id) if parent_remote_id is not None else ""
        self._refresh_dirty_state(node.item)

    def _refresh_item_core_fields(self, item: WorkItem) -> None:
        item.remote_id = _parse_int(item.fields.get(self.id_col)) if self.id_col else item.remote_id
        item.rev = _parse_int(item.fields.get(self.rev_col)) if self.rev_col else item.rev
        item.work_item_type = self.row_type(item.fields)
        item.title = self.row_title(item.fields)

    def _refresh_dirty_state(self, item: WorkItem) -> None:
        if item.state in {"new", "deleted"}:
            return

        hierarchy_changed = (
            not self._initializing
            and item.parent_local_id != item.original_parent_local_id
        )

        if item.fields != item.original_fields or hierarchy_changed:
            item.state = "modified"
        else:
            item.state = "unchanged"

    def _move_sibling(self, local_id: str, offset: int) -> bool:
        node = self._require_real_node(local_id)

        if not node.parent:
            return False

        siblings = node.parent.children
        index = siblings.index(node)
        new_index = index + offset

        if new_index < 0 or new_index >= len(siblings):
            return False

        siblings[index], siblings[new_index] = siblings[new_index], siblings[index]

        if node.parent.item:
            child_ids = node.parent.item.children
            child_ids[index], child_ids[new_index] = child_ids[new_index], child_ids[index]

        return True

    def _require_real_node(self, local_id: str) -> WorkItemNode:
        node = self.get_node(local_id)

        if not node or not node.item:
            raise KeyError(f"No work item with local ID {local_id!r}.")

        return node

    def _is_descendant(self, possible_descendant: WorkItemNode, ancestor: WorkItemNode) -> bool:
        current = possible_descendant

        while current.parent is not None:
            if current.parent is ancestor:
                return True
            current = current.parent

        return False

    def _snapshot_original_hierarchy(self) -> None:
        for node in self.all_nodes:
            if node.item:
                node.item.original_parent_local_id = node.item.parent_local_id

    def _sync_item_hierarchy_from_tree(self) -> None:
        for node in self.all_nodes:
            if node.item:
                node.item.children = []

        def visit(parent: WorkItemNode) -> None:
            for child in parent.children:
                if child.item:
                    child.item.parent_local_id = parent.item.local_id if parent.item else None
                    if parent.item:
                        parent.item.children.append(child.item.local_id)
                visit(child)

        visit(self.root)

    def _reset_validation_messages(self) -> None:
        self.validation_messages = []

        for node in self.all_nodes:
            if node.item:
                node.item.validation.clear()

    def _record_validation(self, validation: ValidationMessage) -> None:
        self.validation_messages.append(validation)

        if validation.local_id:
            node = self.nodes_by_local_id.get(validation.local_id)
            if node and node.item:
                node.item.validation.append(validation)

    def _source_sort_key(self, node: WorkItemNode) -> int:
        if node.item and node.item.source_row_index is not None:
            return node.item.source_row_index

        return 10**12

    def _validate_title_level_shape(self) -> None:
        if not self.title_level_columns:
            return

        stack_by_level: dict[int, WorkItemNode] = {}

        for node in sorted(self.all_nodes, key=self._source_sort_key):
            item = node.item

            if not item or item.source_row_index is None:
                continue

            populated = get_populated_title_levels(item.fields, self.title_level_columns)

            if len(populated) > 1:
                self._add_validation(
                    "error",
                    "Only one Title N column should be populated per row.",
                    node,
                    row_index=item.source_row_index,
                )

            level = populated[0][0] if populated else None

            if level is None:
                continue

            if level > 1 and (level - 1) not in stack_by_level:
                self._add_validation(
                    "warning",
                    f"Title level {level} has no previous level {level - 1} parent.",
                    node,
                    row_index=item.source_row_index,
                )

            stack_by_level[level] = node

            for existing_level in list(stack_by_level.keys()):
                if existing_level > level:
                    del stack_by_level[existing_level]

    def _validate_parent_id_fields(self) -> None:
        if not self.parent_col or not self.id_col:
            return

        by_remote_id: dict[str, WorkItemNode] = {}

        for node in self.all_nodes:
            item_id = self.row_id(node.row)

            if item_id and item_id not in by_remote_id:
                by_remote_id[item_id] = node

        for node in self.all_nodes:
            item = node.item

            if not item:
                continue

            parent_id = str(item.fields.get(self.parent_col, "")).strip()
            node_id = self.row_id(item.fields)

            if not parent_id:
                continue

            if parent_id == node_id:
                self._add_validation(
                    "error",
                    f"Work item {node_id} cannot be its own parent.",
                    node,
                    row_index=item.source_row_index,
                    field=self.parent_col,
                )
            elif parent_id not in by_remote_id:
                self._add_validation(
                    "warning",
                    f"Parent ID {parent_id} was not found in this file.",
                    node,
                    row_index=item.source_row_index,
                    field=self.parent_col,
                )

        self._validate_parent_id_cycles(by_remote_id)

    def _validate_parent_id_cycles(self, by_remote_id: dict[str, WorkItemNode]) -> None:
        reported_cycles: set[frozenset[str]] = set()

        for start in self.all_nodes:
            current = start
            path: list[str] = []
            path_by_id: dict[str, WorkItemNode] = {}

            while current.item:
                current_id = self.row_id(current.item.fields)

                if not current_id:
                    break

                if current_id in path_by_id:
                    cycle_ids = frozenset(path[path.index(current_id):])

                    if cycle_ids not in reported_cycles:
                        reported_cycles.add(cycle_ids)
                        cycle_node = path_by_id[current_id]
                        self._add_validation(
                            "error",
                            f"Work item {current_id} would create a parent-child cycle.",
                            cycle_node,
                            row_index=cycle_node.item.source_row_index if cycle_node.item else None,
                            field=self.parent_col,
                        )
                    break

                path.append(current_id)
                path_by_id[current_id] = current
                parent_id = str(current.item.fields.get(self.parent_col, "")).strip()

                if not parent_id or parent_id not in by_remote_id:
                    break

                current = by_remote_id[parent_id]

    def _validate_required_fields(self) -> None:
        for node in self.all_nodes:
            item = node.item

            if not item or item.state == "deleted":
                continue

            if not str(item.title).strip():
                title_field = self.title_col
                populated = get_populated_title_levels(item.fields, self.title_level_columns)
                if populated:
                    title_field = populated[0][1]
                self._add_validation(
                    "error",
                    "Work item title is required.",
                    node,
                    row_index=item.source_row_index,
                    field=title_field,
                )

            if not str(item.work_item_type).strip():
                self._add_validation(
                    "error",
                    "Work Item Type is required.",
                    node,
                    row_index=item.source_row_index,
                    field=self.type_col,
                )

    def _validate_remote_ids(self) -> None:
        if not self.id_col:
            return

        seen: dict[int, WorkItemNode] = {}

        for node in self.all_nodes:
            item = node.item

            if not item:
                continue

            raw_id = str(item.fields.get(self.id_col, "")).strip()
            parsed_id = _parse_int(raw_id)

            if raw_id and parsed_id is None:
                self._add_validation(
                    "error",
                    "Work item ID must be an integer.",
                    node,
                    row_index=item.source_row_index,
                    field=self.id_col,
                )
                continue

            if item.state == "new" and parsed_id is not None:
                self._add_validation(
                    "error",
                    "New work items must not have a remote ID.",
                    node,
                    row_index=item.source_row_index,
                    field=self.id_col,
                )

            if parsed_id is None:
                continue

            if parsed_id in seen:
                self._add_validation(
                    "error",
                    f"Duplicate work item ID {parsed_id}.",
                    node,
                    row_index=item.source_row_index,
                    field=self.id_col,
                )
            else:
                seen[parsed_id] = node

    def _validate_parent_link_consistency(self) -> None:
        for node in self.all_nodes:
            item = node.item

            if not item:
                continue

            expected_parent = node.parent.item.local_id if node.parent and node.parent.item else None

            if item.parent_local_id != expected_parent:
                self._add_validation(
                    "error",
                    "Parent local ID does not match the tree parent.",
                    node,
                    row_index=item.source_row_index,
                )

            if node.parent and node.parent.item and item.local_id not in node.parent.item.children:
                self._add_validation(
                    "error",
                    "Parent child list does not include this work item.",
                    node,
                    row_index=item.source_row_index,
                )

    def _validate_cycles(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()
        reported: set[str] = set()

        def visit(node: WorkItemNode) -> None:
            if not node.item:
                return

            local_id = node.item.local_id

            if local_id in visiting:
                if local_id not in reported:
                    reported.add(local_id)
                    self._add_validation(
                        "error",
                        "Work item hierarchy contains a cycle.",
                        node,
                        row_index=node.item.source_row_index,
                    )
                return

            if local_id in visited:
                return

            visiting.add(local_id)

            for child in node.children:
                visit(child)

            visiting.remove(local_id)
            visited.add(local_id)

        for node in self.all_nodes:
            visit(node)

    def _validate_deleted_parents(self) -> None:
        for node in self.all_nodes:
            item = node.item

            if not item or item.state != "deleted":
                continue

            for child in node.children:
                if child.item and child.item.state != "deleted":
                    self._add_validation(
                        "error",
                        "Deleted work item has non-deleted children.",
                        node,
                        row_index=item.source_row_index,
                    )
                    break

    def _add_validation(
        self,
        severity: str,
        message: str,
        node: WorkItemNode | None = None,
        row_index: int | None = None,
        field: str | None = None,
    ) -> None:
        local_id = node.item.local_id if node and node.item else None
        validation = ValidationMessage(
            severity=severity,  # type: ignore[arg-type]
            message=message,
            local_id=local_id,
            row_index=row_index,
            field=field,
        )

        self._record_validation(validation)
