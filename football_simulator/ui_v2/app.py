from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from football_simulator.ui_v2.main_window import MainWindow
from football_simulator.ui_v2.services import SimulatorUIService
from football_simulator.ui_v2.theme import APP_STYLE


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Football Simulator UI v2")
    app.setStyleSheet(APP_STYLE)
    window = MainWindow(SimulatorUIService())
    window.show()
    sys.exit(app.exec())
