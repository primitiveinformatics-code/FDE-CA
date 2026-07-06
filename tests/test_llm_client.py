from unittest.mock import MagicMock, patch

import pytest

from bank_statement_analyzer.cost_meter import CostMeter
from bank_statement_analyzer.llm.client import ClaudeToolClient, NoApiKeyError


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
