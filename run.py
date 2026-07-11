#!/usr/bin/env python3
"""Entry point for the Multi-AI Control Center desktop app."""
import sys

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.ui.style import APP_QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Multi-AI Control Center")
    app.setOrganizationName("MultiAI")
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
