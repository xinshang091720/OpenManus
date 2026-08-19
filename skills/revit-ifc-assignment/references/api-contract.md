# Revit API contract

Base URL: `http://localhost:5000//api/RevitApi`. All operations use POST and expect `{code, msg}`; `200` succeeds and `400`, `405`, `500` fail the workflow.

| Operation | Request |
| --- | --- |
| OpenRevitFile | `rvtFilePath` |
| ClearParameters | no body |
| OneClickIdentifier | `standardId`, `marjorName` |
| GetLevelIfcIdent | `standardId` |
| GetIfcIdent | `standardId`, `marjorName` |
| OneClickAssignment | `standardId`, `data` |
| SaveAs | `folderPath` |

`OneClickIdentifier` returns Revit elements. Preserve every original element field. `ComPonentId` is the instance key; write `IdentName`, `IdentID`, and `MarjorID` before assigning.

Candidate records contain `IdentName`, `IdentId`, `MarjorId`, `ParentID`, and `Grouping`. Query `GetLevelIfcIdent` only when `ComCategoryName` equals `标高`; query `GetIfcIdent` for every other category.
