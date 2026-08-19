"""Focused regression coverage for the optional room-text classifier.

The CAD/DXF and Revit paths deliberately stay outside this file: these tests
only protect the small LLM fallback used for ambiguous Chinese labels.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import ezdxf
import openai
import pytest

from q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates import (
    dwg_room_extractor as extractor,
)


def _candidate(text: str) -> dict:
    return {
        "raw_text": text,
        "clean_text": text,
        "clean_chinese": text,
        "has_chinese": True,
        "layer": "ROOM_TEXT",
        "type": "TEXT",
        "pos": (0.0, 0.0, 0.0),
    }


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=result))]
        )


class _FakeClient:
    def __init__(self, responses):
        self.completions = _FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)

def _fake_client(*responses):
    client = _FakeClient(responses)
    return client, client.completions


def test_room_text_classifier_treats_malformed_json_as_a_single_label_failure(monkeypatch):
    """A broken response must not abort the drawing or trigger SDK retry waits."""
    client, completions = _fake_client('{"is_room": ', '{"is_room": true}')
    monkeypatch.setattr(extractor, "client", client)

    item, is_room = extractor.judge_room_by_qwen(_candidate("候诊区"))

    assert item["clean_text"] == "候诊区"
    assert is_room is False
    assert len(completions.calls) == 1


def test_room_text_classifier_stops_after_one_network_failure(monkeypatch):
    """One unavailable label must not turn into a 10-minute blocking retry loop."""
    client, completions = _fake_client(TimeoutError("network stalled"), '{"is_room": true}')
    monkeypatch.setattr(extractor, "client", client)

    item, is_room = extractor.judge_room_by_qwen(_candidate("候诊区"))

    assert item["clean_text"] == "候诊区"
    assert is_room is False
    assert len(completions.calls) == 1


def test_room_text_classifier_uses_fast_non_thinking_model_request(monkeypatch):
    """Room-label classification must not silently consume the main thinking model."""
    client, completions = _fake_client('{"is_room": false}')
    monkeypatch.setattr(extractor, "client", client)
    monkeypatch.setattr(extractor, "ROOM_TEXT_MODEL", "qwen3.5-flash")

    _, is_room = extractor.judge_room_by_qwen(_candidate("候诊区"))

    assert is_room is False
    assert len(completions.calls) == 1
    request = completions.calls[0]
    assert request["model"] == "qwen3.5-flash"
    assert request["extra_body"] == {"enable_thinking": False}
    assert request["max_tokens"] == 32


@pytest.mark.parametrize(
    ("room_model", "legacy_fast_model", "runtime_model", "expected_model"),
    [
        ("room-text-fast-model", "legacy-fast-model", "main-thinking-model", "room-text-fast-model"),
        (None, "legacy-fast-model", "main-thinking-model", "legacy-fast-model"),
        (None, None, "main-thinking-model", "main-thinking-model"),
    ],
)
def test_room_text_model_config_has_explicit_priority_and_bounds_sdk_wait(
    monkeypatch,
    room_model,
    legacy_fast_model,
    runtime_model,
    expected_model,
):
    """The tiny classifier can use the same credentials without inheriting the main model."""
    constructor_calls: list[dict] = []

    class ConstructorClient:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(openai, "OpenAI", ConstructorClient)
        scoped.setenv("BEESYNC_LLM_API_KEY", "test-key")
        scoped.setenv("BEESYNC_LLM_BASE_URL", "https://example.invalid/v1")
        for name, value in (
            ("BEESYNC_LLM_MODEL", runtime_model),
            ("QWEN35_FLASH_MODEL", legacy_fast_model),
            ("BEESYNC_ROOM_TEXT_MODEL", room_model),
        ):
            if value is None:
                # ``load_dotenv`` runs during module import.  An explicit
                # empty value prevents a developer-local .env from changing
                # this priority test while still behaving as "not set".
                scoped.setenv(name, "")
            else:
                scoped.setenv(name, value)
        scoped.setenv("BEESYNC_ROOM_TEXT_TIMEOUT_SECONDS", "999")

        importlib.reload(extractor)
        assert extractor.ROOM_TEXT_MODEL == expected_model
        assert constructor_calls == [{
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "timeout": 60.0,
            "max_retries": 0,
        }]

    # Restore the module's process-wide configuration for later tests.
    importlib.reload(extractor)


def test_room_text_fallback_deduplicates_candidates_and_filters_obvious_annotations(tmp_path, monkeypatch):
    """Only ambiguous, unique labels go to the LLM; source DXF entities stay intact."""
    drawing = ezdxf.new()
    msp = drawing.modelspace()
    msp.add_text("候诊区", dxfattribs={"layer": "ROOM_TEXT", "insert": (0, 0)})
    msp.add_text("候诊区", dxfattribs={"layer": "ROOM_TEXT", "insert": (1, 0)})
    msp.add_text("上", dxfattribs={"layer": "ROOM_TEXT", "insert": (2, 0)})
    msp.add_text("公用设备房面积：1350平方米", dxfattribs={"layer": "ROOM_TEXT", "insert": (3, 0)})
    dxf_path = tmp_path / "floor.dxf"
    drawing.saveas(dxf_path)

    calls: list[str] = []

    def classify(item):
        calls.append(item["clean_text"])
        return item, item["clean_text"] == "候诊区"

    monkeypatch.setattr(extractor, "judge_room_by_qwen", classify)

    result = extractor.process_dwg_room_extraction(str(dxf_path), max_workers=1)

    assert calls == ["候诊区"]
    # Both CAD labels remain legitimate room labels; only model requests are deduplicated.
    assert [item["RoomName"] for item in result["room_texts"]] == ["候诊区", "候诊区"]
