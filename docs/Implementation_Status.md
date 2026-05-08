# ADOViewer Implementation Status

Source plan: [ADOViewer_Implementation_Plan.md](../ADOViewer_Implementation_Plan.md)

## Current Checkpoint

Current position in the plan: **Milestone 4 - CSV Export**.

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

Milestone 4 has started:

- Azure-compatible tree CSV export is implemented with `Title N` hierarchy columns.
- Exports are generated from the local tree in parent-before-child order, so new unsaved parent-child links do not need Azure IDs.
- All-new exports omit the `ID` column; mixed existing/new exports keep existing IDs and leave new IDs blank.
- Export rows exclude source `Parent ID`, normal `Title`, and old `Title N` hierarchy columns while preserving custom/non-hierarchy fields.
- Deleted work items are omitted from Azure tree CSV export.
- The File menu and toolbar can export the current model to an Azure tree CSV after validation passes.
- Tests cover all-new hierarchy export, mixed existing/new IDs, deleted item omission, validation blocking, and UTF-8 CSV writing.

## Next Planned Work

Next position in the plan: **Milestone 4 - CSV Export**.

Expected next work:

- Export round-trip CSV.
- Split or block web-import CSVs over 1,000 Work Items.
- Exclude ADOViewer metadata and preserve custom fields.
- Add export preview.
