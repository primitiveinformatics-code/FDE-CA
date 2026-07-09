"""Application entrypoint."""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from bank_statement_analyzer.logging_config import configure_logging
from bank_statement_analyzer.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def _install_excepthook() -> None:
    def handle(exc_type, exc_value, exc_traceback):
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle


def main() -> int:
    log_path = configure_logging()
    _install_excepthook()
    logger.info(
        "Application starting (logfile: %s; set BSA_LOG_LEVEL=DEBUG for verbose per-page/per-row tracing)",
        log_path,
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Bank Statement Analyzer")
    window = MainWindow()
    window.show()
    exit_code = app.exec()

    logger.info("Application exiting (code %s)", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
