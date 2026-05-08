# ADOViewer Work Item Editor Implementation Plan

## Purpose

Turn `ADOViewer.py` from a read-only Azure DevOps CSV viewer into a Windows-friendly viewer/editor for Azure DevOps Work Items. The app should stay simple at first glance, but support the real workflow: import a CSV export, view and edit the hierarchy, add new Work Items that do not yet have Azure IDs, export a valid CSV, and optionally publish changes directly to Azure DevOps.

The central design rule is: never use Azure DevOps `ID` as the app's only identity. Azure assigns IDs only after a Work Item is saved, so the editor needs local stable IDs for unsaved items and for parent-child relationships.

## Current App Review

`ADOViewer.py` is a useful first pass:

- Standard-library-only Python/Tkinter app.
- Reads CSV using `utf-8-sig`, `utf-8`, then `cp1252`.
- Uses `csv.Sniffer` and `csv.DictReader`.
- Detects common Azure DevOps columns such as ID, Work Item Type, Title, State, Assigned To, Tags, Parent ID, Area Path, Iteration Path, and scheduling fields.
- Reconstructs a tree from either:
  - ID + Parent ID columns.
  - `Title 1`, `Title 2`, `Title 3`, etc.
  - Fallback grouping by Work Item Type.
- Shows a `ttk.Treeview` with common columns.
- Supports filter, expand/collapse, detail panel, and double-click URL opening.

Key gaps and risks:

- No edit, save, export, validation, undo, or dirty-state tracking.
- `WorkItemNode.key` is based on Azure ID when available, or row index when not. This is not stable enough once users insert, delete, reorder, or save/reopen new items.
- The model is row-dictionary based. This preserves CSV data, but makes it hard to distinguish original values, edited values, derived values, and app metadata.
- Duplicate IDs are not validated. In `build_tree_from_parent_column`, duplicate keys overwrite earlier nodes in `by_id`.
- Parent ID hierarchy cannot represent new unsaved parent-child links when the parent has no Azure ID.
- `Title N` hierarchy is parsed, but there is no validator for Azure's important rule that each row should have text in only one `Title N` column.
- No protection against hierarchy cycles, orphaned parent IDs, skipped/gapped title levels, or rows without required Azure fields.
- The filter repopulates and expands everything; it does not preserve selection or expanded state.
- The details pane is plain text and read-only. It is useful for inspection, but not for editing.
- The UI has fixed columns and no column chooser.
- The current app title/status line contains a mojibake dash in `status_var.set(...)`; use plain ASCII or proper UTF-8.
- There are no automated tests.

## Research Summary

Microsoft's CSV import/export path supports bulk work item import without Excel, but new Work Items must not include an `ID` column value. For new items, `Work Item Type` and `Title` are required, and the web import flow assigns IDs only after the user saves imported items. Microsoft also documents a 1,000 Work Item limit per CSV import file.

Azure DevOps CSV can preserve parent-child hierarchy by using indented title columns (`Title 1`, `Title 2`, etc.). This is the native CSV answer to the "new item has no parent ID yet" problem. Do not rely on a `Parent ID` column to link brand new imported child items to brand new imported parents in the CSV path.

The Excel integration uses the same tree-list idea. It converts a list to `Title 1`, `Title 2`, etc., publishes the rows, then refreshes after Azure assigns IDs. Microsoft warns that sorting tree lists can corrupt the inferred hierarchy, so ADOViewer exports must keep a deliberate parent-before-child order.

For direct API publishing, Azure DevOps REST creates one Work Item at a time with JSON Patch, and updates Work Items with JSON Patch. Parent-child links are Work Item relations. To make a child point at a known parent, add a relation with `rel` equal to `System.LinkTypes.Hierarchy-Reverse` and a URL to the parent Work Item. This supports the user's batch-by-level idea: create or update all parents at one level, capture their IDs, then create or link the next level.

The REST batch endpoint can group independent Work Item update/create requests, but do not put a parent create and a child create that depends on the parent's new ID in the same unresolved batch. Batch requests at one level are safe because all parent IDs for that level are already known.

Existing Microsoft and community tools suggest useful patterns:

- Azure Boards web bulk edit is good for applying the same change to many existing items, but Microsoft points users to CSV import for bulk add or different field values per row.
- Excel tree lists provide a proven UX vocabulary: Add Child, Indent, Outdent, Publish, Refresh.
- Azure DevOps Migration Tools preserve links, field mappings, and retries. ADOViewer should not become a full migration tool, but should borrow the concepts of link validation, field mapping, and resumable publish logs.
- Microsoft's `azure-devops` Python package is a thin wrapper around the REST APIs. It can be considered later, but the plan should target the REST contract directly so upload behavior is explicit and testable.

Sources:

- Microsoft CSV import/export docs: https://learn.microsoft.com/en-us/azure/devops/boards/queries/import-work-items-from-csv?view=azure-devops
- Microsoft Excel bulk add/modify docs: https://learn.microsoft.com/en-us/azure/devops/boards/backlogs/office/bulk-add-modify-work-items-excel?view=azure-devops
- Microsoft bulk edit docs: https://learn.microsoft.com/en-us/azure/devops/boards/backlogs/bulk-modify-work-items?view=azure-devops
- Microsoft Work Items Create REST API: https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/create?view=azure-devops-rest-7.1
- Microsoft Work Items Update REST API: https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/update?view=azure-devops-rest-7.1
- Microsoft Work Items Batch Get REST API: https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/get-work-items-batch?view=azure-devops-rest-7.1
- Microsoft WIQL REST API: https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/wiql/query-by-wiql?view=azure-devops-rest-7.1
- Microsoft Work Item Relation Types REST API: https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-item-relation-types/list?view=azure-devops-rest-7.1
- Microsoft Q&A on Parent ID CSV limitation: https://learn.microsoft.com/en-gb/answers/questions/5878109/parent-id-isnt-getting-applied-to-the-user-story-w
- Microsoft Azure DevOps Python API: https://github.com/microsoft/azure-devops-python-api
- Azure DevOps Migration Tools: https://github.com/nkdAgility/azure-devops-migration-tools
- Azure DevOps Migration Tools link tool: https://devopsmigration.io/docs/reference/tools/tfs-work-item-link-tool/

## Product Direction

Build ADOViewer as an offline-first editor with optional online publishing.

The first reliable version should support:

- Open CSV.
- Build a real editable tree model.
- Edit existing Work Item fields.
- Add new root items, siblings, and children.
- Move items in the hierarchy with Up, Down, Indent, and Outdent.
- Track New, Modified, Deleted, and Unchanged states.
- Save an ADOViewer project/session file.
- Export an Azure-compatible CSV.
- Export a "round-trip" CSV that preserves all original fields.
- Validate before export.
- Run automated tests for import, export, and modification.

The next version should add direct Azure DevOps publishing:

- Configure organization URL, project, and token.
- Preview an upload plan.
- Dry-run validation with `validateOnly=true` where possible.
- Upload existing updates and new Work Items.
- Create new Work Items by level so parent IDs exist before child creation.
- Read back created/updated items and refresh local IDs/revisions.
- Produce a publish report.

## Recommended Architecture

Refactor out of a single file once behavior is covered by tests. Keep `ADOViewer.py` as a small entry point so the app remains easy to run on Windows.

Proposed modules:

- `adoviewer/models.py`
  - Dataclasses for Work Items, fields, tree nodes, validation messages, and changes.
- `adoviewer/csv_io.py`
  - CSV read/write, encoding handling, dialect preservation, field detection, ADO tree CSV export.
- `adoviewer/tree_model.py`
  - Build tree from Parent ID, Title levels, or flat rows.
  - Flatten tree to parent-before-child order.
  - Reparent, insert, delete, reorder, indent, outdent.
- `adoviewer/change_tracking.py`
  - Original values, current values, dirty flags, undo/redo snapshots or commands.
- `adoviewer/validation.py`
  - Local validation for required fields, hierarchy shape, duplicate IDs, invalid title levels, missing parents, unsupported types, and export readiness.
- `adoviewer/ado_client.py`
  - Azure DevOps REST client.
  - Auth, create, update, batch get, WIQL, field metadata, relation metadata.
- `adoviewer/publish.py`
  - Build and execute upload plans.
  - Batch-by-level create/update/link algorithm.
  - Publish log and resume support.
