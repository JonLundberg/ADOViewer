"""Azure DevOps REST client.

Uses only stdlib (urllib) so no extra dependencies are needed.
PATs are accepted at call time and never stored or logged.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

_API_VERSION = "7.1"


@dataclass
class AdoConnectionSettings:
    org_url: str
    project: str

    def __post_init__(self) -> None:
        self.org_url = self.org_url.rstrip("/")


class AdoClientError(Exception):
    """Raised for Azure DevOps API errors with safe, token-free messages."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeTransport:
    """Pluggable HTTP transport used by tests to avoid real network calls."""

    def request(self, method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
        raise NotImplementedError("FakeTransport must be configured for each test")


class _UrllibTransport:
    """Default production transport backed by urllib."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    def request(self, method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
        req = urllib.request.Request(url, method=method, headers=headers, data=body)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, raw


class AdoClient:
    """Thin REST client for Azure DevOps Work Item Tracking APIs.

    Parameters
    ----------
    settings:
        Connection settings (org URL and project).
    pat:
        Personal access token. Never stored beyond this instance lifetime.
        Never included in log messages or exception text.
    transport:
        Optional HTTP transport override; used by tests.
    """

    def __init__(
        self,
        settings: AdoConnectionSettings,
        pat: str,
        transport: _FakeTransport | _UrllibTransport | None = None,
    ) -> None:
        self._settings = settings
        # Build the auth header once; do not keep the raw PAT.
        encoded = base64.b64encode(f":{pat}".encode()).decode()
        self._auth_header = f"Basic {encoded}"
        self._transport = transport or _UrllibTransport()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        """Verify the org URL and project are reachable and accessible.

        Returns the project JSON on success; raises AdoClientError on failure.
        """
        path = f"{self._settings.org_url}/_apis/projects/{urllib.parse.quote(self._settings.project)}"
        return self._get(path)

    def get_relation_types(self) -> list[dict[str, Any]]:
        """Return all work item relation type definitions."""
        path = f"{self._settings.org_url}/_apis/wit/workitemrelationtypes"
        data = self._get(path)
        return data.get("value", [])

    def get_work_item_types(self) -> list[dict[str, Any]]:
        """Return all work item type definitions for the configured project."""
        path = f"{self._settings.org_url}/{urllib.parse.quote(self._settings.project)}/_apis/wit/workitemtypes"
        data = self._get(path)
        return data.get("value", [])

    def get_fields(self) -> list[dict[str, Any]]:
        """Return all field definitions available in the configured project."""
        path = f"{self._settings.org_url}/{urllib.parse.quote(self._settings.project)}/_apis/wit/fields"
        data = self._get(path)
        return data.get("value", [])

    def batch_get_work_items(
        self,
        ids: list[int],
        expand: str = "Relations",
    ) -> list[dict[str, Any]]:
        """Fetch work items by ID in chunks of 200 (Azure DevOps limit).

        Parameters
        ----------
        ids:
            Remote work item IDs to fetch.
        expand:
            ``$expand`` value; defaults to ``Relations``.

        Returns a flat list of work item dicts.
        """
        results: list[dict[str, Any]] = []
        path = (
            f"{self._settings.org_url}/{urllib.parse.quote(self._settings.project)}"
            "/_apis/wit/workitemsbatch"
        )
        for chunk in _chunks(ids, 200):
            body = {"ids": chunk, "$expand": expand}
            data = self._post_json(path, body)
            results.extend(data.get("value", []))
        return results

    def create_work_item(
        self,
        work_item_type: str,
        patch: list[dict[str, Any]],
        validate_only: bool = False,
    ) -> dict[str, Any]:
        """Create a new work item via JSON Patch.

        Returns the created work item dict including ``id`` and ``rev``.
        """
        path = (
            f"{self._settings.org_url}/{urllib.parse.quote(self._settings.project)}"
            f"/_apis/wit/workitems/${urllib.parse.quote(work_item_type)}"
        )
        extra: dict[str, str] = {}
        if validate_only:
            extra["validateOnly"] = "true"
        return self._patch_json_patch(path, patch, **extra)

    def update_work_item(
        self,
        remote_id: int,
        patch: list[dict[str, Any]],
        validate_only: bool = False,
    ) -> dict[str, Any]:
        """Update an existing work item via JSON Patch.

        Returns the updated work item dict including ``id`` and ``rev``.
        """
        path = (
            f"{self._settings.org_url}/{urllib.parse.quote(self._settings.project)}"
            f"/_apis/wit/workitems/{remote_id}"
        )
        extra: dict[str, str] = {}
        if validate_only:
            extra["validateOnly"] = "true"
        return self._patch_json_patch(path, patch, **extra)

    def get_work_item(
        self,
        remote_id: int,
        expand: str | None = "Relations",
    ) -> dict[str, Any]:
        """Fetch a single work item by remote ID."""
        path = (
            f"{self._settings.org_url}/{urllib.parse.quote(self._settings.project)}"
            f"/_apis/wit/workitems/{remote_id}"
        )
        extra: dict[str, str] = {}
        if expand:
            extra["$expand"] = expand
        return self._get(path, **extra)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, **extra_params: str) -> dict[str, Any]:
        params: dict[str, str] = {"api-version": _API_VERSION}
        params.update(extra_params)
        url = f"{path}?{urllib.parse.urlencode(params)}"
        status, raw = self._transport.request("GET", url, self._base_headers(), None)
        return self._parse_response(status, raw, url)

    def _post_json(self, path: str, body: dict[str, Any], **extra_params: str) -> dict[str, Any]:
        params: dict[str, str] = {"api-version": _API_VERSION}
        params.update(extra_params)
        url = f"{path}?{urllib.parse.urlencode(params)}"
        encoded = json.dumps(body).encode()
        headers = {**self._base_headers(), "Content-Type": "application/json"}
        status, raw = self._transport.request("POST", url, headers, encoded)
        return self._parse_response(status, raw, url)

    def _patch_json_patch(self, path: str, body: list[dict[str, Any]], **extra_params: str) -> dict[str, Any]:
        params: dict[str, str] = {"api-version": _API_VERSION}
        params.update(extra_params)
        url = f"{path}?{urllib.parse.urlencode(params)}"
        encoded = json.dumps(body).encode()
        headers = {**self._base_headers(), "Content-Type": "application/json-patch+json"}
        status, raw = self._transport.request("PATCH", url, headers, encoded)
        return self._parse_response(status, raw, url)

    def _base_headers(self) -> dict[str, str]:
        return {"Authorization": self._auth_header, "Accept": "application/json"}

    def _parse_response(self, status: int, raw: bytes, url: str) -> dict[str, Any]:
        if status == 401:
            raise AdoClientError("Authentication failed. Check that your PAT is valid and has the required scopes.", status_code=401)
        if status == 403:
            raise AdoClientError("Access denied. Your PAT may lack work item write or read permission.", status_code=403)
        if status == 404:
            raise AdoClientError(
                f"Resource not found (404). Verify the organization URL and project name are correct.",
                status_code=404,
            )
        if status >= 400:
            # Try to extract the Azure error message without leaking the URL (which may contain tokens).
            try:
                err = json.loads(raw)
                msg = err.get("message") or err.get("errorCode") or "Unknown server error"
            except Exception:
                msg = "Unknown server error"
            raise AdoClientError(f"Azure DevOps API error {status}: {msg}", status_code=status)

        try:
            return json.loads(raw)
        except Exception as exc:
            raise AdoClientError(f"Unexpected non-JSON response from Azure DevOps API.") from exc


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _chunks(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
