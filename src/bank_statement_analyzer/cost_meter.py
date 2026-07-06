"""FR-12: live token/cost tracking with a configurable warning threshold.

One `CostMeter` instance is created per run and shared by every LLM call
the orchestrator makes. The UI polls `.summary()` for the sidebar
counter and listens for the threshold callback to raise the warning
banner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from bank_statement_analyzer.config import CostConfig, DEFAULT_CONFIG


@dataclass
class CostMeter:
    cfg: CostConfig = field(default_factory=lambda: DEFAULT_CONFIG.cost)
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0
    _threshold_callback: Callable[[float], None] | None = field(default=None, repr=False)
    _warned: bool = field(default=False, repr=False)

    def on_threshold_exceeded(self, callback: Callable[[float], None]) -> None:
        self._threshold_callback = callback

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.call_count += 1
        if not self._warned and self.estimated_cost_usd > self.cfg.warn_threshold_usd:
            self._warned = True
            if self._threshold_callback:
                self._threshold_callback(self.estimated_cost_usd)

    @property
    def estimated_cost_usd(self) -> float:
        return round(
            (self.input_tokens / 1_000_000) * self.cfg.input_price_per_mtok
            + (self.output_tokens / 1_000_000) * self.cfg.output_price_per_mtok,
            4,
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def summary(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "call_count": self.call_count,
            "estimated_cost_usd": self.estimated_cost_usd,
            "threshold_exceeded": self._warned,
        }
