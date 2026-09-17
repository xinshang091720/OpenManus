"""High-level AR room creation and naming tool."""

from typing import Any

from pydantic import Field

from app.revit.client import RevitApiClient
from app.revit.operations import RevitOperationUnknown
from app.revit.room_creation import ArRoomCreationWorkflow
from app.tool.base import BaseTool, ToolResult


class RevitCreateAndNameArRooms(BaseTool):
    name: str = "revit_create_and_name_ar_rooms"
    description: str = (
        "For one AR Revit model, creates rooms for closed areas and names them from every project DWG "
        "in one folder. Drawing-title information is preferred over filename hints. If a drawing's "
        "floor cannot be confirmed, returns selection_required before changing Revit; continue with "
        "user-confirmed floor_overrides. Then safely SaveAs before IFC delivery. Returns a compact summary, "
        "saved model path, and local audit path."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "rvt_file_path": {"type": "string", "description": "Absolute AR .rvt model path."},
            "dwg_folder_path": {"type": "string", "description": "Absolute folder containing room-name DWGs."},
            "discipline": {
                "type": "string",
                "description": "User-confirmed discipline. Use AR or 建筑 for this tool; the filename is not validated.",
            },
            "allow_length_mismatch": {
                "type": "boolean",
                "default": True,
                "description": "Use the confirmed legacy midpoint-only translation policy; no rotation or scaling is applied.",
            },
            "save_folder_path": {
                "type": "string",
                "description": "Optional safe SaveAs result folder. When omitted, uses a sibling result folder beside the source model.",
            },
            "floor_overrides": {
                "type": "array",
                "description": (
                    "Optional user-confirmed mapping for drawings whose floor could not be uniquely "
                    "identified. Each dwg_path must be the absolute path returned in selection_required; "
                    "floor_numbers may contain one floor or an inclusive range expanded as numbers."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "dwg_path": {
                            "type": "string",
                            "description": "Absolute path of the DWG returned for confirmation.",
                        },
                        "floor_numbers": {
                            "description": "Confirmed Revit floor number(s) or range, for example [1], [6, 7, 8], or a range string like '6-29' / '6~29' / '6至29层'.",
                        },
                    },
                    "required": ["dwg_path", "floor_numbers"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["rvt_file_path", "dwg_folder_path", "discipline"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self,
        rvt_file_path: str,
        dwg_folder_path: str,
        discipline: str,
        allow_length_mismatch: bool = True,
        save_folder_path: str | None = None,
        floor_overrides: list[dict[str, Any]] | None = None,
    ) -> ToolResult:
        workflow = ArRoomCreationWorkflow(self.client)
        try:
            return self.success_response(
                await workflow.run(
                    rvt_file_path=rvt_file_path,
                    dwg_folder_path=dwg_folder_path,
                    discipline=discipline,
                    allow_length_mismatch=allow_length_mismatch,
                    save_folder_path=save_folder_path,
                    floor_overrides=floor_overrides,
                )
            )
        except (ValueError, OSError, RuntimeError, RevitOperationUnknown) as error:
            return self.fail_response(str(error))
        finally:
            workflow.close()
