"""Main application window (PySide6).

Flow: add PDFs (prompting per-file passwords) -> set client name -> pick
an LLM provider and its API key (stored via OS keyring) -> optional
review toggle (FR-11) -> Run -> Needs-attention review -> Export
workbook + CSV.

The setup/monitoring sections (Statements, Run Settings, Log Output,
Token Usage & Cost) live in a `SidePanel` (`ui.side_panel`) instead of
being stacked on top of each other: only one is visible at a time, and
the panel auto-selects the relevant one as the user progresses through
the flow (see the `side_panel.select_page` calls below). It can also be
driven manually, or collapsed entirely, from its own toggle button.
"""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bank_statement_analyzer import secrets_store
from bank_statement_analyzer.agents.orchestrator import FileInput, Orchestrator
from bank_statement_analyzer.config import DEFAULT_LLM_PROVIDER
from bank_statement_analyzer.cost_meter import CostMeter
from bank_statement_analyzer.llm.client import create_llm_client
from bank_statement_analyzer.parsing.pdf_parser import is_password_protected
from bank_statement_analyzer.run_context import RunResult
from bank_statement_analyzer.ui.cost_sidebar import CostSidebar
from bank_statement_analyzer.ui.needs_attention_view import NeedsAttentionView
from bank_statement_analyzer.ui.review_grid import ReviewGrid
from bank_statement_analyzer.ui.side_panel import SidePanel

logger = logging.getLogger(__name__)

PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "openrouter": "OpenRouter",
}


class ApiKeyDialog(QDialog):
    def __init__(self, provider: str, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.setWindowTitle(f"{PROVIDER_LABELS.get(provider, provider)} API key")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("API key is stored in your OS credential vault, never in plaintext."))
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        existing = secrets_store.load_api_key(provider)
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
        secrets_store.delete_api_key(self.provider)
        self.key_input.clear()


class ConnectionTestWorker(QThread):
    """Sends a throwaway "Hi" to the selected provider/model to confirm
    the API key and model name actually work, off the UI thread. Uses a
    scratch `CostMeter` rather than the orchestrator's shared one - this
    is a diagnostic ping, not part of a run's tracked cost."""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, provider: str, api_key: str, model: str):
        super().__init__()
        self.provider = provider
        self.api_key = api_key
        self.model = model

    def run(self) -> None:
        try:
            client = create_llm_client(self.provider, self.api_key, CostMeter(), self.model)
            if client is None:
                raise RuntimeError("No API key configured.")
            reply = client.test_connection("Hi")
            self.succeeded.emit(reply)
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI rather than crashing
            self.failed.emit(str(e))


class _ThresholdBridge(QObject):
    triggered = Signal(float)


