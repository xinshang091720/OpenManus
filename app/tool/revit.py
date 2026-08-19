"""Safe, typed Revit tools backed by the locally installed company plugin."""

from pathlib import Path
from typing import Any, Dict, List

from pydantic import Field

from app.revit import (
    RevitApiClient,
    RevitApiError,
    RevitIfcAssignmentWorkflow,
    WorkflowError,
    group_unmatched_elements,
)
from app.revit.operations import call_revit_operation
from app.revit.save_result import saved_model_name_collision
from app.tool.base import BaseTool, ToolResult


class RevitPrepareAssignment(BaseTool):
    """Preview matching results and unmatched-element groups before any model mutation."""

    name: str = "revit_prepare_assignment"
    description: str = (
        "Calls the local Revit plugin to identify elements for one standard and major, "
        "then returns a read-only preview of unmatched-element groups. "
        "This tool does not change the Revit model."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "standard_id": {
                "type": "integer",
                "description": "Standard ID used by the Revit plugin.",
            },
            "marjor_name": {
                "type": "string",
                "description": "Major name, such as 建筑、电气 or 给排水.",
            },
        },
        "required": ["standard_id", "marjor_name"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(self, standard_id: int, marjor_name: str) -> ToolResult:
        try:
            response = await self.client.one_click_identifier(standard_id, marjor_name)
        except RevitApiError as error:
            return self.fail_response(str(error))

        elements = response.get("data")
        if not isinstance(elements, list):
            return self.fail_response(
                "Revit plugin returned a non-list identification result"
            )
        if not all(isinstance(element, dict) for element in elements):
            return self.fail_response(
                "Revit plugin identification result contains invalid elements"
            )

        groups = group_unmatched_elements(elements)
        unmatched_count = sum(len(group) for group in groups.values())
        group_summaries = {
            group_key: {
                "count": len(group),
                "sample_elements": group[:5],
                "assignable_elements": group if len(group) <= 20 else None,
            }
            for group_key, group in groups.items()
        }
        preview: Dict[str, Any] = {
            "standard_id": standard_id,
            "marjor_name": marjor_name,
            "total_elements": len(elements),
            "matched_elements": len(elements) - unmatched_count,
            "unmatched_elements": unmatched_count,
            "unmatched_groups": group_summaries,
            "next_action": "Review groups, resolve identifiers, then call an approved assignment skill.",
        }
        return self.success_response(preview)


class RevitOpenFile(BaseTool):
    """Open a Revit model through the local company plugin."""

    name: str = "revit_open_file"
    description: str = (
        "Requests the already running local Revit plugin to open a model. "
        "It does not discover or choose a Revit application version; use revit_launch_versioned_model for that."
    )
    parameters: dict = {
        "type": "object",
        "properties": {"rvt_file_path": {"type": "string", "description": "Absolute path to the .rvt file."}},
        "required": ["rvt_file_path"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(self, rvt_file_path: str) -> ToolResult:
        try:
            response = await call_revit_operation(
                self.client, "OpenRevitFile", self.client.open_revit_file, rvt_file_path
            )
        except RevitApiError as error:
            return self.fail_response(str(error))
        return self.success_response({"message": response.get("msg", "模型已打开")})


class RevitGetLevelIfcIdentifiers(BaseTool):
    """Read level IFC identifier candidates from the local Revit plugin."""

    name: str = "revit_get_level_ifc_identifiers"
    description: str = "Returns read-only IFC identifier candidates for Revit levels."
    parameters: dict = {
        "type": "object",
        "properties": {
            "standard_id": {"type": "integer", "description": "Business standard ID."},
        },
        "required": ["standard_id"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(self, standard_id: int) -> ToolResult:
        try:
            response = await self.client.get_level_ifc_ident(standard_id)
        except RevitApiError as error:
            return self.fail_response(str(error))
        return self.success_response(response)


class RevitGetIfcIdentifiers(BaseTool):
    """Read IFC identifier candidates for one Revit standard and major."""

    name: str = "revit_get_ifc_identifiers"
    description: str = (
        "Returns read-only IFC identifier candidates for one standard and major."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "standard_id": {"type": "integer", "description": "Business standard ID."},
            "marjor_name": {"type": "string", "description": "Major name."},
        },
        "required": ["standard_id", "marjor_name"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(self, standard_id: int, marjor_name: str) -> ToolResult:
        try:
            response = await self.client.get_ifc_ident(standard_id, marjor_name)
        except RevitApiError as error:
            return self.fail_response(str(error))
        return self.success_response(response)


class RevitClearParameters(BaseTool):
    """Clear shared parameters from the active Revit document."""

    name: str = "revit_clear_parameters"
    description: str = (
        "Clears shared parameters from the active Revit document. This changes the model."
    )
    parameters: dict = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(self) -> ToolResult:
        try:
            response = await self.client.clear_parameters()
        except RevitApiError as error:
            return self.fail_response(str(error))
        return self.success_response(
            {"message": response.get("msg", "Parameters cleared")}
        )


class RevitApplyAssignment(BaseTool):
    """Apply an explicitly approved identifier assignment list to Revit."""

    name: str = "revit_apply_assignment"
    description: str = (
        "Applies an approved identifier assignment list to the active Revit document. This changes the model."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "standard_id": {"type": "integer", "description": "Business standard ID."},
            "assignments": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Approved Revit assignment records.",
            },
        },
        "required": ["standard_id", "assignments"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self, standard_id: int, assignments: List[Dict[str, Any]]
    ) -> ToolResult:
        if not assignments:
            return self.fail_response("At least one approved assignment is required")
        try:
            response = await self.client.one_click_assignment(standard_id, assignments)
        except RevitApiError as error:
            return self.fail_response(str(error))
        return self.success_response(
            {
                "requested_assignments": len(assignments),
                "plugin_message": response.get("msg", "Assignments applied"),
            }
        )


class RevitSaveAs(BaseTool):
    """Save the active Revit document to a target directory."""

    name: str = "revit_save_as"
    description: str = (
        "Saves the active Revit model into a local target folder. When source_model_path names a file "
        "already present in that folder, creates a new result/result-N subfolder to avoid overwriting it."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "folder_path": {"type": "string", "description": "Absolute target folder path."},
            "source_model_path": {"type": "string", "description": "Optional source model path used to avoid same-name overwrite."},
        },
        "required": ["folder_path"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    @staticmethod
    def _prepare_target_folder(folder_path: str, source_model_path: str | None) -> Path:
        target = Path(folder_path)
        if not target.is_absolute():
            raise ValueError("folder_path must be absolute")
        if target.exists() and not target.is_dir():
            raise ValueError("folder_path must be a directory")
        target.mkdir(parents=True, exist_ok=True)
        if not source_model_path or not saved_model_name_collision(target, source_model_path):
            return target
        index = 0
        while True:
            suffix = "result" if index == 0 else f"result-{index}"
            candidate = target / suffix
            try:
                candidate.mkdir()
                return candidate
            except FileExistsError:
                index += 1

    async def execute(self, folder_path: str, source_model_path: str | None = None) -> ToolResult:
        try:
            target = self._prepare_target_folder(folder_path, source_model_path)
            response = await call_revit_operation(
                self.client, "SaveAs", self.client.save_as, str(target)
            )
        except (OSError, ValueError, RevitApiError) as error:
            return self.fail_response(str(error))
        return self.success_response(
            {"message": response.get("msg", "保存成功"), "save_folder_path": str(target)}
        )


class RevitAssignIfcIdentifiers(BaseTool):
    """Assign IFC identifiers to the active Revit document without opening or saving it."""

    name: str = "revit_assign_ifc_identifiers"
    description: str = (
        "Identifies and batch assigns IFC identifiers in the active Revit model. "
        "It never opens, validates, or saves a model; use the dedicated tools when needed. "
        "The current release has one registered reporting standard, Shenzhen 109003: "
        "use it automatically and do not ask the user for a Standard ID."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "rvt_file_path": {"type": "string", "description": "Absolute .rvt model path. The model must already be open in Revit."},
            "discipline": {
                "type": "string",
                "description": "User-confirmed model discipline (AR/ST/AC/PD/EL or its Chinese name). A filename is only a hint and must not block the operation.",
            },
            "standard_id": {
                "type": "integer",
                "default": 109003,
                "description": "Current registered reporting standard. Default Shenzhen 109003; do not request it from the user.",
            },
        },
        "required": ["rvt_file_path", "discipline"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    @staticmethod
    def _agent_summary(report: Dict[str, Any]) -> Dict[str, Any]:
        """Keep detailed candidate payloads out of the agent context window."""
        summary_keys = (
            "standard_id", "marjor_name", "clear_existing", "identify_attempts",
                "total_elements", "already_matched_count", "returned_unmatched_count", "assigned_count",
                "low_confidence_group_count", "review_batch_size", "review_batch_count",
                "max_parallel_review_batches", "assignment_message",
        )
        return {
            **{key: report.get(key) for key in summary_keys},
            "groups": [
                {
                    "group_key": group["group_key"],
                    "count": group["count"],
                    "route": group["route"],
                    "selected": group["selected"],
                    "grouping_keyword": group["grouping_keyword"],
                    "grouping_filter_fallback": group["grouping_filter_fallback"],
                    "review_status": group["review_status"],
                    "program_low_confidence": group["program_low_confidence"],
                }
                for group in report.get("groups", [])
            ],
            "details_in_audit_report": False,
        }

    async def execute(
        self,
        rvt_file_path: str,
        discipline: str,
        standard_id: int | None = 109003,
    ) -> ToolResult:
        try:
            report = await RevitIfcAssignmentWorkflow(client=self.client).run(
                rvt_file_path=rvt_file_path,
                discipline=discipline,
                standard_id=109003 if standard_id is None else int(standard_id),
                clear_existing=True,
            )
        except WorkflowError as error:
            return self.fail_response(str(error))
        return self.success_response(self._agent_summary(report))
