import asyncio
import io
import json
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

import importlib.util

from app.revit.client import RevitApiClient

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "q_agent_function_module"
    / "ohresult"
    / "CAD_Git_Coordinates"
    / "my_code"
    / "ifc_function"
    / "ifc_SZ-IFC_to_docx.py"
)
_spec = importlib.util.spec_from_file_location("sz_ifc_module", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

extract_failed_element_ids_from_docx = _mod.extract_failed_element_ids_from_docx
call_open_delivery_api = _mod.call_open_delivery_api


def _create_mock_docx(tables_data: list[list[list[str]]]) -> io.BytesIO:
    """Helper to create a docx in-memory zip containing custom tables."""
    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    root = ET.Element(f"{{{w_ns}}}document")
    body = ET.SubElement(root, f"{{{w_ns}}}body")

    for tbl_data in tables_data:
        tbl = ET.SubElement(body, f"{{{w_ns}}}tbl")
        for row_data in tbl_data:
            tr = ET.SubElement(tbl, f"{{{w_ns}}}tr")
            for cell_text in row_data:
                tc = ET.SubElement(tr, f"{{{w_ns}}}tc")
                p = ET.SubElement(tc, f"{{{w_ns}}}p")
                r = ET.SubElement(p, f"{{{w_ns}}}r")
                t = ET.SubElement(r, f"{{{w_ns}}}t")
                t.text = cell_text

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", xml_bytes)
    buffer.seek(0)
    return buffer


def test_extract_failed_element_ids_all_passed(tmp_path):
    # Table with all passed rules (no '未通过' row)
    doc_buffer = _create_mock_docx([
        [
            ["规则序号", "规则名称"],
            ["构件分类", "构件ID（建模软件ID、GUID、IfcID）", "构件数量"],
            ["1", "深圳构件标识存在且同深圳建筑信息模型语义字典标准保持一致"],
        ]
    ])
    docx_file = tmp_path / "all_passed.docx"
    docx_file.write_bytes(doc_buffer.read())

    ids = extract_failed_element_ids_from_docx(docx_file)
    assert ids == []


def test_extract_failed_element_ids_multiple_rules(tmp_path):
    # Table with multiple unpassed rules
    doc_buffer = _create_mock_docx([
        [
            ["规则序号", "规则名称"],
            ["构件分类", "构件ID（建模软件ID、GUID、IfcID）", "构件数量"],
            ["1", "深圳构件标识存在且同深圳建筑信息模型语义字典标准保持一致"],
            ["未通过", "3080319; 3080210; ", "2"],
            ["2", "项目基点东西坐标应满足要求"],
            ["未通过", "70; 3080210;", "2"],  # contains duplicate 3080210
        ]
    ])
    docx_file = tmp_path / "failed_rules.docx"
    docx_file.write_bytes(doc_buffer.read())

    ids = extract_failed_element_ids_from_docx(docx_file)
    # Deduplicated and numerically sorted
    assert ids == ["70", "3080210", "3080319"]


def test_extract_failed_element_ids_real_sample_files_if_exist():
    sample_dir = Path(r"C:\Users\jly23\Desktop\质检报告")
    if not sample_dir.is_dir():
        pytest.skip("Desktop 质检报告 目录不存在，跳过真实样本测试")

    test_st = sample_dir / "测试-ST-模型质量检查报告.docx"
    if test_st.exists():
        ids = extract_failed_element_ids_from_docx(test_st)
        assert ids == ["3080210", "3080319"]

    b3_el = sample_dir / "满京华雪象_地下室_B3-EL_质检报告.docx"
    if b3_el.exists():
        ids = extract_failed_element_ids_from_docx(b3_el)
        assert "70" in ids
        assert "7901715" in ids
        assert len(ids) == 19  # 70 (deduplicated across 2 rules) + 18 other elements = 19 unique

    b3_st = sample_dir / "满京华雪象_地下室_B3-ST_质检报告.docx"
    if b3_st.exists():
        ids = extract_failed_element_ids_from_docx(b3_st)
        assert len(ids) == 354
        assert "2931692" in ids
        assert "3058362" in ids


def test_call_open_delivery_api_success(monkeypatch):
    captured = {}

    class MockResponse:
        status_code = 200

        def json(self):
            return {"code": 200, "msg": "打开成功 "}

    def mock_post(url, json=None, headers=None, timeout=60):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return MockResponse()

    import requests
    monkeypatch.setattr(requests, "post", mock_post)

    result = call_open_delivery_api(["2448647", "2461670"])
    assert result["code"] == 200
    assert result["msg"] == "打开成功 "
    assert captured["json"] == {"ElementIds": ["2448647", "2461670"]}
    assert "OpenDelivery" in captured["url"]


def test_call_open_delivery_api_with_env_url(monkeypatch):
    captured = {}

    class MockResponse:
        status_code = 200

        def json(self):
            return {"code": 200, "msg": "打开成功 "}

    def mock_post(url, json=None, headers=None, timeout=60):
        captured["url"] = url
        captured["json"] = json
        return MockResponse()

    import requests
    monkeypatch.setattr(requests, "post", mock_post)
    monkeypatch.setenv("BEESYNC_REVIT_API_BASE_URL", "http://127.0.0.1:40123/api/RevitApi/")

    result = call_open_delivery_api(["2448647"])
    assert result["code"] == 200
    assert captured["url"] == "http://127.0.0.1:40123/api/RevitApi/OpenDelivery"
    assert captured["json"] == {"ElementIds": ["2448647"]}


def test_call_open_delivery_api_error(monkeypatch):
    def mock_post(*_args, **_kwargs):
        raise ConnectionError("Connection refused")

    import requests
    monkeypatch.setattr(requests, "post", mock_post)

    result = call_open_delivery_api(["2448647"])
    assert result["code"] == 500
    assert "Connection refused" in result["msg"]


def test_revit_api_client_open_delivery():
    captured = {}

    class MockClient(RevitApiClient):
        async def _post_operation(self, path, payload, wait_forever):
            captured["path"] = path
            captured["payload"] = payload
            captured["wait_forever"] = wait_forever
            return {"code": 200, "msg": "打开成功 "}

    client = MockClient("http://localhost:5000//api/RevitApi")
    response = asyncio.run(client.open_delivery(["2448647", 2461670]))
    assert response["code"] == 200
    assert captured["path"] == "/OpenDelivery"
    assert captured["payload"] == {"ElementIds": ["2448647", "2461670"]}
