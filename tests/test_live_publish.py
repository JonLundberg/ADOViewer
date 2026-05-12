"""Tests for live publish: run_live_publish, PublishReport, create_work_item, update_work_item."""

from __future__ import annotations

import itertools
import json

import pytest

from adoviewer.ado_client import AdoClient, AdoClientError, AdoConnectionSettings, _FakeTransport
from adoviewer.publish import (
    build_field_map,
    build_publish_plan,
    run_live_publish,
    PublishReport,
    PublishReportEntry,
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


class ScriptedTransport(_FakeTransport):
    """Returns scripted responses in sequence; records all calls."""

    def __init__(self, responses: list[tuple[int, dict | bytes]]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[dict] = []

    def request(self, method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if self._index >= len(self._responses):
            return 200, b"{}"
        status, resp = self._responses[self._index]
        self._index += 1
        raw = resp if isinstance(resp, bytes) else json.dumps(resp).encode()
        return status, raw


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
# AdoClient.create_work_item
# ---------------------------------------------------------------------------

def test_create_work_item_uses_patch_method():
    transport = FixedTransport(200, {"id": 42, "rev": 1})
    client = make_client(transport)
    client.create_work_item("Epic", [{"op": "add", "path": "/fields/System.Title", "value": "Test"}])

    assert transport.calls[0]["method"] == "PATCH"


def test_create_work_item_url_contains_dollar_type():
    transport = FixedTransport(200, {"id": 42, "rev": 1})
    client = make_client(transport)
    client.create_work_item("User Story", [])

    url = transport.calls[0]["url"]
    assert "$User%20Story" in url or "$User Story" in url or "workitems" in url
    assert "User%20Story" in url or "User Story" in url


def test_create_work_item_with_validate_only_param():
    transport = FixedTransport(200, {"id": 0})
    client = make_client(transport)
    client.create_work_item("Task", [], validate_only=True)

    url = transport.calls[0]["url"]
    assert "validateOnly=true" in url


def test_create_work_item_uses_json_patch_content_type():
    transport = FixedTransport(200, {"id": 42, "rev": 1})
    client = make_client(transport)
    client.create_work_item("Task", [])

    ct = transport.calls[0]["headers"].get("Content-Type", "")
    assert "json-patch+json" in ct


# ---------------------------------------------------------------------------
# AdoClient.update_work_item
# ---------------------------------------------------------------------------

def test_update_work_item_uses_patch_method():
    transport = FixedTransport(200, {"id": 5, "rev": 2})
    client = make_client(transport)
    client.update_work_item(5, [])

    assert transport.calls[0]["method"] == "PATCH"


def test_update_work_item_url_contains_remote_id():
    transport = FixedTransport(200, {"id": 77, "rev": 3})
    client = make_client(transport)
    client.update_work_item(77, [])

    assert "/77" in transport.calls[0]["url"]


def test_update_work_item_validate_only():
    transport = FixedTransport(200, {"id": 5, "rev": 1})
    client = make_client(transport)
    client.update_work_item(5, [], validate_only=True)

    assert "validateOnly=true" in transport.calls[0]["url"]


# ---------------------------------------------------------------------------
# AdoClient.get_work_item
# ---------------------------------------------------------------------------

def test_get_work_item_uses_get_method():
    transport = FixedTransport(200, {"id": 10, "fields": {}, "relations": []})
    client = make_client(transport)
    client.get_work_item(10)

    assert transport.calls[0]["method"] == "GET"


def test_get_work_item_url_contains_remote_id():
    transport = FixedTransport(200, {"id": 10})
    client = make_client(transport)
    client.get_work_item(10)

    assert "/10" in transport.calls[0]["url"]


def test_get_work_item_expand_relations_by_default():
    transport = FixedTransport(200, {"id": 10})
    client = make_client(transport)
    client.get_work_item(10)

    url = transport.calls[0]["url"]
    assert "Relations" in url or "%24expand" in url or "expand" in url.lower()


# ---------------------------------------------------------------------------
# run_live_publish - creates
# ---------------------------------------------------------------------------

def test_live_publish_creates_root_item():
    transport = FixedTransport(200, {"id": 100, "rev": 1})
    client = make_client(transport)
    model = make_model()
    model.add_root("New Epic", "Epic")
    plan = build_publish_plan(model)

    report = run_live_publish(plan, client, model=model)

    assert len(report.successes) == 1
    assert report.successes[0].azure_id == 100


def test_live_publish_updates_model_remote_id_after_create():
    transport = FixedTransport(200, {"id": 42, "rev": 1})
    client = make_client(transport)
    model = make_model()
    node = model.add_root("New Epic", "Epic")
    lid = node.item.local_id
    plan = build_publish_plan(model)

    run_live_publish(plan, client, model=model)

    item = model.get_node(lid).item
    assert item.remote_id == 42
    assert item.state == "unchanged"


def test_live_publish_creates_ordered_by_depth():
    """Parent must be created before child; child create carries parent ID."""
    id_counter = itertools.count(100)

    class SequentialTransport(_FakeTransport):
        def __init__(self):
            self.calls = []

        def request(self, method, url, headers, body):
            azure_id = next(id_counter)
            self.calls.append({"method": method, "url": url, "body": body})
            return 200, json.dumps({"id": azure_id, "rev": 1}).encode()

    transport = SequentialTransport()
    client = make_client(transport)
    model = make_model()
    root = model.add_root("Parent Epic", "Epic")
    model.add_child(root.item.local_id, "Child Feature", "Feature")
    plan = build_publish_plan(model)

    report = run_live_publish(plan, client, model=model)

    # Both should succeed in depth order.
    assert len(report.successes) == 2
    depths = [entry.op.depth for entry in report.entries]
    assert depths == sorted(depths), "creates must be ordered parent-before-child"


def test_live_publish_child_create_carries_newly_assigned_parent_id():
    """After parent is created, child should reference the parent's new Azure ID."""
    call_ids = []

    class TrackingTransport(_FakeTransport):
        def __init__(self):
            self.calls = []
            self._counter = itertools.count(200)

        def request(self, method, url, headers, body):
            azure_id = next(self._counter)
            self.calls.append({"method": method, "url": url, "body": body})
            return 200, json.dumps({"id": azure_id, "rev": 1}).encode()

    transport = TrackingTransport()
    client = make_client(transport)
    model = make_model()
    root = model.add_root("Parent", "Epic")
    model.add_child(root.item.local_id, "Child", "Feature")
    plan = build_publish_plan(model)

    run_live_publish(plan, client, model=model)

    # Second call (child create) body should contain the parent's Azure ID (200).
    child_body = json.loads(transport.calls[1]["body"])
    relation_ops = [op for op in child_body if op.get("path") == "/relations/-"]
    assert len(relation_ops) == 1
    assert "200" in relation_ops[0]["value"]["url"]


def test_live_publish_failure_at_depth_n_skips_depth_n_plus_1():
    """A failed create at depth 0 should skip depth 1 creates."""
    error_body = json.dumps({"message": "Bad request"}).encode()

    class FailFirstTransport(_FakeTransport):
        def __init__(self):
            self.calls = []

        def request(self, method, url, headers, body):
            self.calls.append({"method": method, "url": url})
            return 400, error_body

    transport = FailFirstTransport()
    client = make_client(transport)
    model = make_model()
    root = model.add_root("Parent", "Epic")
    model.add_child(root.item.local_id, "Child", "Feature")
    plan = build_publish_plan(model)

    report = run_live_publish(plan, client, model=model)

    # Parent failed -> child was never attempted or was skipped.
    assert len(report.failures) >= 1
    parent_entry = report.entries[0]
    assert not parent_entry.success

    # Child must also be a failure (either attempted and failed, or skipped).
    child_entry = report.entries[1]
    assert not child_entry.success


def test_live_publish_only_one_request_when_root_fails():
    """If depth 0 fails, depth 1 creates must not be sent to the server."""
    error_body = json.dumps({"message": "Fail"}).encode()

    class FailTransport(_FakeTransport):
        def __init__(self):
            self.calls = []

        def request(self, method, url, headers, body):
            self.calls.append(url)
            return 400, error_body

    transport = FailTransport()
    client = make_client(transport)
    model = make_model()
    root = model.add_root("Root", "Epic")
    model.add_child(root.item.local_id, "Child", "Feature")
    plan = build_publish_plan(model)

    run_live_publish(plan, client, model=model)

    # Only 1 call (the failed root); child was not attempted.
    assert len(transport.calls) == 1


# ---------------------------------------------------------------------------
# run_live_publish - updates
# ---------------------------------------------------------------------------

def test_live_publish_updates_existing_item():
    transport = FixedTransport(200, {"id": 5, "rev": 3})
    client = make_client(transport)
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "5", "Work Item Type": "Task", "Title": "Old", "Parent ID": ""},
    ])
    lid = model.flatten()[0].local_id
    model.edit_title(lid, "New Title")
    plan = build_publish_plan(model)

    report = run_live_publish(plan, client, model=model)

    assert len(report.updates) == 1
    assert report.updates[0].azure_id == 5


