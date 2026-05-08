from __future__ import annotations

import csv
import itertools
from pathlib import Path

import pytest

from adoviewer.csv_io import (
    CsvExportError,
    build_azure_tree_csv,
    build_round_trip_csv,
    read_csv_file,
    render_csv_text,
    write_azure_tree_csv,
    write_round_trip_csv,
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


def test_azure_tree_export_blocks_more_than_1000_work_items():
    model = WorkItemModel(
        ["ID", "Work Item Type", "Title"],
        [],
        local_id_factory=local_ids(),
    )

    for index in range(1001):
        model.add_root(f"Task {index}", "Task")

    with pytest.raises(CsvExportError, match="at most 1000 work items"):
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


def test_render_csv_text_matches_writer_quoting():
    text = render_csv_text(
        ["Title", "Description"],
        [
            {
                "Title": "Comma, quote \" and newline",
                "Description": "Line 1\nLine 2",
            },
        ],
    )

    assert text == 'Title,Description\r\n"Comma, quote "" and newline","Line 1\nLine 2"\r\n'


def test_round_trip_export_preserves_parent_id_field_order_and_custom_fields():
    model = load_model("parent_id_tree.csv")
    cart = model.root.children[0].children[0]
    new_child = model.add_child(
        cart.item.local_id,
        "Offline task",
        "Task",
        fields={"Tags": "Migration; Offline"},
    )
    model.edit_field(new_child.item.local_id, "Custom Field", "Preserved")

    fieldnames, rows = build_round_trip_csv(model)

    assert fieldnames == [
        "ID",
        "Work Item Type",
        "Title",
        "State",
        "Parent ID",
        "Tags",
        "Custom Field",
    ]
    assert [row["Title"] for row in rows] == [
        "Checkout",
        "Cart",
        "Add item",
        "Offline task",
        "Payment",
    ]
    assert rows[3]["ID"] == ""
    assert rows[3]["Parent ID"] == "11"
    assert rows[3]["Tags"] == "Migration; Offline"
    assert rows[3]["Custom Field"] == "Preserved"


def test_round_trip_export_regenerates_title_levels_from_local_tree():
    model = load_model("title_levels_tree.csv")
    platform = model.root.children[0]
    api = platform.children[0]
    ui = platform.children[1]
    model.indent(ui.item.local_id)
    story = api.children[0]
    model.add_child(story.item.local_id, "Export preview", "Task", fields={"State": "New"})

    fieldnames, rows = build_round_trip_csv(model)

    assert fieldnames == [
        "ID",
        "Work Item Type",
        "Title 1",
        "Title 2",
        "Title 3",
        "Title 4",
        "State",
    ]
    assert "Title" not in fieldnames
    assert [row["Title 1"] for row in rows] == ["Platform", "", "", "", ""]
    assert [row["Title 2"] for row in rows] == ["", "API", "", "", ""]
    assert [row["Title 3"] for row in rows] == ["", "", "List work items", "", "UI"]
    assert [row["Title 4"] for row in rows] == ["", "", "", "Export preview", ""]
    assert all(
        sum(1 for key, value in row.items() if key.startswith("Title ") and value) == 1
        for row in rows
    )


def test_round_trip_export_round_trips_title_level_hierarchy(tmp_path):
    model = load_model("title_levels_tree.csv")
    api = model.root.children[0].children[0]
    model.add_child(api.item.local_id, "Export commas, quotes, and\nnewlines", "Task")
    output_path = tmp_path / "round-trip.csv"

    write_round_trip_csv(model, str(output_path))
    fieldnames, rows = read_csv_file(str(output_path))
    loaded = WorkItemModel(fieldnames, rows, local_id_factory=local_ids())

    platform = loaded.root.children[0]
    loaded_api = platform.children[0]

    assert [item.title for item in loaded.flatten()] == [
        "Platform",
        "API",
        "List work items",
        "Export commas, quotes, and\nnewlines",
        "UI",
    ]
    assert loaded_api.children[1].item.title == "Export commas, quotes, and\nnewlines"
