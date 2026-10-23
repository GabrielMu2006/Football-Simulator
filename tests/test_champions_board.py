"""Completed titles are counted once; both leagues have a quick champions view."""
import copy
import shutil
import tempfile
import unittest
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabBar

from tests import support
from football_simulator.app import records
from football_simulator.app.service import GameService
from football_simulator.game import rules
from football_simulator.persistence_v2 import store
from football_simulator.ui.components import DataTable
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.pages.honors import TeamHonorLeaderboard
from football_simulator.ui.theme import apply_theme


class ChampionDataTests(unittest.TestCase):
    def world(self):
        return {'season_number': 2, 'teams': {'a': {'club_en': 'A'}, 'b': {'club_en': 'B'}},
                'season': {'finalized': False, 'championships': {}}, 'archives': []}

    def title(self, comp, team, season=1, identifier='same'):
        return {'title_id': identifier, 'season_number': season, 'competition_id': comp,
                'champion_team_id': team}

    def test_same_edition_current_archive_and_different_title_ids_count_once(self):
        w = self.world()
        title = self.title(rules.PREMIER, 'a', 2)
        w['season']['championships'] = {'one': title, 'duplicate': dict(title, title_id='other')}
        w['archives'] = [{'season_number': 2, 'championships': {'one': title}, 'honor_totals': {'a': 240}}] * 2
        w['edition_archives'] = {'2:premier': {'season_number': 2, 'competition_id': rules.PREMIER, 'championship': title}}
        before = copy.deepcopy(w)
        data = records.cumulative_team_honors(w)
        self.assertEqual(data['rows'][0]['trophies'], 1)
        self.assertEqual(data['rows'][0]['points'], 240)
        self.assertEqual(records.competition_champions(w, rules.PREMIER)[0]['team_id'], 'a')
        self.assertEqual(w, before)

    def test_all_eight_trophies_include_second_league_but_exclude_playoff(self):
        w = self.world()
        w['season']['championships'] = {c: self.title(c, 'a', 2) for c in rules.ALL_COMPETITION_IDS}
        data = records.cumulative_team_honors(w)
        self.assertEqual(next(r['trophies'] for r in data['rows'] if r['team_id'] == 'a'), 8)
        self.assertEqual(records.competition_champions(w, rules.PLAYOFF), [])
        # A title can be decided while the season honor total is still pending.
        self.assertTrue(all(r['points'] == 0 and r['rank'] is None for r in data['rows']))

    def test_legacy_final_tables_orders_and_cup_results_have_read_only_fallbacks(self):
        w = self.world()
        w['archives'] = [{'season_number': 1, 'premier_table': [{'team_id': 'a'}],
                          'second_order': ['b', 'a'], 'cup_champions': {rules.CUP_IDS[0]: 'a'}}]
        before = copy.deepcopy(w)
        self.assertEqual(records.competition_champions(w, rules.PREMIER)[0]['team_id'], 'a')
        self.assertEqual(records.competition_champions(w, rules.SECOND)[0]['team_id'], 'b')
        self.assertEqual({r['team_id']: r['trophies'] for r in records.cumulative_team_honors(w)['rows']}, {'a': 2, 'b': 1})
        self.assertEqual(w, before)

    def test_championship_record_wins_over_fallback_and_newest_season_is_first(self):
        w = self.world()
        w['archives'] = [{'season_number': 1, 'premier_table': [{'team_id': 'b'}],
                          'championships': {'x': self.title(rules.PREMIER, 'a')}}]
        w['season']['championships'] = {'x': self.title(rules.PREMIER, 'b', 2)}
        self.assertEqual([(r['season'], r['team_id']) for r in records.competition_champions(w, rules.PREMIER)], [(2, 'b'), (1, 'a')])

    def test_no_champion_is_inferred_from_live_league_leader(self):
        w = self.world()
        w['season']['final_tables'] = {'premier': [{'team_id': 'a', 'points': 99}]}
        self.assertEqual(records.competition_champions(w, rules.PREMIER), [])
        self.assertTrue(all(r['trophies'] == 0 for r in records.cumulative_team_honors(w)['rows']))

    def test_completed_edition_can_supply_final_table_before_whole_season_archive(self):
        w = self.world()
        w['edition_archives'] = {'2:second': {'season_number': 2, 'competition_id': rules.SECOND,
                                            'tables': {'second': [{'team_id': 'b'}]}}}
        self.assertEqual(records.competition_champions(w, rules.SECOND)[0]['team_id'], 'b')


class ChampionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='fs_champion_tests_')
        support.install_test_data_root(cls.tmp)
        cls.app = support.ensure_qapp()
        apply_theme(cls.app)
        store.import_package(support.PROJECT_ROOT/'tests/fixtures_vnext/s3-finalized.fsarchive', 'final')
        cls.window = MainWindow(GameService())
        cls.window.setFixedSize(1280, 900)
        cls.window.show()
        cls.window.open_save('final')
        cls.world = store.load_world('final', readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def champions(self, comp, season=None):
        self.window.navigate('competition', competition_id=comp, **({'season': season} if season else {}))
        QTest.qWait(30)
        page = self.window.stack.currentWidget()
        tabs = page.findChild(QTabBar, 'DetailSections')
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == '历届冠军')
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(30)
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        return self.window.stack.currentWidget().findChild(DataTable, 'LeagueChampions')

    def test_both_leagues_have_all_frozen_champions_crests_and_f5_state(self):
        before = store.load_world('final', readonly=True)
        for comp in (rules.PREMIER, rules.SECOND):
            with self.subTest(comp=comp):
                table = self.champions(comp)
                oracle = [(a['season_number'], a[f'{comp}_table'][0]['team_id']) for a in reversed(self.world['archives'])]
                self.assertEqual([(r['season'], r['team_id']) for r in table._rows], oracle)
                self.assertTrue(all(not table.item(i, 1).icon().isNull() for i in range(table.rowCount())))
                QTest.keyClick(self.window, Qt.Key_F5)
                QTest.qWait(30)
                table = self.window.stack.currentWidget().findChild(DataTable, 'LeagueChampions')
                self.assertEqual([(r['season'], r['team_id']) for r in table._rows], oracle)
                self.assertEqual(table.horizontalScrollBar().maximum(), 0)
        self.assertEqual(store.load_world('final', readonly=True), before)

    def test_champion_team_click_back_and_season_review_then_f5(self):
        table = self.champions(rules.PREMIER)
        row = table._rows[-1]
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=table.visualItemRect(table.item(table.rowCount()-1, 1)).center())
        QTest.qWait(30)
        self.assertEqual(self.window._current_route, ('team', {'team_id': row['team_id']}))
        self.window.go_back()
        QTest.qWait(30)
        table = self.window.stack.currentWidget().findChild(DataTable, 'LeagueChampions')
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=table.visualItemRect(table.item(table.rowCount()-1, 2)).center())
        QTest.qWait(30)
        self.assertEqual(self.window._current_route, ('competition', {'competition_id': rules.PREMIER, 'season': row['season']}))
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(30)
        page = self.window.stack.currentWidget()
        self.assertEqual(page._display_world['season_number'], row['season'])
        self.assertEqual(page.findChild(DataTable)._rows[0]['team_id'], row['team_id'])
        self.assertFalse(self.window.error_label.text())

    def test_viewing_old_edition_keeps_full_league_champion_history(self):
        table = self.champions(rules.SECOND, season=1)
        self.assertEqual([r['season'] for r in table._rows], [3, 2, 1])

    def test_trophy_column_matches_independent_unique_titles_and_preserves_points_rank(self):
        self.window.navigate('honors')
        QTest.qWait(30)
        board = self.window.stack.currentWidget().findChild(TeamHonorLeaderboard)
        expected = Counter(t['champion_team_id'] for a in self.world['archives'] for t in a['championships'].values())
        self.assertEqual([c['key'] for c in board.table._columns], ['rank', 'team', 'trophies', 'points'])
        self.assertEqual({r['team_id']: r['trophies'] for r in board.data['rows']}, {tid: expected[tid] for tid in self.world['teams']})
        for r in board.data['rows']:
            self.assertEqual(r['points'], sum(a['honor_totals'].get(r['team_id'], 0) for a in self.world['archives']))
        self.assertEqual(board.table.horizontalScrollBar().maximum(), 0)


if __name__ == '__main__':
    unittest.main()
