"""存档 UI 流程：新建→初始化→删除到回收站→恢复。"""
from __future__ import annotations

import random
import unittest

from PySide6.QtWidgets import QApplication

from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.main_window import MainWindow

from tests import support
from tests.support import FreezeTestCase


class SaveUiFlowTests(FreezeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = support.ensure_qapp()
        self.service = GameService(rng_factory=lambda: random.Random(4))
        self.window = MainWindow(self.service)
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        super().tearDown()

    def test_create_initialize_delete_and_restore(self) -> None:
        self.assertFalse(self.window.has_save)
        self.window.create_save("flow")
        self.assertFalse(self.window.has_save)
        self.assertEqual(store.load_world("flow") if store.database_path("flow").exists() else None, None)
        self.window.initialize_save("flow")
        self.app.processEvents()
        self.assertTrue(self.window.has_save)
        self.assertEqual(store.load_world("flow")["season"]["week"], 1)
        self.assertIsNotNone(self.window.stack.currentWidget(), "初始化后内容区不能为空")
        self.assertEqual(self.window._current_route[0], "dashboard")
        self.assertIsNotNone(self.window.stack.currentWidget())
        self.window.navigate("teams")
        self.app.processEvents()
        self.assertIn("teams", self.window._pages)
        self.window.delete_save("flow")
        self.app.processEvents()
        self.assertFalse(store.database_path("flow").exists())
        self.assertFalse(self.window.has_save)
        trashed = self.service.list_trash()
        self.assertTrue(trashed)
        self.window.restore_trash(trashed[0]["path"], "flow")
        self.assertTrue(store.database_path("flow").exists())
        self.window.open_save("flow")
        self.app.processEvents()
        self.assertTrue(self.window.has_save)
        self.assertIsNotNone(self.window.stack.currentWidget())
        self.assertEqual(self.window._current_route[0], "dashboard")


if __name__ == "__main__":
    unittest.main()


def tearDownModule() -> None:
    """模块结束时销毁本模块的 Qt 页面/窗口，防止跨模块累计到 exit139。"""
    support.cleanup_qt_widgets()
