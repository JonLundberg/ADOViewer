from __future__ import annotations

import csv
import itertools
from pathlib import Path

import pytest

from adoviewer.csv_io import (
    CsvExportError,
    build_azure_tree_csv,
    read_csv_file,
    write_azure_tree_csv,
)
from adoviewer.tree_model import WorkItemModel


FIXTURES = Path(__file__).parent / "fixtures"


def local_ids():
    counter = itertools.count(1)
    return lambda: f"wi-{next(counter)}"


def load_model(name: str) -> WorkItemModel:
    fieldnames, rows = read_csv_file(str(FIXTURES / name))
    return WorkItemModel(fieldnames, rows, local_id_factory=local_ids())


def test_azure_tree_export_for_all_new_items_omits_id_and_uses_title_levels():
    model = WorkItemModel(
        ["ID", "Work Item Type", "Title", "State", "Tags", "Custom Field"],
        [],
        local_id_factory=local_ids(),
    )
    epic = model.add_root(
        "Offline epic",
        "Epic",
        fields={"State": "New", "Tags": "Migration", "Custom Field": "Alpha"},
    )
    feature = model.add_child(
        epic.item.local_id,
        "Offline feature",
        "Feature",
        fields={"State": "New", "Tags": "Migration", "Custom Field": "Beta"},
    )
    model.add_child(
        feature.item.local_id,
        "Offline story",
        "User Story",
        fields={"State": "New", "Tags": "Migration", "Custom Field": "Gamma"},
    )

    fieldnames, rows = build_azure_tree_csv(model)

    assert fieldnames == [
        "Work Item Type",
        "Title 1",
        "Title 2",
        "Title 3",
        "State",
        "Tags",
        "Custom Field",
    ]
    assert [row["Work Item Type"] for row in rows] == ["Epic", "Feature", "User Story"]
    assert [row["Title 1"] for row in rows] == ["Offline epic", "", ""]
    assert [row["Title 2"] for row in rows] == ["", "Offline feature", ""]
    assert [row["Title 3"] for row in rows] == ["", "", "Offline story"]
    assert all(
        sum(1 for key, value in row.items() if key.startswith("Title ") and value) == 1
        for row in rows
    )
    assert [row["Custom Field"] for row in rows] == ["Alpha", "Beta", "Gamma"]


def test_azure_tree_export_for_mixed_existing_and_new_items_blanks_new_ids():
    model = load_model("parent_id_tree.csv")
    epic = model.root.children[0]
    cart = epic.children[0]
    model.add_child(cart.item.local_id, "Offline task", "Task")

    fieldnames, rows = build_azure_tree_csv(model)

    assert fieldnames[:4] == ["ID", "Work Item Type", "Title 1", "Title 2"]
    assert "Parent ID" not in fieldnames
    assert "Title" not in fieldnames
    assert [row["Title 1"] for row in rows] == ["Checkout", "", "", "", ""]
    assert [row["Title 2"] for row in rows] == ["", "Cart", "", "", "Payment"]
    assert [row["Title 3"] for row in rows] == ["", "", "Add item", "Offline task", ""]
    assert [row["ID"] for row in rows] == ["10", "11", "12", "", "13"]


def test_azure_tree_export_skips_deleted_items():
    model = load_model("parent_id_tree.csv")
    payment = model.root.children[0].children[1]

    model.soft_delete(payment.item.local_id)

    _fieldnames, rows = build_azure_tree_csv(model)

    assert [row["Title 1"] or row["Title 2"] or row["Title 3"] for row in rows] == [
        "Checkout",
        "Cart",
        "Add item",
    ]


def test_azure_tree_export_blocks_validation_errors():
    model = WorkItemModel(
        ["ID", "Work Item Type", "Title"],
        [],
        local_id_factory=local_ids(),
    )
    model.add_root("", "Task")

    with pytest.raises(CsvExportError):
        build_azure_tree_csv(model)


def test_write_azure_tree_csv_outputs_readable_utf8_sig_csv(tmp_path):
    model = WorkItemModel(
        ["ID", "Work Item Type", "Title", "State"],
        [],
        local_id_factory=local_ids(),
    )
    model.add_root("Title, with comma", "Task", fields={"State": "New"})
    output_path = tmp_path / "azure-tree.csv"

    fieldnames, rows = write_azure_tree_csv(model, str(output_path))

    assert fieldnames == ["Work Item Type", "Title 1", "State"]
    assert rows[0]["Title 1"] == "Title, with comma"

    with open(output_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        written_rows = list(reader)

    assert reader.fieldnames == ["Work Item Type", "Title 1", "State"]
    assert written_rows == [
        {
            "Work Item Type": "Task",
            "Title 1": "Title, with comma",
            "State": "New",
        },
    ]
