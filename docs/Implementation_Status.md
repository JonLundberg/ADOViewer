# ADOViewer Implementation Status

Source plan: [ADOViewer_Implementation_Plan.md](../ADOViewer_Implementation_Plan.md)

## Current Checkpoint

Current position in the plan: **Milestone 3 - Editor UI**.

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

Milestone 3 has started:

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

## Next Planned Work

Next position in the plan: **Milestone 3 - Editor UI**.

Expected next work:

- Add project save/reopen support.
- Preserve expansion across edits and filters.
- Add a column chooser.
