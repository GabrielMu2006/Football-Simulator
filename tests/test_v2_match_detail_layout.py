"""UIR2-003 回归：比赛详情元信息与长链接的三档布局。"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.components import EntityLink
from football_simulator.ui.main_window import MainWindow
from tests import support

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures_vnext"


class MatchDetailLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uir2_layout_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(20261020))
        cls.service.import_save(FIXTURES / "s3-finalized.fsarchive", "s3layout")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.window.open_save("s3layout")
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def _page(self):
        self.window.navigate("match", match_id=self.match_id)
        QTest.qWait(120)
        return self.window._pages["match"]

    def _assert_fit(self, label: str) -> None:
        page = self.window._pages["match"]
        self.assertLessEqual(page.scroll.widget().width(), page.scroll.viewport().width() + 1, label)
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0, label)
        self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())

    def test_two_samples_three_sizes_f5_and_resize(self) -> None:
        for self.match_id in ("m02-00920", "m02-00761"):
            self.window.open_save("s3layout")
            page = self._page()
            for width, height in ((1280, 800), (1440, 900), (1680, 980), (1440, 900), (1280, 800)):
                self.window.setFixedSize(width, height)
                QTest.qWait(220)
                self._assert_fit(f"{self.match_id} {width}")
                self.window.refresh_all()
                QTest.qWait(120)
                self.assertEqual(self.window._current_route[0], "match")
                self._assert_fit(f"{self.match_id} F5 {width}")

    def test_venue_stage_status_and_links_are_readable_and_addressable(self) -> None:
        self.match_id = "m02-00920"
        page = self._page()
        self.window.setFixedSize(1280, 800)
        QTest.qWait(200)
        labels = [label.text() for label in page.findChildren(QLabel) if label.text()]
        joined = " ".join(labels)
        detail = queries.match_detail(store.load_world("s3layout"), "s3layout", self.match_id)
        home = detail["fixture"]["home_name"]
        away = detail["fixture"]["away_name"]
        self.assertIn(home, joined)
        self.assertIn(away, joined)
        self.assertIn("钢铁熔炉球场", joined)
        self.assertIn("The Iron Forge", joined)
        self.assertIn("八强", joined)
        self.assertIn("已完赛", joined)
        links = [link for link in page.findChildren(EntityLink) if getattr(link, "kind", "") == "match"]
        self.assertGreaterEqual(len(links), 2)
        by_id = {link.entity_id: link for link in links}
        self.assertIn("m02-00919", by_id)
        self.assertIn("m02-00920", by_id)
        QTest.mouseClick(by_id["m02-00919"], Qt.LeftButton)
        QTest.qWait(120)
        self.assertEqual(self.window._current_route[1].get("match_id"), "m02-00919")
        self.assertEqual(self.window.stack.currentWidget(), self.window._pages["match"])
        self.window.go_back()
        QTest.qWait(120)
        self.assertEqual(self.window._current_route[1].get("match_id"), "m02-00920")
        by_id = {link.entity_id: link for link in self.window._pages["match"].findChildren(EntityLink) if getattr(link, "kind", "") == "match"}
        QTest.mouseClick(by_id["m02-00920"], Qt.LeftButton)
        QTest.qWait(100)
        self.assertEqual(self.window._current_route[1].get("match_id"), "m02-00920")


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
