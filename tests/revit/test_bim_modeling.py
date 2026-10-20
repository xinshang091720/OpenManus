import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from app.api.runtime import RuntimeManus
from app.mcp.server import MCPServer
from app.revit.client import RevitApiClient
from app.revit.modeling import RevitBimModelingWorkflow, _normalise_major
from app.tool.revit_modeling import RevitBimModeling


def test_revit_client_has_modeling_methods():
    client = RevitApiClient()
    assert hasattr(client, "create_model")
    assert hasattr(client, "create_level_view")
    assert hasattr(client, "create_components")


def test_normalise_major():
    assert _normalise_major("建筑") == "建筑"
    assert _normalise_major("AR") == "建筑"
    assert _normalise_major("arch") == "建筑"
    assert _normalise_major("结构") == "结构"
    assert _normalise_major("ST") == "结构"
    import pytest
    with pytest.raises(ValueError):
        _normalise_major(None)


def test_mcp_server_registers_revit_bim_modeling():
    server = MCPServer()
    assert "revit_bim_modeling" in server.tools
    assert isinstance(server.tools["revit_bim_modeling"], RevitBimModeling)


def test_runtime_write_tools_includes_bim_modeling():
    assert "revit_bim_modeling" in RuntimeManus()._REVIT_WRITE_TOOLS


def test_tool_param_schema():
    tool = RevitBimModeling()
    param = tool.to_param()
    assert param["type"] == "function"
    assert param["function"]["name"] == "revit_bim_modeling"
    props = param["function"]["parameters"]["properties"]
    assert "cad_folder" in props
    assert "model_name" in props
    assert "discipline" in props
    assert "model_save_path" in props
    assert "skip_components" in props
    assert set(param["function"]["parameters"]["required"]) == {"cad_folder", "discipline"}


def test_workflow_execution_mocked(tmp_path):
    from unittest.mock import patch

    # Prepare dummy DWG folder with mock elevation dwg
    elev_dwg = tmp_path / "1-2立面图.dwg"
    elev_dwg.write_bytes(b"dummy")
    plan_dwg = tmp_path / "1层结构平面图.dwg"
    plan_dwg.write_bytes(b"dummy")

    mock_client = AsyncMock(spec=RevitApiClient)
    mock_client.plugin_status.return_value = {"status": "ready"}
    mock_client.base_point_setting_is_correct.return_value = {"code": 400}
    mock_client._post.return_value = {"code": 200}
    mock_client.create_model.return_value = {"code": 200, "msg": "创建成功"}
    mock_client.create_level_view.return_value = {"code": 200, "msg": "创建标高成功"}
    mock_client.create_components.return_value = {"code": 200, "msg": "构件生成完成"}

    workflow = RevitBimModelingWorkflow(mock_client)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def dummy_hold():
        yield

    workflow.lock.hold = dummy_hold

    mock_elev_result = {
        "msg": "success",
        "data": [
            {"floor": "1F", "elevation": 0.0},
            {"floor": "2F", "elevation": 3600.0},
        ],
    }
    mock_aligned_result = {
        "msg": "success",
        "leveDatas": [
            {"floor": "1F", "elevation": 0.0, "dwgsFilePaths": [str(plan_dwg)]},
            {"floor": "2F", "elevation": 3600.0, "dwgsFilePaths": [str(plan_dwg)]},
        ],
    }

    async def run_test():
        with patch(
            "app.revit.modeling.extract_elevation_batch",
            return_value=mock_elev_result,
        ), patch(
            "app.revit.modeling.bind_elevations_and_plans",
            return_value=mock_aligned_result,
        ):
            return await workflow.run(
                cad_folder=str(tmp_path),
                model_name="TestModel",
                major_name="结构",
                model_save_path=str(tmp_path / "output"),
                skip_components=False,
            )

    result = asyncio.run(run_test())

    # Verify calls
    assert mock_client.create_model.called
    assert mock_client.create_level_view.called
    assert mock_client.create_components.called
    assert mock_client.create_components.call_args[0][0] == "结构"
    assert result["status"] == "success"
    assert result["major"] == "结构"
    assert result["levels_count"] == 2


