"""Tests for adoviewer/ado_client.py using a fake HTTP transport."""

from __future__ import annotations

import base64
import json

import pytest

from adoviewer.ado_client import (
    AdoClient,
    AdoClientError,
    AdoConnectionSettings,
    _chunks,
    _FakeTransport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FixedTransport(_FakeTransport):
    """Returns a fixed (status, body) for every request."""

    def __init__(self, status: int, body: dict | None = None, raw: bytes | None = None) -> None:
        self._status = status
        self._raw = raw if raw is not None else json.dumps(body or {}).encode()
        self.calls: list[dict] = []

    def request(self, method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        return self._status, self._raw


def make_client(transport: _FakeTransport, org_url: str = "https://dev.azure.com/myorg", project: str = "MyProject") -> AdoClient:
    settings = AdoConnectionSettings(org_url=org_url, project=project)
    return AdoClient(settings, pat="test-pat-value", transport=transport)


# ---------------------------------------------------------------------------
# Auth header tests
# ---------------------------------------------------------------------------

def test_auth_header_is_basic_base64_of_colon_pat():
    transport = FixedTransport(200, {"name": "MyProject", "state": "wellFormed"})
    client = make_client(transport)
    client.test_connection()

    call = transport.calls[0]
    expected = "Basic " + base64.b64encode(b":test-pat-value").decode()
    assert call["headers"]["Authorization"] == expected


def test_auth_header_does_not_contain_raw_pat_as_plaintext():
    transport = FixedTransport(200, {"name": "MyProject"})
    client = make_client(transport)
    client.test_connection()

    auth = transport.calls[0]["headers"]["Authorization"]
    # Raw PAT must not appear unencoded.
    assert "test-pat-value" not in auth


# ---------------------------------------------------------------------------
# URL construction tests
# ---------------------------------------------------------------------------

def test_test_connection_url_includes_org_and_project():
    transport = FixedTransport(200, {"name": "MyProject"})
    client = make_client(transport)
    client.test_connection()

    url = transport.calls[0]["url"]
    assert "dev.azure.com/myorg" in url
    assert "projects/MyProject" in url
    assert "api-version=" in url


def test_get_relation_types_url():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    client.get_relation_types()

    url = transport.calls[0]["url"]
    assert "workitemrelationtypes" in url
    assert "api-version=" in url


def test_get_work_item_types_url_includes_project():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    client.get_work_item_types()

    url = transport.calls[0]["url"]
    assert "MyProject" in url
    assert "workitemtypes" in url


def test_get_fields_url_includes_project():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    client.get_fields()

    url = transport.calls[0]["url"]
    assert "MyProject" in url
    assert "/fields" in url


def test_batch_get_url_includes_project():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    client.batch_get_work_items([1, 2, 3])

    url = transport.calls[0]["url"]
    assert "MyProject" in url
    assert "workitemsbatch" in url


def test_trailing_slash_on_org_url_is_normalized():
    transport = FixedTransport(200, {"name": "MyProject"})
    settings = AdoConnectionSettings(org_url="https://dev.azure.com/myorg/", project="MyProject")
    client = AdoClient(settings, pat="pat", transport=transport)
    client.test_connection()

    url = transport.calls[0]["url"]
    # Double slash before _apis must not appear.
    assert "//_apis" not in url


# ---------------------------------------------------------------------------
# HTTP method and Content-Type tests
# ---------------------------------------------------------------------------

def test_get_relation_types_uses_get_method():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    client.get_relation_types()

    assert transport.calls[0]["method"] == "GET"


def test_batch_get_uses_post_method():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    client.batch_get_work_items([42])

    assert transport.calls[0]["method"] == "POST"


def test_batch_get_content_type_is_json():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    client.batch_get_work_items([42])

    ct = transport.calls[0]["headers"].get("Content-Type", "")
    assert "application/json" in ct


# ---------------------------------------------------------------------------
# batch_get chunking
# ---------------------------------------------------------------------------

def test_batch_get_chunks_ids_into_groups_of_200():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    ids = list(range(1, 502))  # 501 items => 3 chunks
    client.batch_get_work_items(ids)

    assert len(transport.calls) == 3
    bodies = [json.loads(call["body"]) for call in transport.calls]
    assert len(bodies[0]["ids"]) == 200
    assert len(bodies[1]["ids"]) == 200
    assert len(bodies[2]["ids"]) == 101


def test_batch_get_returns_flattened_results():
    def mk_item(i):
        return {"id": i, "fields": {}}

    calls_made = []

    class CountingTransport(_FakeTransport):
        def request(self, method, url, headers, body):
            chunk = json.loads(body)["ids"]
            items = [mk_item(i) for i in chunk]
            calls_made.append(len(chunk))
            return 200, json.dumps({"value": items}).encode()

    client = make_client(CountingTransport())
    result = client.batch_get_work_items(list(range(1, 6)))
    assert len(result) == 5
    assert {r["id"] for r in result} == {1, 2, 3, 4, 5}


def test_batch_get_expands_relations_by_default():
    transport = FixedTransport(200, {"value": []})
    client = make_client(transport)
    client.batch_get_work_items([1])

    body = json.loads(transport.calls[0]["body"])
    assert body["$expand"] == "Relations"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_401_raises_with_auth_message():
    transport = FixedTransport(401, raw=b"Unauthorized")
    client = make_client(transport)

    with pytest.raises(AdoClientError) as exc_info:
        client.test_connection()

    assert exc_info.value.status_code == 401
    err = str(exc_info.value)
    assert "401" not in err or "Authentication" in err  # message should be safe/readable
    assert "PAT" in err or "authentication" in err.lower()


def test_403_raises_with_permission_message():
    transport = FixedTransport(403, raw=b"Forbidden")
    client = make_client(transport)

    with pytest.raises(AdoClientError) as exc_info:
        client.test_connection()

    assert exc_info.value.status_code == 403
    assert "permission" in str(exc_info.value).lower() or "denied" in str(exc_info.value).lower()


def test_404_raises_with_not_found_message():
    transport = FixedTransport(404, raw=b"Not Found")
    client = make_client(transport)

    with pytest.raises(AdoClientError) as exc_info:
        client.test_connection()

    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value).lower() or "404" in str(exc_info.value)


def test_5xx_raises_with_server_error_message():
    body = json.dumps({"message": "Internal Server Error", "errorCode": 0}).encode()
    transport = FixedTransport(500, raw=body)
    client = make_client(transport)

    with pytest.raises(AdoClientError) as exc_info:
        client.test_connection()

    assert exc_info.value.status_code == 500


def test_error_message_does_not_contain_raw_pat():
    transport = FixedTransport(401, raw=b"Unauthorized")
    client = make_client(transport)

    with pytest.raises(AdoClientError) as exc_info:
        client.test_connection()

    assert "test-pat-value" not in str(exc_info.value)


def test_non_json_response_raises_client_error():
    transport = FixedTransport(200, raw=b"<html>not json</html>")
    client = make_client(transport)

    with pytest.raises(AdoClientError):
        client.test_connection()


# ---------------------------------------------------------------------------
# Return value tests
# ---------------------------------------------------------------------------

def test_get_relation_types_returns_value_list():
    rel = {"referenceName": "System.LinkTypes.Hierarchy-Reverse", "name": "Child"}
    transport = FixedTransport(200, {"value": [rel], "count": 1})
    client = make_client(transport)

    result = client.get_relation_types()
    assert result == [rel]


def test_get_work_item_types_returns_value_list():
    wt = {"name": "Epic", "referenceName": "Microsoft.VSTS.WorkItemTypes.Epic"}
    transport = FixedTransport(200, {"value": [wt]})
    client = make_client(transport)

    result = client.get_work_item_types()
    assert result == [wt]


def test_get_fields_returns_value_list():
    field = {"name": "Title", "referenceName": "System.Title"}
    transport = FixedTransport(200, {"value": [field]})
    client = make_client(transport)

    result = client.get_fields()
    assert result == [field]


# ---------------------------------------------------------------------------
# _chunks utility
# ---------------------------------------------------------------------------

def test_chunks_empty_list():
    assert list(_chunks([], 10)) == []


def test_chunks_exact_multiple():
    result = list(_chunks([1, 2, 3, 4], 2))
    assert result == [[1, 2], [3, 4]]


def test_chunks_remainder():
    result = list(_chunks([1, 2, 3, 4, 5], 2))
    assert result == [[1, 2], [3, 4], [5]]


def test_chunks_larger_than_list():
    result = list(_chunks([1, 2], 10))
    assert result == [[1, 2]]
