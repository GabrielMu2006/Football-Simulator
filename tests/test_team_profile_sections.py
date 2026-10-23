"""Team trophies and season results have separate purposes; recent results page by five."""
import shutil
import tempfile
import unittest
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QPushButton, QTabBar

from tests import support
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.components import DataTable, MatchRowCard
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.pages.honors import TeamTrophyCollection
from football_simulator.ui.theme import apply_theme
from football_simulator.ui.trophies import TrophyImage


class TeamProfileSectionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='fs_team_sections_')
        support.install_test_data_root(cls.tmp)
        cls.app = support.ensure_qapp()
        apply_theme(cls.app)
        store.import_package(support.PROJECT_ROOT/'tests/fixtures_vnext/s3-finalized.fsarchive', 'teams')
        cls.window = MainWindow(GameService())
        cls.window.setFixedSize(1280, 900)
        cls.window.show()
        cls.window.open_save('teams')
        cls.world = store.load_world('teams', readonly=True)
        cls.team = 'team-glorious-vision'

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def open(self, section='overview', team=None):
        team = team or self.team
        self.window.navigate('team', team_id=team)
        page = self.window.stack.currentWidget()
        page.set_detail_section(team, section)
        QTest.qWait(40)
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        return page

    def recent(self, page):
        return page.findChild(__import__('PySide6').QtWidgets.QWidget, 'TeamRecentResults')

    def setUp(self):
        page = self.window._pages.get('team')
        if page is not None:
            page._detail_states.clear()

    def oracle(self, team=None, comp=''):
        team = team or self.team
        rows = [f for f in self.world['season']['fixtures'] if f['status']=='completed' and team in (f['home_team_id'], f['away_team_id']) and (not comp or f['competition']==comp)]
        return sorted(rows, key=lambda f: (int(f['week']), f['match_id']), reverse=True)

    def test_honors_has_trophies_and_no_season_matrix_history_has_matrix_only(self):
        before = store.load_world('teams', readonly=True)
        page = self.open('honors')
        cabinet = page.findChild(TeamTrophyCollection)
        self.assertIsNotNone(cabinet)
        self.assertEqual(len(cabinet.findChildren(TrophyImage)), 8)
        self.assertIsNone(page.findChild(DataTable, 'TeamSeasonMatrix'))
        expected = sum(t['champion_team_id']==self.team for a in self.world['archives'] for t in a['championships'].values())
        self.assertEqual(cabinet.data['total_titles'], expected)
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(40)
        self.assertIsNotNone(page.findChild(TeamTrophyCollection))
        page = self.open('history')
        self.assertIsNotNone(page.findChild(DataTable, 'TeamSeasonMatrix'))
        self.assertIsNone(page.findChild(TeamTrophyCollection))
        self.assertEqual(store.load_world('teams', readonly=True), before)

    def test_overview_and_fixture_results_both_have_fixed_five_page(self):
        for section in ('overview', 'fixtures'):
            page = self.open(section)
            recent = self.recent(page)
            selector = recent.findChild(QComboBox, 'TeamRecentCompetition')
            selector.setCurrentIndex(0)
            QTest.qWait(30)
            recent = self.recent(page)
            pager = recent.findChild(macos.Pagination)
            while pager.previous.isEnabled():
                QTest.mouseClick(pager.previous, Qt.LeftButton)
                QTest.qWait(20)
            self.assertEqual(pager.state['page_size'], 5)
            self.assertFalse(pager.sizes.isVisible())
            self.assertEqual([c.match_id for c in recent.findChildren(MatchRowCard)], [f['match_id'] for f in self.oracle()[:5]])
            QTest.mouseClick(pager.following, Qt.LeftButton)
            QTest.qWait(30)
            self.assertEqual([c.match_id for c in recent.findChildren(MatchRowCard)], [f['match_id'] for f in self.oracle()[5:10]])
            self.assertTrue(all(c.property('teamResult') for c in recent.findChildren(MatchRowCard)))

    def test_competition_filter_resets_page_and_survives_f5_match_roundtrip(self):
        page = self.open('fixtures')
        recent = self.recent(page)
        box = recent.findChild(QComboBox, 'TeamRecentCompetition')
        box.setCurrentIndex(box.findData('premier'))
        QTest.qWait(30)
        recent = self.recent(page)
        pager = recent.findChild(macos.Pagination)
        self.assertEqual(pager.state['page'], 1)
        QTest.mouseClick(pager.following, Qt.LeftButton)
        QTest.qWait(30)
        expected = [f['match_id'] for f in self.oracle(comp='premier')[5:10]]
        self.assertEqual([c.match_id for c in recent.findChildren(MatchRowCard)], expected)
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(40)
        recent = self.recent(page)
        self.assertEqual(recent.findChild(QComboBox, 'TeamRecentCompetition').currentData(), 'premier')
        self.assertEqual([c.match_id for c in recent.findChildren(MatchRowCard)], expected)
        first = recent.findChildren(MatchRowCard)[0]
        page.scroll.ensureWidgetVisible(first)
        QTest.mouseClick(first, Qt.LeftButton)
        QTest.qWait(40)
        self.assertEqual(self.window._current_route, ('match', {'match_id': expected[0]}))
        self.window.go_back()
        QTest.qWait(40)
        recent = self.recent(page)
        self.assertEqual([c.match_id for c in recent.findChildren(MatchRowCard)], expected)
        self.assertEqual(recent.findChild(QComboBox, 'TeamRecentCompetition').currentData(), 'premier')

    def test_filter_short_competition_then_switch_team_has_independent_state(self):
        page = self.open('fixtures')
        counts = Counter(f['competition'] for f in self.oracle())
        comp = min(counts, key=counts.get)
        box = self.recent(page).findChild(QComboBox, 'TeamRecentCompetition')
        box.setCurrentIndex(box.findData(comp))
        QTest.qWait(30)
        recent = self.recent(page)
        self.assertEqual([c.match_id for c in recent.findChildren(MatchRowCard)], [f['match_id'] for f in self.oracle(comp=comp)[:5]])
        self.assertEqual(recent.findChild(macos.Pagination).total, counts[comp])
        other = next(t for t in self.world['teams'] if t != self.team)
        page = self.open('fixtures', other)
        self.assertEqual(self.recent(page).findChild(QComboBox, 'TeamRecentCompetition').currentData(), '')

    def test_trophy_season_link_opens_edition_and_back_retains_honors_tab(self):
        page = self.open('honors')
        cabinet = page.findChild(TeamTrophyCollection)
        button = next(b for b in cabinet.findChildren(QPushButton) if b.text() == '第 2 赛季')
        page.scroll.ensureWidgetVisible(button)
        QTest.mouseClick(button, Qt.LeftButton)
        QTest.qWait(40)
        self.assertEqual(self.window._current_route[0], 'history_competition')
        self.assertEqual(self.window._current_route[1]['season'], 2)
        self.window.go_back()
        QTest.qWait(40)
        self.assertEqual(page.detail_state(self.team)['section'], 'honors')
        self.assertIsNotNone(page.findChild(TeamTrophyCollection))


if __name__ == '__main__':
    unittest.main()
