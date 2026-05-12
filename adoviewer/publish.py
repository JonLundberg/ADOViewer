"""Publish plan builder and dry-run executor for Azure DevOps Work Items.

This module constructs an ordered publish plan from a WorkItemModel and
optionally executes a validateOnly dry run against the Azure DevOps REST API.
Live publishing (Milestone 7) will reuse the same plan structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from adoviewer.tree_model import WorkItemModel, WorkItemNode
    from adoviewer.ado_client import AdoClient


# ---------------------------------------------------------------------------
# System-managed CSV display names that must never be sent to the REST API
# ---------------------------------------------------------------------------

#: CSV column header names that Azure DevOps manages server-side.
#: These are excluded from create and update patch bodies.
SYSTEM_MANAGED_DISPLAY_NAMES: frozenset[str] = frozenset(
    {
        "ID",
        "Rev",
        "Changed Date",
        "Changed By",
        "Created Date",
        "Created By",
        "Authorized As",
        "Authorized Date",
        "Revised Date",
        "Watermark",
        "Attached File Count",
        "Comment Count",
        "Link Type",
        "Linked Work Item",
        "Remote Work Item ID",
        "Activated Date",
        "Activated By",
        "Resolved Date",
        "Resolved By",
        "Closed Date",
        "Closed By",
        # ADOViewer-internal keys
        "Parent ID",
        "Title 1",
        "Title 2",
        "Title 3",
        "Title 4",
        "Title 5",
        "Title 6",
        "Title 7",
        "Title 8",
        "Title 9",
        "Title 10",
    }
)

# Hierarchy/title columns matched dynamically (Title N)
_TITLE_LEVEL_PREFIX = "Title "

# ---------------------------------------------------------------------------
# Built-in fallback field name map (display name -> API reference name)
# Used when live field metadata has not been fetched from the server.
# ---------------------------------------------------------------------------

KNOWN_FIELD_MAP: dict[str, str] = {
    "Title": "System.Title",
    "Work Item Type": "System.WorkItemType",
    "State": "System.State",
    "Assigned To": "System.AssignedTo",
    "Description": "System.Description",
    "Tags": "System.Tags",
    "Area Path": "System.AreaPath",
    "Iteration Path": "System.IterationPath",
    "Priority": "Microsoft.VSTS.Common.Priority",
    "Severity": "Microsoft.VSTS.Common.Severity",
    "Story Points": "Microsoft.VSTS.Scheduling.StoryPoints",
    "Effort": "Microsoft.VSTS.Scheduling.Effort",
    "Remaining Work": "Microsoft.VSTS.Scheduling.RemainingWork",
    "Completed Work": "Microsoft.VSTS.Scheduling.CompletedWork",
    "Original Estimate": "Microsoft.VSTS.Scheduling.OriginalEstimate",
    "Business Value": "Microsoft.VSTS.Common.BusinessValue",
    "Risk": "Microsoft.VSTS.Common.Risk",
    "Acceptance Criteria": "Microsoft.VSTS.Common.AcceptanceCriteria",
    "Repro Steps": "Microsoft.VSTS.TCM.ReproSteps",
    "System Info": "Microsoft.VSTS.TCM.SystemInfo",
    "Found In": "Microsoft.VSTS.Build.FoundIn",
    "Integration Build": "Microsoft.VSTS.Build.IntegrationBuild",
    "Start Date": "Microsoft.VSTS.Scheduling.StartDate",
    "Target Date": "Microsoft.VSTS.Scheduling.TargetDate",
    "URL": "System.TeamProject",  # URL is not a writable API field
}

# Fields we never send even if present in KNOWN_FIELD_MAP or metadata
_ALWAYS_EXCLUDED_REFERENCE_NAMES: frozenset[str] = frozenset(
    {
        "System.Id",
        "System.Rev",
        "System.ChangedDate",
        "System.ChangedBy",
        "System.CreatedDate",
        "System.CreatedBy",
        "System.AuthorizedAs",
        "System.AuthorizedDate",
        "System.RevisedDate",
        "System.Watermark",
        "System.AttachedFileCount",
        "System.CommentCount",
        "System.TeamProject",  # URL column maps here; not writable
    }
)

# Warning triggers: fields that may not import correctly
_CAUTION_DISPLAY_NAMES: dict[str, str] = {
    "State": "State is set to the process default for new items unless the target project accepts the specified value.",
    "Assigned To": "Assigned To must match a valid identity in the target project.",
    "Area Path": "Area Path must exist in the target project.",
    "Iteration Path": "Iteration Path must exist in the target project.",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

OperationType = Literal["create", "update", "reparent"]


@dataclass
class PublishOperation:
    """One atomic publish action: create, update, or reparent a work item."""

    op_type: OperationType
    local_id: str
    remote_id: int | None  # None for new creates
    title: str
    work_item_type: str
    depth: int
    parent_remote_id: int | None  # None means root (no parent relation)
    parent_local_id: str | None
    fields_to_send: dict[str, str]  # display_name -> value
    fields_excluded: list[tuple[str, str]]  # [(display_name, reason)]
    rev: int | None = None  # for optimistic-concurrency test on updates


@dataclass
class PublishPlan:
    """Ordered publish plan built from the dirty nodes in a WorkItemModel."""

    operations: list[PublishOperation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def creates(self) -> list[PublishOperation]:
        return [op for op in self.operations if op.op_type == "create"]

    @property
    def updates(self) -> list[PublishOperation]:
        return [op for op in self.operations if op.op_type == "update"]

    @property
    def reparents(self) -> list[PublishOperation]:
        return [op for op in self.operations if op.op_type == "reparent"]

    def creates_by_depth(self) -> dict[int, list[PublishOperation]]:
        """Group create operations by tree depth (0 = root)."""
        by_depth: dict[int, list[PublishOperation]] = {}
        for op in self.creates:
            by_depth.setdefault(op.depth, []).append(op)
        return dict(sorted(by_depth.items()))

    def max_depth(self) -> int:
        depths = [op.depth for op in self.creates]
        return max(depths, default=0)

    def summary_lines(self) -> list[str]:
        """Human-readable summary lines for the preview dialog."""
        lines: list[str] = []
        creates = self.creates
        updates = self.updates
        reparents = self.reparents

        lines.append(f"Creates:  {len(creates)}")
        lines.append(f"Updates:  {len(updates)}")
        lines.append(f"Reparents: {len(reparents)}")

        if creates:
            lines.append("")
            lines.append("Creates by depth:")
            for depth, ops in self.creates_by_depth().items():
                label = "root" if depth == 0 else f"level {depth}"
                lines.append(f"  depth {depth} ({label}): {len(ops)} item(s)")

        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  * {w}")

        return lines


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------

def build_field_map(field_metadata: list[dict[str, Any]] | None) -> dict[str, str]:
    """Return a display_name -> reference_name map.

    If ``field_metadata`` is provided (fetched from the Azure DevOps API),
    it is merged on top of the built-in fallback map so that custom fields
    and project-specific names are resolved correctly.
    """
    combined = dict(KNOWN_FIELD_MAP)
    if field_metadata:
        for f in field_metadata:
            name = f.get("name", "")
            ref = f.get("referenceName", "")
            if name and ref:
                combined[name] = ref
    return combined


def _is_system_managed(display_name: str) -> bool:
    if display_name in SYSTEM_MANAGED_DISPLAY_NAMES:
        return True
    if display_name.startswith(_TITLE_LEVEL_PREFIX):
        try:
            int(display_name[len(_TITLE_LEVEL_PREFIX):])
            return True
        except ValueError:
            pass
    return False


def resolve_fields(
    item_fields: dict[str, str],
    field_map: dict[str, str],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Split item fields into (to_send, excluded).

    Returns:
        to_send: display_name -> value for fields that can be sent.
        excluded: list of (display_name, reason) for fields that are skipped.
    """
    to_send: dict[str, str] = {}
    excluded: list[tuple[str, str]] = []

    for display_name, value in item_fields.items():
        if not display_name or not value:
            continue

        if _is_system_managed(display_name):
            excluded.append((display_name, "system-managed"))
            continue

        ref_name = field_map.get(display_name)
        if ref_name is None:
            excluded.append((display_name, "unknown field - no reference name resolved"))
            continue

        if ref_name in _ALWAYS_EXCLUDED_REFERENCE_NAMES:
            excluded.append((display_name, "read-only system field"))
            continue

        to_send[display_name] = value

    return to_send, excluded


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

