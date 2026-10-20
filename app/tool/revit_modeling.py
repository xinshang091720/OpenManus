"""High-level Revit BIM modeling tool: CAD extraction, level creation, and component modeling."""

from typing import Any, Optional

from pydantic import Field

from app.revit.client import RevitApiClient
from app.revit.modeling import RevitBimModelingWorkflow
from app.tool.base import BaseTool, ToolResult


class RevitBimModeling(BaseTool):
    name: str = "revit_bim_modeling"
    description: str = (
        "Creates a Revit BIM model from CAD drawings. Scans the DWG folder, extracts elevations "
        "from elevation/section drawings, binds each level only to the explicitly requested "
        "discipline's floor plans, creates the RVT model and level views, and generates generic "
        "architectural/structural components where supported."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "cad_folder": {
                "type": "string",
                "description": (
                    "Absolute path to the project CAD root folder containing DWG drawings (elevations and floor plans). "
                    "Always pass the project root folder (e.g. 'C:\\Users\\...\\清华院'); do NOT append imaginary subfolders like '\\建施'. "
                    "The tool automatically scans all subdirectories and matches discipline drawings."
                ),
            },
            "model_name": {
                "type": "string",
                "description": "Optional name for the new RVT model (e.g. '1栋_结构'). Defaults to folder name + discipline.",
            },
            "discipline": {
                "type": "string",
                "description": "Required. Must match the user's requested discipline: 建筑、结构、电气、给排水、暖通、立面剖面 (English/code aliases are accepted). Never replace a requested discipline with another discipline based on which drawings look more complete.",
            },
            "rvt_file_path": {
                "type": "string",
                "description": "Optional desired .rvt path/name. By default this creates a new model; if the path already exists, a unique new filename is selected. Only reuse an existing file when reuse_existing_model=true.",
            },
            "reuse_existing_model": {
                "type": "boolean",
                "default": False,
                "description": "Set true only when the user explicitly asks to continue editing the specified existing RVT. Default false creates a new model even if an older same-named model exists.",
            },
            "revit_version": {
                "type": "integer",
                "description": "Optional installed Revit year for a new model, such as 2024. Existing RVT files select their own compatible version automatically.",
            },
            "model_save_path": {
                "type": "string",
                "description": "Optional destination folder for saving the created RVT model. Defaults to the cad_folder.",
            },
            "skip_components": {
                "type": "boolean",
                "default": False,
                "description": "If true, stops after creating levels and views without running component generation.",
            },
            "floor_overrides": {
                "type": "array",
                "description": (
                    "Optional floor mapping overrides for ambiguous or multi-floor drawings, "
                    "e.g. [{'dwg_name': '五~十一层楼面结构平面布置图.dwg', 'floors': [5, 6, 7, 8, 9, 10, 11]}]"
                ),
                "items": {"type": "object"},
            },
        },
        "required": ["cad_folder", "discipline"],
        "additionalProperties": False,
    }
    client: Any = Field(default_factory=RevitApiClient, exclude=True)

    async def execute(
        self,
        cad_folder: str,
        discipline: str,
        model_name: Optional[str] = None,
        model_save_path: Optional[str] = None,
        rvt_file_path: Optional[str] = None,
        reuse_existing_model: bool = False,
        revit_version: Optional[int] = None,
        skip_components: bool = False,
        floor_overrides: Optional[list] = None,
    ) -> ToolResult:
        workflow = RevitBimModelingWorkflow(self.client)
        try:
            result = await workflow.run(
                cad_folder=cad_folder,
                model_name=model_name,
                major_name=discipline,
                model_save_path=model_save_path,
                rvt_file_path=rvt_file_path,
                reuse_existing_model=reuse_existing_model,
                revit_version=revit_version,
                skip_components=skip_components,
                floor_overrides=floor_overrides,
            )
            # Return diagnostic content to the agent so it can explain the
            # failed phase and next safe action instead of ending at a raw tool error.
            return self.success_response(result)
        except Exception as error:
            return self.success_response({
                "status": "failed",
                "error_type": type(error).__name__,
                "message": str(error),
                "next_action": (
                    "先检查 Revit 当前模型与插件连接状态，再决定是否重试；如果是超时，"
                    "不要盲目重发可能已执行的建模请求。"
                ),
            })
