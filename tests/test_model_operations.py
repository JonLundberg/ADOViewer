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
