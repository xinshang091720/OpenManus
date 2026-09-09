---
name: revit-room-sync
description: English reference for creating and naming rooms from DWG data in a user-confirmed AR Revit model, plus the legacy preview/apply path. Do not infer or enforce the discipline from the RVT filename.
---

# Revit Room Creation and Synchronization

`revit_create_and_name_ar_rooms` reads every project DWG from an explicit folder, validates the agreed midpoint-only translation, creates rooms, writes room names, and safely saves the model. Call `ensure_autocad_running` first. If the user supplied an absolute AutoCAD 2020 `.lnk`, pass it unchanged as `shortcut_path`; the tool verifies that it targets the registered AutoCAD 2020 and opens it through Windows Shell without searching the project folder or another shortcut. Otherwise it uses a matching official Start Menu shortcut. It reports ready only when the AutoCAD 2020 window, `AutoCAD.Application.23.1`, and PID agree. AutoCAD 2026 may remain open but is not used. TArch loads with AutoCAD 2020; the launch stage does not send separate TArch commands.

Resolve each DWG floor from an explicit user mapping first, then an explicit drawing title/frame or unambiguous normalized DWG filename. Weak internal drawing annotations (e.g. equipment notes like outdoor unit callouts) must not override a clear filename. A drawing titled as a floor range applies to every floor in that range. If a genuine ambiguity occurs, the Agent should first use contextual knowledge to arbitrate whether incidental text can be dismissed and automatically continue with `floor_overrides`; if it cannot be determined, ask once for the exact DWG-to-floor mapping. Do not rename, copy, or omit source DWGs to force a result.

The tool changes Revit but does not modify source DWGs. A filename may suggest a discipline during clarification, but it never blocks, copies, or renames the model.

When `revit_open_project_model` returns `ready`, the target document and Revit bridge are ready for the next operation. Continue to CAD readiness and room creation without asking the user to reconfirm that Revit opened.

Use `revit_preview_room_sync` and `revit_apply_room_sync` only when the user explicitly requests the legacy two-step preview and confirmation path for existing rooms. Preview is read-only; apply does not create rooms.

After successful room creation, preserve and report `saved_model_path`. Continue with only the later stage the user requests; do not reopen the original RVT, recreate rooms, or assign IFC identifiers merely because the user asks for quality inspection.

Keep Revit calls serialized. The Runtime reports each DWG, CAD command, temporary DXF, grid, room creation, naming, and SaveAs stage. Retain only the proven two-second AutoCAD 2020 document-settle wait. A user-supplied `.lnk` is acceptable only after its target matches the registered AutoCAD 2020; otherwise scan neither disks nor unrelated shortcuts. Do not search for or launch a standalone TArch executable, overwrite results, alter source DWGs, or retry an uncertain write.
