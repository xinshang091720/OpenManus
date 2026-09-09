"""Agent tool for dynamic AutoCAD code execution and automation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.cad.executor import CadCodeExecutor
from app.tool.base import BaseTool, ToolResult


class CadRunCode(BaseTool):
    """Dynamically execute Python-COM or AutoLISP code in the local AutoCAD application."""

    name: str = "cad_run_code"
    description: str = (
        "在本地 AutoCAD 当前活动图纸中动态执行 Python-COM 脚本或 AutoLISP/命令行指令。"
        "上下文已自动注入: acad(应用程序), doc(活动图纸), model_space(模型空间), paper_space(布局空间), "
        "layers(图层集合), blocks(图块集合), APoint(x, y, z 坐标点转换函数), send_command(命令发送函数)。"
        "特别注意: 只有通过 print(...) 输出的内容才会被捕获回显，函数返回值不会被返回。务必在脚本中 print 统计或完成信息。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "待执行的 Python-COM 代码字符串、AutoLISP 代码或 AutoCAD 命令行序列。",
            },
            "language": {
                "type": "string",
                "enum": ["python", "lisp", "command"],
                "default": "python",
                "description": "代码语言类型: 'python' (推荐，直接操作对象模型), 'lisp' (AutoLISP 表达式), 或 'command' (命令行命令序列)。",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 300,
                "default": 30,
                "description": "代码执行超时时间(秒，默认 30 秒)。",
            },
        },
        "required": ["code"],
    }

    executor: CadCodeExecutor = Field(default_factory=CadCodeExecutor, exclude=True)

    async def execute(
        self,
        code: str,
        language: Literal["python", "lisp", "command"] = "python",
        timeout: int = 30,
    ) -> ToolResult:
        """Execute the provided code against AutoCAD and return ToolResult."""
        res = await self.executor.execute(code=code, language=language, timeout=timeout)
        doc_name = res.get("doc_name") or "当前图纸"

        if not res["success"]:
            error_msg = res.get("error") or "未知执行错误"
            stdout_info = f"\n执行期间的标准输出:\n{res['stdout']}" if res.get("stdout") else ""
            return ToolResult(
                error=f"[{doc_name}] 执行失败:\n{error_msg}{stdout_info}",
                output=res.get("stdout") or None,
            )

        stdout_text = res.get("stdout", "").strip()
        if not stdout_text:
            stdout_text = f"代码在 AutoCAD 文档 [{doc_name}] 中成功执行，但未产生任何 print() 输出。建议下次通过 print() 打印受影响的图元数量或结果。"

        return ToolResult(output=f"[{doc_name}] 执行成功:\n{stdout_text}")
