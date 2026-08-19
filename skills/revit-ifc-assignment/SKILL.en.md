---
name: revit-ifc-assignment
description: English reference for assigning Shenzhen IFC identifiers in Revit. Use when the user explicitly asks to assign, rebuild, or reassign identifiers; do not trigger assignment merely because the user asks for quality inspection, export, or confirms that SZ-IFC is ready.
---

# Revit IFC Identifier Assignment

`revit_run_ifc_assignment` performs one rebuild: it clears old identifiers once, identifies once, selects candidates, assigns once, and safely saves once. This changes Revit data; it is separate from SZ-IFC submission inspection.

Prefer an RVT explicitly supplied in the current request; otherwise use the unique most recent successful model checkpoint and discipline in history. `rvt_file_path` is a continuation and safe-SaveAs location reference while assignment writes to the current active Revit document. Do not browse, read, or enumerate a project folder merely to validate or rediscover that checkpoint: legacy plugin releases can save the active result without a filename extension. If no unique checkpoint exists, ask once whether assignment should apply to the current Revit model and request its discipline. Never reject, copy, or rename a model because of its filename.

Use the tool only for an explicit assignment, rebuild, supplement, or reassignment request. Do not use it solely for quality inspection, export, or when the user confirms loading (e.g. "loaded", "ready"). For an exported IFC, ask the user to load the absolute path in SZ-IFC; once history confirms loading, call `revit_inspect_ifc` directly without repeating assignment or export.

Read the complete `history` before choosing the next action. A Skill activation in a new Run loads instructions and tools; it does not restart business work. If assignment already succeeded, continue from its verified `saved_model_path`; if only a saved folder or legacy extensionless checkpoint is recorded, use it as the active-model continuation clue rather than scanning the project. If a write timed out or its result is unknown, explain that uncertainty and do not retry automatically.

Revit calls remain serialized. A failed stage ends the current Run; never clear, identify, or assign again after a failure or unknown result. Do not start a second Revit instance, overwrite deliverables, or rename user models. Long calls can run for up to two hours and report periodic elapsed-time progress.

When user action is required, record the completed stage, absolute model or IFC path, requested action, expected reply, and the next business tool in the final response.

Read `references/data-flow.md` for grouping details and `references/api-contract.md` for plugin troubleshooting.
