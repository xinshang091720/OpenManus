from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import Field

from app.mcp.revit_lock import RevitProcessLock
from app.revit.client import RevitApiClient, RevitApiError
from app.revit.operations import RevitOperationUnknown, call_revit_operation
from app.revit.project_delivery import prepare_save_folder
from app.revit.save_result import resolve_fresh_saved_model_path, snapshot_folder_files
from app.revit.workflow import RevitIfcAssignmentWorkflow as CoreWorkflow, WorkflowError
from app.tool.base import BaseTool, ToolResult


EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class RevitWorkflow:
    """Runtime wrapper that adds auditable stage events to the stable core workflow."""

    def __init__(self, event_sink: EventSink | None = None, client: RevitApiClient | None = None) -> None:
        self.event_sink = event_sink
        self.client = client or RevitApiClient()
        self.lock = RevitProcessLock()

    async def _emit(self, event: str, stage: str, summary: str) -> None:
        if self.event_sink:
            await self.event_sink(event, {"workflow": "revit_ifc_assignment", "stage": stage, "summary": summary})

    async def run(
        self,
        rvt_file_path: str,
        discipline: Optional[str] = None,
        standard_id: int | None = 109003,
        clear_existing: bool = True,
    ) -> dict[str, Any]:
        await self._emit("workflow_stage_started", "ifc_assignment", "正在执行 IFC 标识识别、匹配和批量赋参。")
        try:
            async with self.lock.hold():
                await self.client.ensure_plugin_ready()
                report = await CoreWorkflow(client=self.client).run(
                    rvt_file_path=rvt_file_path,
                    discipline=discipline,
                    standard_id=standard_id,
                    clear_existing=clear_existing,
                )
                # Save the IFC-assigned model to a distinct subfolder so the
                # on-disk .rvt reflects the current identifier state.  The
                # room-creation step already saved to result-N/; saving here
                # to result-N/ifc-assigned/ keeps both checkpoints separate.
                source = Path(rvt_file_path)
                ifc_folder = source.parent / "ifc-assigned"
                save_target = prepare_save_folder(str(ifc_folder), str(source))
                save_before = snapshot_folder_files(save_target)
                await call_revit_operation(
                    self.client, "SaveAs", self.client.save_as, str(save_target)
                )
                saved_model = resolve_fresh_saved_model_path(
                    save_target, str(source), save_before
                )
                report["saved_to"] = str(save_target)
                report["saved_model_path"] = str(saved_model) if saved_model else None
                report["saved_model_path_status"] = (
                    "verified" if saved_model else "unverified"
                )
        except (WorkflowError, RevitApiError) as error:
            await self._emit("workflow_stage_failed", "ifc_assignment", str(error))
            if isinstance(error, WorkflowError):
                raise
            raise WorkflowError("plugin_preflight", str(error)) from error
        finally:
            self.lock.close()
        await self._emit("workflow_stage_completed", "ifc_assignment", "IFC 标识匹配和批量赋参已完成。")
        return report


class RevitRunIfcAssignment(BaseTool):
    """High-level, Skill-scoped entry point for IFC assignment."""

    name: str = "revit_run_ifc_assignment"
    description: str = (
        "Runs the complete Revit IFC identifier workflow for the active model: clears existing IFC "
        "identifiers first, then performs matching, deterministic candidate ranking, batched review and batch assignment."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "rvt_file_path": {
                "type": "string",
                "description": (
                    "Absolute path of the already-confirmed model checkpoint. "
                    "The assignment writes to the current active Revit document; this path is "
                    "used only as a continuation and safe-SaveAs location reference."
                ),
            },
            "discipline": {
                "type": "string",
                "description": (
                    "Model discipline: AR-建筑, ST-结构, AC-通风空调, PD-给排水, EL-电气 (or Chinese name). "
                    "Inferred automatically from rvt_file_path filename if omitted."
                ),
            },
            "standard_id": {
                "type": "integer",
                "default": 109003,
                "description": "Regional standard ID for IFC identifiers (default Shenzhen 109003).",
            },
        },
        "required": ["rvt_file_path"],
        "additionalProperties": False,
    }
    event_sink: Any = Field(default=None, exclude=True)

    @staticmethod
    def _summary(report: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "status", "standard_id", "marjor_name", "clear_existing", "identify_attempts",
            "total_elements", "already_matched_count", "returned_unmatched_count",
            "assigned_count", "supplementary_assigned_count", "low_confidence_group_count",
            "review_batch_size", "review_batch_count", "max_parallel_review_batches",
            "assignment_message", "saved_to", "saved_model_path", "saved_model_path_status",
        )
        return {key: report.get(key) for key in fields}

    async def execute(
        self,
        rvt_file_path: str,
        discipline: Optional[str] = None,
        standard_id: int | None = 109003,
    ) -> ToolResult:
        try:
            report = await RevitWorkflow(event_sink=self.event_sink).run(
                rvt_file_path=rvt_file_path,
                discipline=discipline,
                standard_id=109003 if standard_id is None else int(standard_id),
                clear_existing=True,
            )
            return self.success_response(self._summary(report))
        except WorkflowError as error:
            return self.fail_response(str(error))
        except RevitOperationUnknown as error:
            return self.success_response(
                {
                    "status": "timed_out_unknown",
                    "stage": "ifc_assignment",
                    "message": str(error),
                }
            )
