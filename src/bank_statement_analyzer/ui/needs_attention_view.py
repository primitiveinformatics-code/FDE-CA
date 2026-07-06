""""Needs attention" flag table (FR-2, FR-3, FR-4, FR-9)."""
from __future__ import annotations

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from bank_statement_analyzer.run_context import NeedsAttentionItem

HEADERS = ["Issue", "File", "Ref", "Suggestion"]


class NeedsAttentionView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def set_items(self, items: list[NeedsAttentionItem]) -> None:
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = [item.issue, item.file, item.ref or "", item.suggestion]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
