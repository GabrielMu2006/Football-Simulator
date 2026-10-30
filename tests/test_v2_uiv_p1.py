"""UIV P1 回归：奖项查询、空选秀契约、真实导航重入。"""

from __future__ import annotations

import hashlib
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QTabBar

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.game import rules
from football_simulator.game import world as game_world
from football_simulator.game import transfers
from football_simulator.persistence_v2 import store
from football_simulator.ui.components import EntityLink
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui import macos_presentation as macos
from tests import support

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures_vnext"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finish_to_draft(service: GameService, name: str) -> None:
    for _ in range(180):
        world = store.load_world(name)
        pending = world["season"]["pending"]
        if pending.get("ability_review"):
            service.apply_ability_review(name, {row["player_id"]: True for row in pending["ability_review"]})
            continue
        if pending.get("draft", {}).get("status") == "awaiting_input":
            return
        if pending.get("transfer_review"):
            service.apply_transfer_reviews(name, {row["trade_id"]: True for row in pending["transfer_review"]})
            continue
        if world["season"].get("season_complete"):
            return
        service.advance_week(name)
    raise AssertionError("未生成选秀待办")


class AwardAndDraftBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uiv_p1_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(20261020))
        cls.service.import_save(FIXTURES / "s3-finalized.fsarchive", "s3uiv")
        cls.service.import_save(FIXTURES / "s2-week25.fsarchive", "s2uiv")
        # The historical fixture contains proposals generated before the value
        # limit existed. Keep the archived matches/history, but exercise this
        # navigation/submit regression with valid current-window proposals.
        review_world = store.load_world("s2uiv")
        proposals = transfers.generate_proposals(review_world, random.Random(5), "winter", game_world._player_value)
        if len(proposals) < 2:
            raise AssertionError("导航回归需要至少两笔合法提案，以同时验证接受和拒绝")
        review_world["season"]["pending"]["transfer_review"] = proposals
        store.write_world("s2uiv", review_world)
        cls.service.create_save("abilitybase")
        cls.service.initialize_save("abilitybase")
        for _ in range(90):
            world = store.load_world("abilitybase")
            pending = world["season"]["pending"]
            if pending.get("ability_review"):
                break
            if pending.get("transfer_review"):
                cls.service.apply_transfer_reviews("abilitybase", {row["trade_id"]: True for row in pending["transfer_review"]})
                continue
            if world["season"].get("season_complete"):
                break
            cls.service.advance_week("abilitybase")
        cls.service.create_save("draftbase")
        cls.service.initialize_save("draftbase")
        finish_to_draft(cls.service, "draftbase")
        cls.base_dir = store.save_dir("draftbase")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def copy_case(self, name: str) -> Path:
        target = store.save_dir(name)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.base_dir, target)
        return target

    def test_award_query_all_eight_and_secondary(self) -> None:
        world = store.load_world("s3uiv")
        for competition in (*rules.CUP_IDS, rules.PREMIER, rules.SECOND):
            dto = queries.competition_detail(world, "s3uiv", competition)
            self.assertIsInstance(dto.get("awards"), dict)
            # NH-040: Second Division now has the same three awards; Promotion
            # Playoffs remains absent. Historical freeze is asserted below.
            self.assertTrue(dto["awards"]["top_scorer"], competition)
            self.assertTrue(dto["awards"]["assist_leader"], competition)
            self.assertTrue(dto["awards"]["mvp"], competition)
            raw = world["season"]["awards"]["competitions"][competition]
            for key in ("top_scorer", "assist_leader", "mvp"):
                self.assertEqual(
                    [item["label"] for item in dto["awards"][key]],
                    [item["label"] for item in raw[key]],
                )
                self.assertEqual(
                    [item["player_id"] for item in dto["awards"][key]],
                    [item["player_id"] for item in raw[key]],
                )

    def test_award_query_is_read_only(self) -> None:
        path = store.database_path("s3uiv")
        before = sha256(path)
        world = store.load_world("s3uiv")
        for competition in (*rules.CUP_IDS, rules.PREMIER, rules.SECOND):
            queries.competition_detail(world, "s3uiv", competition)
        self.assertEqual(before, sha256(path))

    def test_eight_award_pages_render_without_error(self) -> None:
        self.window.open_save("s3uiv")
        QTest.qWait(100)
        for competition in (*rules.CUP_IDS, rules.PREMIER, rules.SECOND):
            self.window.navigate("competition", competition_id=competition)
            QTest.qWait(80)
            self.assertTrue(self.window.error_label.isHidden(), f"{competition}: {self.window.error_label.text()}")
            page = self.window.stack.currentWidget()
            self.assertIs(page, self.window._pages["competition"])
            if macos.enabled() and competition in (*rules.CUP_IDS, rules.PREMIER, rules.SECOND):
                tabs = next(bar for bar in page.findChildren(QTabBar) if any(bar.tabText(i) == "个人奖项" for i in range(bar.count())))
                index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "个人奖项")
                page.scroll.ensureWidgetVisible(tabs)
                QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
                QTest.qWait(50)
            labels = [label.text() for label in page.findChildren(QLabel) if label.text()]
            # 获奖者可点击，姓名渲染在链接按钮上；文本断言同时覆盖两类控件。
            from PySide6.QtWidgets import QAbstractButton

            texts = labels + [button.text() for button in page.findChildren(QAbstractButton) if button.text()]
            self.assertTrue(any("个人奖项" in text for text in texts), competition)
            expected = queries.competition_detail(self.window.world(), "s3uiv", competition)["awards"]
            # The new UI does not expose a secondary-league MVP, even for
            # this legacy fixture; the legacy frozen query remains unchanged.
            keys = ("top_scorer", "assist_leader") if competition == rules.SECOND else ("top_scorer", "assist_leader", "mvp")
            for key in keys:
                for winner in expected[key]:
                    self.assertTrue(any(winner["label"] in text for text in texts), (competition, key, winner["label"]))

    def test_draft_none_all_partial_empty_invalid_and_duplicate(self) -> None:
        candidates = store.load_world("draftbase")["season"]["pending"]["draft"]["candidates"]
        names = [row["name"] for row in candidates]

        before_world = store.load_world("draftbase")
        before_players = set(before_world["players"])
        before_rosters = {team_id: [row["player_id"] for row in team["roster"]] for team_id, team in before_world["teams"].items()}
        self.copy_case("draft-auto")
        auto = self.service.apply_draft("draft-auto")
        self.assertGreater(len(auto["drafted"]), 0)
        self.assertEqual(auto["skipped"], [])
        self.assertFalse(store.load_world("draft-auto")["season"]["pending"].get("draft"))

        self.copy_case("draft-none")
        none = self.service.apply_draft("draft-none", [])
        world = store.load_world("draft-none")
        self.assertEqual(none["drafted"], [])
        self.assertEqual(len(none["skipped"]), len(candidates))
        self.assertEqual({row["name"] for row in none["skipped"]}, set(names))
        self.assertEqual(len(world["season"]["draft_log"][-1]["undrafted"]), len(candidates))
        self.assertFalse(world["season"]["pending"].get("draft"))
        self.assertEqual(set(world["players"]), before_players)
        self.assertEqual({team_id: [row["player_id"] for row in team["roster"]] for team_id, team in world["teams"].items()}, before_rosters)

        self.copy_case("draft-noslot")
        no_slot_world = store.load_world("draft-noslot")
        team_id = no_slot_world["season"]["pending"]["draft"]["draft_order"][0]
        no_slot_world["season"]["pending"]["draft"]["draft_order"] = [team_id]
        for entry in no_slot_world["teams"][team_id]["roster"]:
            no_slot_world["players"][entry["player_id"]]["is_real"] = True
        store.write_world("draft-noslot", no_slot_world)
        no_slot = self.service.apply_draft("draft-noslot")
        self.assertEqual(no_slot["drafted"], [])
        self.assertEqual(len(no_slot["no_slot"]), len(candidates))
        self.assertEqual(len(no_slot["undrafted"]), len(candidates))

        self.copy_case("draft-partial")
        partial = self.service.apply_draft("draft-partial", [names[0]])
        self.assertTrue(all(row["name"] == names[0] for row in partial["drafted"]))
        self.assertEqual({row["name"] for row in partial["skipped"]}, set(names) - {names[0]})

        before = sha256(store.database_path("draft-partial"))
        with self.assertRaises(game_world.WorldError):
            self.service.apply_draft("draft-partial", ["不存在的候选"])
        self.assertEqual(before, sha256(store.database_path("draft-partial")))
        with self.assertRaises(game_world.WorldError):
            self.service.apply_draft("draft-partial", [])

    def test_ability_f5_preserves_route_history_and_checks(self) -> None:
        self.window.open_save("abilitybase")
        self.window.navigate("transfers")
        QTest.qWait(120)
        page = self.window._pages["transfers"]
        checks = page.findChildren(__import__("PySide6").QtWidgets.QCheckBox)
        self.assertTrue(checks)
        QTest.mouseClick(checks[0], Qt.LeftButton, Qt.NoModifier, QPoint(6, 11))
        QTest.qWait(60)
        self.assertFalse(checks[0].isChecked())
        history = list(self.window._back)
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(150)
        self.assertEqual(self.window._current_route[0], "transfers")
        self.assertEqual(self.window._back, history)
        refreshed = self.window._pages["transfers"].findChildren(__import__("PySide6").QtWidgets.QCheckBox)
        self.assertFalse(refreshed[0].isChecked())

    def test_ui_all_unchecked_writes_zero_and_keeps_followup_possible(self) -> None:
        self.copy_case("draft-ui")
        expected_names = {row["name"] for row in store.load_world("draft-ui")["season"]["pending"]["draft"]["candidates"]}
        self.window.open_save("draft-ui")
        self.window.navigate("draft")
        QTest.qWait(120)
        page = self.window._pages["draft"]
        checks = page.findChildren(__import__("PySide6").QtWidgets.QCheckBox)
        self.assertGreater(len(checks), 0)
        for check in checks:
            QTest.mouseClick(check, Qt.LeftButton, Qt.NoModifier, QPoint(6, 11))
        QTest.qWait(80)
        self.assertTrue(all(not check.isChecked() for check in checks))
        self.window.refresh_all()
        QTest.qWait(120)
        page = self.window._pages["draft"]
        self.assertTrue(all(not check.isChecked() for check in page.findChildren(__import__("PySide6").QtWidgets.QCheckBox)))
        self.window.navigate("team", team_id=next(iter(store.load_world("draft-ui")["teams"])))
        QTest.qWait(80)
        self.window.go_back()
        QTest.qWait(120)
        page = self.window._pages["draft"]
        self.assertTrue(all(not check.isChecked() for check in page.findChildren(__import__("PySide6").QtWidgets.QCheckBox)))
        confirm = page.findChild(__import__("PySide6").QtWidgets.QPushButton, "SubmitDraft")
        QTest.mouseClick(confirm, Qt.LeftButton)
        QTest.qWait(180)
        world = store.load_world("draft-ui")
        self.assertFalse(world["season"]["pending"].get("draft"))
        log = world["season"]["draft_log"][-1]
        self.assertEqual(log["results"], [])
        self.assertEqual(len(log["skipped"]), len(log["undrafted"]))
        self.assertTrue(self.window.error_label.isHidden(), self.window.error_label.text())
        if macos.enabled():
            from football_simulator.ui.components import DataTable
            table = page.findChild(DataTable, "DraftUnassigned")
            self.assertIsNotNone(table)
            self.assertEqual({row["name"] for row in table._rows}, expected_names)
            self.assertEqual(len(table._rows), len(expected_names), "跳过者不能同时在未分配名单里重复显示")
            self.assertTrue(all(row["result"] == "已跳过" for row in table._rows))
            self.assertIsNone(page.findChild(DataTable, "DraftResults"))

    def test_real_navigation_player_back_and_f5_preserve_route_and_draft(self) -> None:
        self.window.open_save("s2uiv")
        self.window.navigate("transfers")
        QTest.qWait(120)
        page = self.window._pages["transfers"]
        checks = support.transfer_approval_controls(page)
        self.assertTrue(checks)
        support.choose_transfer(page, 0, False)
        QTest.qWait(60)
        self.assertFalse(checks[0].isChecked())
        link = next(link for link in page.findChildren(EntityLink) if getattr(link, "kind", "") == "player")
        QTest.mouseClick(link, Qt.LeftButton)
        QTest.qWait(160)
        self.assertEqual(self.window._current_route[0], "player")
        self.assertIsInstance(self.window.stack.currentWidget(), type(self.window._pages["player"]))
        self.assertIn("球员", self.window.breadcrumb.text())
        self.assertIn(("transfers", {}), self.window._back)
        history = list(self.window._back)
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(160)
        self.assertEqual(self.window._current_route[0], "player")
        self.assertEqual(self.window._back, history)
        QTest.mouseClick(self.window.back_button, Qt.LeftButton)
        QTest.qWait(160)
        self.assertEqual(self.window._current_route[0], "transfers")
        self.assertIs(self.window.stack.currentWidget(), self.window._pages["transfers"])
        checks = support.transfer_approval_controls(self.window._pages["transfers"])
        self.assertFalse(checks[0].isChecked())
        page = self.window._pages["transfers"]
        trade_id = next(iter(page.decisions))
        accepted = {key for key, value in page.decisions.items() if value}
        self.assertTrue(accepted)
        submit = page.findChild(__import__("PySide6").QtWidgets.QPushButton, "SubmitTransfers")
        with mock.patch("football_simulator.ui.pages.tools.QMessageBox.warning") as warning:
            QTest.mouseClick(submit, Qt.LeftButton)
        warning.assert_not_called()
        QTest.qWait(180)
        world = store.load_world("s2uiv")
        self.assertFalse(world["season"]["pending"].get("transfer_review"))
        history = queries.transfers_page(world)["history"]
        self.assertFalse(any(row.get("trade_id") == trade_id for row in history))
        self.assertTrue(accepted <= {row.get("trade_id") for row in history})

    def test_sidebar_user_navigation_still_works(self) -> None:
        self.window.navigate("dashboard")
        QTest.qWait(80)
        rect = self.window.tools.visualItemRect(self.window.tools.item(0))
        QTest.mouseClick(self.window.tools.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
        QTest.qWait(120)
        self.assertEqual(self.window._current_route[0], "transfers")
        self.assertIs(self.window.stack.currentWidget(), self.window._pages["transfers"])


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
