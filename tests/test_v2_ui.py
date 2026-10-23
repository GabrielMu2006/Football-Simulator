"""真实 Qt 外壳与 15 类路由：尺寸、滚动面、筛选、搜索、异步停止。"""

from __future__ import annotations

import random
import shutil
import tempfile
import time
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractScrollArea, QApplication

from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.trophies import TrophyImage
from football_simulator.ui.main_window import MainWindow, ROUTE_CLASSES

from tests import support


SIZES = ((1280, 800), (1440, 900), (1680, 980))


def _active_scroll_surfaces(widget) -> list:
    surfaces = []
    for area in widget.findChildren(QAbstractScrollArea):
        if not area.isVisible() or area.height() <= 0:
            continue
        if area.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff:
            continue
        if area.verticalScrollBar().maximum() > 0:
            surfaces.append(area)
    return surfaces


def _finish(service: GameService, save_name: str) -> None:
    for _ in range(260):
        world = store.load_world(save_name)
        pending = world["season"]["pending"]
        if pending.get("ability_review"):
            service.apply_ability_review(save_name, {i["player_id"]: True for i in pending["ability_review"]})
            continue
        if pending.get("draft", {}).get("status") == "awaiting_input":
            service.apply_draft(save_name)
            continue
        if pending.get("transfer_review"):
            service.apply_transfer_reviews(save_name, {i["trade_id"]: True for i in pending["transfer_review"]})
            continue
        if world["season"].get("season_complete"):
            return
        service.advance_week(save_name)


class ShellRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_v2_ui_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(42))
        cls.service.create_save("ui")
        cls.service.initialize_save("ui")
        _finish(cls.service, "ui")
        cls.service.start_next_season("ui")
        for _ in range(40):
            world = store.load_world("ui")
            pending = world["season"]["pending"]
            if pending.get("transfer_review"):
                cls.service.apply_transfer_reviews("ui", {i["trade_id"]: True for i in pending["transfer_review"]})
                continue
            if world["season"]["week"] >= 30:
                break
            cls.service.advance_week("ui")
        cls.world = store.load_world("ui")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def _routes(self):
        world = self.world
        team_id = next(iter(world["teams"]))
        player_id = next(iter(world["players"]))
        completed = next(f for f in world["season"]["fixtures"] if f["status"] == "completed")
        routes = [
            ("dashboard", {}),
            ("weekly", {}),
            ("matches", {}),
            ("match", {"match_id": completed["match_id"]}),
            ("competitions", {}),
            ("season", {}),
            ("teams", {}),
            ("team", {"team_id": team_id}),
            ("players", {}),
            ("player", {"player_id": player_id}),
            ("history", {}),
            ("transfers", {}),
            ("draft", {}),
            ("saves", {}),
        ]
        routes += [("competition", {"competition_id": cup_id}) for cup_id in ("aureole", "confluence", "starpath", "ember", "ascendant", "zenith")]
        return routes

    def test_all_route_types_render_without_error(self) -> None:
        for name, params in self._routes():
            with self.subTest(route=name, params=params):
                self.window.navigate(name, **params)
                self.app.processEvents()
                self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())
                page = self.window.stack.currentWidget()
                self.assertIsInstance(page, ROUTE_CLASSES[name])
                self.assertIs(self.window._pages[name], page)

    def test_at_most_one_active_vertical_scroll_surface(self) -> None:
        for size in SIZES:
            self.window.resize(*size)
            self.app.processEvents()
            for name, params in self._routes():
                with self.subTest(size=size, route=name, params=params):
                    self.window.navigate(name, **params)
                    self.app.processEvents()
                    surfaces = _active_scroll_surfaces(self.window.stack.currentWidget())
                    self.assertLessEqual(len(surfaces), 1, f"{name}: {[type(s).__name__ for s in surfaces]}")

    def test_primary_action_and_sidebar_fit_1280(self) -> None:
        self.window.resize(1280, 800)
        self.window.navigate("dashboard")
        self.app.processEvents()
        self.assertTrue(self.window.primary.isVisible())
        self.assertTrue(self.window.primary.isEnabled())
        self.assertGreaterEqual(self.window.primary.width(), 120)
        from PySide6.QtWidgets import QFrame

        sidebar = self.window.findChild(QFrame, "Sidebar")
        self.assertGreaterEqual(sidebar.width(), 192)
        self.assertLessEqual(sidebar.width(), 230)
        self.assertTrue(self.window.season_button.isVisible())

    def test_search_back_and_forward_keep_stable_ids(self) -> None:
        team_id = next(iter(self.world["teams"]))
        name = self.world["teams"][team_id].get("name_zh") or self.world["teams"][team_id]["club_en"]
        self.window.search.setText(name)
        self.window._search_activated()
        self.app.processEvents()
        self.assertEqual(self.window._current_route[0], "team")
        self.assertEqual(self.window._current_route[1]["team_id"], team_id)
        self.window.go_back()
        self.app.processEvents()
        self.assertNotEqual(self.window._current_route[0], "team")
        self.window.go_forward()
        self.app.processEvents()
        self.assertEqual(self.window._current_route, ("team", {"team_id": team_id}))

    def test_refresh_keeps_route_and_filters(self) -> None:
        self.window.navigate("matches")
        page = self.window._pages["matches"]
        index = page.competition.findData("aureole")
        page.competition.setCurrentIndex(index)
        self.app.processEvents()
        self.assertEqual(page.filters["competition"], "aureole")
        self.window.refresh_all()
        self.app.processEvents()
        self.assertEqual(self.window._current_route[0], "matches")
        self.assertEqual(self.window._pages["matches"].filters["competition"], "aureole")

    def test_real_cup_trophy_is_accessible_and_stable_by_id(self) -> None:
        self.window.navigate("competition", competition_id="ember")
        self.app.processEvents()
        page = self.window.stack.currentWidget()
        emblems = page.findChildren(TrophyImage)
        self.assertEqual(len(emblems), 1)
        self.assertEqual(emblems[0].competition_id, "ember")
        self.assertTrue(emblems[0].accessibleName())
        self.assertTrue(emblems[0].property("assetReady"))

    def test_late_worker_result_with_wrong_token_is_ignored(self) -> None:
        before = store.load_world("ui")["season"]["week"]
        status_before = self.window.status_line.text()
        self.window._on_week_done("some-other-save", 999, {"week": 99})
        self.assertEqual(store.load_world("ui")["season"]["week"], before)
        self.assertEqual(self.window.status_line.text(), status_before)

    def test_watch_toggle_and_mode_persist_per_save(self) -> None:
        team_id = next(iter(self.world["teams"]))
        self.window.toggle_watch(team_id)
        self.assertIn(team_id, store.load_world("ui")["settings"]["watchlist"])
        self.window.toggle_watch(team_id)
        self.assertNotIn(team_id, store.load_world("ui")["settings"]["watchlist"])
        self.service.set_mode("ui", "auto")
        self.assertEqual(store.load_world("ui")["settings"]["mode"], "auto")
        self.service.set_mode("ui", "key_pause")


class WorkerStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_v2_worker_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(9))
        cls.service.create_save("worker")
        cls.service.initialize_save("worker")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1280, 800)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def _wait_worker(self, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if self.window._worker is None:
                return
            time.sleep(0.01)
        self.fail("异步推进未在超时内结束")

    def test_one_week_transaction_and_stop_after_week(self) -> None:
        self.window.navigate("dashboard")
        self.app.processEvents()
        start = store.load_world("worker")["season"]["week"]
        self.window._start_worker()
        self._wait_worker()
        self.assertEqual(store.load_world("worker")["season"]["week"], start + 1)
        self.window._start_worker()
        self.app.processEvents()
        self.window._request_stop()
        self._wait_worker()
        self.assertEqual(store.load_world("worker")["season"]["week"], start + 2)
        self.assertTrue(self.window.stop_button.isHidden())
        self.assertTrue(self.window.primary.isEnabled())


if __name__ == "__main__":
    unittest.main()


def tearDownModule() -> None:
    """模块结束时销毁本模块的 Qt 页面/窗口，防止跨模块累计到 exit139。"""
    support.cleanup_qt_widgets()