def test_workflow_fallback_seed_when_no_active_document(tmp_path):
    from unittest.mock import patch
    from app.revit.client import RevitApiError

    elev_dwg = tmp_path / "1-2立面图.dwg"
    elev_dwg.write_bytes(b"dummy")
    plan_dwg = tmp_path / "1层结构平面图.dwg"
    plan_dwg.write_bytes(b"dummy")

    mock_client = AsyncMock(spec=RevitApiClient)
    mock_client.plugin_status.return_value = {"status": "ready"}
    mock_client.base_point_setting_is_correct.return_value = {"code": 400}
    mock_client.open_revit_file.return_value = {"code": 200, "msg": "打开成功"}
    # First call to create_model raises "未打开Revit文档", second succeeds
    mock_client.create_model.side_effect = [
        RevitApiError("未打开Revit文档", endpoint="/CreateModel", http_status=200, plugin_code=400),
        {"code": 200, "msg": "创建成功"},
    ]
    mock_client.create_level_view.return_value = {"code": 200, "msg": "创建标高成功"}
    mock_client.create_components.return_value = {"code": 200, "msg": "构件生成完成"}

    workflow = RevitBimModelingWorkflow(mock_client)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def dummy_hold():
        yield

    workflow.lock.hold = dummy_hold

    mock_elev_result = {
        "msg": "success",
        "data": [{"floor": "1F", "elevation": 0.0}],
    }
    mock_aligned_result = {
        "msg": "success",
        "leveDatas": [{"floor": "1F", "elevation": 0.0, "dwgsFilePaths": [str(plan_dwg)]}],
    }

    async def run_test():
        with patch("app.revit.modeling.extract_elevation_batch", return_value=mock_elev_result), \
             patch("app.revit.modeling.bind_elevations_and_plans", return_value=mock_aligned_result):
            return await workflow.run(
                cad_folder=str(tmp_path),
                model_name="TestModel",
                major_name="结构",
                model_save_path=str(tmp_path / "output"),
                skip_components=False,
            )

    result = asyncio.run(run_test())

    # Verify OpenRevitFile was called to activate seed document
    assert mock_client.open_revit_file.called
    assert mock_client.create_model.call_count == 2
    assert result["status"] == "success"


def test_workflow_continues_on_active_model_without_reopening(tmp_path):
    from unittest.mock import patch

    elev_dwg = tmp_path / "1-2立面图.dwg"
    elev_dwg.write_bytes(b"dummy")
    plan_dwg = tmp_path / "1层结构平面图.dwg"
    plan_dwg.write_bytes(b"dummy")
    existing_model = tmp_path / "output" / "TestModel.rvt"
    existing_model.parent.mkdir(parents=True, exist_ok=True)
    existing_model.write_bytes(b"existing_rvt")

    mock_client = AsyncMock(spec=RevitApiClient)
    mock_client.plugin_status.return_value = {"status": "ready"}
    # Revit already has an active document matching TestModel
    mock_client.base_point_setting_is_correct.return_value = {
        "code": 200,
        "msg": "active",
        "data": {
            "DocumentTitle": "TestModel.rvt",
            "DocumentPath": str(existing_model),
        },
    }
    mock_client.create_level_view.return_value = {"code": 200, "msg": "创建标高成功"}
    mock_client.create_components.return_value = {"code": 200, "msg": "构件生成完成"}

    workflow = RevitBimModelingWorkflow(mock_client)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def dummy_hold():
        yield

    workflow.lock.hold = dummy_hold

    mock_elev_result = {
        "msg": "success",
        "data": [{"floor": "1F", "elevation": 0.0}],
    }
    mock_aligned_result = {
        "msg": "success",
        "leveDatas": [{"floor": "1F", "elevation": 0.0, "dwgsFilePaths": [str(plan_dwg)]}],
    }

    async def run_test():
        with patch("app.revit.modeling.extract_elevation_batch", return_value=mock_elev_result), \
             patch("app.revit.modeling.bind_elevations_and_plans", return_value=mock_aligned_result):
            return await workflow.run(
                cad_folder=str(tmp_path),
                model_name="TestModel.rvt",
                major_name="结构",
                model_save_path=str(tmp_path / "output"),
                rvt_file_path=str(existing_model),
                reuse_existing_model=True,
                skip_components=False,
            )

    result = asyncio.run(run_test())

    # Neither OpenRevitFile nor CreateModel should be called because Revit is already active on this model!
    assert not mock_client.open_revit_file.called
    assert not mock_client.create_model.called
    assert result["status"] == "success"


