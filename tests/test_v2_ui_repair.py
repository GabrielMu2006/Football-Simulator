"""UIR P1 回归：瑞士资格、排名文案、周次、表格、侧栏、草稿与换季。"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QPushButton

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.game import world as game_world
from football_simulator.persistence_v2 import store
from football_simulator.ui.components import DataTable, _atmosphere_pixmap
from football_simulator.ui.main_window import MainWindow

from tests import support


class RuleCopyRepairTests(unittest.TestCase):
    def test_swiss_status_separates_qualification_from_seed(self) -> None:
        state = {
            "records": {"A": [3, 0], "B": [3, 1], "C": [2, 2], "D": [0, 3]},
            "qualified": ["A", "B"],
            "eliminated": ["D"],
            "knockout_seeds": {},
        }
        self.assertEqual(queries.swiss_team_status("A", state)[:2], ("已晋级 · 种子待定", "qualified"))
        self.assertEqual(queries.swiss_team_status("B", state)[:2], ("已晋级 · 种子待定", "qualified"))
        self.assertEqual(queries.swiss_team_status("C", state)[:2], ("争夺资格中", "active"))
        self.assertEqual(queries.swiss_team_status("D", state)[:2], ("已淘汰", "eliminated"))
        state["knockout_seeds"] = {"A": 1, "B": 2}
        self.assertEqual(queries.swiss_team_status("A", state)[0], "已晋级 · 种子 1")
        self.assertEqual(queries.swiss_team_status("B", state)[0], "已晋级 · 种子 2")

    def test_ranking_copy_only_calls_actual_leader_first(self) -> None:
        self.assertEqual(game_world.ranking_movement_text(1, 0), "，升至榜首。")
        self.assertEqual(game_world.ranking_movement_text(3, 0), "，与榜首同分。")
        self.assertEqual(game_world.ranking_movement_text(3, 4), "，落后榜首 4 分。")


class WeekEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uir_week_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(42))
        cls.service.create_save("week")
        cls.service.initialize_save("week")
        cls.service.advance_week("week")
        cls.world = store.load_world("week")

    @classmethod
    def tearDownClass(cls) -> None:
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_completed_week_is_two_and_events_are_week_one(self) -> None:
        self.assertEqual(self.world["season"]["week"], 2)
        weeks = {event["week"] for event in self.world["story_events"]}
        self.assertEqual(weeks, {1})

    def test_no_first_week_seed_ranking_story(self) -> None:
        self.assertFalse([event for event in self.world["story_events"] if event["type"] == "ranking"])


class UiBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uir_ui_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(7))
        cls.service.create_save("ui-repair")
        cls.service.initialize_save("ui-repair")
        cls.service.advance_week("ui-repair")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_sidebar_all_nine_entries_visible_three_sizes(self) -> None:
        for width, height in ((1280, 800), (1440, 900), (1680, 980)):
            self.window.resize(width, height)
            QTest.qWait(80)
            for nav in (self.window.nav, self.window.tools):
                self.assertEqual(nav.verticalScrollBar().maximum(), 0, f"{width}: 侧栏出现了隐藏滚动")
                for index in range(nav.count()):
                    rect = nav.visualItemRect(nav.item(index))
                    self.assertLessEqual(rect.bottom(), nav.viewport().height(), f"{width}/{index}: 导航被裁切")

    def test_table_score_click_and_enter_open_match(self) -> None:
        self.window.navigate("matches")
        self.app.processEvents()
        page = self.window._pages["matches"]
        table = page.table
        table.selectRow(0)
        rect = table.visualItemRect(table.item(0, 4))
        QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        QTest.qWait(80)
        first = self.window._current_route
        self.assertEqual(first[0], "match")
        self.window.go_back()
        QTest.qWait(80)
        table = self.window._pages["matches"].table
        table.selectRow(1)
        table.setFocus()
        QTest.keyClick(table, Qt.Key_Return)
        QTest.qWait(80)
        self.assertEqual(self.window._current_route[0], "match")
        self.assertNotEqual(self.window._current_route, first)

    def test_route_scroll_is_restored_and_new_entity_starts_at_top(self) -> None:
        self.window.navigate("matches")
        self.app.processEvents()
        page = self.window._pages["matches"]
        bar = page.table.verticalScrollBar()
        bar.setValue(min(4, bar.maximum()))
        value = bar.value()
        self.assertGreater(value, 0)
        team_id = next(iter(self.service.load_world("ui-repair")["teams"]))
        self.window.navigate("team", team_id=team_id)
        self.app.processEvents()
        detail = self.window._pages["team"]
        self.assertEqual(detail.scroll.verticalScrollBar().value(), 0)
        self.window.go_back()
        QTest.qWait(120)
        self.assertEqual(self.window._pages["matches"].table.verticalScrollBar().value(), value)

    def test_focus_atmosphere_asset_loads_with_fallback_ready(self) -> None:
        pixmap = _atmosphere_pixmap()
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertGreaterEqual(pixmap.width(), 1200)

    def test_position_and_stage_columns_are_not_clipped(self) -> None:
        self.window.resize(1280, 800)
        self.window.navigate("players")
        QTest.qWait(80)
        players = self.window._pages["players"]
        position_col = next(index for index, column in enumerate(players.table._columns) if column["key"] == "position")
        self.assertGreaterEqual(players.table.columnWidth(position_col), 58)
        self.window.navigate("matches")
        QTest.qWait(80)
        matches = self.window._pages["matches"]
        stage_col = next(index for index, column in enumerate(matches.table._columns) if column["key"] == "stage_text")
        self.assertFalse(matches.table.isColumnHidden(stage_col), "1280 下赛事阶段被隐藏")

    def test_week_copy_distinguishes_completed_and_next(self) -> None:
        self.window.navigate("dashboard")
        self.app.processEvents()
        page = self.window._pages["dashboard"]
        self.assertIn("推进第 2 周", self.window.primary.text())
        self.window.mode_box.setCurrentIndex(self.window.mode_box.findData("auto"))
        self.window.refresh_all()
        self.app.processEvents()
        self.assertIn("连续观察", self.window.primary.text())
        self.window.mode_box.setCurrentIndex(self.window.mode_box.findData("key_pause"))
        self.window.refresh_all()
        self.app.processEvents()
        self.assertIn("查看第 1 周周报", page.weekly_button.text())
        header_text = " ".join(label.text() for label in page.findChildren(__import__("PySide6").QtWidgets.QLabel) if label.text())
        self.assertIn("已完成第 1 周", header_text)


class TransferDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uir_transfer_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(42))
        cls.service.create_save("transfer")
        cls.service.initialize_save("transfer")
        cls._finish_first_season()
        cls.service.start_next_season("transfer")
        for _ in range(90):
            world = store.load_world("transfer")
            if world["season"]["pending"].get("transfer_review"):
                break
            cls._resolve_non_transfer()
            cls.service.advance_week("transfer")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.app.processEvents()
        cls.window.navigate("transfers")
        cls.app.processEvents()

    @classmethod
    def _resolve_non_transfer(cls) -> bool:
        world = store.load_world("transfer")
        pending = world["season"]["pending"]
        if pending.get("ability_review"):
            cls.service.apply_ability_review("transfer", {row["player_id"]: True for row in pending["ability_review"]})
            return True
        if pending.get("draft", {}).get("status") == "awaiting_input":
            cls.service.apply_draft("transfer")
            return True
        return False

    @classmethod
    def _finish_first_season(cls) -> None:
        for _ in range(160):
            world = store.load_world("transfer")
            if world["season"].get("season_complete"):
                break
            pending = world["season"]["pending"]
            if pending.get("transfer_review"):
                cls.service.apply_transfer_reviews("transfer", {row["trade_id"]: True for row in pending["transfer_review"]})
                continue
            if cls._resolve_non_transfer():
                continue
            cls.service.advance_week("transfer")
        for _ in range(12):
            world = store.load_world("transfer")
            pending = world["season"]["pending"]
            if pending.get("transfer_review"):
                cls.service.apply_transfer_reviews("transfer", {row["trade_id"]: True for row in pending["transfer_review"]})
                continue
            if cls._resolve_non_transfer():
                continue
            break

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_rejected_choice_survives_refresh_back_and_writes_rejected(self) -> None:
        page = self.window._pages["transfers"]
        checks = support.transfer_approval_controls(page)
        self.assertTrue(checks)
        trade_id = next(iter(page.decisions))
        support.choose_transfer(page, 0, False)
        QTest.qWait(50)
        self.assertFalse(checks[0].isChecked())
        self.assertFalse(page.decisions[trade_id])
        self.window.refresh_all()
        QTest.qWait(120)
        checks = support.transfer_approval_controls(self.window._pages["transfers"])
        self.assertFalse(checks[0].isChecked(), "F5 后勾选被默认批准覆盖")
        player_id = next(iter(self.service.load_world("transfer")["players"]))
        self.window.navigate("player", player_id=player_id)
        QTest.qWait(80)
        self.window.go_back()
        QTest.qWait(120)
        checks = support.transfer_approval_controls(self.window._pages["transfers"])
        self.assertFalse(checks[0].isChecked(), "详情往返后勾选被重建覆盖")
        transfers_page = self.window._pages["transfers"] if hasattr(self, "window") else page
        submit = transfers_page.findChild(QPushButton, "SubmitTransfers")
        if submit is None:
            submit = page.findChild(QPushButton, "SubmitTransfers")
        QTest.mouseClick(submit, Qt.LeftButton)
        QTest.qWait(150)
        world = store.load_world("transfer")
        self.assertFalse(world["season"]["pending"].get("transfer_review"))
        history = queries.transfers_page(world)["history"]
        self.assertFalse([row for row in history if row.get("trade_id") == trade_id], "拒绝的交易不应进入落盘历史")


class NextSeasonUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uir_next_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(11))
        cls.service.create_save("next")
        cls.service.initialize_save("next")
        for _ in range(160):
            world = store.load_world("next")
            if world["season"].get("season_complete"):
                break
            pending = world["season"]["pending"]
            if pending.get("transfer_review"):
                cls.service.apply_transfer_reviews("next", {row["trade_id"]: True for row in pending["transfer_review"]})
                continue
            if pending.get("ability_review"):
                cls.service.apply_ability_review("next", {row["player_id"]: True for row in pending["ability_review"]})
                continue
            if pending.get("draft", {}).get("status") == "awaiting_input":
                cls.service.apply_draft("next")
                continue
            cls.service.advance_week("next")
        for _ in range(12):
            world = store.load_world("next")
            pending = world["season"]["pending"]
            if pending.get("ability_review"):
                cls.service.apply_ability_review("next", {row["player_id"]: True for row in pending["ability_review"]})
            elif pending.get("draft", {}).get("status") == "awaiting_input":
                cls.service.apply_draft("next")
            elif pending.get("transfer_review"):
                cls.service.apply_transfer_reviews("next", {row["trade_id"]: True for row in pending["transfer_review"]})
            else:
                break
        cls.copy_name = "next-copy"
        shutil.copytree(support.sim_runtime.data_root() / "saves" / "next", support.sim_runtime.data_root() / "saves" / cls.copy_name)
        cls.window = MainWindow(cls.service)
        cls.window.resize(1280, 800)
        cls.window.show()
        cls.window.navigate("dashboard")
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def _page_button(self):
        page = self.window._pages["dashboard"]
        return next(button for button in page.findChildren(QPushButton) if button.text() == "开始下一赛季")

    def test_page_cancel_does_not_write_and_top_confirm_only_once(self) -> None:
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            QTest.mouseClick(self._page_button(), Qt.LeftButton)
            QTest.qWait(80)
        self.assertEqual(store.load_world("next")["season_number"], 1)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            QTest.mouseClick(self.window.primary, Qt.LeftButton)
            QTest.qWait(150)
        world = store.load_world("next")
        self.assertEqual((world["season_number"], world["season"]["week"]), (2, 1))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), patch.object(
            QMessageBox, "information", return_value=QMessageBox.Ok
        ):
            self.assertFalse(self.window.start_next_season())
        self.assertEqual(store.load_world("next")["season_number"], 2)

    def test_page_confirm_uses_same_action(self) -> None:
        self.window.open_save(self.copy_name)
        QTest.qWait(100)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            QTest.mouseClick(self._page_button(), Qt.LeftButton)
            QTest.qWait(150)
        world = store.load_world(self.copy_name)
        self.assertEqual((world["season_number"], world["season"]["week"]), (2, 1))


class HumanDecisionDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uir_decisions_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(13))
        cls.service.create_save("decisions")
        cls.service.initialize_save("decisions")
        for _ in range(160):
            world = store.load_world("decisions")
            if world["season"].get("season_complete"):
                break
            pending = world["season"]["pending"]
            if pending.get("transfer_review"):
                cls.service.apply_transfer_reviews("decisions", {row["trade_id"]: True for row in pending["transfer_review"]})
                continue
            if pending.get("ability_review"):
                break
            cls.service.advance_week("decisions")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.window.navigate("transfers")
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_ability_choice_survives_refresh_and_rejection_keeps_old_ability(self) -> None:
        page = self.window._pages["transfers"]
        checks = page.findChildren(QCheckBox)
        self.assertTrue(checks)
        player_id, check = next(iter(page._ability_checks.items()))
        old_ability = store.load_world("decisions")["players"][player_id]["ability"]
        QTest.mouseClick(check, Qt.LeftButton, Qt.NoModifier, QPoint(6, 11))
        QTest.qWait(50)
        self.assertFalse(check.isChecked())
        self.window.refresh_all()
        QTest.qWait(100)
        refreshed = self.window._pages["transfers"]._ability_checks[player_id]
        self.assertFalse(refreshed.isChecked(), "能力审核刷新后被默认勾选覆盖")
        self.window.navigate("player", player_id=player_id)
        QTest.qWait(80)
        self.window.go_back()
        QTest.qWait(120)
        checks = self.window._pages["transfers"]._ability_checks
        self.assertFalse(checks[player_id].isChecked(), "能力审核详情往返后丢失选择")
        submit = next(button for button in self.window._pages["transfers"].findChildren(QPushButton) if button.text() == "提交本批能力审核")
        QTest.mouseClick(submit, Qt.LeftButton)
        QTest.qWait(150)
        world = store.load_world("decisions")
        self.assertFalse(world["season"]["pending"].get("ability_review"))
        self.assertEqual(world["players"][player_id]["ability"], old_ability, "取消批准仍写入了能力")

    def test_draft_choice_survives_refresh_and_confirm_uses_same_selection(self) -> None:
        self.window.navigate("draft")
        QTest.qWait(100)
        page = self.window._pages["draft"]
        checks = page.findChildren(QCheckBox)
        self.assertTrue(checks)
        skipped = next(iter(page.selected))
        check = next(item for item in checks if item.text().startswith(skipped))
        QTest.mouseClick(check, Qt.LeftButton, Qt.NoModifier, QPoint(6, 11))
        QTest.qWait(50)
        self.assertFalse(page.selected[skipped])
        self.window.refresh_all()
        QTest.qWait(100)
        page = self.window._pages["draft"]
        check = next(item for item in page.findChildren(QCheckBox) if item.text().startswith(skipped))
        self.assertFalse(check.isChecked(), "选秀预览刷新后丢失取消选择")
        self.window.navigate("team", team_id=next(iter(self.service.load_world("decisions")["teams"])))
        QTest.qWait(80)
        self.window.go_back()
        QTest.qWait(120)
        page = self.window._pages["draft"]
        check = next(item for item in page.findChildren(QCheckBox) if item.text().startswith(skipped))
        self.assertFalse(check.isChecked(), "选秀预览详情往返后丢失取消选择")
        confirm = page.findChild(QPushButton, "SubmitDraft")
        QTest.mouseClick(confirm, Qt.LeftButton)
        QTest.qWait(150)
        world = store.load_world("decisions")
        log = queries.draft_page(world)["logs"][-1]
        selected = {row["player"] for row in log["results"]}
        self.assertNotIn(skipped, selected, "取消勾选的候选仍被分配")
