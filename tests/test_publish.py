"""Tests for adoviewer/publish.py - publish plan building, field resolution, and dry run."""

from __future__ import annotations

import itertools
import json

import pytest

from adoviewer.ado_client import AdoClient, AdoClientError, AdoConnectionSettings
from adoviewer.ado_client import _FakeTransport
from adoviewer.publish import (
    SYSTEM_MANAGED_DISPLAY_NAMES,
    build_create_patch,
    build_field_map,
    build_publish_plan,
    build_update_patch,
    run_dry_run,
    resolve_fields,
    PublishPlan,
    DryRunResult,
)
from adoviewer.tree_model import WorkItemModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def local_ids():
    counter = itertools.count(1)
    return lambda: f"wi-{next(counter)}"


def make_model(fieldnames=None, rows=None) -> WorkItemModel:
    if fieldnames is None:
        fieldnames = ["ID", "Work Item Type", "Title", "State"]
    return WorkItemModel(fieldnames, rows or [], local_id_factory=local_ids())


class FixedTransport(_FakeTransport):
    def __init__(self, status: int, body: dict | None = None, raw: bytes | None = None) -> None:
        self._status = status
        self._raw = raw if raw is not None else json.dumps(body or {}).encode()
        self.calls: list[dict] = []

    def request(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        return self._status, self._raw


def make_client(transport, org_url="https://dev.azure.com/myorg", project="MyProject") -> AdoClient:
    settings = AdoConnectionSettings(org_url=org_url, project=project)
    return AdoClient(settings, pat="test-pat", transport=transport)


# ---------------------------------------------------------------------------
# build_field_map
# ---------------------------------------------------------------------------

def test_build_field_map_includes_builtin_entries():
    fm = build_field_map(None)
    assert fm["Title"] == "System.Title"
    assert fm["State"] == "System.State"
    assert fm["Assigned To"] == "System.AssignedTo"


def test_build_field_map_merges_live_metadata():
    meta = [{"name": "My Custom Field", "referenceName": "Custom.MyField"}]
    fm = build_field_map(meta)
    assert fm["My Custom Field"] == "Custom.MyField"
    assert fm["Title"] == "System.Title"  # built-in preserved


def test_build_field_map_live_metadata_overrides_builtin():
    meta = [{"name": "Title", "referenceName": "System.Title.Override"}]
    fm = build_field_map(meta)
    assert fm["Title"] == "System.Title.Override"


# ---------------------------------------------------------------------------
# resolve_fields
# ---------------------------------------------------------------------------

def test_resolve_fields_sends_known_fields():
    fm = build_field_map(None)
    to_send, excluded = resolve_fields({"Title": "My Item", "State": "New"}, fm)
    assert "Title" in to_send
    assert "State" in to_send
    assert not excluded


def test_resolve_fields_excludes_system_managed_id():
    fm = build_field_map(None)
    _, excluded = resolve_fields({"ID": "123"}, fm)
    names = [e[0] for e in excluded]
    assert "ID" in names


def test_resolve_fields_excludes_title_level_columns():
    fm = build_field_map(None)
    _, excluded = resolve_fields({"Title 1": "Epic", "Title 2": "Feature"}, fm)
    names = [e[0] for e in excluded]
    assert "Title 1" in names
    assert "Title 2" in names


def test_resolve_fields_excludes_unknown_custom_field_without_metadata():
    fm = build_field_map(None)
    _, excluded = resolve_fields({"Completely Unknown Field XYZ": "value"}, fm)
    names = [e[0] for e in excluded]
    assert "Completely Unknown Field XYZ" in names


def test_resolve_fields_includes_custom_field_when_in_metadata():
    meta = [{"name": "Sprint Goal", "referenceName": "Custom.SprintGoal"}]
    fm = build_field_map(meta)
    to_send, excluded = resolve_fields({"Sprint Goal": "ship v2"}, fm)
    assert "Sprint Goal" in to_send
    assert not any(e[0] == "Sprint Goal" for e in excluded)


def test_resolve_fields_skips_empty_values():
    fm = build_field_map(None)
    to_send, _ = resolve_fields({"Title": "Hello", "State": ""}, fm)
    assert "State" not in to_send


# ---------------------------------------------------------------------------
# build_publish_plan - ordering and structure
# ---------------------------------------------------------------------------

def test_plan_new_root_creates_at_depth_0():
    model = make_model()
    model.add_root("Root item", "Epic")
    plan = build_publish_plan(model)
    assert len(plan.creates) == 1
    assert plan.creates[0].depth == 0
    assert plan.creates[0].op_type == "create"
    assert plan.creates[0].remote_id is None


def test_plan_new_creates_ordered_by_depth():
    model = make_model()
    root = model.add_root("Epic", "Epic")
    feat = model.add_child(root.item.local_id, "Feature", "Feature")
    model.add_child(feat.item.local_id, "Story", "User Story")

    plan = build_publish_plan(model)
    creates = plan.creates

    assert len(creates) == 3
    depths = [op.depth for op in creates]
    assert depths == sorted(depths), "creates must be ordered root-to-leaf"
    assert depths[0] == 0
    assert depths[1] == 1
    assert depths[2] == 2


def test_plan_siblings_at_same_depth():
    model = make_model()
    root = model.add_root("Epic", "Epic")
    model.add_child(root.item.local_id, "Feature A", "Feature")
    model.add_child(root.item.local_id, "Feature B", "Feature")

    plan = build_publish_plan(model)
    creates = plan.creates
    depths = [op.depth for op in creates]

    assert depths.count(1) == 2, "two siblings should both be at depth 1"


def test_plan_root_create_has_no_parent_relation():
    model = make_model()
    model.add_root("Root", "Epic")

    plan = build_publish_plan(model)
    assert plan.creates[0].parent_remote_id is None


def test_plan_child_create_carries_parent_remote_id_when_parent_exists():
    # Existing parent (has remote_id=10), new child.
    # Use Parent ID column so build_tree_from_parent_column is used (not grouped).
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "10", "Work Item Type": "Epic", "Title": "Existing Epic", "Parent ID": ""},
    ])

    # The existing epic has remote_id=10 and state=unchanged.
    items = model.flatten()
    assert len(items) == 1
    parent_id = items[0].local_id

    model.add_child(parent_id, "New Feature", "Feature")

    plan = build_publish_plan(model)
    assert len(plan.creates) == 1
    assert plan.creates[0].parent_remote_id == 10


