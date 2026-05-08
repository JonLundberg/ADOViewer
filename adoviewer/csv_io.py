"""CSV reading and Azure DevOps column detection helpers."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Iterable


class CsvExportError(ValueError):
    """Raised when a model cannot be exported to CSV."""


AZURE_WEB_IMPORT_ITEM_LIMIT = 1000


def normalize_column_name(name: str) -> str:
    return "".join(ch.lower() for ch in str(name).strip() if ch.isalnum())


def find_column(fieldnames: Iterable[str], candidates: Iterable[str]) -> str | None:
    """Find a column in fieldnames by normalized exact match."""
    normalized_map = {
        normalize_column_name(col): col
        for col in fieldnames or []
    }

    for candidate in candidates:
        key = normalize_column_name(candidate)
        if key in normalized_map:
            return normalized_map[key]

    return None


def find_first_existing(row: dict[str, str], candidate_columns: Iterable[str | None]) -> str:
    for col in candidate_columns:
        if col and col in row:
            value = str(row.get(col, "")).strip()
            if value:
                return value
    return ""


def looks_like_title_level_column(name: str) -> bool:
    """
    Azure DevOps tree-style CSVs can use:
        Title 1, Title 2, Title 3, ...
    """
    text = str(name).strip().lower()
    if not text.startswith("title "):
        return False

    suffix = text.replace("title", "", 1).strip()
    return suffix.isdigit()


def get_title_level_columns(fieldnames: Iterable[str]) -> list[tuple[int, str]]:
    cols = []
    for col in fieldnames or []:
        if looks_like_title_level_column(col):
            level = int(str(col).strip().split()[-1])
            cols.append((level, col))

    cols.sort(key=lambda x: x[0])
    return cols


def get_populated_title_levels(
    row: dict[str, str],
    title_level_columns: Iterable[tuple[int, str]],
) -> list[tuple[int, str, str]]:
    populated = []

    for level, col in title_level_columns:
        value = str(row.get(col, "")).strip()
        if value:
            populated.append((level, col, value))

    return populated


def get_row_title(
    row: dict[str, str],
    title_col: str | None,
    title_level_columns: Iterable[tuple[int, str]],
) -> str:
    """
    Prefer normal Title. If Title 1/2/3 hierarchy columns exist,
    use the first non-empty Title N column.
    """
    for _level, _col, value in get_populated_title_levels(row, title_level_columns):
        return value

    if title_col:
        return str(row.get(title_col, "")).strip()

    return ""


def get_row_level(
    row: dict[str, str],
    title_level_columns: Iterable[tuple[int, str]],
) -> int | None:
    """Return hierarchy level from Title 1 / Title 2 / Title 3 columns."""
    populated = get_populated_title_levels(row, title_level_columns)

    if not populated:
        return None

    return populated[0][0]


@dataclass(frozen=True)
class ColumnMap:
    id_col: str | None
    rev_col: str | None
    type_col: str | None
    title_col: str | None
    state_col: str | None
    assigned_to_col: str | None
    tags_col: str | None
    url_col: str | None
    parent_col: str | None
    area_col: str | None
    iteration_col: str | None
    effort_col: str | None
    remaining_col: str | None
    completed_col: str | None
    title_level_columns: list[tuple[int, str]]


def detect_columns(fieldnames: Iterable[str]) -> ColumnMap:
    fieldnames = list(fieldnames or [])

    return ColumnMap(
        id_col=find_column(fieldnames, [
            "ID",
            "Id",
            "Work Item ID",
            "System.Id",
        ]),
        rev_col=find_column(fieldnames, [
            "Rev",
            "Revision",
            "System.Rev",
        ]),
        type_col=find_column(fieldnames, [
            "Work Item Type",
            "System.WorkItemType",
            "Type",
        ]),
        title_col=find_column(fieldnames, [
            "Title",
            "System.Title",
        ]),
        state_col=find_column(fieldnames, [
            "State",
            "System.State",
        ]),
        assigned_to_col=find_column(fieldnames, [
            "Assigned To",
            "System.AssignedTo",
        ]),
        tags_col=find_column(fieldnames, [
            "Tags",
            "System.Tags",
        ]),
        url_col=find_column(fieldnames, [
            "URL",
            "Url",
            "Web URL",
            "Work Item URL",
            "Hyperlink",
        ]),
        parent_col=find_column(fieldnames, [
            "Parent",
            "Parent ID",
            "Parent Id",
            "Parent Work Item",
            "Parent Work Item ID",
            "Parent Work Item Id",
            "System.Parent",
        ]),
        area_col=find_column(fieldnames, [
            "Area Path",
            "System.AreaPath",
        ]),
        iteration_col=find_column(fieldnames, [
            "Iteration Path",
            "System.IterationPath",
        ]),
        effort_col=find_column(fieldnames, [
            "Effort",
            "Microsoft.VSTS.Scheduling.Effort",
            "Story Points",
            "Microsoft.VSTS.Scheduling.StoryPoints",
            "Original Estimate",
            "Microsoft.VSTS.Scheduling.OriginalEstimate",
        ]),
        remaining_col=find_column(fieldnames, [
            "Remaining Work",
            "Microsoft.VSTS.Scheduling.RemainingWork",
        ]),
        completed_col=find_column(fieldnames, [
            "Completed Work",
            "Microsoft.VSTS.Scheduling.CompletedWork",
        ]),
        title_level_columns=get_title_level_columns(fieldnames),
    )


def read_csv_file(path: str) -> tuple[list[str], list[dict[str, str]]]:
    """
    Azure DevOps CSV exports are commonly UTF-8 with BOM,
    but Windows/Excel workflows may produce cp1252.
    """
    encodings_to_try = ["utf-8-sig", "utf-8", "cp1252"]
    last_error = None

    for encoding in encodings_to_try:
        try:
            with open(path, "r", newline="", encoding=encoding) as f:
                sample = f.read(4096)
                f.seek(0)

                try:
                    dialect = csv.Sniffer().sniff(sample)
                except csv.Error:
                    dialect = csv.excel

                reader = csv.DictReader(f, dialect=dialect)
                rows = list(reader)
                fieldnames = reader.fieldnames or []

                return fieldnames, rows

        except Exception as ex:  # pragma: no cover - exercised through final error
            last_error = ex

    raise RuntimeError(f"Could not read CSV file: {last_error}")


def iter_model_items_with_depth(model: Any) -> list[tuple[Any, int]]:
    """Return non-deleted real work items in parent-before-child export order."""
    items: list[tuple[Any, int]] = []

    def visit(node: Any, depth: int) -> None:
        if getattr(node, "synthetic", False):
            for child in node.children:
                visit(child, depth)
            return

        item = getattr(node, "item", None)

        if item and item.state != "deleted":
            items.append((item, depth))
            child_depth = depth + 1
        else:
            child_depth = depth

        for child in node.children:
            visit(child, child_depth)

    for child in model.root.children:
        visit(child, 1)

    return items


def build_azure_tree_csv(model: Any) -> tuple[list[str], list[dict[str, str]]]:
    """
    Build an Azure DevOps tree CSV using Title 1 / Title 2 / ... columns.

    The export is intentionally based on the local tree, not Parent ID, so
    unsaved work items can be exported with their hierarchy intact.
    """
    messages = model.validate()
    errors = [message for message in messages if message.severity == "error"]

    if errors:
        raise CsvExportError("Fix validation errors before exporting.")

    items_with_depth = iter_model_items_with_depth(model)

    if len(items_with_depth) > AZURE_WEB_IMPORT_ITEM_LIMIT:
        raise CsvExportError(
            "Azure DevOps web CSV import supports at most "
            f"{AZURE_WEB_IMPORT_ITEM_LIMIT} work items per file; "
            f"this export contains {len(items_with_depth)}."
        )

    max_depth = max((depth for _item, depth in items_with_depth), default=1)
    title_columns = [f"Title {level}" for level in range(1, max_depth + 1)]
    type_col = model.type_col or "Work Item Type"
    id_col = model.id_col or "ID"
    include_id = any(item.remote_id is not None for item, _depth in items_with_depth)

    fieldnames: list[str] = []

    if include_id:
        fieldnames.append(id_col)

    fieldnames.append(type_col)
    fieldnames.extend(title_columns)
    fieldnames.extend(
        field_name
        for field_name in azure_tree_extra_fieldnames(model, id_col, type_col)
        if field_name not in fieldnames
    )

    rows = []

    for item, depth in items_with_depth:
        row = {field_name: "" for field_name in fieldnames}

        if include_id:
            row[id_col] = "" if item.state == "new" else _remote_id_text(item, model)

        row[type_col] = item.work_item_type
        row[title_columns[depth - 1]] = item.title

        for field_name in fieldnames:
            if field_name in {id_col, type_col} or field_name in title_columns:
                continue
            row[field_name] = str(item.fields.get(field_name, ""))

        rows.append(row)

    return fieldnames, rows


def azure_tree_extra_fieldnames(model: Any, id_col: str, type_col: str) -> list[str]:
    excluded = {
        id_col,
        type_col,
        model.title_col,
        model.parent_col,
    }
    excluded.update(column for _level, column in model.title_level_columns)
    excluded.discard(None)

    return [
        field_name
        for field_name in model.fieldnames
        if field_name not in excluded
    ]


def write_azure_tree_csv(model: Any, path: str) -> tuple[list[str], list[dict[str, str]]]:
    fieldnames, rows = build_azure_tree_csv(model)
    write_csv_rows(path, fieldnames, rows)

    return fieldnames, rows


def build_round_trip_csv(model: Any) -> tuple[list[str], list[dict[str, str]]]:
    """
    Build a CSV that preserves the current project/source columns where possible.

    Round-trip export keeps field order and unknown/custom fields intact. If the
    source uses Title N hierarchy columns, those columns are regenerated from the
    current local tree so reparenting and new unsaved hierarchy are represented.
    """
    messages = model.validate()
    errors = [message for message in messages if message.severity == "error"]

    if errors:
        raise CsvExportError("Fix validation errors before exporting.")

    items_with_depth = iter_model_items_with_depth(model)
    fieldnames = list(model.fieldnames)

    if model.title_level_columns:
        max_depth = max((depth for _item, depth in items_with_depth), default=1)
        fieldnames = round_trip_title_level_fieldnames(model, fieldnames, max_depth)

    rows = []

    for item, depth in items_with_depth:
        row = {
            field_name: str(item.fields.get(field_name, ""))
            for field_name in fieldnames
        }

        if model.id_col and model.id_col in row and item.state == "new":
            row[model.id_col] = ""

        if model.type_col and model.type_col in row:
            row[model.type_col] = item.work_item_type

        if model.title_level_columns:
            for field_name in fieldnames:
                if looks_like_title_level_column(field_name):
                    row[field_name] = ""
            row[f"Title {depth}"] = item.title
        elif model.title_col and model.title_col in row:
            row[model.title_col] = item.title

        rows.append(row)

    return fieldnames, rows


def round_trip_title_level_fieldnames(
    model: Any,
    fieldnames: list[str],
    max_depth: int,
) -> list[str]:
    title_level_names = {
        column
        for _level, column in model.title_level_columns
    }
    existing_title_levels = get_title_level_columns(fieldnames)
    fieldnames = [
        field_name
        for field_name in fieldnames
        if field_name != model.title_col or field_name in title_level_names
    ]

    if existing_title_levels:
        insert_at = max(fieldnames.index(column) for _level, column in existing_title_levels) + 1
    else:
        insert_at = len(fieldnames)

    for level in range(1, max_depth + 1):
        column = f"Title {level}"
        if column in fieldnames:
            continue
        fieldnames.insert(insert_at, column)
        insert_at += 1

    return fieldnames


def write_round_trip_csv(model: Any, path: str) -> tuple[list[str], list[dict[str, str]]]:
    fieldnames, rows = build_round_trip_csv(model)
    write_csv_rows(path, fieldnames, rows)

    return fieldnames, rows


def render_csv_text(fieldnames: list[str], rows: list[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, dialect=csv.excel)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def write_csv_rows(path: str, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        f.write(render_csv_text(fieldnames, rows))


def _remote_id_text(item: Any, model: Any) -> str:
    if item.remote_id is not None:
        return str(item.remote_id)

    if model.id_col:
        return str(item.fields.get(model.id_col, "")).strip()

    return ""