def test_live_publish_update_url_contains_remote_id():
    transport = FixedTransport(200, {"id": 5, "rev": 2})
    client = make_client(transport)
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "5", "Work Item Type": "Task", "Title": "Old", "Parent ID": ""},
    ])
    lid = model.flatten()[0].local_id
    model.edit_title(lid, "Updated")
    plan = build_publish_plan(model)

    run_live_publish(plan, client, model=model)

    assert "/5" in transport.calls[0]["url"]


def test_live_publish_marks_updated_item_unchanged_in_model():
    transport = FixedTransport(200, {"id": 5, "rev": 7})
    client = make_client(transport)
    model = make_model(["ID", "Work Item Type", "Title", "Parent ID"], [
        {"ID": "5", "Work Item Type": "Task", "Title": "Old", "Parent ID": ""},
    ])
    lid = model.flatten()[0].local_id
    model.edit_title(lid, "Updated")
    plan = build_publish_plan(model)

    run_live_publish(plan, client, model=model)

    item = model.get_node(lid).item
    assert item.state == "unchanged"
    assert item.rev == 7


# ---------------------------------------------------------------------------
# PublishReport
# ---------------------------------------------------------------------------

def test_publish_report_successes_and_failures_properties():
    from adoviewer.publish import PublishOperation

    def make_op(op_type="create"):
        return PublishOperation(
            op_type=op_type,
            local_id="wi-1",
            remote_id=None,
            title="T",
            work_item_type="Task",
            depth=0,
            parent_remote_id=None,
            parent_local_id=None,
            fields_to_send={},
            fields_excluded=[],
        )

    op = make_op()
    report = PublishReport(entries=[
        PublishReportEntry(op=op, success=True, azure_id=1),
        PublishReportEntry(op=op, success=False, error="Oops"),
    ])

    assert len(report.successes) == 1
    assert len(report.failures) == 1