- `adoviewer/ui.py`
  - Tkinter app, tree, details editor, menus, dialogs.
- `tests/`
  - Unit tests for model, CSV, tree operations, export, validation, upload planner, and API client request formation.

Dependency strategy:

- Keep GUI dependencies to Python standard library (`tkinter`, `csv`, `json`, `uuid`, `urllib` if needed).
- For REST, use `requests` if adding one dependency is acceptable. It makes auth, JSON Patch headers, timeouts, and tests easier.
- If zero external dependencies remains a hard requirement, implement REST with `urllib.request` and isolate it behind `AdoClient` so tests and future replacement are straightforward.
- Use `pytest` for tests. It is a development dependency only.

## Canonical Data Model

Do not treat CSV rows as the canonical model. Parse CSV rows into a model, keep original row data for round-trip export, and serialize back to CSV only at the edges.

Suggested `WorkItem` fields:

```python
@dataclass
class WorkItem:
    local_id: str
    remote_id: int | None
    rev: int | None
    work_item_type: str
    title: str
    fields: dict[str, str]
    original_fields: dict[str, str]
    parent_local_id: str | None
    children: list[str]
    source_row_index: int | None
    state: Literal["unchanged", "new", "modified", "deleted"]
    validation: list[ValidationMessage]
```

Identity rules:

- `local_id` is generated by ADOViewer and never sent as Azure `ID`.
- Existing Work Items have `remote_id` from CSV/API.
- New Work Items have `remote_id = None`.
- Parent-child links inside the app always use `parent_local_id`, never Parent ID.
- ADOViewer project/session files persist `local_id` and `parent_local_id`.
- Azure-compatible exports strip ADOViewer metadata.

Recommended local ID format:

- `wi-{uuid4}` for all imported and new items.
- Optionally derive deterministic IDs from source file path + row index for a read-only import, but switch to persisted UUIDs once saved as an ADOViewer project.

Field rules:

- Preserve all CSV columns and values, including unknown custom fields.
- Track field display names from CSV separately from API reference names.
- For CSV import/export, use display names exactly as found.
- For REST publish, resolve display names to reference names using field metadata or a local mapping.
- Treat Azure system-managed fields carefully. For new CSV imports, exclude fields Microsoft says not to include, such as ID and system-managed date/user fields. For REST create/update, include only fields accepted by the target process and current operation.

## Hierarchy Strategy

ADOViewer should support three hierarchy inputs:

1. `Title N` tree CSV.
2. ID + Parent ID CSV.
3. Flat CSV grouped by type.

Internally, all three become the same local tree.

For `Title N` imports:

- Sort `Title N` columns numerically.
- Validate that each Work Item row has exactly one non-empty `Title N`.
- Level is the populated title column number.
- Parent is the nearest previous row with a lower level.
- Flag skipped levels such as a `Title 3` row directly under a `Title 1` row.

For ID + Parent ID imports:

- Validate unique non-empty IDs.
- Parent ID maps to an imported Work Item when present.
- Missing parents become warnings, not silent roots.
- Self-parent links are errors.
- Detect cycles.

For flat imports:

- Do not synthesize real Work Item parents. Group nodes are UI-only.
- If the user adds hierarchy in a flat import, convert to a real tree and export via `Title N` columns.

## Editing UX

Keep the main screen simple:

- Top toolbar:
  - Open
  - Save
  - Save As
  - Export CSV
  - Add Root
  - Add Child
  - Add Sibling
  - Delete/Restore
  - Up
  - Down
  - Indent
  - Outdent
  - Validate
  - Publish
  - Filter
- Main tree/table:
  - Title tree column.
  - ID.
  - Type.
  - State.
  - Assigned To.
  - Effort/Points.
  - Area.
  - Iteration.
  - Tags.
  - Status marker for New/Modified/Deleted/Error.
- Details editor:
  - Form tab for common fields.
  - Raw Fields tab for every CSV column.
  - Links/Hierarchy tab showing parent and children.
  - Validation tab listing item-specific problems.
- Status bar:
  - File name.
  - Row count.
  - New/modified/deleted counts.
  - Validation error count.

