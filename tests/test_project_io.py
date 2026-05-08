from __future__ import annotations

import itertools
from pathlib import Path

from adoviewer.csv_io import read_csv_file
from adoviewer.project_io import load_project_file, save_project_file
from adoviewer.tree_model import WorkItemModel


FIXTURES = Path(__file__).parent / "fixtures"


def local_ids(start: int = 1):
    counter = itertools.count(start)
    return lambda: f"wi-{next(counter)}"


def load_model(name: str) -> WorkItemModel:
    fieldnames, rows = read_csv_file(str(FIXTURES / name))
    return WorkItemModel(fieldnames, rows, local_id_factory=local_ids())


def test_project_round_trip_preserves_local_ids_hierarchy_and_dirty_state(tmp_path):
    model = load_model("parent_id_tree.csv")
    epic = model.root.children[0]
    cart = epic.children[0]
    story = cart.children[0]
    payment = epic.children[1]

    model.edit_title(cart.item.local_id, "Cart checkout")
    model.soft_delete(payment.item.local_id)
    new_child = model.add_child(cart.item.local_id, "Offline task", "Task")
    model.move_up(payment.item.local_id)

    project_path = tmp_path / "items.adoviewer.json"
    save_project_file(model, str(project_path), source_path="source.csv")

    document = load_project_file(str(project_path), local_id_factory=local_ids())
    loaded = document.model
    loaded_epic = loaded.get_node(epic.item.local_id)
    loaded_cart = loaded.get_node(cart.item.local_id)
    loaded_story = loaded.get_node(story.item.local_id)
    loaded_payment = loaded.get_node(payment.item.local_id)
    loaded_new_child = loaded.get_node(new_child.item.local_id)

    assert document.source_path == "source.csv"
    assert [item.local_id for item in loaded.flatten()] == [
        epic.item.local_id,
        payment.item.local_id,
        cart.item.local_id,
        story.item.local_id,
        new_child.item.local_id,
    ]
    assert loaded_epic.item.children == [payment.item.local_id, cart.item.local_id]
    assert loaded_cart.item.parent_local_id == epic.item.local_id
    assert loaded_story.item.parent_local_id == cart.item.local_id
    assert loaded_payment.item.state == "deleted"
    assert loaded_cart.item.title == "Cart checkout"
    assert loaded_cart.item.original_fields["Title"] == "Cart"
    assert loaded_cart.item.state == "modified"
    assert loaded_new_child.item.remote_id is None
    assert loaded_new_child.item.state == "new"

    sibling = loaded.add_sibling(loaded_new_child.item.local_id, "Another offline task", "Task")

    assert sibling.item.local_id == "wi-6"
    assert sibling.item.parent_local_id == loaded_cart.item.local_id
