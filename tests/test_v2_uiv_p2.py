"""UIV P2 回归：长内容、比分语义、新闻关联、氛围图。"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui import components
from football_simulator.ui.components import FocusScoreCard
from football_simulator.ui.main_window import MainWindow
from tests import support

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures_vnext"


class P2UiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uiv_p2_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(20261020))
        cls.service.import_save(FIXTURES / "s2-week25.fsarchive", "s2p2")
        cls.service.import_save(FIXTURES / "s3-finalized.fsarchive", "s3p2")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def open_fixture(self, name: str) -> None:
        self.window.open_save(name)
        QTest.qWait(100)
        self.window.navigate("dashboard")
        QTest.qWait(140)


class LongContentLayoutTests(P2UiBase):
    def test_three_sizes_refresh_and_resize_keep_content_inside_viewport(self) -> None:
        self.open_fixture("s2p2")
        for width, height in ((1280, 800), (1440, 900), (1680, 980), (1440, 900), (1280, 800)):
            self.window.setFixedSize(width, height)
            QTest.qWait(220)
            page = self.window._pages["dashboard"]
            body = page.scroll.widget()
            self.assertLessEqual(body.width(), page.scroll.viewport().width() + 1, f"{width}: body overflows")
            self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0, f"{width}: horizontal scroll")
            right = page.weekly_button.mapTo(self.window, page.weekly_button.rect().topRight()).x()
            self.assertLessEqual(right, self.window.width(), f"{width}: weekly button clipped")
            focus = page.findChild(FocusScoreCard)
            self.assertIsNotNone(focus)
            bottom = focus.mapTo(page, focus.rect().bottomLeft()).y()
            self.assertLessEqual(bottom, page.scroll.widget().height() + 1, f"{width}: focus bottom clipped")
            self.window.refresh_all()
            QTest.qWait(120)
            self.assertEqual(self.window._current_route[0], "dashboard")
            self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)

    def test_long_detail_page_has_no_horizontal_overflow(self) -> None:
        self.open_fixture("s2p2")
        world = store.load_world("s2p2")
        fixture = next(item for item in world["season"]["fixtures"] if item.get("status") == "completed")
        self.window.navigate("match", match_id=fixture["match_id"])
        QTest.qWait(140)
        page = self.window._pages["match"]
        self.assertLessEqual(page.scroll.widget().width(), page.scroll.viewport().width() + 1)
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)


class ScoreSemanticsTests(P2UiBase):
    def _focus(self, save: str, match_id: str, size=(760, 420)) -> FocusScoreCard:
        world = store.load_world(save)
        detail = queries.match_detail(world, save, match_id)
        self.assertIsNotNone(detail)
        focus = FocusScoreCard()
        focus.resize(*size)
        focus.set_match(world, detail["fixture"], events=detail["events"][:1], meaning="结果", tie=detail["tie"])
        focus.show()
        QTest.qWait(60)
        return focus

    def test_archived_two_leg_this_match_vs_aggregate(self) -> None:
        first = self._focus("s3p2", "m02-00919")
        second = self._focus("s3p2", "m02-00920")
        try:
            world = store.load_world("s3p2")
            first_dto = queries.match_detail(world, "s3p2", "m02-00919")["fixture"]
            second_dto = queries.match_detail(world, "s3p2", "m02-00920")["fixture"]
            self.assertEqual(first.score_label.text(), f"{first_dto['total_home']}–{first_dto['total_away']}")
            self.assertIn("第 1 回合", first.status.text())
            self.assertIn("总比分", first.aggregate_label.text())
            self.assertEqual(second.score_label.text(), f"{second_dto['total_home']}–{second_dto['total_away']}")
            self.assertIn("第 2 回合", second.status.text())
            self.assertNotIn("总比分", second.score_label.text())
            self.assertIn(first_dto["home_name"] if False else "Ironvale FC", second.aggregate_label.text())
            self.assertIn("Glorious Vision", second.aggregate_label.text())
        finally:
            first.close()
            second.close()

    def test_detail_page_focus_and_aggregate_are_consistent(self) -> None:
        self.open_fixture("s3p2")
        self.window.navigate("match", match_id="m02-00920")
        QTest.qWait(140)
        page = self.window._pages["match"]
        self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())
        focus = page.findChild(FocusScoreCard)
        detail = queries.match_detail(store.load_world("s3p2"), "s3p2", "m02-00920")
        self.assertEqual(focus.score_label.text(), f"{detail['fixture']['total_home']}–{detail['fixture']['total_away']}")
        self.assertIn("第 2 回合", focus.status.text())
        self.assertIn("Ironvale FC", focus.aggregate_label.text())
        labels = [label.text() for label in page.findChildren(__import__("PySide6").QtWidgets.QLabel) if label.text()]
        self.assertTrue(any("总比分" in text for text in labels))


class NewsAssociationTests(P2UiBase):
    def test_seed202_week11_focus_does_not_take_third_party_ranking(self) -> None:
        seed_service = GameService(rng_factory=lambda: random.Random(202))
        seed_service.create_save("seed202")
        seed_service.initialize_save("seed202")
        for _ in range(30):
            world = store.load_world("seed202")
            if world["season"]["week"] >= 12:
                break
            pending = world["season"]["pending"]
            if pending.get("ability_review"):
                seed_service.apply_ability_review("seed202", {row["player_id"]: True for row in pending["ability_review"]})
            elif pending.get("transfer_review"):
                seed_service.apply_transfer_reviews("seed202", {row["trade_id"]: True for row in pending["transfer_review"]})
            else:
                seed_service.advance_week("seed202")
        seed_window = MainWindow(seed_service)
        seed_window.resize(1440, 900)
        seed_window.show()
        seed_window.open_save("seed202")
        QTest.qWait(100)
        seed_window.navigate("dashboard")
        QTest.qWait(140)
        page = seed_window._pages["dashboard"]
        focus = page.findChild(FocusScoreCard)
        # 本轮起焦点由真实赛况选出（可能不是排序第一场），但仍必须是本周真实比赛，
        # 且不能改成第三方排名新闻。
        world = store.load_world("seed202")
        week = int(world["season"]["week"]) - 1
        week_ids = {
            fixture["match_id"]
            for fixture in world["season"]["fixtures"]
            if int(fixture.get("week") or 0) == week and fixture.get("status") == "completed"
        }
        self.assertIn(focus.match_id, week_ids)
        self.assertTrue(focus.meaning_label.text().strip())
        first = focus.match_id
        seed_window.refresh_all()
        QTest.qWait(120)
        self.assertEqual(page.findChild(FocusScoreCard).match_id, first, "刷新不能换掉焦点")
        seed_window.close()


class AtmosphereTests(P2UiBase):
    def test_focus_renders_image_and_dark_fallback_differently(self) -> None:
        self.open_fixture("s2p2")
        page = self.window._pages["dashboard"]
        focus = page.findChild(FocusScoreCard)
        self.assertIsNotNone(focus)
        normal = focus.grab().toImage()
        normal_colors = _sample_colors(normal)
        original = components._atmosphere_pixmap
        components._atmosphere_pixmap = lambda: None
        try:
            focus.update()
            QTest.qWait(80)
            fallback = focus.grab().toImage()
            fallback_colors = _sample_colors(fallback)
        finally:
            components._atmosphere_pixmap = original
        self.assertGreater(normal_colors, fallback_colors, "氛围图没有产生可见背景差异")
        self.assertNotEqual(_sample_signature(normal), _sample_signature(fallback))
        self.assertTrue(focus.detail.isEnabled())
        self.assertTrue(focus.score_label.text())


def _sample_signature(image) -> tuple:
    values = []
    for x in range(10, min(220, image.width()), 7):
        for y in range(10, min(120, image.height()), 7):
            values.append(image.pixelColor(x, y).rgb())
    return tuple(values)


def _sample_colors(image) -> int:
    colors = set()
    for x in range(10, min(220, image.width()), 5):
        for y in range(10, min(120, image.height()), 5):
            colors.add(image.pixelColor(x, y).rgb())
    return len(colors)


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