Interaction details:

- Double-click title or press F2 to edit the title.
- Double-click URL still opens the work item if a URL is available.
- Right-click tree context menu for add/edit/delete/reparent actions.
- Edits mark the Work Item and window title as dirty.
- New items show blank ID, never a fake Azure ID.
- Deleted items are soft-deleted until save/export/publish.
- Reparenting uses local IDs and updates the tree immediately.
- Filtering should preserve current selection when possible.
- Column chooser lets users show/hide fields without changing data.
- A "Preview Export" or "Preview Publish" dialog should show exactly what will be written or sent.

Avoid turning the app into a full spreadsheet. The tree is the primary control; the detail pane is the editor.

## File Formats

Support two saved outputs:

### 1. ADOViewer Project File

Use JSON, for example `my-items.adoviewer.json`.

Purpose:

- Preserve local IDs.
- Preserve local parent-child hierarchy.
- Preserve dirty states.
- Preserve original CSV column order and dialect.
- Preserve source path and export settings.
- Preserve publish mapping after Azure IDs are assigned.

This is the safest working format while editing new unsaved items.

### 2. Azure-Compatible CSV Export

Purpose:

- Upload through Azure DevOps web CSV import.
- Share with users who only need CSV.

Rules:

- New Work Items must have no `ID` value. Prefer omitting the ID column if every row is new; leave it blank for mixed update/new exports only if Azure accepts the mixed path for the target scenario.
- Include `Work Item Type` and `Title` or `Title N`.
- For hierarchy, export `Title 1`, `Title 2`, etc., with exactly one title column populated per row.
- Export rows in parent-before-child order.
- Preserve custom fields where valid.
- Exclude ADOViewer local metadata.
- For CSV web import of new items, warn users that Azure sets State to default New and may reject identity/path values that are not valid in the target project.
- If there are more than 1,000 rows for the web import path, split into multiple files or block with a clear validation message.

### Optional: Round-Trip CSV

Purpose:

- Keep the source CSV layout as intact as possible.
- Useful before direct publishing exists.

Rules:

- Preserve original field order, delimiter, quoting behavior where feasible, and encoding preference.
- If the source was Title-level hierarchy, update `Title N` columns.
- If the source was Parent ID hierarchy and new items exist, convert to Title-level hierarchy for those rows or require Azure-compatible export mode.
- Include all non-system fields unless validation says otherwise.

## Validation Rules

Validation should run on open, before save/export, and before publish.

Tree validation:

- Every real node has a non-empty title.
- Every real node has a non-empty Work Item Type unless the export/publish mode can infer one.
- No duplicate remote IDs.
- No cycles.
- New items do not have remote IDs.
- Existing items have integer remote IDs.
- At most one parent per Work Item.
- Parent-child depth does not exceed export column count; export can add more `Title N` columns automatically.
- Deleted nodes with non-deleted children require either cascading delete, reparent children, or cancel.

CSV-specific validation:

- For `Title N` exports, exactly one title column is populated per row.
- Do not write ADOViewer metadata into Azure-compatible CSV.
- For web import path, enforce the 1,000 item limit.
- For new item CSV import, warn/block unsupported or system-managed fields.
- Preserve CSV quoting for commas, quotes, newlines, and rich text fields.

REST publish validation:

- Organization URL and project are configured.
- Token is present but not stored in the project file.
- Current user has work write permission.
- Work Item Type exists in target project.
- Field names can be resolved to API reference names.
- Required fields for each Work Item Type are present.
- Area Path and Iteration Path exist or user accepts server-side failure.
- Relation type `System.LinkTypes.Hierarchy-Reverse` exists and is enabled.
- Existing items have current `rev` if using optimistic concurrency.
- Reparent operations know the existing parent relation index before removal.

## Direct Azure DevOps Publish Plan

The publishing feature should be implemented after CSV editing/export is stable.

### Authentication

Initial practical approach:

- Ask for Organization URL, Project, and Personal Access Token.
- Store Organization URL and Project in app settings or project file.
- Store PAT in Windows Credential Manager if a small dependency is allowed, or require it each session.
- Never write PAT to CSV, JSON project files, logs, or exception text.

PAT/API scope:

