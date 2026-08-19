"""Typed Agent tools for base-point, IFC export, and SZ-IFC delivery."""

from typing import Any

from pydantic import Field

from app.revit.client import RevitApiClient
from app.revit.project_delivery import RevitProjectDelivery
from app.tool.base import BaseTool, ToolResult


class RevitSetBasePoint(BaseTool):
    name: str = "revit_set_base_point"
    description: str = "Extracts base-point coordinates from one supplied DWG and applies them to the active Revit model."
    parameters: dict = {
        "type": "object", "properties": {
            "dwg_path": {"type": "string"},
            "rvt_file_path": {"type": "string", "description": "Optional model to open before modifying its base point."},
            "save_folder_path": {"type": "string", "description": "Optional safe result folder for SaveAs after success."},
        },
        "required": ["dwg_path"], "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self, dwg_path: str, rvt_file_path: str | None = None, save_folder_path: str | None = None
    ) -> ToolResult:
        workflow = RevitProjectDelivery(self.client)
        try:
            return self.success_response(
                await workflow.set_base_point(dwg_path, rvt_file_path, save_folder_path)
            )
        except (ValueError, OSError, RuntimeError) as error:
            return self.fail_response(str(error))
        finally:
            workflow.close()


class RevitExportIfc(BaseTool):
    name: str = "revit_export_ifc"
    description: str = "Exports the active Revit model to an explicit IFC path and waits for the IFC and Excel deliverables."
    parameters: dict = {
        "type": "object",
        "properties": {
            "ifc_file_path": {"type": "string"},
            "rvt_file_path": {
                "type": "string",
                "description": "Optional model to open before export. Omit it immediately after room creation so the active saved result model is exported.",
            },
            "recovery_model_path": {
                "type": "string",
                "description": "Deprecated compatibility hint. Its folder is checked for outputs; it never triggers a restart or retry.",
            },
            "timeout_seconds": {"type": "integer", "default": 7200},
        },
        "required": ["ifc_file_path"], "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self,
        ifc_file_path: str,
        timeout_seconds: int = 7200,
        rvt_file_path: str | None = None,
        recovery_model_path: str | None = None,
    ) -> ToolResult:
        workflow = RevitProjectDelivery(self.client)
        try:
            return self.success_response(
                await workflow.export_ifc(
                    ifc_file_path,
                    timeout_seconds,
                    rvt_file_path,
                    recovery_model_path,
                )
            )
        except (ValueError, OSError, RuntimeError) as error:
            return self.fail_response(str(error))
        finally:
            workflow.close()


class RevitInspectIfc(BaseTool):
    name: str = "revit_inspect_ifc"
    description: str = "Runs local SZ-IFC inspection for one exported IFC and returns its generated DOCX report path."
    parameters: dict = {
        "type": "object",
        "properties": {
            "ifc_file_path": {"type": "string"},
            "profession": {"type": "string", "description": "可选专业；支持业务代码、深圳标准代码或中文专业名。缺省时仅在文件名可唯一判断时自动选择。"},
            "rule_name": {"type": "string", "description": "可选的准确 SZ-IFC 规则名称。"},
            "output_docx_path": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 7200},
        },
        "required": ["ifc_file_path"], "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self,
        ifc_file_path: str,
        profession: str | None = None,
        output_docx_path: str | None = None,
        rule_name: str | None = None,
        timeout_seconds: int = 7200,
    ) -> ToolResult:
        workflow = RevitProjectDelivery(self.client)
        try:
            return self.success_response(
                await workflow.inspect_ifc(
                    ifc_file_path,
                    profession,
                    output_docx_path,
                    rule_name,
                    timeout_seconds,
                )
            )
        except (ValueError, OSError, RuntimeError) as error:
            return self.fail_response(str(error))
        finally:
            workflow.close()


class RevitExportAndInspectIfc(BaseTool):
    """Export one IFC deliverable and immediately create its SZ-IFC report."""

    name: str = "revit_export_and_inspect_ifc"
    description: str = (
        "Legacy combined delivery retained for source compatibility only. Agent flows should export IFC, "
        "ask the user to load it in SZ-IFC, verify readiness with sz_ifc_open_model, then call revit_inspect_ifc."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "ifc_file_path": {"type": "string", "description": "Absolute target IFC path."},
            "rvt_file_path": {
                "type": "string",
                "description": "Optional model to open before export. Omit it immediately after room creation so the active saved result model is exported.",
            },
            "profession": {
                "type": "string",
                "description": "User-confirmed AR/ST/AC/PD/EL or SZ-IFC profession; never inferred as a requirement from the filename.",
            },
            "output_docx_path": {"type": "string", "description": "Optional absolute DOCX report path or output folder."},
            "timeout_seconds": {"type": "integer", "default": 7200},
            "recovery_model_path": {"type": "string"},
        },
        "required": ["ifc_file_path", "profession"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self,
        ifc_file_path: str,
        rvt_file_path: str | None = None,
        profession: str = "",
        output_docx_path: str | None = None,
        timeout_seconds: int = 7200,
        recovery_model_path: str | None = None,
    ) -> ToolResult:
        workflow = RevitProjectDelivery(self.client)
        try:
            exported = await workflow.export_ifc(
                ifc_file_path, timeout_seconds, rvt_file_path, recovery_model_path
            )
            if exported.get("status") != "completed":
                return self.success_response(exported)
            inspected = await workflow.inspect_ifc(
                exported["ifc_path"], profession, output_docx_path
            )
            if inspected.get("status") != "completed":
                return self.success_response({**exported, **inspected})
            return self.success_response(
                {
                    "status": "completed",
                    "ifc_path": exported["ifc_path"],
                    "xlsx_path": exported["xlsx_path"],
                    "report_path": inspected["report_path"],
                    "profession": inspected["profession"],
                    "message": "IFC 导出与 SZ-IFC 质检已完成。",
                }
            )
        except (TimeoutError, ValueError, OSError, RuntimeError) as error:
            return self.fail_response(str(error))
        finally:
            workflow.close()
