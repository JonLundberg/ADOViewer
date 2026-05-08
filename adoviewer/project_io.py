"""ADOViewer project file read/write helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .tree_model import WorkItemModel


PROJECT_FORMAT = "adoviewer.project"
PROJECT_VERSION = 1


@dataclass
class ProjectDocument:
    model: WorkItemModel
    source_path: str | None = None


def model_to_project(model: WorkItemModel, source_path: str | None = None) -> dict[str, object]:
    return {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "source_path": source_path,
        "fieldnames": list(model.fieldnames),
        "root_order": [
            item.local_id
            for item in model.flatten()
            if item.parent_local_id is None
        ],
        "work_items": [
            {
                "local_id": item.local_id,
                "remote_id": item.remote_id,
                "rev": item.rev,
                "work_item_type": item.work_item_type,
                "title": item.title,
                "fields": dict(item.fields),
                "original_fields": dict(item.original_fields),
                "parent_local_id": item.parent_local_id,
                "original_parent_local_id": item.original_parent_local_id,
                "children": list(item.children),
                "source_row_index": item.source_row_index,
                "state": item.state,
            }
            for item in model.flatten()
        ],
    }


def project_to_model(
    data: dict[str, object],
    local_id_factory: Callable[[], str] | None = None,
) -> ProjectDocument:
    if data.get("format") != PROJECT_FORMAT:
        raise ValueError("This is not an ADOViewer project file.")

    version = data.get("version")
    if version != PROJECT_VERSION:
        raise ValueError(f"Unsupported ADOViewer project version: {version!r}.")

    fieldnames = data.get("fieldnames")
    work_items = data.get("work_items")
    root_order = data.get("root_order")

    if not isinstance(fieldnames, list):
        raise ValueError("Project file fieldnames must be a list.")

    if not isinstance(work_items, list):
        raise ValueError("Project file work_items must be a list.")

    if root_order is not None and not isinstance(root_order, list):
        raise ValueError("Project file root_order must be a list.")

    model = WorkItemModel.from_project_items(
        [str(fieldname) for fieldname in fieldnames],
        work_items,
        root_order=[str(local_id) for local_id in root_order] if root_order else None,
        local_id_factory=local_id_factory,
    )
    source_path = data.get("source_path")

    return ProjectDocument(
        model=model,
        source_path=str(source_path) if source_path else None,
    )


def save_project_file(
    model: WorkItemModel,
    path: str,
    source_path: str | None = None,
) -> None:
    data = model_to_project(model, source_path=source_path)

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_project_file(
    path: str,
    local_id_factory: Callable[[], str] | None = None,
) -> ProjectDocument:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Project file root must be an object.")

    return project_to_model(data, local_id_factory=local_id_factory)
