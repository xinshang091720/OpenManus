# Revit IFC Assignment Data Flow

## Scope of model review

The language model reviews only one representative of each initially unmatched three-level group. It never receives every model element or the full IFC catalogue.

`OneClickIdentifier` first runs the plugin's existing rule matching and returns only components with an empty `IdentName`. Therefore `returned_unmatched_count` means "records handed to this workflow", not total elements in the Revit document. The plugin does not currently expose its earlier matched count or a total element count.

## End-to-end transformation

1. Opening, matching, quality validation, and saving are separate tools. The agent calls only the operations needed by the task. For an assignment request with a supplied unopened model, it opens the model first; for a save request, it calls save only after the requested work is complete.
2. The assignment tool uses the user-confirmed discipline (`AR/ST/AC/PD/EL` or its Chinese name) to map the plugin major name and calls `OneClickIdentifier(standardId, marjorName)` once. A filename code may be shown as a candidate when asking the user, but it never blocks or renames a model. Failure or an unknown result ends the current Run; the tool does not clear or identify again.
3. The plugin returns initially unmatched component records such as:

```json
{
  "ComPonentId": "2451475",
  "ComTypeName": "砌体 200mm",
  "ComFamlyName": "基本墙",
  "ComCategoryName": "墙",
  "IdentID": null
}
```

4. The workflow groups equal non-empty paths. For example, 172 component records with `砌体 200mm > 基本墙 > 墙` become one group. Its representative and count are retained; its 172 IDs are retained locally for later expansion.
5. If the group category is exactly `标高`, fetch the level catalogue once. Every other group fetches the selected-major catalogue once, then applies the deterministic `Grouping` prefilter: 模型→建筑, 房间/面积→空间, 管道系统/风管系统→系统, otherwise→构件. The catalogue is never sent wholesale to a language model.
6. Local code scores each candidate against the representative's type, family, category, candidate name, known parent path, and grouping. Keep the five best candidates.
7. The model receives one small review payload per group:

```json
{
  "group_key": "砌体 200mm > 基本墙 > 墙",
  "hierarchy_terms": ["砌体 200mm", "基本墙", "墙"],
  "program_best": {"identifier_id": "392714", "identifier_name": "建筑内墙"},
  "candidates": ["top five compact candidate records"]
}
```

The model must select one supplied `identifier_id`. If it fails or returns an ID outside the five candidates, deterministic program-best fallback is used and the audit marks the review status.

8. The selected identifier is copied to every original record in the group. The example group produces 172 assignment records, preserving each original `ComPonentId` and component fields:

```json
{
  "ComPonentId": "2451475",
  "ComTypeName": "砌体 200mm",
  "ComFamlyName": "基本墙",
  "ComCategoryName": "墙",
  "IdentName": "建筑内墙",
  "IdentID": "392714",
  "MarjorID": "112011"
}
```

9. Send all expanded records once to `OneClickAssignment`. Save the result only when the user task or the high-level assignment tool requires it. SZ-IFC submission inspection is a separate operation and never restarts assignment by itself.
10. Return a compact assignment outcome to the agent. Do not write an audit JSON file.

## Token boundary

No language-model tokens are used for opening, grouping, scoring, assignment, quality, saving, or audit writing. Review cost scales with distinct groups, not component count: one representative plus five candidates per group.