def test_workflow_shares_multi_floor_dwgs_without_disk_copy(tmp_path):
    from unittest.mock import patch

    elev_dwg = tmp_path / "1-2立面图.dwg"
    elev_dwg.write_bytes(b"dummy")
    shared_dwg = tmp_path / "五~十一层楼面结构平面布置图.dwg"
    shared_dwg.write_bytes(b"dwg_content")

    mock_client = AsyncMock(spec=RevitApiClient)
    mock_client.plugin_status.return_value = {"status": "ready"}
    mock_client.base_point_setting_is_correct.return_value = {"code": 200}
    mock_client.create_level_view.return_value = {"code": 200, "msg": "创建标高成功"}
    mock_client.create_components.return_value = {"code": 200, "msg": "构件生成完成"}

    workflow = RevitBimModelingWorkflow(mock_client)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def dummy_hold():
        yield

    workflow.lock.hold = dummy_hold

    mock_elev_result = {
        "msg": "success",
        "data": [
            {"floor": "F5", "elevation": 14.7},
            {"floor": "F6", "elevation": 18.1},
            {"floor": "F7", "elevation": 21.5},
        ],
    }
    mock_aligned_result = {
        "msg": "success",
        "leveDatas": [
            {"floor": "F5", "elevation": 14.7, "dwgsFilePaths": [str(shared_dwg)]},
            {"floor": "F6", "elevation": 18.1, "dwgsFilePaths": [str(shared_dwg)]},
            {"floor": "F7", "elevation": 21.5, "dwgsFilePaths": [str(shared_dwg)]},
        ],
    }

    async def run_test():
        with patch("app.revit.modeling.extract_elevation_batch", return_value=mock_elev_result), \
             patch("app.revit.modeling.bind_elevations_and_plans", return_value=mock_aligned_result):
            return await workflow.run(
                cad_folder=str(tmp_path),
                model_name="TestMultiFloor",
                major_name="结构",
                model_save_path=str(tmp_path / "output"),
                skip_components=False,
            )

    result = asyncio.run(run_test())
    assert result["status"] == "success"

    # Verify that multi-floor drawings directly point to the original DWG without disk copying
    call_args = mock_client.create_level_view.call_args[0]
    sent_leve_datas = call_args[0]
    f5_dwg = sent_leve_datas[0]["dwgsFilePaths"][0]
    f6_dwg = sent_leve_datas[1]["dwgsFilePaths"][0]
    f7_dwg = sent_leve_datas[2]["dwgsFilePaths"][0]

    assert f5_dwg == str(shared_dwg)
    assert f6_dwg == str(shared_dwg)
    assert f7_dwg == str(shared_dwg)
    # Ensure no extra copy files were spawned in tmp_path
    copied_files = list(tmp_path.glob("*_F*.dwg"))
    assert len(copied_files) == 0


def test_elevation_plan_matcher_coverage_regex():
    import sys
    _MY_CODE_DIR = Path(__file__).resolve().parents[2] / "q_agent_function_module" / "ohresult_1" / "CAD_Git_Coordinates" / "my_code"
    if str(_MY_CODE_DIR) not in sys.path:
        sys.path.insert(0, str(_MY_CODE_DIR))
    _ELEV_DIR = _MY_CODE_DIR / "Elevation_Marking"
    if str(_ELEV_DIR) not in sys.path:
        sys.path.insert(0, str(_ELEV_DIR))
    from elevation_plan_matcher import ElevationPlanMatcher

    # Above-ground multi-floor range
    cov1 = ElevationPlanMatcher.parse_plan_floor_coverage("五~十一层楼面结构平面布置图.dwg")
    assert cov1 == {5, 6, 7, 8, 9, 10, 11}

    cov2 = ElevationPlanMatcher.parse_plan_floor_coverage("5~11张楼面结构平面布置图.dwg")
    assert cov2 == {5, 6, 7, 8, 9, 10, 11}

    cov3 = ElevationPlanMatcher.parse_plan_floor_coverage("6~30层平面图.dwg")
    assert cov3 == set(range(6, 31))

    # Underground multi-floor range
    cov4 = ElevationPlanMatcher.parse_plan_floor_coverage("地下一层~地下三层平面图.dwg")
    assert cov4 == {-3, -2, -1}

    cov5 = ElevationPlanMatcher.parse_plan_floor_coverage("负1~负3层平面图.dwg")
    assert cov5 == {-3, -2, -1}