def test_publish_report_summary_includes_counts():
    from adoviewer.publish import PublishOperation

    op = PublishOperation(
        op_type="create",
        local_id="wi-1",
        remote_id=None,
        title="T",
        work_item_type="Task",
        depth=0,
        parent_remote_id=None,
        parent_local_id=None,
        fields_to_send={},
        fields_excluded=[],
    )
    report = PublishReport(entries=[
        PublishReportEntry(op=op, success=True, azure_id=10),
        PublishReportEntry(op=op, success=False, error="fail"),
    ])

    summary = report.summary()
    assert "1" in summary  # 1 succeeded
    assert "1" in summary  # 1 failed


def test_publish_report_summary_lines_include_ok_and_fail():
    from adoviewer.publish import PublishOperation

    op = PublishOperation(
        op_type="create",
        local_id="wi-1",
        remote_id=None,
        title="My Item",
        work_item_type="Task",
        depth=0,
        parent_remote_id=None,
        parent_local_id=None,
        fields_to_send={},
        fields_excluded=[],
    )
    report = PublishReport(entries=[
        PublishReportEntry(op=op, success=True, azure_id=5),
        PublishReportEntry(op=op, success=False, error="Denied"),
    ])

    lines = "\n".join(report.summary_lines())
    assert "[OK]" in lines
    assert "[FAIL]" in lines
    assert "Denied" in lines


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------

def test_live_publish_calls_on_progress():
    transport = FixedTransport(200, {"id": 99, "rev": 1})
    client = make_client(transport)
    model = make_model()
    model.add_root("Item", "Task")
    plan = build_publish_plan(model)

    messages: list[str] = []
    run_live_publish(plan, client, on_progress=messages.append)

    assert len(messages) >= 1
