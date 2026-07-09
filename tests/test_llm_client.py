import json
from unittest.mock import MagicMock, patch

import pytest

from bank_statement_analyzer.cost_meter import CostMeter
from bank_statement_analyzer.llm.client import (
    ClaudeToolClient,
    NoApiKeyError,
    OpenRouterToolClient,
    create_llm_client,
)
from bank_statement_analyzer.llm.tools_schema import CLASSIFIER_TOOL


def _fake_response(tool_name: str, tool_input: dict, input_tokens=100, output_tokens=20):
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


def test_missing_api_key_raises():
    with pytest.raises(NoApiKeyError):
        ClaudeToolClient(api_key="", cost_meter=CostMeter())


@patch("anthropic.Anthropic")
def test_call_tool_extracts_input_and_tracks_cost(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        "report_income_classification",
        {"counterparty": "ACME", "itr_head": "Salary", "confidence": "high"},
    )
    mock_anthropic_cls.return_value = mock_client

    meter = CostMeter()
    client = ClaudeToolClient(api_key="fake-key", cost_meter=meter)
    result = client.classify_income("ACME", ["salary credit"])

    assert result["itr_head"] == "Salary"
    assert meter.input_tokens == 100
    assert meter.output_tokens == 20
    assert meter.call_count == 1


@patch("anthropic.Anthropic")
def test_locate_columns_returns_none_on_low_confidence(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        "report_column_mapping",
        {"date": 0, "description": 1, "confidence": "low"},
    )
    mock_anthropic_cls.return_value = mock_client

    client = ClaudeToolClient(api_key="fake-key", cost_meter=CostMeter())
    assert client.locate_columns(["a", "b"], [["1", "2"]]) is None


@patch("anthropic.Anthropic")
def test_locate_account_info_returns_none_on_low_confidence(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        "report_account_info",
        {"confidence": "low"},
    )
    mock_anthropic_cls.return_value = mock_client

    client = ClaudeToolClient(api_key="fake-key", cost_meter=CostMeter())
    assert client.locate_account_info("some header text") is None


@patch("anthropic.Anthropic")
def test_locate_account_info_extracts_fields(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        "report_account_info",
        {
            "bank": "HDFC Bank", "account_holder_name": "JOHN DOE", "account_number": "1234567890",
            "ifsc": "HDFC0001234", "period_start": "2025-04-01", "period_end": "2026-03-31",
            "confidence": "high",
        },
    )
    mock_anthropic_cls.return_value = mock_client

    client = ClaudeToolClient(api_key="fake-key", cost_meter=CostMeter())
    result = client.locate_account_info("some header text")

    assert result["account_holder_name"] == "JOHN DOE"
    assert result["period_start"] == "2025-04-01"
    assert "confidence" not in result


@patch("anthropic.Anthropic")
def test_cost_meter_threshold_callback_fires(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        "report_income_classification",
        {"counterparty": "ACME", "itr_head": "Salary", "confidence": "high"},
        input_tokens=2_000_000, output_tokens=500_000,
    )
    mock_anthropic_cls.return_value = mock_client

    meter = CostMeter()
    fired = {}
    meter.on_threshold_exceeded(lambda cost: fired.setdefault("cost", cost))

    client = ClaudeToolClient(api_key="fake-key", cost_meter=meter)
    client.classify_income("ACME", ["salary credit"])

    assert "cost" in fired
    assert fired["cost"] == meter.estimated_cost_usd


# --------------------------------------------------------------- OpenRouter

def _fake_openrouter_response(tool_name: str, tool_input: dict, prompt_tokens=100, completion_tokens=20):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {"name": tool_name, "arguments": json.dumps(tool_input)},
                }],
            },
        }],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }
    return resp


def test_openrouter_missing_api_key_raises():
    with pytest.raises(NoApiKeyError):
        OpenRouterToolClient(api_key="", cost_meter=CostMeter())


@patch("httpx.Client")
def test_openrouter_call_tool_extracts_input_and_tracks_cost(mock_httpx_cls):
    mock_client = MagicMock()
    mock_client.post.return_value = _fake_openrouter_response(
        "report_income_classification",
        {"counterparty": "ACME", "itr_head": "Salary", "confidence": "high"},
    )
    mock_httpx_cls.return_value = mock_client

    meter = CostMeter()
    client = OpenRouterToolClient(api_key="fake-key", cost_meter=meter)
    result = client.classify_income("ACME", ["salary credit"])

    assert result["itr_head"] == "Salary"
    assert meter.input_tokens == 100
    assert meter.output_tokens == 20
    assert meter.call_count == 1
    # forced tool_choice targets the right function, in OpenAI's tool format
    _, kwargs = mock_client.post.call_args
    assert kwargs["json"]["tool_choice"] == {"type": "function", "function": {"name": "report_income_classification"}}
    assert kwargs["json"]["tools"][0]["function"]["parameters"] == CLASSIFIER_TOOL["input_schema"]


@patch("httpx.Client")
def test_openrouter_locate_columns_returns_none_on_low_confidence(mock_httpx_cls):
    mock_client = MagicMock()
    mock_client.post.return_value = _fake_openrouter_response(
        "report_column_mapping",
        {"date": 0, "description": 1, "confidence": "low"},
    )
    mock_httpx_cls.return_value = mock_client

    client = OpenRouterToolClient(api_key="fake-key", cost_meter=CostMeter())
    assert client.locate_columns(["a", "b"], [["1", "2"]]) is None


def test_create_llm_client_factory():
    meter = CostMeter()
    assert create_llm_client("anthropic", None, meter) is None
    assert isinstance(create_llm_client("anthropic", "key", meter), ClaudeToolClient)
    assert isinstance(create_llm_client("openrouter", "key", meter), OpenRouterToolClient)
    with pytest.raises(ValueError):
        create_llm_client("not-a-real-provider", "key", meter)
