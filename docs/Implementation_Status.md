# ADOViewer Implementation Status

Source plan: [ADOViewer_Implementation_Plan.md](../ADOViewer_Implementation_Plan.md)

## Current Checkpoint

Current position in the plan: **Milestone 7 - Live Publish**.

Milestone 1 is complete: the app has a package structure, CSV IO helpers, model/tree construction modules, pytest fixtures, and import behavior tests while keeping `ADOViewer.py` as the runnable entry point.

Milestone 2 is implemented in the model layer:

- Canonical work item structures live in `adoviewer/models.py`.
- CSV imports are converted into `WorkItemModel` nodes in `adoviewer/tree_model.py`.
- Hierarchy uses `local_id` and `parent_local_id`; it does not require Azure remote IDs.
- The model supports add root, add child, add sibling, edit field/title, soft delete, restore, reparent, move up/down, indent, and outdent.
- Dirty state tracks new, modified, deleted, and unchanged items, including hierarchy changes.
- Validators cover required title/type, duplicate or invalid remote IDs, missing parents, parent cycles, parent link consistency, and deleted parents with active children.
- Focused tests cover import behavior and model operations under `tests/`.

Milestone 2 intentionally defers undo/redo. The implementation plan allows clear dirty tracking first if command objects are not worth the complexity yet.

Milestone 3 is complete:

- Toolbar and menu commands now call model operations for add root, add child, add sibling, edit title, delete/restore, move up/down, indent, outdent, and validate.
- The tree includes a local status column for new, modified, deleted, warning, and error states.
- Tree rows are tagged for dirty and validation states.
- The status bar summarizes total items, dirty counts, and validation counts after load and edits.
- Selection is restored after command-driven tree refreshes when the selected item remains visible.
- The details pane now has editable tabs for common fields, raw CSV fields, and item-level validation messages.
- Common-field edits update title/type/state/assignment/path/tag fields through the model and refresh dirty/validation state.
- Raw-field edits preserve all original CSV columns and can edit multi-line values.
- The model refreshes detected standard columns when the editor introduces fields such as `Work Item Type` or `State`.
- The tree has a right-click context menu for add root/child/sibling, edit title, delete/restore, move, indent/outdent, make root, open URL, and validate.
- Right-clicking selects the clicked row before showing actions, and actions are disabled when they do not apply to the current target.
- Project save/reopen support is implemented with `.adoviewer.json` files.
- Project files preserve CSV field order, source path, local IDs, local parent-child order, original/current fields, Azure IDs/revisions, and new/modified/deleted state.
- The File menu and toolbar can open projects, save the current project, and save as a new project file. `Ctrl+S` saves the current project.
- The app can start directly from an `.adoviewer.json` project path.
- Project round-trip tests cover local ID, hierarchy, source path, original fields, and dirty-state preservation.
- Tree expansion state is now preserved across edits, validation refreshes, and filter changes using stable local/synthetic node keys instead of transient Treeview item IDs.
- Filtering opens matching ancestor paths temporarily so matches are visible, while clearing the filter restores the prior expansion state unless the user explicitly changed it.
- A column chooser is available from the View menu and toolbar.
- The chooser can show, hide, reset, and apply the tree's data columns without changing work item data, raw fields, or project contents.

Milestone 4 is complete:

- Azure-compatible tree CSV export is implemented with `Title N` hierarchy columns.
- Exports are generated from the local tree in parent-before-child order, so new unsaved parent-child links do not need Azure IDs.
- All-new exports omit the `ID` column; mixed existing/new exports keep existing IDs and leave new IDs blank.
- Export rows exclude source `Parent ID`, normal `Title`, and old `Title N` hierarchy columns while preserving custom/non-hierarchy fields.
- Deleted work items are omitted from Azure tree CSV export.
- The File menu and toolbar can export the current model to an Azure tree CSV after validation passes.
- Tests cover all-new hierarchy export, mixed existing/new IDs, deleted item omission, validation blocking, and UTF-8 CSV writing.
- Round-trip CSV export is implemented for preserving the current source/project field order and custom fields.
- Round-trip exports regenerate `Title N` columns from the local tree when the source uses title-level hierarchy, including added depth columns for deeper local trees.
- The File menu and toolbar can export round-trip CSV files after validation passes.
- Round-trip tests cover field order, custom fields, title-level hierarchy regeneration, and re-importing an exported hierarchy with commas, quotes, and newlines.
- Azure tree CSV export blocks files with more than 1,000 active work items, matching the Azure DevOps web CSV import limit.
- Row-limit tests cover the 1,000-item web import guard.
- Both CSV export modes write only CSV field data and omit ADOViewer project metadata such as local IDs and local parent IDs.
- Both export modes preserve custom/non-hierarchy CSV fields where valid.
- Export commands now open a preview dialog that renders the full CSV text before saving.
- Preview saving writes the same generated rows shown in the dialog.
- CSV rendering tests cover quoting for commas, quotes, and newlines.