def test_plan_child_create_parent_remote_id_none_when_parent_is_also_new():
    model = make_model()
    root = model.add_root("New Epic", "Epic")
    model.add_child(root.item.local_id, "New Feature", "Feature")

    plan = build_publish_plan(model)
    feature_op = [op for op in plan.creates if op.work_item_type == "Feature"][0]
    # Parent is also new, has no remote_id yet.
    assert feature_op.parent_remote_id is None
    assert feature_op.parent_local_id == root.item.local_id


def test_plan_modified_item_generates_update():
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "5", "Work Item Type": "Task", "Title": "Old Title", "Parent ID": ""},
    ])
    items = model.flatten()
    lid = items[0].local_id
    model.edit_title(lid, "New Title")

    plan = build_publish_plan(model)
    assert len(plan.updates) == 1
    assert plan.updates[0].remote_id == 5
    assert plan.updates[0].op_type == "update"


def test_plan_deleted_items_are_omitted():
    model = make_model()
    node = model.add_root("To delete", "Task")
    model.soft_delete(node.item.local_id)

    plan = build_publish_plan(model)
    assert len(plan.creates) == 0
    assert len(plan.updates) == 0


def test_plan_deleted_existing_item_adds_warning():
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "7", "Work Item Type": "Task", "Title": "Existing", "Parent ID": ""},
    ])
    lid = model.flatten()[0].local_id
    model.soft_delete(lid)

    plan = build_publish_plan(model)
    assert any("deleted" in w.lower() or "manual" in w.lower() for w in plan.warnings)


