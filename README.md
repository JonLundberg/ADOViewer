# ADOViewer

A Windows-friendly Python/Tkinter viewer and editor for Azure DevOps Work Items.

ADOViewer lets you import an Azure DevOps CSV export, view and edit the work item hierarchy, add new work items, and export a valid CSV back to Azure DevOps — all without needing Excel or a live connection. When you are ready, it can also publish directly to Azure DevOps via the REST API.

---

## Features

- **Open CSV** — Import Azure DevOps work item CSV exports. Supports UTF-8 BOM and cp1252 encoding. Detects ID + Parent ID, Title 1/2/3 hierarchies, and flat exports.
- **Editable tree** — Add, edit, rename, delete, and reparent work items. Hierarchy uses stable local IDs so new items can be parents before they have Azure IDs.
- **Details panel** — Common fields, raw CSV fields, and validation tabs.
- **Project files** — Save and reopen `.adoviewer.json` project files that preserve local IDs, hierarchy, dirty state, and all custom CSV fields.
- **Azure-compatible CSV export** — Exports `Title 1`/`Title 2`/… hierarchy columns. New items get blank IDs. Blocked at >1,000 rows (Azure web import limit).
- **Round-trip CSV export** — Preserves original field order and custom fields; regenerates title columns from the local tree.
- **Validation** — Checks required fields, duplicate IDs, cycles, missing parents, and skipped title levels before any export or publish.
- **Azure DevOps REST publish** — Connects with a Personal Access Token. Publishes creates level-by-level so parent IDs are always known before child creation. Updates existing items. Reparents work items that moved in the hierarchy.
- **Dry run** — Sends `validateOnly=true` requests so you can check server-side validation without modifying any data.
- **Publish preview** — Offline summary of what will be created, updated, and reparented, with warnings about fields that may not transfer correctly.

---

## Requirements

- **Python 3.11+** (Python 3.14 tested)
- **Windows** (Tkinter is included with the standard Python distribution for Windows)
- No runtime dependencies — uses only the Python standard library (`tkinter`, `csv`, `json`, `uuid`, `urllib`)

For development / testing:

```
pytest>=8.0
```

---

## Installation

### Run from source

```powershell
# Clone or download the repository.
git clone <repo-url>
cd ADOViewer

# Optional: create a virtual environment.
py -3 -m venv .venv
.venv\Scripts\activate

# Install development dependencies (only needed for tests).
py -3 -m pip install -r requirements-dev.txt

# Launch the app.
py -3 ADOViewer.py

# Or pass a file directly.
py -3 ADOViewer.py "C:\Path\To\WorkItems.csv"
py -3 ADOViewer.py "C:\Path\To\Project.adoviewer.json"
```

### Run tests

```powershell
py -3 -m pytest
```

---

## Building a standalone executable (Windows)

