"""Exchange choices/projections and frozen per-season team/player views."""
import copy
import hashlib
import random

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QComboBox, QPushButton

from tests import support
from tests.test_macos_identity_p2 import _Base, FIXTURES
from football_simulator.app import profile_history, queries, records
from football_simulator.game import rules, transfers, world as game_world
from football_simulator.persistence_v2 import store
from football_simulator.ui.components import DataTable, EntityLink
from football_simulator.ui.transfer_cards import ExchangeProposalCard, ExchangeTeamCard, real_position_counts


class SeasonAndExchangeViewsTests(_Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        store.import_package(FIXTURES / "s2-week25.fsarchive", "review")
        world = store.load_world("review")
        for seed in range(30):
            proposals = transfers.generate_proposals(world, random.Random(seed), "winter", game_world._player_value)
            if any(3 in (len(row["players_a"]), len(row["players_b"])) for row in proposals) and len(proposals) >= 2:
                break
        else:
            raise AssertionError("真实球队夹具未产生三人交换")
        world["season"]["pending"]["transfer_review"] = proposals
        store.write_world("review", world)

    def setUp(self):
        self.window.open_save("final")

    def test_every_team_season_has_all_competitions_with_frozen_finishes(self):
        tid = "team-glorious-vision"
        rows = profile_history.team_season_results(self.world, "final", tid)
        self.assertEqual(len(rows), 27)
        self.assertEqual(len({(r["season"], r["competition"]) for r in rows}), 27)
        by_key = {(r["season"], r["competition"]): r for r in rows}
        for archive in self.world["archives"]:
            season = archive["season_number"]
            for comp, key in ((rules.PREMIER, "premier_table"), (rules.SECOND, "second_table")):
                ids = [row["team_id"] for row in archive[key]]
                expected = f"第 {ids.index(tid) + 1} 名" if tid in ids else "未参赛"
                self.assertEqual(by_key[(season, comp)]["result"], expected)
            for comp in rules.CUP_IDS:
                rank = archive["cup_placements"].get(comp, {}).get(tid)
                if rank:
                    self.assertEqual(by_key[(season, comp)]["result"], profile_history.FINISH_LABELS[rules.honor_key_for_rank(comp, rank)])
        self.assertTrue(all(by_key[(1, comp)]["result"] == "未举办" for comp in rules.CUP_IDS))

    def test_promoted_team_keeps_historical_division_and_playoff_result(self):
        archive = next(a for a in self.world["archives"] if a["season_number"] == 2)
        tid = archive["transition"]["playoff_winner"]
        rows = {(r["season"], r["competition"]): r for r in profile_history.team_season_results(self.world, "final", tid)}
        order = [row["team_id"] for row in archive["second_table"]]
        self.assertEqual(rows[(2, rules.SECOND)]["result"], f"第 {order.index(tid) + 1} 名")
        self.assertEqual(rows[(2, rules.PLAYOFF)]["result"], "附加赛冠军 · 升级")

    def test_player_selected_season_matches_sql_and_sums_without_double_counting(self):
        pid = "real::darwin-nunez"
        conn = store.open_readonly("final")
        try:
            for season in (1, 2, 3):
                dto = profile_history.player_season_stats(self.world, "final", pid, season)
                expected = conn.execute("SELECT COUNT(*), COALESCE(SUM(p.goals),0), COALESCE(SUM(p.assists),0) FROM player_match_stats p JOIN matches m ON m.match_id=p.match_id WHERE p.player_id=? AND p.season_number=? AND p.appeared=1 AND m.status='completed'", (pid, season)).fetchone()
                self.assertEqual(tuple(dto["totals"][key] for key in ("appearances", "goals", "assists")), tuple(expected))
                self.assertEqual({r["competition"] for r in dto["rows"]}, set(rules.ALL_COMPETITION_IDS))
            total = records.career_totals("final", pid)
            selected = [profile_history.player_season_stats(self.world, "final", pid, s)["totals"] for s in (1, 2, 3)]
            for key in ("appearances", "goals", "assists"):
                self.assertEqual(sum(row[key] for row in selected), total[key])
        finally:
            conn.close()

    def test_queries_do_not_mutate_database_or_archive(self):
        path = store.database_path("final")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        saved = copy.deepcopy(self.world)
        profile_history.team_season_results(self.world, "final", "team-glorious-vision")
        profile_history.player_season_stats(self.world, "final", "real::darwin-nunez", 1)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(saved, self.world)

    def test_player_season_selection_survives_f5_and_navigation(self):
        page = self.open("player", player_id="real::darwin-nunez")
        page = self.click_tab(page, "赛季数据")
        box = page.findChild(QComboBox, "PlayerStatsSeason")
        box.setCurrentIndex(box.findData(1))
        QTest.qWait(80)
        expected = profile_history.player_season_stats(self.world, "final", "real::darwin-nunez", 1)["team_rows"][:10]
        self.assertEqual(page.findChild(DataTable, "PlayerSeasonStats")._rows, expected)
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(100)
        self.assertEqual(page.findChild(QComboBox, "PlayerStatsSeason").currentData(), 1)
        self.open("team", team_id="team-glorious-vision")
        self.window.go_back()
        QTest.qWait(100)
        self.assertEqual(page.findChild(QComboBox, "PlayerStatsSeason").currentData(), 1)
        self.assertEqual(page.findChild(DataTable, "PlayerSeasonStats")._rows, expected)

    def test_team_history_is_a_readable_paged_table(self):
        page = self.open("team", team_id="team-glorious-vision")
        page = self.click_tab(page, "历季战绩")
        matrix = page.findChild(DataTable, "TeamSeasonMatrix")
        self.assertEqual(matrix.rowCount(), 3)
        self.assertEqual(len(matrix._columns), 11)
        page = self.click_tab(page, "单赛季详情")
        self.assertEqual(page.findChild(DataTable, "TeamSeasonResults").rowCount(), 9)
        box = page.findChild(QComboBox, "TeamHistorySeason")
        box.setCurrentIndex(box.findData(0))
        QTest.qWait(80)
        table = page.findChild(DataTable, "TeamSeasonResults")
        self.assertEqual(table.rowCount(), 10)
        next_page = page.findChild(QPushButton, "PagerNext")
        self.assertTrue(next_page.isEnabled())
        QTest.mouseClick(next_page, Qt.LeftButton)
        QTest.qWait(60)
        self.assertTrue(any(row["season"] == 2 for row in table._rows))
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(80)
        self.assertEqual(page.findChild(QComboBox, "TeamHistorySeason").currentData(), 0)

    def test_transfer_has_two_club_panels_and_large_explicit_choices(self):
        self.window.open_save("review")
        page = self.open("transfers")
        cards = page.findChildren(ExchangeProposalCard)
        self.assertGreaterEqual(len(cards), 2)
        for proposal in cards:
            self.assertEqual(len(proposal.findChildren(ExchangeTeamCard)), 2)
            self.assertEqual(proposal.findChildren(QCheckBox), [])
            for button in (proposal.accept, proposal.reject):
                self.assertGreaterEqual(button.height(), 44)
                self.assertGreaterEqual(button.width(), 160)
                self.assertTrue(button.isCheckable())
            for panel in proposal.teams:
                self.assertEqual(panel.current_counts, real_position_counts(self.window.world(), panel.team_id))
                self.assertEqual(set(panel.count_labels), {"GK", "DF", "MF", "FW"})

    def test_rejection_recalculates_batch_counts_and_survives_f5(self):
        self.window.open_save("review")
        page = self.open("transfers")
        first = page.findChildren(ExchangeProposalCard)[0]
        tid = first.trade_id
        # Also verify native keyboard activation, without a tiny indicator.
        first.reject.setFocus()
        QTest.keyClick(first.reject, Qt.Key_Space)
        QTest.qWait(60)
        self.assertFalse(page.decisions[tid])
        world = self.window.world()
        expected = {team: real_position_counts(world, team) for team in world["teams"]}
        for item in queries.transfers_page(world)["pending"]:
            if not page.decisions[item["trade_id"]]:
                continue
            for a, b in (("a", "b"), ("b", "a")):
                for player in item["players_" + a]:
                    expected[item["team_" + a]][player["position"]] -= 1
                    expected[item["team_" + b]][player["position"]] += 1
        for panel in page.findChildren(ExchangeTeamCard):
            self.assertEqual(panel.projected_counts, dict(expected[panel.team_id]))
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(100)
        first = next(card for card in page.findChildren(ExchangeProposalCard) if card.trade_id == tid)
        self.assertTrue(first.reject.isChecked())
        self.assertFalse(first.accept.isChecked())

    def test_native_control_toggle_updates_draft_without_a_pointer_click(self):
        self.window.open_save("review")
        page = self.open("transfers")
        first = page.findChildren(ExchangeProposalCard)[0]
        first.reject.setChecked(True)
        self.assertFalse(page.decisions[first.trade_id])
        self.assertIn("拒绝", first.status.text())
        first.accept.setChecked(True)
        self.assertTrue(page.decisions[first.trade_id])
        self.assertIn("接受", first.status.text())

    def test_new_views_fit_all_three_desktop_widths(self):
        for route, params, tab in (("team", {"team_id": "team-glorious-vision"}, "历季战绩"), ("player", {"player_id": "real::darwin-nunez"}, "赛季数据"), ("transfers", {}, None)):
            self.window.open_save("review" if route == "transfers" else "final")
            page = self.open(route, **params)
            if tab:
                page = self.click_tab(page, tab)
            for width in (1280, 1440, 1680):
                self.window.setFixedSize(width, 900)
                QTest.qWait(80)
                self.assertLessEqual(page.scroll.widget().width(), page.scroll.viewport().width() + 1, (route, width))
                self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
                for proposal in page.findChildren(ExchangeProposalCard):
                    for panel in proposal.teams:
                        self.assertTrue(proposal.rect().contains(panel.mapTo(proposal, panel.rect().bottomRight())), (route, width))
        self.window.setFixedSize(1440, 900)
