"""Thin wrapper around the Anthropic tool-use API.

Every call here is a *forced* single-tool call (`tool_choice`) so the
response is always a structured `tool_use` block, never free text. Every
call records token usage on the shared `CostMeter` (FR-12). Callers are
responsible for masking any PII out of `user_content` before it reaches
this module (see `pii.masker`) — this module has no opinion on content,
it only manages the API round-trip and cost accounting.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from bank_statement_analyzer.cost_meter import CostMeter
from bank_statement_analyzer.llm.tools_schema import ADJUDICATOR_TOOL, CLASSIFIER_TOOL, FIELD_LOCATOR_TOOL


class NoApiKeyError(RuntimeError):
    pass


@dataclass
class ClaudeToolClient:
    api_key: str
    cost_meter: CostMeter
    model: str = "claude-sonnet-5"

    def __post_init__(self) -> None:
        if not self.api_key:
            raise NoApiKeyError("No Anthropic API key configured. Set it in the app's settings dialog.")
        import anthropic
        self._client = anthropic.Anthropic(api_key=self.api_key)

    def call_tool(
        self,
        system: str,
        user_content: str,
        tool: dict,
        max_tokens: int = 1024,
    ) -> dict:
        """Send one message, forcing a response via `tool`. Returns the
        tool's `input` dict and records usage on the cost meter."""
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user_content}],
        )
        self.cost_meter.add_usage(response.usage.input_tokens, response.usage.output_tokens)
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError(f"Model did not return a {tool['name']!r} tool_use block")

    def locate_columns(self, header_row: list[str], sample_rows: list[list[str]]) -> dict | None:
        """Map an unrecognized statement layout's columns. `sample_rows`
        must already be PII-masked narration text by the caller."""
        system = (
            "You map bank-statement table columns to a canonical schema: "
            "date, description, debit, credit, amount, drcr, balance. "
            "A layout has either separate debit/credit columns, or one "
            "amount column plus one Dr/Cr indicator column."
        )
        payload = json.dumps({"header": header_row, "sample_rows": sample_rows})
        result = self.call_tool(system, payload, FIELD_LOCATOR_TOOL, max_tokens=512)
        if result.get("confidence") == "low":
            return None
        return {k: v for k, v in result.items() if k != "confidence" and v is not None}

    def adjudicate_match(self, anchor_ref: str, candidates: list[dict]) -> dict:
        """Resolve an ambiguous self-transfer/refund leftover. `candidates`
        entries carry only refs/dates/amounts/masked narration."""
        system = (
            "You adjudicate ambiguous bank-transaction matches: given one "
            "anchor transaction and several plausible counterpart "
            "candidates (same amount, nearby date), pick the single best "
            "match by narration similarity, or none if genuinely unclear."
        )
        payload = json.dumps({"anchor_ref": anchor_ref, "candidates": candidates})
        return self.call_tool(system, payload, ADJUDICATOR_TOOL, max_tokens=512)

    def classify_income(self, counterparty: str, sample_descriptions: list[str]) -> dict:
        """Suggest an ITR head for a grouped, non-transfer income source
        whose counterparty keyword rules didn't already resolve it."""
        system = (
            "You suggest an Indian ITR income head for a grouped bank "
            "credit source, from a fixed list, given the counterparty "
            "label and a few sample (PII-masked) narrations."
        )
        payload = json.dumps({"counterparty": counterparty, "sample_descriptions": sample_descriptions})
        return self.call_tool(system, payload, CLASSIFIER_TOOL, max_tokens=512)
