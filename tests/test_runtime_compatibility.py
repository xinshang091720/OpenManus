from app.config import LLMSettings, config
from app.llm import _add_provider_thinking_option, _retryable_llm_error
from app.tool.python_execute import PythonExecute
from app.tool.web_search import WebSearch


def test_browser_llm_profile_disables_thinking_without_changing_default_profile():
    assert config.llm["browser"].enable_thinking is False
    assert config.llm["default"].enable_thinking is None
    assert config.llm["default"].temperature == 0.2
    assert config.llm["browser"].temperature == 0.0


def test_invalid_request_errors_are_not_retried():
    assert not _retryable_llm_error(ValueError("invalid parameter"))
    assert _retryable_llm_error(RuntimeError("temporary network error"))


def test_provider_thinking_option_uses_openai_sdk_extra_body():
    params = {"model": "example", "extra_body": {"provider_flag": "keep"}}
    _add_provider_thinking_option(params, False)
    assert "enable_thinking" not in params
    assert params["extra_body"] == {
        "provider_flag": "keep",
        "enable_thinking": False,
    }


def test_search_rejects_relative_result_urls():
    assert not WebSearch._is_absolute_http_url("/s?wd=shenzhen-weather")
    assert not WebSearch._is_absolute_http_url("")
    assert WebSearch._is_absolute_http_url("https://www.example.cn/weather")


def test_python_execute_exposes_a_bounded_timeout():
    timeout = PythonExecute().parameters["properties"]["timeout"]
    assert timeout["default"] == 15
    assert timeout["maximum"] == 60