def test_plan_system_managed_fields_excluded():
    # Add a new item with a system-managed field; it should be excluded from the patch.
    model = make_model(["ID", "Work Item Type", "Title", "Changed Date"])
    model.add_root("New Task", "Task", fields={"Changed Date": "2024-01-01"})

    plan = build_publish_plan(model)
    assert len(plan.creates) == 1
    op = plan.creates[0]
    excluded_names = [e[0] for e in op.fields_excluded]
    assert "Changed Date" in excluded_names


def test_plan_rev_included_in_update_when_known():
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "99", "Work Item Type": "Bug", "Title": "Known bug", "Parent ID": ""},
    ])
    items = model.flatten()
    item = items[0]
    item.rev = 3
    model.edit_title(item.local_id, "Fixed bug")

    plan = build_publish_plan(model)
    assert len(plan.updates) == 1
    assert plan.updates[0].rev == 3


# ---------------------------------------------------------------------------
# build_create_patch
# ---------------------------------------------------------------------------

def test_create_patch_adds_title_field():
    from adoviewer.publish import PublishOperation
    op = PublishOperation(
        op_type="create",
        local_id="wi-1",
        remote_id=None,
        title="My Epic",
        work_item_type="Epic",
        depth=0,
        parent_remote_id=None,
        parent_local_id=None,
        fields_to_send={"Title": "My Epic", "State": "New"},
        fields_excluded=[],
    )
    fm = build_field_map(None)
    patch = build_create_patch(op, fm, org_url="https://dev.azure.com/myorg")

    paths = [p["path"] for p in patch]
    assert "/fields/System.Title" in paths
    assert "/fields/System.State" in paths


def test_create_patch_adds_parent_relation_when_remote_id_known():
    from adoviewer.publish import PublishOperation
    op = PublishOperation(
        op_type="create",
        local_id="wi-2",
        remote_id=None,
        title="Feature",
        work_item_type="Feature",
        depth=1,
        parent_remote_id=42,
        parent_local_id="wi-1",
        fields_to_send={"Title": "Feature"},
        fields_excluded=[],
    )
    fm = build_field_map(None)
    patch = build_create_patch(op, fm, org_url="https://dev.azure.com/myorg")

    relation_ops = [p for p in patch if p.get("path") == "/relations/-"]
    assert len(relation_ops) == 1
    rel = relation_ops[0]["value"]
    assert rel["rel"] == "System.LinkTypes.Hierarchy-Reverse"
    assert "42" in rel["url"]


def test_create_patch_no_parent_relation_when_root():
    from adoviewer.publish import PublishOperation
    op = PublishOperation(
        op_type="create",
        local_id="wi-1",
        remote_id=None,
        title="Root Epic",
        work_item_type="Epic",
        depth=0,
        parent_remote_id=None,
        parent_local_id=None,
        fields_to_send={"Title": "Root Epic"},
        fields_excluded=[],
    )
    fm = build_field_map(None)
    patch = build_create_patch(op, fm)
    relation_ops = [p for p in patch if p.get("path") == "/relations/-"]
    assert len(relation_ops) == 0


# ---------------------------------------------------------------------------
# build_update_patch
# ---------------------------------------------------------------------------

def test_update_patch_includes_test_rev_when_known():
    from adoviewer.publish import PublishOperation
    op = PublishOperation(
        op_type="update",
        local_id="wi-5",
        remote_id=5,
        title="Updated",
        work_item_type="Task",
        depth=0,
        parent_remote_id=None,
        parent_local_id=None,
        fields_to_send={"Title": "Updated"},
        fields_excluded=[],
        rev=7,
    )
    fm = build_field_map(None)
    patch = build_update_patch(op, fm)

    test_ops = [p for p in patch if p.get("op") == "test" and p.get("path") == "/rev"]
    assert len(test_ops) == 1
    assert test_ops[0]["value"] == 7


