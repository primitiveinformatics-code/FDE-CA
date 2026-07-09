"""FR-12: live token/cost display + threshold warning banner.

Lives as a page inside `ui.side_panel.SidePanel` (see `MainWindow`), so
unlike the other panel pages it owns no collapsible chrome of its own —
the side panel already provides show/hide.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class CostSidebar(QWidget):
    def __init__(self, warn_threshold_usd: float, parent=None):
        super().__init__(parent)
        self.warn_threshold_usd = warn_threshold_usd

        layout = QVBoxLayout(self)

        self.tokens_label = QLabel("Tokens: 0 in / 0 out")
        self.cost_label = QLabel("Estimated cost: $0.00")
        self.calls_label = QLabel("API calls: 0")
        for w in (self.tokens_label, self.cost_label, self.calls_label):
            layout.addWidget(w)

        self.warning_banner = QLabel("")
        self.warning_banner.setWordWrap(True)
        self.warning_banner.setStyleSheet(
            "background-color: #FDECEA; color: #B71C1C; padding: 6px; border-radius: 4px;"
        )
        self.warning_banner.setVisible(False)

        layout.addWidget(self.warning_banner)
        layout.addStretch(1)

    def update_summary(self, summary: dict) -> None:
        self.tokens_label.setText(f"Tokens: {summary.get('input_tokens', 0):,} in / {summary.get('output_tokens', 0):,} out")
        self.cost_label.setText(f"Estimated cost: ${summary.get('estimated_cost_usd', 0.0):.4f}")
        self.calls_label.setText(f"API calls: {summary.get('call_count', 0)}")
        if summary.get("threshold_exceeded"):
            self.show_warning(summary.get("estimated_cost_usd", 0.0))

    def show_warning(self, cost_usd: float) -> None:
        self.warning_banner.setText(
            f"Cost warning: this run's estimated cost (${cost_usd:.2f}) has exceeded "
            f"the configured threshold (${self.warn_threshold_usd:.2f})."
        )
        self.warning_banner.setVisible(True)

    def reset(self) -> None:
        self.tokens_label.setText("Tokens: 0 in / 0 out")
        self.cost_label.setText("Estimated cost: $0.00")
        self.calls_label.setText("API calls: 0")
        self.warning_banner.setVisible(False)