You can package ADOViewer as a single `.exe` using [PyInstaller](https://pyinstaller.org/).

```powershell
py -3 -m pip install pyinstaller
pyinstaller ADOViewer.spec
```

The resulting executable will be in `dist\ADOViewer\ADOViewer.exe`.

If you do not have a spec file, generate one and then build:

```powershell
pyinstaller --onefile --windowed --name ADOViewer ADOViewer.py
```

> **Note**: The `--windowed` flag suppresses the console window on Windows. Omit it if you want to see print/logging output.

---

## Quick start

1. In Azure DevOps, go to **Boards > Work Items > Export to CSV**.
2. Open ADOViewer and choose **File > Open CSV** (or drag-and-drop).
3. The hierarchy is reconstructed automatically from the CSV structure.
4. Edit work items in the details panel. New items get a blank ID until published.
5. Choose **File > Save Project** to save your session as an `.adoviewer.json` file.
6. Choose **File > Export Azure Tree CSV** to produce a CSV ready for Azure DevOps web import.
7. *(Optional)* Configure **Azure DevOps > Connection Settings** with your organization URL, project, and PAT, then use **Azure DevOps > Publish to Azure DevOps** for direct publish.

---

## File formats

### ADOViewer project file (`.adoviewer.json`)

Stores local IDs, hierarchy, dirty state, original CSV field order, and Azure remote IDs and revisions. This is the recommended working format when editing new items that have not yet been published.

The PAT is **never** written to project files.

### Azure-compatible CSV export

Export uses `Title 1`, `Title 2`, … columns to encode hierarchy. New items have a blank ID column. Rows are ordered parent-before-child.

Import this file into Azure DevOps via **Boards > Work Items > Import Work Items**.

### Round-trip CSV export

Preserves the original source CSV field order and custom columns. Updates `Title N` columns from the local tree. Useful before direct REST publishing is needed.

---

## Azure DevOps connection

1. Go to **Azure DevOps > Connection Settings**.
2. Enter your **Organization URL** (e.g., `https://dev.azure.com/my-org`) and **Project**.
3. Enter your **Personal Access Token** — it is held in memory for the session only and never saved to disk.
4. Click **Save**, then use **Azure DevOps > Test Connection** to verify.

### Required PAT scopes

| Operation | Required scope |
|-----------|----------------|
| Read metadata, refresh | Work Items (Read) |
| Create / update items | Work Items (Read & Write) |

---

## Project structure

```
ADOViewer.py            Entry point (Tkinter app)
adoviewer/
  __init__.py
  models.py             WorkItem, WorkItemNode, ValidationMessage dataclasses
  tree_model.py         WorkItemModel — tree operations, dirty tracking, validation
  csv_io.py             CSV read/write, column detection, Azure tree and round-trip export
  project_io.py         .adoviewer.json save/load
  ado_client.py         Azure DevOps REST client (urllib, no extra dependencies)
  publish.py            Publish plan, field resolution, dry run, live publish
tests/
  fixtures/             Sample CSV files used by the test suite
  test_import_behavior.py
  test_model_operations.py
  test_csv_export.py
  test_project_io.py
  test_ado_client.py
  test_publish.py
  test_live_publish.py
docs/
  Implementation_Status.md
samples/
  flat_workitems.csv    Example flat work item export
  hierarchy.csv         Example Title 1/2/3 hierarchy export
requirements-dev.txt    pytest (development only)
```

---

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+O` | Open CSV |
| `Ctrl+Shift+O` | Open Project |
| `Ctrl+S` | Save Project |
| `Ctrl+Shift+S` | Save Project As |
| `Ctrl+N` | New Root Item |
| `Enter` | New Sibling |
| `Shift+Enter` | New Child |
| `F2` | Rename selected item |
| `Del` | Delete / Restore selected item |
| `Tab` | Indent (make child of previous sibling) |
| `Shift+Tab` | Outdent (promote to sibling of parent) |
| `Ctrl+↑` | Move Up |
| `Ctrl+↓` | Move Down |
| `Ctrl+F` | Focus search / filter |
| `Esc` | Clear filter |

---

## Validation rules

ADOViewer validates before export and publish:

- Every work item must have a non-empty Title and Work Item Type.
- No duplicate Azure remote IDs in the same import.
- No parent-child cycles.
- Missing parent IDs are warnings, not hard errors.
- Title N columns: exactly one column must be populated per row, no skipped levels.
- Azure-compatible export blocks more than 1,000 active work items.
- New work items must not have a remote ID.
- Deleted work items with children require resolution before export.

---

## Known limitations

- **Deleted items**: Soft-delete is tracked locally and items are omitted from exports. Azure DevOps deletion (recycle) is not performed — manual action is required in the web UI.
- **Rich text fields**: Description and Acceptance Criteria are preserved in CSV round-trips but the editor shows plain text.
- **Reparent via CSV web import**: Azure DevOps does not support parent-child hierarchy via `Parent ID` for new items in web CSV import. ADOViewer's Azure tree export uses `Title N` columns instead.
- **PAT storage**: The PAT is never persisted. You will be prompted once per session.