class AnalysisWorker(QThread):
    progress = Signal(str, int)
    finished_ok = Signal(object)
    failed = Signal(str)
    cost_updated = Signal(dict)

    def __init__(
        self, orchestrator: Orchestrator, files: list[FileInput], client_label: str, api_key: str | None,
        provider: str = DEFAULT_LLM_PROVIDER, model: str | None = None, keep_metadata_local: bool = False,
    ):
        super().__init__()
        self.orchestrator = orchestrator
        self.files = files
        self.client_label = client_label
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.keep_metadata_local = keep_metadata_local

    def run(self) -> None:
        def on_progress(msg: str, pct: int) -> None:
            self.progress.emit(msg, pct)
            self.cost_updated.emit(self.orchestrator.cost_meter.summary())

        try:
            result = self.orchestrator.run(
                self.files, self.client_label, self.api_key, progress_callback=on_progress,
                keep_metadata_local=self.keep_metadata_local, provider=self.provider, model=self.model,
            )
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI rather than crashing
            logger.exception("Analysis run failed")
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bank Statement Analyzer")
        self.resize(1150, 750)

        self.orchestrator = Orchestrator()
        self._threshold_bridge = _ThresholdBridge()
        self.file_passwords: dict[str, str | None] = {}
        self.file_banks: dict[str, str | None] = {}
        self.last_run: RunResult | None = None
        self.worker: AnalysisWorker | None = None
        # Lightweight, non-secret UI preferences (which provider/model was
        # last selected) - not the API keys themselves, those stay in the
        # OS keyring via secrets_store.
        self._settings = QSettings("BankStatementAnalyzer", "Preferences")

        self._build_ui()

        self._threshold_bridge.triggered.connect(self.cost_sidebar.show_warning)
        self.orchestrator.cost_meter.on_threshold_exceeded(lambda cost: self._threshold_bridge.triggered.emit(cost))

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.splitter = splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        self.side_panel = SidePanel()
        self.side_panel.collapsed_changed.connect(self._on_panel_collapsed_changed)
        self.side_panel.add_page("statements", "Statements", self._build_statements_page())
        self.side_panel.add_page("settings", "Run Settings", self._build_settings_page())
        self.side_panel.add_page("log", "Log Output", self._build_log_page())
        self.cost_sidebar = CostSidebar(self.orchestrator.config.cost.warn_threshold_usd)
        self.side_panel.add_page("cost", "Token Usage && Cost", self.cost_sidebar)

        main_content = QWidget()
        main_layout = QVBoxLayout(main_content)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        main_layout.addWidget(self.summary_label)

        self.tabs = QTabWidget()
        self.needs_attention_view = NeedsAttentionView()
        self.review_grid = ReviewGrid()
        self.tabs.addTab(self.needs_attention_view, "Needs attention")
        main_layout.addWidget(self.tabs, stretch=1)

        self.export_btn = QPushButton("Export workbook...")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        main_layout.addWidget(self.export_btn)

        splitter.addWidget(self.side_panel)
        splitter.addWidget(main_content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def _on_panel_collapsed_changed(self, collapsed: bool, rail_width: int) -> None:
        """Drive the splitter directly rather than relying on the panel's
        own size constraints - a QSplitter doesn't shrink a pane on its
        own just because that pane's content became invisible, so left
        unhandled the rail ends up floating in leftover splitter space
        instead of docking to the left edge."""
        if collapsed:
            self._expanded_splitter_sizes = self.splitter.sizes()
            total = sum(self._expanded_splitter_sizes) or (rail_width + 800)
            self.splitter.setSizes([rail_width, max(total - rail_width, 0)])
        else:
            sizes = getattr(self, "_expanded_splitter_sizes", None)
            if sizes:
                self.splitter.setSizes(sizes)

    def _build_statements_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.file_list = QListWidget()
        layout.addWidget(self.file_list)
        file_btn_row = QHBoxLayout()
        add_btn = QPushButton("Add PDF files...")
        add_btn.clicked.connect(self._add_files)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected)
        file_btn_row.addWidget(add_btn)
        file_btn_row.addWidget(remove_btn)
        layout.addLayout(file_btn_row)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        client_row = QHBoxLayout()
        client_row.addWidget(QLabel("Client name:"))
        self.client_name_input = QLineEdit()
        client_row.addWidget(self.client_name_input)
        layout.addLayout(client_row)

        provider_group = QGroupBox("LLM provider")
        provider_layout = QVBoxLayout(provider_group)

        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Provider:"))
        self.provider_combo = QComboBox()
        for key, label in PROVIDER_LABELS.items():
            self.provider_combo.addItem(label, userData=key)
        saved_provider = self._settings.value("llm_provider", DEFAULT_LLM_PROVIDER)
        idx = self.provider_combo.findData(saved_provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self.provider_combo, stretch=1)
        provider_layout.addLayout(provider_row)

        self.model_row = QWidget()
        model_row = QHBoxLayout(self.model_row)
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.addWidget(QLabel("Model:"))
        self.model_input = QLineEdit()
        self.model_input.editingFinished.connect(self._on_model_edited)
        model_row.addWidget(self.model_input, stretch=1)
        provider_layout.addWidget(self.model_row)
        self._load_model_for_provider(self._current_provider())

        api_row = QHBoxLayout()
        self.api_key_status = QLabel("")
        api_btn = QPushButton("Set API key...")
        api_btn.clicked.connect(self._open_api_key_dialog)
        self.test_connection_btn = QPushButton("Test connection")
        self.test_connection_btn.clicked.connect(self._test_connection)
        api_row.addWidget(self.api_key_status, stretch=1)
        api_row.addWidget(api_btn)
        api_row.addWidget(self.test_connection_btn)
        provider_layout.addLayout(api_row)

        layout.addWidget(provider_group)

        self.review_toggle = QCheckBox("Review before export (FR-11)")
        self.review_toggle.setChecked(False)
        layout.addWidget(self.review_toggle)

        self.keep_metadata_local_toggle = QCheckBox("Keep names && account numbers local (skip LLM for these fields)")
        self.keep_metadata_local_toggle.setChecked(False)
        self.keep_metadata_local_toggle.setToolTip(
            "Off (default): if a statement's account holder name / account number / IFSC / period "
            "can't be confidently read by rule-based extraction, send that statement's header text "
            "to the selected LLM provider to fill it in.\n"
            "On: never send that header text to the API — unresolved fields are left blank and the "
            "file is flagged for manual review instead."
        )
        layout.addWidget(self.keep_metadata_local_toggle)

        self.run_btn = QPushButton("Run analysis")
        self.run_btn.clicked.connect(self._run_analysis)
        layout.addWidget(self.run_btn)
        layout.addStretch(1)

        self._update_api_key_status()
        return page

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)
        return page

    def _current_provider(self) -> str:
        return self.provider_combo.currentData() or DEFAULT_LLM_PROVIDER

    def _default_model_for_provider(self, provider: str) -> str:
        return self.orchestrator.config.model_name if provider == "anthropic" else self.orchestrator.config.openrouter_model_name

    def _model_settings_key(self, provider: str) -> str:
        return f"{provider}_model"

    def _load_model_for_provider(self, provider: str) -> None:
        """Swap the model field's placeholder/value for the newly selected
        provider - each provider remembers its own last-used model
        (Claude model name for Anthropic, OpenRouter model slug, e.g.
        `openai/gpt-4o`, for OpenRouter) so switching providers doesn't
        clobber the other's setting."""
        self.model_input.setPlaceholderText(self._default_model_for_provider(provider))
        saved_model = self._settings.value(self._model_settings_key(provider), "")
        self.model_input.setText(saved_model or "")

    def _on_model_edited(self) -> None:
        provider = self._current_provider()
        self._settings.setValue(self._model_settings_key(provider), self.model_input.text().strip())

    def _update_api_key_status(self) -> None:
        """Local-only "is a key already stored" indicator — lets the user
        tell at a glance whether they need to set one, without opening
        the key dialog or making any network call."""
        provider = self._current_provider()
        status = "configured" if secrets_store.has_api_key(provider) else "not set"
        self.api_key_status.setText(f"{PROVIDER_LABELS.get(provider, provider)} API key: {status}")

    def _on_provider_changed(self, _index: int) -> None:
        provider = self._current_provider()
        self._settings.setValue("llm_provider", provider)
        self._load_model_for_provider(provider)
        self._update_api_key_status()

    # ------------------------------------------------------------- actions

    def _add_files(self) -> None:
        had_files_before = self.file_list.count() > 0
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

        if not had_files_before and self.file_list.count() > 0:
            # First files just added - the natural next step is run settings.
            self.side_panel.select_page("settings")

    def _remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            path = item.data(Qt.ItemDataRole.UserRole)
            self.file_passwords.pop(path, None)
            self.file_banks.pop(path, None)
            self.file_list.takeItem(self.file_list.row(item))

    def _open_api_key_dialog(self) -> None:
        provider = self._current_provider()
        dialog = ApiKeyDialog(provider, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            key = dialog.key_input.text().strip()
            if key:
                try:
                    secrets_store.save_api_key(key, provider)
                except secrets_store.KeyringUnavailableError as e:
                    QMessageBox.critical(self, "Could not store API key", str(e))
        self._update_api_key_status()

    def _test_connection(self) -> None:
        provider = self._current_provider()
        provider_label = PROVIDER_LABELS.get(provider, provider)
        api_key = secrets_store.load_api_key(provider)
        if not api_key:
            QMessageBox.warning(self, "No API key", f"No {provider_label} API key configured. Set one first.")
            return
        model = self.model_input.text().strip() or self._default_model_for_provider(provider)

        self.test_connection_btn.setEnabled(False)
        self.test_connection_btn.setText("Testing...")
        self._connection_test_worker = ConnectionTestWorker(provider, api_key, model)
        self._connection_test_worker.succeeded.connect(self._on_connection_test_succeeded)
        self._connection_test_worker.failed.connect(self._on_connection_test_failed)
        self._connection_test_worker.start()

    def _on_connection_test_succeeded(self, reply: str) -> None:
        self.test_connection_btn.setEnabled(True)
        self.test_connection_btn.setText("Test connection")
        QMessageBox.information(self, "Connection OK", f"Model replied:\n\n{reply}")

    def _on_connection_test_failed(self, message: str) -> None:
        self.test_connection_btn.setEnabled(True)
        self.test_connection_btn.setText("Test connection")
        QMessageBox.critical(self, "Connection failed", message)

    def _run_analysis(self) -> None:
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "No files", "Add at least one PDF statement first.")
            self.side_panel.select_page("statements")
            return

        provider = self._current_provider()
        provider_label = PROVIDER_LABELS.get(provider, provider)
        api_key = secrets_store.load_api_key(provider)
        if not api_key:
            QMessageBox.warning(
                self, "No API key",
                f"No {provider_label} API key configured. The run will proceed in fully-deterministic "
                "mode (no field-locator/adjudicator/classifier LLM assistance) and ambiguous "
                "cases will be flagged for manual review instead of resolved.",
            )

        model = self.model_input.text().strip() or None

        files = []
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            files.append(FileInput(path=path, bank=self.file_banks.get(path), password=self.file_passwords.get(path)))

        client_label = self.client_name_input.text().strip() or "Unnamed client"

        logger.info(
            "Starting run for client=%r with %d file(s), provider=%s", client_label, len(files), provider,
        )

        self.run_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.cost_sidebar.reset()
        self.side_panel.select_page("log")

        self.worker = AnalysisWorker(
            self.orchestrator, files, client_label, api_key,
            provider=provider, model=model,
            keep_metadata_local=self.keep_metadata_local_toggle.isChecked(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.cost_updated.connect(self.cost_sidebar.update_summary)
        self.worker.finished_ok.connect(self._on_run_finished)
        self.worker.failed.connect(self._on_run_failed)
        self.worker.start()

    def _on_progress(self, message: str, pct: int) -> None:
        self.log_view.appendPlainText(message)
        self.progress_bar.setValue(pct)

    def _on_run_failed(self, message: str) -> None:
        self.run_btn.setEnabled(True)
        QMessageBox.critical(self, "Run failed", message)

    def _on_run_finished(self, result: RunResult) -> None:
        self.run_btn.setEnabled(True)
        self.progress_bar.setValue(100)
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

        QMessageBox.information(
            self, "Analysis complete",
            f"Analysis complete for {result.client_label}.\n\n"
            f"Needs-attention flags: {len(result.needs_attention)}.\n\n"
            "Review the results, then use \"Export workbook...\" to save the workbook and CSV.",
        )

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
            logger.exception("Export failed")
            QMessageBox.critical(self, "Export failed", str(e))
            return
        logger.info("Exported workbook to %s (csv %s)", xlsx_path, csv_path)
        self.cost_sidebar.update_summary(self.orchestrator.cost_meter.summary())
        QMessageBox.information(self, "Export complete", f"Workbook saved to:\n{xlsx_path}\n\nCSV saved to:\n{csv_path}")
