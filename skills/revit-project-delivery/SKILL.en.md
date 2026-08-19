---
name: revit-project-delivery
description: English reference for local Revit, AutoCAD/TArch, and SZ-IFC delivery tools covering model launch, room creation, IFC assignment/export, manual loading confirmation, inspection, and reporting. Continue from history instead of treating keywords as a mandatory end-to-end workflow.
---

# Revit Project Delivery

Select only the capability needed for the current request:

- `revit_set_base_point` writes base-point data extracted from a supplied DWG.
- `ensure_autocad_running` reuses AutoCAD 2020 or selects its registered installation. When the user gives an absolute `.lnk`, pass it as `shortcut_path`; the tool verifies that its target is the registered AutoCAD 2020 and opens it through Windows Shell. Otherwise it uses the matching official Start Menu shortcut. It returns ready only after the window, versioned COM, and PID agree. AutoCAD 2026 may remain open but is never used by the room workflow. TArch loads with CAD; no TArch command is sent during launch.
- `revit_create_and_name_ar_rooms` creates and names rooms in a user-confirmed AR model, then safely saves it.
- `revit_run_ifc_assignment` assigns identifiers only when the user explicitly requests assignment or reassignment.
- `revit_open_project_model` opens a unique RVT with the matching Revit release and waits until the exact model window and Revit bridge are ready. After `ready`, continue directly to the next business tool; do not ask the user to reconfirm an already-open model.
- `revit_export_ifc` submits once and returns only after the scoped final confirmation is resolved and stable IFC/Excel artifacts pass integrity checks. Detect the exact completion text inside the target Revit process and click only that dialog's confirmation button.
- `sz_ifc_open_model` is retained as a manual-readiness probe. It never launches SZ-IFC or changes file associations; it only confirms that the user already loaded the exact IFC and that the inspection tab is enabled.
- `revit_inspect_ifc` runs the selected SZ-IFC rule and exports a verified DOCX report.

In this project, “quality inspection” means SZ-IFC submission inspection. When an IFC path is available but loading is not confirmed, tell the user to load that absolute path manually. `sz_ifc_open_model` may perform one immediate read-only readiness check. When history confirms the same model is loaded or the user replies "loaded, continue" / "ready", inspect directly with `revit_inspect_ifc`. Never restart identifier assignment or export unless the user explicitly asks.

Use explicit paths or search only inside a user-supplied folder. Automatically use a unique discipline code (`AR/ST/AC/PD/EL/A/S/FS/SS/E/T/P/M`); ask only for `G`, conflicts, or non-unique rules. Revit and SZ-IFC operations remain serialized and can wait up to two hours while reporting progress every 30 seconds. An IFC file appearing alone never completes export: wait for the scoped final confirmation as well. A no-activity SZ launch may request manual loading after 60 seconds. Never overwrite deliverables, rename models, or repeat an uncertain action.

Use `ask_human` only for explicit selection/user-action/unknown-state results. Include the completed stage, absolute path, requested action, expected reply, and next tool.
