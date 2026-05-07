from __future__ import annotations

import itertools
from pathlib import Path

from adoviewer.csv_io import detect_columns, read_csv_file
from adoviewer.tree_model import WorkItemModel


FIXTURES = Path(__file__).parent / "fixtures"


def local_ids():
    counter = itertools.count(1)
    return lambda: f"wi-{next(counter)}"


def load_model(name: str) -> WorkItemModel:
    fieldnames, rows = read_csv_file(str(FIXTURES / name))
    return WorkItemModel(fieldnames, rows, local_id_factory=local_ids())


def test_reads_csv_and_detects_common_columns():
    fieldnames, rows = read_csv_file(str(FIXTURES / "flat_basic.csv"))
    columns = detect_columns(fieldnames)

    assert fieldnames == ["ID", "Work Item Type", "Title", "State", "Custom Field"]
    assert rows[0]["Title"] == "Crash on launch"
    assert rows[1]["Custom Field"] == "Beta"
    assert columns.id_col == "ID"
    assert columns.type_col == "Work Item Type"
    assert columns.title_col == "Title"
    assert columns.state_col == "State"


def test_parent_id_tree_import_uses_local_ids_for_hierarchy():
    model = load_model("parent_id_tree.csv")

    epic = model.root.children[0]
    cart = epic.children[0]
    story = cart.children[0]
    payment = epic.children[1]

    assert epic.item.local_id == "wi-1"
    assert epic.item.remote_id == 10
    assert cart.item.local_id == "wi-2"
    assert cart.item.parent_local_id == "wi-1"
    assert story.item.parent_local_id == "wi-2"
    assert payment.item.parent_local_id == "wi-1"
    assert epic.item.children == ["wi-2", "wi-4"]
    assert model.row_title(story.row) == "Add item"


def test_title_level_tree_import_preserves_parent_before_child_order():
    model = load_model("title_levels_tree.csv")

    platform = model.root.children[0]
    api = platform.children[0]
    story = api.children[0]
    ui = platform.children[1]

    assert model.title_level_columns == [(1, "Title 1"), (2, "Title 2"), (3, "Title 3")]
    assert [item.title for item in model.flatten()] == [
        "Platform",
        "API",
        "List work items",
        "UI",
    ]
    assert api.item.parent_local_id == platform.item.local_id
    assert story.item.parent_local_id == api.item.local_id
    assert ui.item.parent_local_id == platform.item.local_id


def test_flat_csv_import_groups_by_type_with_synthetic_nodes():
    model = load_model("flat_basic.csv")

    group_titles = [node.row["Synthetic Title"] for node in model.root.children]

    assert group_titles == ["Bug", "Task"]
    assert all(group.synthetic for group in model.root.children)
    assert [node.item.local_id for node in model.all_nodes] == ["wi-1", "wi-2"]
    assert model.root.children[0].children[0].item.title == "Crash on launch"


def test_import_records_warnings_for_missing_parent_ids():
    model = load_model("missing_parent.csv")

    assert model.root.children[0].item.title == "Orphaned child"
    assert [(msg.severity, msg.field) for msg in model.validation_messages] == [
        ("warning", "Parent ID"),
    ]


def test_import_records_title_level_shape_problems():
    model = load_model("bad_title_levels.csv")

    messages = [(msg.severity, msg.message) for msg in model.validation_messages]

    assert ("error", "Only one Title N column should be populated per row.") in messages
    assert ("warning", "Title level 3 has no previous level 2 parent.") in messages
