"""Unit tests for AutoCAD code execution engine and CadRunCode tool."""

import asyncio
from unittest.mock import MagicMock, patch
import pytest

from app.cad.client import APoint, CadClient, CadNoActiveDocumentError, CadNotRunningError
from app.cad.executor import CadCodeExecutor
from app.tool.cad_execute import CadRunCode


class MockCadEntity:
    def __init__(self, obj_name: str, text: str = "", layer: str = "0"):
        self.ObjectName = obj_name
        self.TextString = text
        self.Layer = layer


class MockCadDocument:
    def __init__(self, name: str = "Drawing1.dwg"):
        self.Name = name
        self.ModelSpace = [
            MockCadEntity("AcDbText", text="标高 3.000", layer="TEXT"),
            MockCadEntity("AcDbLine", layer="0"),
        ]
        self.PaperSpace = []
        self.Layers = MagicMock()
        self.Blocks = MagicMock()
        self.SelectionSets = MagicMock()
        self.commands_sent = []

    def SendCommand(self, cmd: str):
        self.commands_sent.append(cmd)

    def GetVariable(self, var_name: str):
        return 1

    def SetVariable(self, var_name: str, val: int):
        pass


class MockCadClient(CadClient):
    def __init__(self, doc: MockCadDocument = None):
        super().__init__()
        self._mock_doc = doc or MockCadDocument()
        self._mock_acad = MagicMock()
        self._mock_acad.ActiveDocument = self._mock_doc

    def get_application(self, auto_launch: bool = True):
        return self._mock_acad

    def get_active_document(self, auto_create_if_empty: bool = False):
        return self._mock_doc


def test_apoint_creation():
    pt = APoint(100.0, 200.0, 300.0)
    assert pt is not None


def test_cad_executor_python_success():
    async def _test():
        mock_doc = MockCadDocument("TestPlan.dwg")
        mock_client = MockCadClient(mock_doc)
        executor = CadCodeExecutor(client=mock_client)

        script = """
print(f"当前图纸名称: {doc.Name}")
count = 0
for entity in model_space:
    if entity.ObjectName == 'AcDbText':
        count += 1
print(f"找到文字对象数量: {count}")
"""

        result = await executor.execute(code=script, language="python", timeout=10)
        assert result["success"] is True
        assert "当前图纸名称: TestPlan.dwg" in result["stdout"]
        assert "找到文字对象数量: 1" in result["stdout"]
        assert result["error"] is None
        assert result["doc_name"] == "TestPlan.dwg"

    asyncio.run(_test())


def test_cad_executor_syntax_error():
    async def _test():
        mock_client = MockCadClient()
        executor = CadCodeExecutor(client=mock_client)

        bad_script = "for x in:\n    print(x)"
        result = await executor.execute(code=bad_script, language="python", timeout=10)
        assert result["success"] is False
        assert "Python 语法错误" in result["error"]

    asyncio.run(_test())


def test_cad_executor_runtime_error():
    async def _test():
        mock_client = MockCadClient()
        executor = CadCodeExecutor(client=mock_client)

        err_script = "raise ValueError('找不到指定名称的图层')"
        result = await executor.execute(code=err_script, language="python", timeout=10)
        assert result["success"] is False
        assert "找不到指定名称的图层" in result["error"]

    asyncio.run(_test())


def test_cad_executor_command_mode():
    async def _test():
        mock_doc = MockCadDocument()
        mock_client = MockCadClient(mock_doc)
        executor = CadCodeExecutor(client=mock_client)

        result = await executor.execute(code="-PURGE A * N", language="command", timeout=10)
        assert result["success"] is True
        assert len(mock_doc.commands_sent) == 1
        assert mock_doc.commands_sent[0].startswith("-PURGE")

    asyncio.run(_test())


def test_cad_executor_timeout():
    async def _test():
        mock_client = MockCadClient()
        executor = CadCodeExecutor(client=mock_client)

        slow_script = "import time; time.sleep(2)"
        result = await executor.execute(code=slow_script, language="python", timeout=1)
        assert result["success"] is False
        assert "超时" in result["error"]

    asyncio.run(_test())


def test_cad_run_code_tool():
    async def _test():
        mock_doc = MockCadDocument("FloorPlan.dwg")
        mock_client = MockCadClient(mock_doc)
        executor = CadCodeExecutor(client=mock_client)

        tool = CadRunCode(executor=executor)
        assert tool.name == "cad_run_code"

        # Test execution success
        res = await tool.execute(code="print('Hello from Agent')", language="python")
        assert res.error is None
        assert "Hello from Agent" in res.output
        assert "FloorPlan.dwg" in res.output

        # Test execution error
        err_res = await tool.execute(code="1 / 0", language="python")
        assert err_res.error is not None
        assert "division by zero" in err_res.error

    asyncio.run(_test())