def test_floor_overrides_in_matcher():
    import sys
    _MY_CODE_DIR = Path(__file__).resolve().parents[2] / "q_agent_function_module" / "ohresult_1" / "CAD_Git_Coordinates" / "my_code"
    if str(_MY_CODE_DIR) not in sys.path:
        sys.path.insert(0, str(_MY_CODE_DIR))
    _ELEV_DIR = _MY_CODE_DIR / "Elevation_Marking"
    if str(_ELEV_DIR) not in sys.path:
        sys.path.insert(0, str(_ELEV_DIR))
    from elevation_plan_matcher import ElevationPlanMatcher

    elev_records = [
        {"floor": "F5", "elevation": "14.7"},
        {"floor": "F6", "elevation": "18.1"},
        {"floor": "F7", "elevation": "21.5"},
    ]
    plans = ["C:\\project\\柱平面布置图.dwg"]

    # Provide override to map it to 5~7
    overrides = [
        {"dwg_name": "柱平面布置图", "floors": [5, 6, 7]}
    ]
    res = ElevationPlanMatcher.match_elevations_with_plans(elev_records, plans, floor_overrides=overrides)
    assert len(res) == 3
    assert res[0]["dwgsFilePaths"] == ["C:\\project\\柱平面布置图.dwg"]
    assert res[1]["dwgsFilePaths"] == ["C:\\project\\柱平面布置图.dwg"]
    assert res[2]["dwgsFilePaths"] == ["C:\\project\\柱平面布置图.dwg"]


def test_semantic_elevation_preservation_and_plan_isolation():
    """Verify outdoor ground (室外地坪) and parapet (女儿墙) are preserved and do not bind floor plans."""
    import sys
    _MY_CODE_DIR = Path(__file__).resolve().parents[2] / "q_agent_function_module" / "ohresult_1" / "CAD_Git_Coordinates" / "my_code"
    if str(_MY_CODE_DIR) not in sys.path:
        sys.path.insert(0, str(_MY_CODE_DIR))
    _ELEV_DIR = _MY_CODE_DIR / "Elevation_Marking"
    if str(_ELEV_DIR) not in sys.path:
        sys.path.insert(0, str(_ELEV_DIR))
    from cad_extractor import ElevationCandidate, FloorCandidate
    from elevation_processor import ElevationProcessor
    from floor_standardizer import FloorStandardizer
    from elevation_plan_matcher import ElevationPlanMatcher

    # 1. Test ElevationProcessor dual-elevation isolation with realistic CAD mm coordinates
    elev_cands = [
        ElevationCandidate(x=100.0, y=1000.0, elevation_val=-0.450, elev_str="-0.450", raw_text="-0.450 室外地坪", floor_hint="室外地坪"),
        ElevationCandidate(x=100.0, y=2000.0, elevation_val=0.000, elev_str="0.000", raw_text="±0.000"),
        ElevationCandidate(x=100.0, y=6500.0, elevation_val=4.500, elev_str="4.500", raw_text="4.500 (2F)"),
        ElevationCandidate(x=100.0, y=40500.0, elevation_val=38.500, elev_str="38.500", raw_text="38.500 结构", floor_hint="结构"),
        ElevationCandidate(x=100.0, y=47100.0, elevation_val=45.100, elev_str="45.100", raw_text="45.100 女儿墙", floor_hint="女儿墙"),
    ]
    floor_cands = [
        FloorCandidate(x=200.0, y=1000.0, floor_text="室外地坪"),
        FloorCandidate(x=200.0, y=6500.0, floor_text="2F"),
        FloorCandidate(x=200.0, y=40500.0, floor_text="结构"),
        FloorCandidate(x=200.0, y=47100.0, floor_text="女儿墙"),
    ]

    proc = ElevationProcessor(elev_cands, floor_cands)
    raw_res = proc.process()
    std_res = FloorStandardizer.standardize(raw_res)

    floors_extracted = [r["floor"] for r in std_res]
    assert floors_extracted == ["室外地坪", "F1", "F2", "结构", "女儿墙"]

    # 2. Test Plan Matcher isolation: 室外地坪 and 女儿墙 must NOT match floor plans
    plans = [
        "C:\\drawings\\首层平面图.dwg",
        "C:\\drawings\\二层平面图.dwg",
        "C:\\drawings\\屋面层平面图.dwg",
    ]
    matched = ElevationPlanMatcher.match_elevations_with_plans(std_res, plans)
    floor_to_dwgs = {m["floor"]: m["dwgsFilePaths"] for m in matched}

    assert floor_to_dwgs["室外地坪"] == [], "室外地坪 must have no plan drawings attached"
    assert floor_to_dwgs["F1"] == ["C:\\drawings\\首层平面图.dwg"]
    assert floor_to_dwgs["F2"] == ["C:\\drawings\\二层平面图.dwg"]
    assert floor_to_dwgs["结构"] == ["C:\\drawings\\屋面层平面图.dwg"]
    assert floor_to_dwgs["女儿墙"] == [], "女儿墙 must have no plan drawings attached"