- Read is needed for refresh, metadata, and verification.
- Work write is needed for create/update/link.

### REST Operations

Create Work Item:

- Endpoint: `POST https://dev.azure.com/{org}/{project}/_apis/wit/workitems/${type}?api-version=7.1`
- Content-Type: `application/json-patch+json`
- Body:
  - Add `/fields/System.Title`.
  - Add `/fields/System.WorkItemType` only if the API path/type flow needs it. Usually the type is in the URL.
  - Add other valid fields.
  - If parent remote ID is known, add `/relations/-` with `System.LinkTypes.Hierarchy-Reverse`.

Parent relation patch shape:

```json
{
  "op": "add",
  "path": "/relations/-",
  "value": {
    "rel": "System.LinkTypes.Hierarchy-Reverse",
    "url": "https://dev.azure.com/{org}/{project}/_apis/wit/workItems/{parent_id}",
    "attributes": {
      "comment": "Linked by ADOViewer"
    }
  }
}
```

Update existing Work Item:

- Endpoint: `PATCH https://dev.azure.com/{org}/{project}/_apis/wit/workitems/{id}?api-version=7.1`
- Content-Type: `application/json-patch+json`
- Include `{"op": "test", "path": "/rev", "value": rev}` when `rev` is known.
- Add/replace changed fields.
- Add `System.History` note if useful.
- Reparent by fetching current relations, removing existing parent relation, then adding the new parent relation.

Read back Work Items:

- Use Work Items Batch Get for IDs, with `$expand=Relations`.
- The API documents a maximum of 200 IDs per batch get request.
- Refresh `remote_id`, `rev`, fields, and relations after publish.

### Batch-by-Level Algorithm

This is the recommended direct upload solution for new hierarchy.

Build a publish plan:

1. Validate the local tree.
2. Flatten nodes by depth.
3. Partition each depth into:
   - New creates.
   - Existing field updates.
   - Existing reparent/link updates.
4. Existing roots and existing parents already have `remote_id`.
5. A new child is not eligible until its parent has `remote_id`.

Execution:

```text
for depth in depths_from_root_to_leaf:
    create all new nodes at this depth whose parent remote_id is known
    capture returned IDs and revisions
    read back created IDs in chunks of 200
    update local_id -> remote_id map
    update existing nodes at this depth
    verify expected parent links
```

Batching:

- Requests within one depth are independent after parent IDs are known.
- Use the Azure DevOps `$batch` endpoint for same-depth creates/updates when implemented.
- Keep a sequential fallback. It is easier to debug and useful when one item fails.
- Do not batch unresolved parent and child creates together.

Failure handling:

- Stop by default on the first failed depth so children are not created under missing parents.
- Write a publish report containing local ID, title, operation, request summary, status, Azure ID, and error.
- Keep successful ID mappings in the project file immediately after each successful level.
- Support "retry failed items" from the report.

Idempotency/recovery:

- Direct create returns the new ID. Use that as the primary mapping.
- To recover from a network failure after Azure created an item but before ADOViewer recorded the ID, optionally add a temporary unique tag such as `ADOViewerImport-{session}-{localshort}` on create.
- On retry, search for that tag with WIQL, restore the mapping, then optionally remove the tag.
- Make temporary tags an opt-in setting because some teams do not want tool-generated tags left behind.

### Publish Preview

Before sending changes, show:

- Number of creates, updates, deletes, reparent operations.
- Maximum hierarchy depth.
- Per-level create counts.
- Fields that will be sent.
- Fields that will be ignored and why.
- Warnings about State, Assigned To, Area Path, Iteration Path, unsupported types, and hidden test-related Work Item types.
- Whether the run is dry-run or live.

### Deletes

Treat delete carefully.

Phase 1:

- Do not delete from Azure.
- Soft-delete locally and omit deleted new items from export/publish.
- For existing deleted items, export/publish should produce a warning and require manual action.

Phase 2:

- Add Azure delete/recycle only after the user explicitly opts in.
- Require confirmation and include children impact.

## CSV Export Strategy for New Work Items

Even before direct API publish exists, ADOViewer can solve the no-ID hierarchy problem through `Title N` export.

Example tree:

```text
Epic A
  Feature A1
    User Story A1.1
  Feature A2
```

Export shape:

```csv
Work Item Type,Title 1,Title 2,Title 3,State,Tags
Epic,Epic A,,,,Migration
Feature,,Feature A1,,,Migration
User Story,,,User Story A1.1,,Migration
Feature,,Feature A2,,,Migration
```

Notes:

- The example intentionally leaves `ID` out for an all-new import.
- If the export mixes existing updates and new items, include ID only when needed and leave it blank for new items, after validating the target path supports mixed import.
- For CSV web import, warn that State for new items is set to default New by Azure; if the user needs a non-default State, plan a second update/export or REST publish.

## Similar Solution Lessons Applied

Excel tree list:

- Use `Title N` columns as the CSV hierarchy interchange format.
- Provide Add Child, Indent, Outdent, and Publish/Refresh equivalents.
- Preserve ordering; warn before sorting or arbitrary filtered exports.

Azure Boards web bulk edit:

- Show dirty state clearly.
- Make validation errors visible before save.
- Use bulk edit only for same-value changes; ADOViewer should support per-row differences.

Azure DevOps Migration Tools:

- Explicitly model field maps and link maps.
- Validate links before publishing.
- Keep logs and resumable state.
- Do not try to handle every migration feature in v1; focus on Work Item fields and parent-child links.

Azure DevOps Python API:

- Useful for future convenience, but because it is a thin wrapper, tests should assert the underlying REST requests or use a fake client behind an interface.

## Implementation Milestones

### Milestone 1 - Test Harness and Safe Refactor

Goal: extract behavior without changing the current visible app.

Tasks:

- Create `adoviewer/` package.
- Move CSV reading and column detection into `csv_io.py`.
- Move model/tree construction into `models.py` and `tree_model.py`.
- Keep `ADOViewer.py` as the runnable entry point.
- Add `pytest`.
- Add sample CSV fixtures.
- Add tests for existing import behavior.
- Fix mojibake dash in status text.

Acceptance:

- `py -3 -m py_compile ADOViewer.py` passes.
- `pytest` passes.
- Current viewer behavior still works with sample CSV files.

### Milestone 2 - Canonical Editable Model

Goal: support local IDs, dirty tracking, and tree operations independent of UI.

Tasks:

- Add `WorkItem` dataclass with `local_id`, `remote_id`, fields, original fields, parent ID, children, and state.
- Convert import paths to populate the canonical model.
- Implement add root, add child, add sibling, edit field, soft delete, restore.
- Implement move up/down, indent/outdent, and reparent.
- Implement undo/redo if scoped command objects are not too expensive. If not, add clear dirty tracking first.
- Add validators.

Acceptance:

- New items can be children of other new items.
- No code path requires a remote Azure ID to maintain hierarchy.
- Unit tests cover add/edit/delete/reparent.

### Milestone 3 - Editor UI

Goal: make editing usable without overcomplicating the main screen.

Tasks:

- Add toolbar/menu commands.
- Add details editor with common fields and raw fields tabs.
- Add dirty markers and validation markers in the tree.
- Add right-click context menu.
- Add save-as project file.
- Preserve selection and expanded state across edits/filter.
- Add column chooser.

Acceptance:

- User can open CSV, add a child item, edit fields, reparent it, save project, reopen project, and see the same tree.
- New items display blank ID.
- Validation errors are visible.

### Milestone 4 - CSV Export

Goal: produce useful, validated output before REST publishing.

Tasks:

- Export Azure-compatible tree CSV with `Title N`.
- Export round-trip CSV.
- Split or block web-import CSVs over 1,000 Work Items.
- Exclude ADOViewer metadata.
- Preserve custom fields.
- Add export preview.

Acceptance:

- Exported new hierarchy uses `Title N` columns and no fake IDs.
- Round-trip import/export tests pass.
- CSV handles commas, quotes, newlines, and rich text fields.

### Milestone 5 - Azure DevOps Metadata and Client

Goal: connect safely and read metadata.

Tasks:

- Add connection settings dialog.
- Add PAT handling.
- Implement REST client with timeouts and safe error messages.
- Implement get relation types.
- Implement get work item types/fields.
- Implement batch get work items.
- Add fake client tests.