def _node_depth(node: WorkItemNode) -> int:
    """Return the depth of a WorkItemNode (0 = direct child of tree root)."""
    depth = 0
    current = node.parent
    while current is not None and current.key != "__root__":
        depth += 1
        current = current.parent
    return depth


def build_publish_plan(
    model: WorkItemModel,
    field_metadata: list[dict[str, Any]] | None = None,
) -> PublishPlan:
    """Build an ordered publish plan from the dirty nodes in ``model``.

    New items are sorted by depth so parents are always created before children.
    Modified items generate update operations.
    Items whose parent_local_id changed generate reparent operations.
    Deleted items are skipped (soft-delete only in v1).

    Parameters
    ----------
    model:
        The work item model containing dirty nodes.
    field_metadata:
        Optional list of field dicts from the Azure DevOps API.
        When provided, it extends the built-in field map so that
        custom and project-specific fields can be resolved.
    """
    plan = PublishPlan()
    field_map = build_field_map(field_metadata)

    # Collect all dirty items in parent-before-child (pre-order) order.
    # The model's flatten() already does pre-order traversal.
    dirty_items = [item for item in model.flatten(include_deleted=True) if item.is_dirty]

    # Warn about caution fields present in any dirty item.
    caution_fields_seen: set[str] = set()
    for item in dirty_items:
        for fname in _CAUTION_DISPLAY_NAMES:
            if fname in item.fields and item.fields[fname]:
                caution_fields_seen.add(fname)
    for fname in sorted(caution_fields_seen):
        plan.warnings.append(_CAUTION_DISPLAY_NAMES[fname])

    # Warn about deleted items with remote IDs (not published in v1).
    deleted_with_remote = [it for it in dirty_items if it.state == "deleted" and it.remote_id is not None]
    if deleted_with_remote:
        plan.warnings.append(
            f"{len(deleted_with_remote)} existing item(s) are marked deleted but will not be "
            "removed from Azure DevOps. Manual deletion is required in v1."
        )

    # Build operations.
    for item in dirty_items:
        if item.state == "deleted":
            continue  # soft-delete only; no REST call in v1

        node = model.get_node(item.local_id)
        if node is None:
            continue

        depth = _node_depth(node)

        # Determine the parent's remote ID (may be None if parent is also new).
        parent_remote_id: int | None = None
        parent_local_id = item.parent_local_id
        if parent_local_id:
            parent_node = model.get_node(parent_local_id)
            if parent_node and parent_node.item:
                parent_remote_id = parent_node.item.remote_id

        # Resolve fields (Title and Work Item Type are handled separately).
        send_fields = dict(item.fields)
        # Always include title and type.
        send_fields["Title"] = item.title
        send_fields["Work Item Type"] = item.work_item_type

        fields_to_send, fields_excluded = resolve_fields(send_fields, field_map)

        if item.state == "new":
            plan.operations.append(
                PublishOperation(
                    op_type="create",
                    local_id=item.local_id,
                    remote_id=None,
                    title=item.title,
                    work_item_type=item.work_item_type,
                    depth=depth,
                    parent_remote_id=parent_remote_id,
                    parent_local_id=parent_local_id,
                    fields_to_send=fields_to_send,
                    fields_excluded=fields_excluded,
                    rev=None,
                )
            )
        elif item.state == "modified":
            op_type: OperationType = "update"
            # Check for reparent (parent_local_id changed from original).
            if item.parent_local_id != item.original_parent_local_id:
                op_type = "reparent"

            plan.operations.append(
                PublishOperation(
                    op_type=op_type,
                    local_id=item.local_id,
                    remote_id=item.remote_id,
                    title=item.title,
                    work_item_type=item.work_item_type,
                    depth=depth,
                    parent_remote_id=parent_remote_id,
                    parent_local_id=parent_local_id,
                    fields_to_send=fields_to_send,
                    fields_excluded=fields_excluded,
                    rev=item.rev,
                )
            )

    # Sort operations: creates first (depth-ordered), then updates, then reparents.
    creates = sorted(
        [op for op in plan.operations if op.op_type == "create"],
        key=lambda op: op.depth,
    )
    updates = [op for op in plan.operations if op.op_type == "update"]
    reparents = [op for op in plan.operations if op.op_type == "reparent"]
    plan.operations = creates + updates + reparents

    return plan


