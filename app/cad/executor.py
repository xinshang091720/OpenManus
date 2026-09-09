"""AutoCAD dynamic code execution engine."""

from __future__ import annotations

import asyncio
from io import StringIO
import sys
import traceback
from typing import Any, Dict, Optional

from app.cad.client import APoint, CadClient, CadNoActiveDocumentError, CadNotRunningError
from app.logger import logger


class CadCodeExecutor:
    """Executes dynamic Python-COM and AutoLISP code against active AutoCAD session."""

    def __init__(self, client: Optional[CadClient] = None) -> None:
        self.client = client or CadClient()

    def _prepare_globals(self, doc: Any, acad: Any) -> Dict[str, Any]:
        """Construct the execution namespace injected into Agent's script."""
        return {
            "acad": acad,
            "app": acad,
            "doc": doc,
            "model_space": doc.ModelSpace,
            "mspace": doc.ModelSpace,
            "paper_space": doc.PaperSpace,
            "pspace": doc.PaperSpace,
            "layers": doc.Layers,
            "blocks": doc.Blocks,
            "selection_sets": doc.SelectionSets,
            "utility": getattr(doc, "Utility", None),
            "APoint": APoint,
            "send_command": lambda cmd: doc.SendCommand(cmd if cmd.endswith(("\n", "\r", " ")) else cmd + " "),
        }

    def _sync_execute_python(self, code: str) -> Dict[str, Any]:
        """Synchronously execute Python code with thread COM initialization and stdout capture."""
        self.client.initialize_thread()
        output_buffer = StringIO()
        original_stdout = sys.stdout

        try:
            acad = self.client.get_application(auto_launch=True)
            doc = self.client.get_active_document(auto_create_if_empty=True)
            doc_name = getattr(doc, "Name", "Unknown")

            execution_globals = self._prepare_globals(doc, acad)

            with self.client.silent_mode(doc):
                sys.stdout = output_buffer
                # Compile to detect syntax errors early
                compiled = compile(code, "<cad_agent_script>", "exec")
                exec(compiled, execution_globals)

            stdout_text = output_buffer.getvalue()
            return {
                "success": True,
                "stdout": stdout_text,
                "error": None,
                "doc_name": doc_name,
            }
        except (CadNotRunningError, CadNoActiveDocumentError) as env_err:
            return {
                "success": False,
                "stdout": output_buffer.getvalue(),
                "error": str(env_err),
                "doc_name": None,
            }
        except SyntaxError as syn_err:
            return {
                "success": False,
                "stdout": output_buffer.getvalue(),
                "error": f"Python 语法错误 (行 {syn_err.lineno}): {syn_err.msg}\n{syn_err.text or ''}",
                "doc_name": None,
            }
        except Exception as err:
            formatted_exc = traceback.format_exc()
            return {
                "success": False,
                "stdout": output_buffer.getvalue(),
                "error": f"AutoCAD 脚本执行出错: {err}\n详细堆栈:\n{formatted_exc}",
                "doc_name": None,
            }
        finally:
            sys.stdout = original_stdout
            self.client.uninitialize_thread()

    def _sync_execute_command(self, command: str) -> Dict[str, Any]:
        """Synchronously send command or AutoLISP code to AutoCAD command line."""
        self.client.initialize_thread()
        try:
            acad = self.client.get_application(auto_launch=True)
            doc = self.client.get_active_document(auto_create_if_empty=True)
            doc_name = getattr(doc, "Name", "Unknown")

            # Ensure command finishes with space or newline to trigger execution
            cmd_str = command if command.endswith(("\n", "\r", " ")) else command + " "

            with self.client.silent_mode(doc):
                doc.SendCommand(cmd_str)

            return {
                "success": True,
                "stdout": f"已向 AutoCAD 文档 [{doc_name}] 发送指令:\n{command.strip()}",
                "error": None,
                "doc_name": doc_name,
            }
        except Exception as err:
            return {
                "success": False,
                "stdout": "",
                "error": f"发送指令到 AutoCAD 出错: {err}",
                "doc_name": None,
            }
        finally:
            self.client.uninitialize_thread()

    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """Asynchronously execute script or command with timeout protection."""
        lang = language.lower().strip()
        if lang in ["python", "py"]:
            runner = lambda: self._sync_execute_python(code)
        elif lang in ["lisp", "command", "cmd"]:
            runner = lambda: self._sync_execute_command(code)
        else:
            return {
                "success": False,
                "stdout": "",
                "error": f"不支持的语言类型: '{language}'。请使用 'python' 或 'lisp'/'command'。",
                "doc_name": None,
            }

        try:
            return await asyncio.wait_for(asyncio.to_thread(runner), timeout=float(timeout))
        except asyncio.TimeoutError:
            return {
                "success": False,
                "stdout": "",
                "error": f"AutoCAD 代码执行超时 (超过了 {timeout} 秒)。",
                "doc_name": None,
            }
