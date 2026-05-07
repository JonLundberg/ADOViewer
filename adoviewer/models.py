"""Canonical work item data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


WorkItemState = Literal["unchanged", "new", "modified", "deleted"]
ValidationSeverity = Literal["info", "warning", "error"]


@dataclass
class ValidationMessage:
    severity: ValidationSeverity
    message: str
    local_id: str | None = None
    row_index: int | None = None
    field: str | None = None


@dataclass
class WorkItem:
    local_id: str
    remote_id: int | None
    rev: int | None
    work_item_type: str
    title: str
    fields: dict[str, str]
    original_fields: dict[str, str]
    parent_local_id: str | None
    children: list[str] = field(default_factory=list)
    source_row_index: int | None = None
    state: WorkItemState = "unchanged"
    validation: list[ValidationMessage] = field(default_factory=list)

    @property
    def is_dirty(self) -> bool:
        return self.state in {"new", "modified", "deleted"}


class WorkItemNode:
    """Tree node wrapper used by the UI and tree operations."""

    def __init__(
        self,
        key: str,
        item: WorkItem | None = None,
        row: dict[str, str] | None = None,
        synthetic: bool = False,
    ) -> None:
        self.key = key
        self.item = item
        self.synthetic = synthetic
        self.synthetic_row = row or {}
        self.children: list[WorkItemNode] = []
        self.parent: WorkItemNode | None = None

    @property
    def row(self) -> dict[str, str]:
        if self.item is not None:
            return self.item.fields
        return self.synthetic_row

    @property
    def local_id(self) -> str | None:
        if self.item is None:
            return None
        return self.item.local_id

    def add_child(self, child: WorkItemNode, index: int | None = None) -> None:
        child.parent = self

        if index is None or index >= len(self.children):
            self.children.append(child)
        else:
            self.children.insert(max(index, 0), child)

    def remove_child(self, child: WorkItemNode) -> None:
        self.children.remove(child)
        child.parent = None