# ---------------------------------------------------------------------------
# JSON Patch body builders
# ---------------------------------------------------------------------------

_ADO_ORG_URL_PLACEHOLDER = "https://dev.azure.com/{org}"


def build_create_patch(
    op: PublishOperation,
    field_map: dict[str, str],
    org_url: str = "",
) -> list[dict[str, Any]]:
    """Build the JSON Patch body for a work item create request.

    Includes:
    - ``/fields/{reference_name}`` for each resolved field.
    - A ``System.LinkTypes.Hierarchy-Reverse`` relation if ``parent_remote_id`` is known.
    """
    patch: list[dict[str, Any]] = []

    for display_name, value in op.fields_to_send.items():
        ref_name = field_map.get(display_name)
        if ref_name and ref_name not in _ALWAYS_EXCLUDED_REFERENCE_NAMES:
            patch.append({"op": "add", "path": f"/fields/{ref_name}", "value": value})

    if op.parent_remote_id is not None:
        base = org_url.rstrip("/") if org_url else _ADO_ORG_URL_PLACEHOLDER
        patch.append(
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "System.LinkTypes.Hierarchy-Reverse",
                    "url": f"{base}/_apis/wit/workItems/{op.parent_remote_id}",
                    "attributes": {"comment": "Linked by ADOViewer"},
                },
            }
        )

    return patch


