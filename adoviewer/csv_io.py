"""CSV reading and Azure DevOps column detection helpers."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Iterable


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
