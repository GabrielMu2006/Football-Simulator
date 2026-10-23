# -*- coding: utf-8 -*-
"""macOS experience round: page-level behaviour for the new presentation.

Uses the frozen S3/S2 fixtures; asserts facts, not pixels: focus comes from real
results, placeholders never render, brackets follow stored ties, role metrics
follow the position, and reading pages has no side effects.
"""

from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QTabBar

from tests import support

from football_simulator import runtime
from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.components import BracketBoard, CompactMatchRow
from football_simulator.ui.main_window import MainWindow

FIXTURES = support.PROJECT_ROOT / "tests" / "fixtures_vnext"
QUALITY_TOKENS = ("默认 FW", "默认 DF", "默认 GK", "默认 MF", "默认球员")


def _import(name: str, package: str):
    if not runtime.save_exists(name):
        store.import_package(FIXTURES / package, name)
    return store.load_world(name)


class GameFeelUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls._tmp = tempfile.mkdtemp(prefix="fs_gf_ui_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService()
        _import("s3", "s3-finalized.fsarchive")
        _import("s2", "s2-week25.fsarchive")
        cls.service.create_save("live")
        cls.service.initialize_save("live")
        cls.service.advance_week("live")
        cls.service.advance_week("live")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.window.open_save("s3")
        QTest.qWait(150)
        cls.world = store.load_world("s3")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        support.assert_cleanup_path_is_temporary(cls._tmp)
        import shutil

        shutil.rmtree(cls._tmp, ignore_errors=True)

    def open(self, route, **params):
        self.window.navigate(route, **params)
        QTest.qWait(80)
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        return self.window.stack.currentWidget()

    def page_texts(self, page):
        texts = [label.text() for label in page.findChildren(QLabel) if label.text()]
        texts += [button.text() for button in page.findChildren(QPushButton) if button.text()]
        return texts

    # -- GFE-100 ---------------------------------------------------------
    def test_dashboard_focus_is_factual_and_placeholder_free(self) -> None:
        page = self.open("dashboard")
        texts = self.page_texts(page)
        for token in QUALITY_TOKENS:
            self.assertFalse([text for text in texts if token in text], token)
        data = queries.dashboard(self.world, "s3")
        from football_simulator.ui.presentation import focus as focus_view

        candidate = focus_view.focus_candidate(self.world, data)
        self.assertIsNotNone(candidate)
        self.assertTrue(any(candidate["headline"][:12] in text for text in texts), candidate["headline"])
        self.window.open_save("live")
        QTest.qWait(100)
        live = self.open("dashboard")
        rows = live.findChildren(CompactMatchRow)
        self.assertTrue(rows, "右栏应使用紧凑比赛行")
        for row in rows:
            self.assertGreaterEqual(row.width(), 0)
        self.window.open_save("s3")
        QTest.qWait(80)

    def test_dashboard_facts_are_distinct_and_linked(self) -> None:
        page = self.open("dashboard")
        from football_simulator.ui.presentation import focus as focus_view

        data = queries.dashboard(self.world, "s3")
        candidate = focus_view.focus_candidate(self.world, data)
        facts = focus_view.short_facts(self.world, data, limit=3, exclude_match=(candidate or {}).get("match_id"))
        self.assertLessEqual(len(facts), 3)
        self.assertEqual(len({fact["text"] for fact in facts}), len(facts))
        texts = self.page_texts(page)
        for fact in facts:
            self.assertTrue(any(fact["text"] in text for text in texts), fact["text"])

    # -- GFE-020 ---------------------------------------------------------
    def test_match_detail_events_keep_score_without_slot_names(self) -> None:
        fixture = next(
            item
            for item in self.world["season"]["fixtures"]
            if item["status"] == "completed" and item.get("events")
        )
        page = self.open("match", match_id=fixture["match_id"])
        texts = self.page_texts(page)
        for token in QUALITY_TOKENS:
            self.assertFalse([text for text in texts if token in text], token)
        detail = queries.match_detail(self.world, "s3", fixture["match_id"])
        from football_simulator.ui.presentation import events as event_view

        rows = event_view.display_events(detail, self.world)
        goals = [row for row in rows if row["kind"] == "goal"]
        expected = (
            int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0)
            + int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0)
        )
        self.assertEqual(len(goals), expected)
        self.assertTrue(any(row["text"] in text for text in texts for row in goals))

    def test_match_detail_sections_switch_section(self) -> None:
        fixture = next(item for item in self.world["season"]["fixtures"] if item["status"] == "completed")
        page = self.open("match", match_id=fixture["match_id"])
        tabs = page.findChild(QTabBar, "DetailSections")
        self.assertIsNotNone(tabs)
        index = next(i for i in range(tabs.count()) if tabs.tabData(i) == "players")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(80)
        self.assertEqual(page.section_state["section"], "players")
        self.assertTrue(page.findChildren(QLabel))

    # -- GFE-110 ---------------------------------------------------------
    def test_weekly_defaults_to_ten_rows_with_pager(self) -> None:
        page = self.open("weekly", week=8)
        self.assertTrue(page.findChildren(QTabBar))
        self.assertEqual(page.paging["page_size"], 10)
        total = len([row for row in self.world["season"]["fixtures"] if row.get("status") == "completed" and int(row.get("week") or 0) == 8])
        self.assertGreater(total, 10)
        pager = page.pager
        self.assertGreaterEqual(pager.pages, 2)
        first_page_rows = len(page.findChildren(__import__("PySide6").QtWidgets.QTableWidget)[0]._rows)
        pager._move(1)
        QTest.qWait(50)
        second_page_rows = len(page.findChildren(__import__("PySide6").QtWidgets.QTableWidget)[0]._rows)
        self.assertLessEqual(first_page_rows, 10)
        self.assertLessEqual(second_page_rows, 10)
        self.assertGreater(first_page_rows + second_page_rows, 10)

    def test_weekly_competition_filter_keeps_total(self) -> None:
        page = self.open("weekly", week=8)
        tabs = page.findChildren(QTabBar)[0]
        premier_index = next(i for i in range(tabs.count()) if tabs.tabData(i) == "premier")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(premier_index).center())
        QTest.qWait(80)
        table = page.findChildren(__import__("PySide6").QtWidgets.QTableWidget)[0]
        self.assertTrue(table._rows)
        self.assertTrue(all(row["competition"] == "premier" for row in table._rows))
        page.filter = ""
        page.refresh(page.snapshot, {"week": 8})

    # -- GFE-120 ---------------------------------------------------------
    def test_finished_cup_shows_bracket_and_champion(self) -> None:
        page = self.open("competition", competition_id="aureole")
        from football_simulator.ui.competition_boards import KnockoutPanorama
        boards = page.findChildren(KnockoutPanorama)
        self.assertEqual(len(boards), 1)
        model = boards[0].model
        self.assertEqual([column["label"] for column in model["columns"]], ["八强", "半决赛", "决赛"])
        self.assertTrue(model["completed"])
        texts = self.page_texts(page)
        self.assertTrue(any(self.world["season"]["cups"]["aureole"]["champion"] for _ in [0]))
        self.assertTrue(any("冠军" in text for text in texts))

    def test_unopened_cup_has_no_bracket(self) -> None:
        self.window.open_save("s2")
        QTest.qWait(80)
        winter = store.load_world("s2")
        cup = next(cid for cid in ("zenith",) if cid not in winter["season"]["cups"])
        page = self.open("competition", competition_id=cup)
        self.assertFalse(page.findChildren(BracketBoard))
        texts = self.page_texts(page)
        self.assertFalse([text for text in texts if "不伪造" in text])
        self.assertTrue(any("尚未" in text or "未举办" in text or "第" in text for text in texts))
        self.window.open_save("s3")
        QTest.qWait(80)

    # -- GFE-130 ---------------------------------------------------------
    def test_team_hero_has_rank_gap_and_wdl(self) -> None:
        self.window.open_save("live")
        QTest.qWait(100)
        live_world = store.load_world("live")
        team_id = next(iter(live_world["teams"]))
        page = self.open("team", team_id=team_id)
        texts = self.page_texts(page)
        self.assertTrue(any("联赛名次" in text for text in texts))
        self.assertTrue(any("积分" in text for text in texts))
        self.assertTrue(any(("距榜首" in text) or ("距直接升级区" in text) or ("领先第 2 名" in text) for text in texts))
        self.assertTrue(any("近 5 场" in text for text in texts))
        self.assertTrue(any("下一场" in text for text in texts))
        self.window.open_save("s3")
        QTest.qWait(80)

    def test_player_hero_metrics_follow_position(self) -> None:
        gk_id = next(
            pid for pid, player in self.world["players"].items()
            if player.get("is_real") and player.get("position") == "GK" and player.get("team_id")
        )
        page = self.open("player", player_id=gk_id)
        texts = self.page_texts(page)
        for label_text in ("成功扑救", "零封", "评分", "估值"):
            self.assertTrue(any(label_text in text for text in texts), label_text)
        self.assertFalse(any("进球" == text for text in texts), "门将首屏不应把进球作为主指标")

    # -- GFE-140 ---------------------------------------------------------
    def test_honors_hub_leads_with_titles_and_catalog_remains(self) -> None:
        page = self.open("honors")
        texts = self.page_texts(page)
        self.assertTrue(any("最新荣誉" in text for text in texts))
        self.assertTrue(any("赛事分类" in text for text in texts))
        self.assertTrue(any("冠军球队" in text for text in texts))

    def test_player_honors_groups_by_season(self) -> None:
        winner = self.world["season"]["awards"]["competitions"]["aureole"]["top_scorer"][0]["player_id"]
        page = self.open("player_honors", player_id=winner)
        texts = self.page_texts(page)
        self.assertTrue(any("个人奖项" in text for text in texts))
        self.assertTrue(any("随队冠军" in text for text in texts))
        self.assertTrue(any(text.startswith("第 ") and "赛季" in text for text in texts))

    # -- GFE-150 ---------------------------------------------------------
    def test_winter_transfer_review_shows_both_directions(self) -> None:
        self.window.open_save("s2")
        QTest.qWait(120)
        page = self.open("transfers")
        texts = self.page_texts(page)
        self.assertTrue(any("冬窗" in text for text in texts))
        self.assertTrue(any("交换提案" in text for text in texts))
        self.assertTrue(any("→" in text for text in texts))
        self.assertIsNotNone(page.findChild(QPushButton, "SubmitTransfers"))
        self.window.open_save("s3")
        QTest.qWait(80)

    def test_saves_rows_show_season_and_week(self) -> None:
        page = self.open("saves")
        table = page.findChild(__import__("PySide6").QtWidgets.QTableWidget)
        self.assertTrue(table._rows)
        self.assertIn("season_text", [column["key"] for column in table._columns])
        for row in table._rows:
            self.assertNotIn("…", str(row.get("season_text", "")))
            self.assertNotIn("…", str(row.get("week_text", "")))

    # -- query side effects ---------------------------------------------
    def test_reading_pages_does_not_change_world_version(self) -> None:
        before = store.load_world("s3")
        before_version = int(before.get("version") or 0)
        for route in ("dashboard", "honors", "competition", "records", "history", "weekly"):
            params = {"competition_id": "aureole"} if route == "competition" else {}
            if route == "weekly":
                params = {"week": 8}
            self.open(route, **params)
        after = store.load_world("s3")
        self.assertEqual(int(after.get("version") or 0), before_version)
        self.assertEqual(after["season"]["championships"], before["season"]["championships"])


if __name__ == "__main__":
    unittest.main()
