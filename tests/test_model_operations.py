from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from adoviewer.csv_io import read_csv_file
from adoviewer.tree_model import WorkItemModel


FIXTURES = Path(__file__).parent / "fixtures"


def local_ids():
    counter = itertools.count(1)
    return lambda: f"wi-{next(counter)}"


def load_model(name: str) -> WorkItemModel:
    fieldnames, rows = read_csv_file(str(FIXTURES / name))
    return WorkItemModel(fieldnames, rows, local_id_factory=local_ids())


def test_add_root_and_child_create_local_only_work_items():
    model = WorkItemModel(
        ["ID", "Work Item Type", "Title", "Parent ID"],
        [],
        local_id_factory=local_ids(),
    )

    parent = model.add_root("Offline epic", "Epic")
    child = model.add_child(parent.item.local_id, "Offline feature", "Feature")
    grandchild = model.add_child(child.item.local_id, "Offline story", "User Story")

    assert parent.item.local_id == "wi-1"
    assert parent.item.remote_id is None
    assert child.item.parent_local_id == "wi-1"
    assert grandchild.item.parent_local_id == "wi-2"
    assert parent.item.children == ["wi-2"]
    assert child.item.children == ["wi-3"]
    assert [item.state for item in model.flatten()] == ["new", "new", "new"]
    assert model.dirty_counts() == {
        "new": 3,
        "modified": 0,
        "deleted": 0,
        "unchanged": 0,
    }


def test_add_sibling_preserves_parent_and_insert_position():
    model = load_model("parent_id_tree.csv")
    epic = model.root.children[0]
    cart = epic.children[0]
    payment = epic.children[1]

    sibling = model.add_sibling(cart.item.local_id, "Shipping", "Feature")

    assert [child.item.title for child in epic.children] == ["Cart", "Shipping", "Payment"]
    assert sibling.item.parent_local_id == epic.item.local_id
    assert epic.item.children == ["wi-2", "wi-5", "wi-4"]


def test_edit_existing_field_marks_item_modified_and_can_return_unchanged():
    model = load_model("parent_id_tree.csv")
    cart = model.root.children[0].children[0]

    model.edit_title(cart.item.local_id, "Cart checkout")

    assert cart.item.title == "Cart checkout"
    assert cart.item.fields["Title"] == "Cart checkout"
    assert cart.item.state == "modified"

    model.edit_title(cart.item.local_id, "Cart")

    assert cart.item.title == "Cart"
    assert cart.item.state == "unchanged"


def test_editing_new_standard_field_refreshes_detected_columns():
    model = WorkItemModel(
        ["ID", "Title"],
        [
            {"ID": "1", "Title": "Minimal item"},
        ],
        local_id_factory=local_ids(),
    )
    item = model.all_nodes[0].item

    model.edit_field(item.local_id, "Work Item Type", "Task")
    model.edit_field(item.local_id, "State", "New")

    assert model.type_col == "Work Item Type"
    assert model.state_col == "State"
    assert item.work_item_type == "Task"
    assert model.row_state(item.fields) == "New"


def test_edit_title_updates_existing_title_level_column():
    model = load_model("title_levels_tree.csv")
    api = model.root.children[0].children[0]

    model.edit_title(api.item.local_id, "Services API")

    assert api.item.title == "Services API"
    assert api.item.fields["Title 2"] == "Services API"
    assert "Title" not in model.fieldnames
    assert api.item.state == "modified"


def test_soft_delete_and_restore_preserve_dirty_state_rules():
    model = load_model("parent_id_tree.csv")
    cart = model.root.children[0].children[0]

    model.soft_delete(cart.item.local_id)
    assert cart.item.state == "deleted"

    model.restore(cart.item.local_id)
    assert cart.item.state == "unchanged"

    model.edit_title(cart.item.local_id, "Cart checkout")
    model.soft_delete(cart.item.local_id)
    model.restore(cart.item.local_id)
    assert cart.item.state == "modified"


def test_reparent_updates_local_parent_links_and_parent_id_field():
    model = load_model("parent_id_tree.csv")
    epic = model.root.children[0]
    cart = epic.children[0]
    payment = epic.children[1]

    model.reparent(payment.item.local_id, cart.item.local_id)

    assert payment.parent is cart
    assert payment.item.parent_local_id == cart.item.local_id
    assert payment.item.fields["Parent ID"] == "11"
    assert payment.item.state == "modified"
    assert epic.item.children == ["wi-2"]
    assert cart.item.children == ["wi-3", "wi-4"]


def test_reparent_prevents_cycles():
    model = load_model("parent_id_tree.csv")
    epic = model.root.children[0]
    story = epic.children[0].children[0]

    with pytest.raises(ValueError):
        model.reparent(epic.item.local_id, story.item.local_id)


def test_move_up_and_down_change_sibling_order():
    model = load_model("parent_id_tree.csv")
    epic = model.root.children[0]
    cart = epic.children[0]
    payment = epic.children[1]

    assert model.move_up(payment.item.local_id) is True
    assert [child.item.title for child in epic.children] == ["Payment", "Cart"]
    assert epic.item.children == ["wi-4", "wi-2"]

    assert model.move_down(payment.item.local_id) is True
    assert [child.item.title for child in epic.children] == ["Cart", "Payment"]
    assert epic.item.children == ["wi-2", "wi-4"]

    assert model.move_up(cart.item.local_id) is False


def test_indent_and_outdent_update_local_hierarchy_without_parent_id_column():
    model = load_model("title_levels_tree.csv")
    platform = model.root.children[0]
    api = platform.children[0]
    ui = platform.children[1]

    assert model.indent(ui.item.local_id) is True

    assert ui.parent is api
    assert ui.item.parent_local_id == api.item.local_id
    assert api.item.children == ["wi-3", "wi-4"]
    assert ui.item.state == "modified"

    assert model.outdent(ui.item.local_id) is True

    assert ui.parent is platform
    assert ui.item.parent_local_id == platform.item.local_id
    assert platform.item.children == ["wi-2", "wi-4"]
    assert ui.item.state == "unchanged"


def test_indent_and_outdent_report_noop_boundaries():
    model = load_model("parent_id_tree.csv")
    epic = model.root.children[0]
    cart = epic.children[0]

    assert model.indent(cart.item.local_id) is False
    assert model.outdent(epic.item.local_id) is False


def test_validate_reports_required_fields_and_deleted_parent_conflict():
    model = WorkItemModel(
        ["ID", "Work Item Type", "Title"],
        [],
        local_id_factory=local_ids(),
    )
    model.add_root("", "")
    parent = model.add_root("Parent", "Feature")
    model.add_child(parent.item.local_id, "Child", "Task")
    model.soft_delete(parent.item.local_id)

    messages = [(msg.severity, msg.message) for msg in model.validate()]

    assert ("error", "Work item title is required.") in messages
    assert ("error", "Work Item Type is required.") in messages
    assert ("error", "Deleted work item has non-deleted children.") in messages
