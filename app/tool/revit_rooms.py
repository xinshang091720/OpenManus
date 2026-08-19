"""Agent tools for the safe DWG-room preview/apply workflow."""

from typing import Any

from pydantic import Field

from app.revit.client import RevitApiClient, RevitApiError
from app.revit.room_sync import RoomSyncWorkflow
from app.tool.base import BaseTool, ToolResult


class RevitPreviewRoomSync(BaseTool):
    name: str = "revit_preview_room_sync"
    description: str = (
        "Reads one supplied DWG, validates its grid translation against the active AR Revit model, "
        "and returns a room-update preview. It never writes or saves the Revit model."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "dwg_path": {"type": "string", "description": "Absolute path to one supplied DWG file."},
            "target_model_path": {"type": "string", "description": "Absolute path to the active AR Revit model."},
            "discipline": {
                "type": "string",
                "description": "User-confirmed discipline. Use AR or 建筑; the filename is only a hint.",
            },
            "allow_length_mismatch": {"type": "boolean", "default": False, "description": "Use only after user approval to keep the legacy midpoint-translation behavior when grid lengths differ."},
        },
        "required": ["dwg_path", "target_model_path", "discipline"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self,
        dwg_path: str,
        target_model_path: str,
        discipline: str,
        allow_length_mismatch: bool = False,
    ) -> ToolResult:
        workflow = RoomSyncWorkflow(self.client)
        try:
            return self.success_response(
                await workflow.preview(
                    dwg_path, target_model_path, discipline, allow_length_mismatch
                )
            )
        except (ValueError, RevitApiError) as error:
            return self.fail_response(str(error))
        finally:
            workflow.close()


class RevitApplyRoomSync(BaseTool):
    name: str = "revit_apply_room_sync"
    description: str = (
        "Writes exactly one previously returned room-sync preview to the active Revit model. "
        "Use only after the user has reviewed and confirmed the preview. It never saves the model."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "preview_id": {"type": "string", "description": "preview_id returned by revit_preview_room_sync."},
            "confirmed": {"type": "boolean", "description": "Must be true after the user has approved the preview."},
        },
        "required": ["preview_id", "confirmed"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(self, preview_id: str, confirmed: bool) -> ToolResult:
        workflow = RoomSyncWorkflow(self.client)
        try:
            return self.success_response(await workflow.apply(preview_id, confirmed))
        except (ValueError, RevitApiError) as error:
            return self.fail_response(str(error))
        finally:
            workflow.close()