def test_update_patch_no_test_rev_when_unknown():
    from adoviewer.publish import PublishOperation
    op = PublishOperation(
        op_type="update",
        local_id="wi-5",
        remote_id=5,
        title="Updated",
        work_item_type="Task",
        depth=0,
        parent_remote_id=None,
        parent_local_id=None,
        fields_to_send={"Title": "Updated"},
        fields_excluded=[],
        rev=None,
    )
    fm = build_field_map(None)
    patch = build_update_patch(op, fm)
    test_ops = [p for p in patch if p.get("op") == "test"]
    assert len(test_ops) == 0


# ---------------------------------------------------------------------------
# run_dry_run
# ---------------------------------------------------------------------------

def test_dry_run_sends_validate_only_param():
    transport = FixedTransport(200, {"id": 1, "fields": {}})
    client = make_client(transport)
    model = make_model()
    model.add_root("New Item", "Task")
    plan = build_publish_plan(model)

    run_dry_run(plan, client)

    assert len(transport.calls) >= 1
    url = transport.calls[0]["url"]
    assert "validateOnly=true" in url


def test_dry_run_uses_patch_content_type():
    transport = FixedTransport(200, {"id": 1})
    client = make_client(transport)
    model = make_model()
    model.add_root("New Item", "Task")
    plan = build_publish_plan(model)

    run_dry_run(plan, client)

    ct = transport.calls[0]["headers"].get("Content-Type", "")
    assert "json-patch+json" in ct


def test_dry_run_returns_success_on_200():
    transport = FixedTransport(200, {"id": 1, "rev": 1})
    client = make_client(transport)
    model = make_model()
    model.add_root("New Item", "Task")
    plan = build_publish_plan(model)

    results = run_dry_run(plan, client)
    assert all(r.success for r in results)


def test_dry_run_returns_failure_on_400():
    body = json.dumps({"message": "Invalid field value"}).encode()
    transport = FixedTransport(400, raw=body)
    client = make_client(transport)
    model = make_model()
    model.add_root("Bad Item", "Task")
    plan = build_publish_plan(model)

    results = run_dry_run(plan, client)
    assert any(not r.success for r in results)
    assert any("Invalid field value" in (r.error or "") for r in results)


def test_dry_run_skips_reparent_ops():
    transport = FixedTransport(200, {"id": 99, "rev": 1})
    client = make_client(transport)

    # Create a reparent scenario using Parent ID hierarchy.
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "1", "Work Item Type": "Epic", "Title": "Epic", "Parent ID": ""},
        {"ID": "2", "Work Item Type": "Feature", "Title": "Feature", "Parent ID": ""},
    ])

    items = model.flatten()
    epic = next(it for it in items if it.work_item_type == "Epic")
    feature = next(it for it in items if it.work_item_type == "Feature")

    # Reparent Feature under Epic
    model.reparent(feature.local_id, epic.local_id)

    plan = build_publish_plan(model)
    reparent_ops = plan.reparents

    if reparent_ops:
        results = run_dry_run(plan, client)
        reparent_results = [r for r in results if r.op.op_type == "reparent"]
        for r in reparent_results:
            assert r.success  # skipped counts as success with a note
            assert any("skipped" in msg.lower() for msg in r.server_messages)


# ---------------------------------------------------------------------------
# PublishPlan.summary_lines
# ---------------------------------------------------------------------------

def test_summary_lines_include_create_and_update_counts():
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "10", "Work Item Type": "Epic", "Title": "Old", "Parent ID": ""},
    ])
    lid = model.flatten()[0].local_id
    model.edit_title(lid, "Updated")
    model.add_root("New Epic", "Epic")

    plan = build_publish_plan(model)
    lines = "\n".join(plan.summary_lines())

    assert "Creates:  1" in lines
    assert "Updates:  1" in lines


def test_summary_lines_include_depth_breakdown():
    model = make_model()
    root = model.add_root("Epic", "Epic")
    model.add_child(root.item.local_id, "Feature", "Feature")

    plan = build_publish_plan(model)
    lines = "\n".join(plan.summary_lines())
    assert "depth 0" in lines
    assert "depth 1" in lines
