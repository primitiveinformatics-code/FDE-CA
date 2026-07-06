"""Application entrypoint."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from bank_statement_analyzer.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Bank Statement Analyzer")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