Milestone 5 is complete:

- `adoviewer/ado_client.py` implements `AdoConnectionSettings`, `AdoClientError`, and `AdoClient`.
- `AdoClient` uses urllib (stdlib only) with a pluggable transport so tests never hit the network.
- Auth is formed as Basic base64(":{pat}") at construction time; the raw PAT is discarded and never stored or logged.
- `test_connection()` fetches the Azure DevOps project record to verify org URL and project.
- `get_relation_types()`, `get_work_item_types()`, and `get_fields()` retrieve metadata from the REST API.
- `batch_get_work_items(ids)` chunks requests into groups of 200 (the Azure API limit) and flattens results.
- 401, 403, 404, and 5xx HTTP errors are converted to `AdoClientError` with safe, token-free messages.
- Connection settings (org URL and project only, never PAT) are persisted to `~/.adoviewer_connection.json`.
- PAT is prompted via a masked dialog each session and kept only in memory.
- The UI has a new "Azure DevOps" menu with: Connection Settings, Test Connection, Fetch Work Item Types, and Fetch Fields commands.
- `tests/test_ado_client.py` covers 27 cases: auth header encoding, URL construction, HTTP methods, Content-Type, batch chunking, error codes, PAT not in error messages, return value shapes, and the `_chunks` utility.
- All 59 tests pass.

Milestone 6 is complete:

- `adoviewer/publish.py` implements the publish plan builder and dry-run executor.
- `SYSTEM_MANAGED_DISPLAY_NAMES` lists all CSV column names excluded from REST payloads (ID, Rev, date fields, user fields, Title N hierarchy columns, ADOViewer-internal keys).
- `KNOWN_FIELD_MAP` maps common Azure DevOps display names to REST reference names as a fallback when live field metadata has not been fetched.
- `build_field_map(field_metadata)` merges live field metadata on top of the built-in map.
- `resolve_fields(item_fields, field_map)` splits item fields into (to_send, excluded) with a reason for each exclusion.
- `PublishOperation` records op type, local/remote IDs, depth, parent remote ID, fields to send, and exclusions.
- `PublishPlan` provides `creates`, `updates`, `reparents` properties and `creates_by_depth()` for level-order execution.
- `build_publish_plan(model, field_metadata)` collects dirty items, orders creates by depth, generates update and reparent operations for modified items, and adds warnings for deleted existing items and caution fields.
- `build_create_patch(op, field_map, org_url)` builds a JSON Patch body for creates, including the `System.LinkTypes.Hierarchy-Reverse` parent relation when the parent remote ID is known.
- `build_update_patch(op, field_map)` builds a JSON Patch body for updates, including a `test /rev` operation when the revision is known for optimistic concurrency.
- `run_dry_run(plan, client, field_map)` sends each create/update with `validateOnly=true`; reparents are noted as skipped.
- The "Azure DevOps" menu now has "Publish Preview" (offline summary dialog) and "Dry Run (Validate Only)" (sends validateOnly requests and shows per-operation pass/fail).
- `tests/test_publish.py` covers 32 cases: field map building, field resolution, plan ordering, create depth, parent remote ID propagation, update/reparent classification, rev handling, system-managed field exclusion, patch body construction, parent relation shape, dry-run URL params and Content-Type, and `PublishPlan.summary_lines()`.
- All 91 tests pass.

## Next Planned Work

Next position in the plan: **Milestone 7 - Live Publish**.

Expected next work:

- Implement live creates by level.
- Capture returned IDs and revisions.
- Batch get created/updated Work Items and verify links.
- Implement field updates for existing items.
- Implement reparent for existing items.
- Write publish report.
- Save local ID to remote ID mappings after each successful level.
- Add retry from report.
