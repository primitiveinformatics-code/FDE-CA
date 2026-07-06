"""Main application window (PySide6).

Flow: add PDFs (prompting per-file passwords) -> set client name -> set
API key (once, stored via OS keyring) -> optional review toggle (FR-11)
-> Run -> Needs-attention review -> Export workbook + CSV.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bank_statement_analyzer import secrets_store
from bank_statement_analyzer.agents.orchestrator import FileInput, Orchestrator
from bank_statement_analyzer.parsing.pdf_parser import is_password_protected
from bank_statement_analyzer.run_context import RunResult
from bank_statement_analyzer.ui.cost_sidebar import CostSidebar
from bank_statement_analyzer.ui.needs_attention_view import NeedsAttentionView
from bank_statement_analyzer.ui.review_grid import ReviewGrid


class ApiKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Anthropic API key")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("API key is stored in your OS credential vault, never in plaintext."))
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        existing = secrets_store.load_api_key()
        if existing:
            self.key_input.setText(existing)
        layout.addWidget(self.key_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        clear_btn = QPushButton("Clear stored key")
        clear_btn.clicked.connect(self._clear)
        layout.addWidget(clear_btn)

    def _clear(self) -> None:
        secrets_store.delete_api_key()
        self.key_input.clear()


class _ThresholdBridge(QObject):
    triggered = Signal(float)


class AnalysisWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)
    cost_updated = Signal(dict)

    def __init__(self, orchestrator: Orchestrator, files: list[FileInput], client_label: str, api_key: str | None):
        super().__init__()
        self.orchestrator = orchestrator
        self.files = files
        self.client_label = client_label
        self.api_key = api_key

    def run(self) -> None:
        def on_progress(msg: str) -> None:
            self.progress.emit(msg)
            self.cost_updated.emit(self.orchestrator.cost_meter.summary())

        try:
            result = self.orchestrator.run(self.files, self.client_label, self.api_key, progress_callback=on_progress)
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI rather than crashing
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bank Statement Analyzer")
        self.resize(1100, 750)

        self.orchestrator = Orchestrator()
        self._threshold_bridge = _ThresholdBridge()
        self.file_passwords: dict[str, str | None] = {}
        self.file_banks: dict[str, str | None] = {}
        self.last_run: RunResult | None = None
        self.worker: AnalysisWorker | None = None

        self._build_ui()

        self._threshold_bridge.triggered.connect(self.cost_sidebar.show_warning)
        self.orchestrator.cost_meter.on_threshold_exceeded(lambda cost: self._threshold_bridge.triggered.emit(cost))

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        file_group = QGroupBox("Statements")
        file_layout = QVBoxLayout(file_group)
        self.file_list = QListWidget()
        file_layout.addWidget(self.file_list)
        file_btn_row = QHBoxLayout()
        add_btn = QPushButton("Add PDF files...")
        add_btn.clicked.connect(self._add_files)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected)
        file_btn_row.addWidget(add_btn)
        file_btn_row.addWidget(remove_btn)
        file_layout.addLayout(file_btn_row)
        left_layout.addWidget(file_group)

        settings_group = QGroupBox("Run settings")
        settings_layout = QVBoxLayout(settings_group)
        client_row = QHBoxLayout()
        client_row.addWidget(QLabel("Client name:"))
        self.client_name_input = QLineEdit()
        client_row.addWidget(self.client_name_input)
        settings_layout.addLayout(client_row)

        api_row = QHBoxLayout()
        self.api_key_status = QLabel(self._api_key_status_text())
        api_btn = QPushButton("Set API key...")
        api_btn.clicked.connect(self._open_api_key_dialog)
        api_row.addWidget(self.api_key_status)
        api_row.addWidget(api_btn)
        settings_layout.addLayout(api_row)

        self.review_toggle = QCheckBox("Review before export (FR-11)")
        self.review_toggle.setChecked(False)
        settings_layout.addWidget(self.review_toggle)

        self.run_btn = QPushButton("Run analysis")
        self.run_btn.clicked.connect(self._run_analysis)
        settings_layout.addWidget(self.run_btn)

        left_layout.addWidget(settings_group)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        left_layout.addWidget(self.log_view, stretch=1)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        left_layout.addWidget(self.summary_label)

        self.tabs = QTabWidget()
        self.needs_attention_view = NeedsAttentionView()
        self.review_grid = ReviewGrid()
        self.tabs.addTab(self.needs_attention_view, "Needs attention")
        left_layout.addWidget(self.tabs, stretch=1)

        self.export_btn = QPushButton("Export workbook...")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        left_layout.addWidget(self.export_btn)

        self.cost_sidebar = CostSidebar(self.orchestrator.config.cost.warn_threshold_usd)

        splitter.addWidget(left)
        splitter.addWidget(self.cost_sidebar)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

    def _api_key_status_text(self) -> str:
        return "API key: configured" if secrets_store.load_api_key() else "API key: not set"

    # ------------------------------------------------------------- actions

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add bank statement PDFs", "", "PDF Files (*.pdf)")
        for path in paths:
            if path in self.file_banks:
                continue
            password = None
            if is_password_protected(path):
                text, ok = QInputDialog.getText(
                    self, "Password required", f"Enter password for {os.path.basename(path)}:",
                    QLineEdit.EchoMode.Password,
                )
                password = text if ok else None
            self.file_passwords[path] = password
            self.file_banks[path] = None
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.file_list.addItem(item)

    def _remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            path = item.data(Qt.ItemDataRole.UserRole)
            self.file_passwords.pop(path, None)
            self.file_banks.pop(path, None)
            self.file_list.takeItem(self.file_list.row(item))

    def _open_api_key_dialog(self) -> None:
        dialog = ApiKeyDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            key = dialog.key_input.text().strip()
            if key:
                try:
                    secrets_store.save_api_key(key)
                except secrets_store.KeyringUnavailableError as e:
                    QMessageBox.critical(self, "Could not store API key", str(e))
        self.api_key_status.setText(self._api_key_status_text())

    def _run_analysis(self) -> None:
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "No files", "Add at least one PDF statement first.")
            return
        api_key = secrets_store.load_api_key()
        if not api_key:
            QMessageBox.warning(
                self, "No API key",
                "No Anthropic API key configured. The run will proceed in fully-deterministic "
                "mode (no field-locator/adjudicator/classifier LLM assistance) and ambiguous "
                "cases will be flagged for manual review instead of resolved.",
            )

        files = []
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            files.append(FileInput(path=path, bank=self.file_banks.get(path), password=self.file_passwords.get(path)))

        client_label = self.client_name_input.text().strip() or "Unnamed client"

        self.run_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.log_view.clear()
        self.cost_sidebar.reset()

        self.worker = AnalysisWorker(self.orchestrator, files, client_label, api_key)
        self.worker.progress.connect(self._on_progress)
        self.worker.cost_updated.connect(self.cost_sidebar.update_summary)
        self.worker.finished_ok.connect(self._on_run_finished)
        self.worker.failed.connect(self._on_run_failed)
        self.worker.start()

    def _on_progress(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _on_run_failed(self, message: str) -> None:
        self.run_btn.setEnabled(True)
        QMessageBox.critical(self, "Run failed", message)

    def _on_run_finished(self, result: RunResult) -> None:
        self.run_btn.setEnabled(True)
        self.last_run = result
        totals = result.run_totals
        self.summary_label.setText(
            f"<b>{result.client_label}</b> — "
            f"credits {totals.total_credits:,.2f}, debits {totals.total_debits:,.2f}, "
            f"net {totals.net:,.2f}. "
            f"Self-transfers confirmed: {len(result.self_transfers.confirmed)}. "
            f"Refunds confirmed: {len(result.refunds.confirmed)}. "
            f"Needs-attention flags: {len(result.needs_attention)}."
        )
        self.needs_attention_view.set_items(result.needs_attention)

        if self.review_toggle.isChecked():
            self.review_grid.load_transactions(result.transactions)
            if self.tabs.indexOf(self.review_grid) == -1:
                self.tabs.addTab(self.review_grid, "Review before export")
            self.tabs.setCurrentWidget(self.review_grid)

        self.export_btn.setEnabled(True)

    def _export(self) -> None:
        if self.last_run is None:
            return
        xlsx_path, _ = QFileDialog.getSaveFileName(self, "Save workbook", "analysis.xlsx", "Excel Workbook (*.xlsx)")
        if not xlsx_path:
            return
        csv_path = os.path.splitext(xlsx_path)[0] + ".csv"

        review_edits = self.review_grid.collect_edits() if self.review_toggle.isChecked() else None
        try:
            self.orchestrator.export(self.last_run, xlsx_path, csv_path, review_edits=review_edits)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(e))
            return
        self.cost_sidebar.update_summary(self.orchestrator.cost_meter.summary())
        QMessageBox.information(self, "Export complete", f"Workbook saved to:\n{xlsx_path}\n\nCSV saved to:\n{csv_path}")
