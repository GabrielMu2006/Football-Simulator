"""Qt 单进程生命周期回归：模块页面必须被显式清理。

历史 exit139 的根因不是 QApplication 重建本身，而是旧页面测试把页面留在模块
全局，跨模块累计 2 万+ widget；后续 Qt 分配/枚举时全局控件列表失效。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from tests import support


ROOT = Path(__file__).resolve().parents[1]
# Current Qt/page modules that own a module-level widget cleanup.  The retired
# ui_v2 page modules are archived and must not be listed here; test_current_ui_only
# separately proves discovery no longer imports them.
QT_PAGE_MODULES = (
    "tests/test_macos_detail_navigation.py",
    "tests/test_macos_live_competitions.py",
    "tests/test_macos_observer_views.py",
    "tests/test_v2_ui.py",
    "tests/test_v2_save_flow.py",
    "tests/test_v2_uiv_p1.py",
    "tests/test_v2_uiv_p2.py",
    "tests/test_v2_honor_labels.py",
    "tests/test_v2_match_detail_layout.py",
    "tests/test_v2_tie_presentation.py",
    "tests/test_vnext_presentation.py",
    "tests/test_trophy_integration.py",
)


class QtLifecycleRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = support.ensure_qapp()

    def test_cleanup_removes_module_widgets_and_keeps_application(self) -> None:
        module_windows = []
        for index in range(6):
            window = QMainWindow()
            window.setCentralWidget(QLabel(f"module-{index}"))
            window.show()
            module_windows.append(window)
        self.app.processEvents()
        self.assertGreaterEqual(len(QApplication.topLevelWidgets()), 6)
        support.cleanup_qt_widgets()
        self.app.processEvents()
        self.assertEqual(QApplication.instance(), self.app)
        self.assertEqual(QApplication.topLevelWidgets(), [])
        # 清理后 Qt 仍可正常分配新控件。
        fresh = QLabel("fresh")
        fresh.show()
        self.app.processEvents()
        self.assertTrue(fresh.isVisible())
        fresh.close()
        fresh.deleteLater()
        self.app.processEvents()

    def test_every_qt_test_module_has_teardown_module(self) -> None:
        missing = []
        for relative in QT_PAGE_MODULES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            if "def tearDownModule()" not in text or "cleanup_qt_widgets()" not in text:
                missing.append(relative)
        self.assertFalse(missing, f"缺少 Qt 模块级清理: {missing}")


def tearDownModule() -> None:
    support.cleanup_qt_widgets()

if __name__ == "__main__":
    unittest.main()
