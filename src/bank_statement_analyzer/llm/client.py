"""Thin wrappers around the two supported LLM providers: Anthropic
(`ClaudeToolClient`) and OpenRouter (`OpenRouterToolClient`).

Every call here is a *forced* single-tool call so the response is always
a structured tool-call, never free text. Every call records token usage
on the shared `CostMeter` (FR-12). Callers are responsible for masking
any PII out of `user_content` before it reaches this module (see
`pii.masker`) — this module has no opinion on content, it only manages
the API round-trip and cost accounting.

Both clients expose the same four public methods (`locate_columns`,
`adjudicate_match`, `locate_account_info`, `classify_income`), defined
once on `LLMClient` in terms of an abstract `call_tool` — so callers
(`agents/*.py`) can treat either provider interchangeably. Only
`call_tool` (the actual HTTP round-trip and provider-specific tool-call
format) differs per provider.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from bank_statement_analyzer.cost_meter import CostMeter
from bank_statement_analyzer.llm.tools_schema import (
    ACCOUNT_INFO_TOOL,
    ADJUDICATOR_TOOL,
    CLASSIFIER_TOOL,
    FIELD_LOCATOR_TOOL,
)

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class NoApiKeyError(RuntimeError):
    pass


@dataclass
class LLMClient:
    """Shared base: tool-specific prompts + payload shaping. Subclasses
    implement `call_tool`, the only provider-specific piece."""

    api_key: str
    cost_meter: CostMeter
    model: str = ""

    def __post_init__(self) -> None:
        if not self.api_key:
            raise NoApiKeyError(self._missing_key_message())
        self._init_client()

    def _missing_key_message(self) -> str:  # pragma: no cover - overridden
        return "No API key configured. Set it in the app's settings dialog."

    def _init_client(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def call_tool(
        self,
        system: str,
        user_content: str,
        tool: dict,
        max_tokens: int = 1024,
    ) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

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

    def locate_account_info(self, header_text: str) -> dict | None:
        """Extract account holder name / account number / IFSC / bank /
        statement period from a statement's header text, when regex
        couldn't confidently locate one or more of them.

        Unlike every other call in this module, `header_text` is NOT
        PII-masked by the caller — masking the account number/name would
        defeat the purpose, since those *are* the values being extracted.
        This is a deliberate, scoped exception to the app's general
        "raw PII never leaves the machine" rule, gated behind the UI's
        "keep names & account numbers local" toggle (default off, i.e.
        this call is enabled by default) — see `extraction_agent.py`.
        """
        system = (
            "You extract identifying header fields from an Indian bank "
            "statement's opening text: account holder name, account "
            "number, IFSC code, bank name, and statement period. Report "
            "null for anything not confidently present rather than "
            "guessing."
        )
        result = self.call_tool(system, header_text, ACCOUNT_INFO_TOOL, max_tokens=512)
        if result.get("confidence") == "low":
            return None
        return {k: v for k, v in result.items() if k != "confidence" and v is not None}

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

    def test_connection(self, message: str = "Hi") -> str:  # pragma: no cover - overridden
        """Send a small free-text message (no forced tool-use) and return
        the model's reply verbatim. Used by the UI's "Test connection"
        button to confirm a provider/model/API-key combination actually
        works before running a full analysis."""
        raise NotImplementedError


@dataclass
class ClaudeToolClient(LLMClient):
    model: str = "claude-sonnet-5"

    def _missing_key_message(self) -> str:
        return "No Anthropic API key configured. Set it in the app's settings dialog."

    def _init_client(self) -> None:
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
        logger.info(
            "LLM call: provider=anthropic tool=%s model=%s in_tokens=%d out_tokens=%d",
            tool["name"], self.model, response.usage.input_tokens, response.usage.output_tokens,
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError(f"Model did not return a {tool['name']!r} tool_use block")

    def test_connection(self, message: str = "Hi") -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=64,
            messages=[{"role": "user", "content": message}],
        )
        logger.info("Test connection: provider=anthropic model=%s ok", self.model)
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text:
            raise RuntimeError("Model returned no text content")
        return text.strip()


def _to_openai_function_tool(tool: dict) -> dict:
    """OpenRouter speaks the OpenAI chat-completions function-calling
    format, not Anthropic's `input_schema` shape — convert once here
    rather than maintaining a second copy of every tool schema."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


@dataclass
class OpenRouterToolClient(LLMClient):
    """Routes tool calls through OpenRouter's OpenAI-compatible
    `/chat/completions` endpoint. `model` is an OpenRouter model slug,
    e.g. `anthropic/claude-sonnet-5` or `openai/gpt-4o` — OpenRouter
    hosts many providers behind one API key."""

    model: str = "anthropic/claude-sonnet-5"

    def _missing_key_message(self) -> str:
        return "No OpenRouter API key configured. Set it in the app's settings dialog."

    def _init_client(self) -> None:
        import httpx
        self._client = httpx.Client(
            base_url=OPENROUTER_BASE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://github.com/anthropics",
                "X-Title": "Bank Statement Analyzer",
            },
            timeout=60.0,
        )

    def call_tool(
        self,
        system: str,
        user_content: str,
        tool: dict,
        max_tokens: int = 1024,
    ) -> dict:
        function_tool = _to_openai_function_tool(tool)
        response = self._client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                "tools": [function_tool],
                "tool_choice": {"type": "function", "function": {"name": tool["name"]}},
            },
        )
        response.raise_for_status()
        data = response.json()

        usage = data.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        self.cost_meter.add_usage(input_tokens, output_tokens)
        logger.info(
            "LLM call: provider=openrouter tool=%s model=%s in_tokens=%d out_tokens=%d",
            tool["name"], self.model, input_tokens, output_tokens,
        )

        choices = data.get("choices") or []
        if choices:
            tool_calls = (choices[0].get("message") or {}).get("tool_calls") or []
            for call in tool_calls:
                function = call.get("function") or {}
                if function.get("name") == tool["name"]:
                    return json.loads(function["arguments"])
        raise RuntimeError(f"Model did not return a {tool['name']!r} tool call")

    def test_connection(self, message: str = "Hi") -> str:
        response = self._client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": message}],
            },
        )
        response.raise_for_status()
        data = response.json()
        logger.info("Test connection: provider=openrouter model=%s ok", self.model)
        choices = data.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content")
            if content:
                return content.strip()
        raise RuntimeError("Model returned no text content")


def create_llm_client(provider: str, api_key: str | None, cost_meter: CostMeter, model: str | None = None) -> LLMClient | None:
    """Factory used by the Orchestrator: returns `None` (deterministic-only
    mode) when no key is configured, otherwise the client for `provider`."""
    if not api_key:
        return None
    if provider == "openrouter":
        kwargs = {"api_key": api_key, "cost_meter": cost_meter}
        if model:
            kwargs["model"] = model
        return OpenRouterToolClient(**kwargs)
    if provider == "anthropic":
        kwargs = {"api_key": api_key, "cost_meter": cost_meter}
        if model:
            kwargs["model"] = model
        return ClaudeToolClient(**kwargs)
    raise ValueError(f"Unknown LLM provider: {provider!r}")