Acceptance:

- Connection test can fetch metadata.
- No token is persisted in project files or logs.
- Unit tests verify request URLs, headers, and JSON Patch bodies.

### Milestone 6 - Publish Preview and Dry Run

Goal: generate a publish plan and validate it without changing Azure data.

Tasks:

- Build `PublishPlan` from dirty/new nodes.
- Sort creates by hierarchy depth.
- Resolve field display names to reference names.
- Exclude unsupported/system-managed fields.
- Implement `validateOnly=true` dry run for creates/updates where supported.
- Show preview dialog.

Acceptance:

- Preview shows per-level create counts and field/link operations.
- Dry run reports server validation errors without changing items.
- Tests verify parent-before-child ordering and correct parent relation patches.

### Milestone 7 - Live Publish

Goal: create/update Work Items and refresh local IDs.

Tasks:

- Implement live creates by level.
- Capture returned IDs and revisions.
- Batch get created/updated Work Items and verify links.
- Implement field updates for existing items.
- Implement reparent for existing items.
- Write publish report.
- Save local ID to remote ID mappings after each successful level.
- Add retry from report.

Acceptance:

- New parent and child Work Items publish correctly.
- Child Work Items are linked to newly created parent IDs.
- Local project file updates with Azure IDs.
- A failed child level does not create grandchildren.

### Milestone 8 - Packaging

Goal: make it practical as a Windows app.

Tasks:

- Add `README.md`.
- Add `requirements-dev.txt`.
- Add optional `requirements.txt` if REST dependencies are used.
- Add PyInstaller config or documented build command.
- Add app icon if desired.
- Add sample files.

Acceptance:

- Fresh Windows machine with Python can run from source.
- Packaged executable opens, imports sample CSV, edits, and exports.

## Test Plan

Use `pytest`. Most tests should be pure model/IO tests and not require Tkinter windows or Azure network access.

### Fixtures

Create fixtures under `tests/fixtures/`:

- `flat_basic.csv`
  - ID, Work Item Type, Title, State.
- `parent_id_tree.csv`
  - ID + Parent ID hierarchy.
- `title_levels_tree.csv`
  - `Title 1`, `Title 2`, `Title 3` hierarchy.
- `mixed_existing_new.csv`
  - Existing rows with IDs and new rows without IDs.
- `duplicate_ids.csv`
  - Two rows with same ID.
- `missing_parent.csv`
  - Parent ID not present in file.
- `bad_title_levels.csv`
  - Multiple `Title N` columns populated in one row and skipped levels.
- `utf8_bom.csv`
  - UTF-8 BOM.
- `cp1252.csv`
  - Windows characters encoded as cp1252.
- `rich_text.csv`
  - Commas, quotes, newlines, and HTML-ish rich text fields.

### Import Tests

Tests:

- Reads UTF-8 BOM CSV.
- Reads cp1252 CSV.
- Detects ID, Work Item Type, Title, State, Parent ID, Tags, Area Path, Iteration Path.
- Builds tree from Parent ID.
- Builds tree from `Title N`.
- Groups flat CSV by type without creating real parent Work Items.
- Preserves unknown/custom columns.
- Validates duplicate remote IDs.
- Validates missing parent IDs as warnings.
- Validates multiple populated `Title N` columns as errors.
- Validates skipped title levels.
- Keeps row order stable.

### Modification Tests

Tests:

- Add root Work Item creates local ID and no remote ID.
- Add child under existing item uses parent local ID.
- Add child under new item works.
- Edit title marks item modified.
- Edit custom field preserves field name and value.
- Soft delete existing item marks deleted.
- Soft delete new item can be removed from export.
- Restore deleted item.
- Move up/down changes sibling order.
- Indent/outdent changes parent local ID.
- Reparent prevents cycles.
- Dirty counts are accurate.
- Save project/reopen project preserves local IDs and hierarchy.

### Export Tests

Tests:

- Azure-compatible export for all-new tree omits/fills no ID values.
- Azure-compatible export creates correct `Title 1`, `Title 2`, `Title 3` columns.
- Exactly one `Title N` column is populated per row.
- Parent rows appear before child rows.
- Export preserves custom fields.
- Export excludes ADOViewer local metadata.
- Export quotes commas, quotes, and newlines correctly.
- Export can round-trip back into the same hierarchy.
- Export blocks or splits more than 1,000 rows for web import mode.
- Mixed existing/new export leaves new IDs blank and existing IDs intact.

### Validation Tests

Tests:

- Missing Work Item Type blocks export/publish.
- Missing Title blocks export/publish.
- Duplicate remote ID blocks parent-ID tree import or publish.
- Cycle blocks save/export/publish.
- Unsupported Work Item Type for CSV import is flagged.
- New Work Item with non-empty remote ID is flagged.
- Existing Work Item with non-integer remote ID is flagged.
- Deleted parent with active children requires resolution.

### Publish Planner Tests

Use a fake `AdoClient`; no network.

Tests:

- New creates are ordered by depth.
- Siblings at same depth can be batched together.
- Child create body includes `System.LinkTypes.Hierarchy-Reverse` to parent remote ID.
- Root create body has no parent relation.
- Existing field update includes `test /rev` when rev is known.
- System-managed fields are excluded from create body.
- Field display names resolve to reference names.
- Batch get chunks IDs into groups of 200.
- Failure at depth N prevents depth N+1.
- Returned IDs update `local_id -> remote_id` map.
- Retry skips already-mapped successful items.
- Publish report includes success and failure rows.

### API Client Tests

Use fake HTTP transport or `requests_mock` if `requests` is used.

Tests:

- Correct create URL includes `$` before Work Item Type.
- Correct `Content-Type: application/json-patch+json`.
- PAT auth header is formed without logging token.
- Update URL is correct.
- `validateOnly=true` is added for dry run.
- Server errors are converted to actionable exceptions.
- Batch get sends `$expand=Relations`.

### UI Smoke Tests

Keep UI tests light:

- App starts without an initial file.
- App can load a sample CSV.
- Selecting a node populates details.
- Add Child command updates tree and details.
- Validation command shows errors.

If headless Tkinter is unreliable in CI, keep UI tests manual or mark them optional.

### Integration Tests

Optional and gated by environment variables:

- `ADO_ORG_URL`
- `ADO_PROJECT`
- `ADO_PAT`
- `ADO_TEST_AREA_PATH`
- `ADO_TEST_ITERATION_PATH`

Tests:

- Fetch metadata.
- Create one test Work Item with `validateOnly=true`.
- Create parent and child in a disposable test area, verify relation, then clean up only if an explicit `ADO_RUN_DESTRUCTIVE_TESTS=1` is set.

## Key Design Decisions for the Coding Agent

- Implement local IDs first. This unlocks editing and new-item hierarchy.
- Treat CSV and REST as adapters around the same model.
- Prefer `Title N` export for Azure web CSV import. It is the simplest no-ID hierarchy solution.
- Implement direct REST publish as level-order creates/updates. It is the robust automated solution.
- Do not put tool metadata in Azure-compatible CSV.
- Do not store PATs in project files.
- Do not make destructive Azure deletes part of v1.
- Add tests before deep UI work so refactoring the single-file prototype is controlled.

## Open Questions

These should be resolved before REST publish implementation:

- Should the app remain strict standard-library-only, or can it use `requests`, `pytest`, and optionally `keyring`?
- Is direct Azure publish required in v1, or is Azure-compatible CSV export enough for the first editable release?
- Which Azure DevOps process templates and Work Item Types must be supported first?
- Are rich text fields such as Description and Acceptance Criteria expected in CSV exports?
- Should ADOViewer keep a tool-generated temporary tag for idempotent publish recovery, or avoid adding tags by default?
- Should existing Azure Work Item deletion be supported, or only local soft-delete/export omission?

## Suggested First Coding Task

Start with Milestone 1 and Milestone 2:

1. Create the package/module structure.
2. Add fixtures and tests for current import behavior.
3. Introduce `WorkItem` with local IDs.
4. Preserve the existing viewer behavior while switching the UI to the new model.
5. Add model-level operations for add/edit/delete/reparent.

This creates the foundation for both CSV export and direct Azure publishing without making the UI or network layer carry the hard identity problem.
 