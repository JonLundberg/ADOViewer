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

        self.root = WorkItemNode("__root__", synthetic=True)
        self.all_nodes: list[WorkItemNode] = []
        self.nodes_by_local_id: dict[str, WorkItemNode] = {}
        self.validation_messages: list[ValidationMessage] = []

        self.build_tree()

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

        item.fields[field_name] = value
        self._refresh_item_core_fields(item)
        self._refresh_dirty_state(item)

    def edit_title(self, local_id: str, title: str) -> None:
        title_col = self._ensure_title_column()
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
        else:
            self._attach_existing_node(new_parent, node, index)

    def move_up(self, local_id: str) -> bool:
        return self._move_sibling(local_id, -1)

    def move_down(self, local_id: str) -> bool:
        return self._move_sibling(local_id, 1)

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

    def _node_from_row(self, index: int, row: dict[str, str]) -> WorkItemNode:
        local_id = self.local_id_factory()
        item = WorkItem(
            local_id=local_id,
            remote_id=_parse_int(self.row_id(row)),
            rev=_parse_int(row.get(self.rev_col)) if self.rev_col else None,
            work_item_type=self.row_type(row),
            title=self.row_title(row),
            fields=row,
            original_fields=dict(row),
            parent_local_id=None,
            source_row_index=index,
            state="unchanged",
        )
        node = WorkItemNode(local_id, item=item)
        self.all_nodes.append(node)
        self.nodes_by_local_id[local_id] = node
        return node

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
            local_id=self.local_id_factory(),
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

        return self.title_col

    def _ensure_type_column(self) -> str:
        if self.type_col:
            return self.type_col

        self.type_col = "Work Item Type"
        if self.type_col not in self.fieldnames:
            self.fieldnames.append(self.type_col)

        return self.type_col

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

        if item.fields != item.original_fields:
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
        self.validation_messages.append(validation)

        if node and node.item:
            node.item.validation.append(validation)