def build_update_patch(
    op: PublishOperation,
    field_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Build the JSON Patch body for a work item update request.

    Includes an optimistic-concurrency ``test /rev`` when ``rev`` is known.
    """
    patch: list[dict[str, Any]] = []

    if op.rev is not None:
        patch.append({"op": "test", "path": "/rev", "value": op.rev})

    for display_name, value in op.fields_to_send.items():
        ref_name = field_map.get(display_name)
        if ref_name and ref_name not in _ALWAYS_EXCLUDED_REFERENCE_NAMES:
            patch.append({"op": "add", "path": f"/fields/{ref_name}", "value": value})

    return patch


# ---------------------------------------------------------------------------
# Dry-run executor
# ---------------------------------------------------------------------------

@dataclass
class DryRunResult:
    op: PublishOperation
    success: bool
    error: str | None = None
    server_messages: list[str] = field(default_factory=list)


def run_dry_run(
    plan: PublishPlan,
    client: AdoClient,
    field_map: dict[str, str] | None = None,
) -> list[DryRunResult]:
    """Send create/update requests with ``validateOnly=true``.

    No work items are created or modified. Server-side validation errors
    are captured and returned per operation.

    Parameters
    ----------
    plan:
        The publish plan to validate.
    client:
        An authenticated AdoClient.
    field_map:
        Optional resolved display_name -> reference_name map.
        Falls back to the built-in map when not provided.
    """
    from adoviewer.ado_client import AdoClientError

    fm = field_map or build_field_map(None)
    settings = client._settings
    org_url = settings.org_url
    project = settings.project
    results: list[DryRunResult] = []

    for op in plan.operations:
        if op.op_type == "reparent":
            # Reparent requires knowledge of existing relations; skip in dry run.
            results.append(DryRunResult(op=op, success=True, error=None, server_messages=["Reparent dry run skipped."]))
            continue

        try:
            if op.op_type == "create":
                patch = build_create_patch(op, fm, org_url=org_url)
                import urllib.parse
                wi_type = urllib.parse.quote(op.work_item_type)
                path = f"{org_url}/{urllib.parse.quote(project)}/_apis/wit/workitems/${wi_type}"
                client._patch_json_patch(path, patch, validateOnly="true")
            else:
                patch = build_update_patch(op, fm)
                import urllib.parse
                path = f"{org_url}/{urllib.parse.quote(project)}/_apis/wit/workitems/{op.remote_id}"
                client._patch_json_patch(path, patch, validateOnly="true")

            results.append(DryRunResult(op=op, success=True))

        except AdoClientError as exc:
            results.append(DryRunResult(op=op, success=False, error=str(exc)))
        except Exception as exc:
            results.append(DryRunResult(op=op, success=False, error=f"Unexpected error: {exc}"))

    return results
