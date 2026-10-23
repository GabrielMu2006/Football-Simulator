"""Real controls for transfer direction, bounded detail sections and column widths."""
from __future__ import annotations
import shutil, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QHeaderView, QTabBar
from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui import transfer_history as transfers
from football_simulator.ui.components import DataTable, EntityLink, MatchRowCard
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.pages.entities import PlayerProfilePage, TeamProfilePage
from tests import support


@unittest.skipUnless(sys.platform == "darwin", "macOS presentation")
class DetailNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fs_detail_navigation_"))
        support.install_test_data_root(str(cls.tmp))
        cls.app = support.ensure_qapp()
        cls.svc = GameService()
        cls.svc.import_save(Path(__file__).parent/'fixtures_vnext/s3-finalized.fsarchive', 'details')
        cls.window = MainWindow(cls.svc)
        cls.window.setFixedSize(1440, 900)
        cls.window.show()
        cls.window.open_save('details')
        cls.world = cls.window.world()
        cls.trade = cls.world['transfer_history'][0]
        cls.player_id = cls.trade['players_a'][0]
        cls.team_id = cls.trade['team_a']

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def open(self, route, **params):
        self.window.navigate(route, **params)
        QTest.qWait(40)
        self.assertFalse(self.window.error_label.text())
        return self.window.stack.currentWidget()

    def section(self, page, key):
        tabs = page.findChild(QTabBar, 'DetailSections')
        index = next(i for i in range(tabs.count()) if tabs.tabData(i) == key)
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(40)
        self.assertFalse(self.window.error_label.text())

    def test_transfer_ids_define_direction_for_both_sides(self):
        row = self.trade
        for own, opponent, outgoing, incoming in ((row['team_a'], row['team_b'], row['players_a'], row['players_b']), (row['team_b'], row['team_a'], row['players_b'], row['players_a'])):
            movement = transfers.team_movement(row, own)
            self.assertEqual(movement, {'opponent_id':opponent, 'outgoing':outgoing, 'incoming':incoming})
            self.assertNotEqual(movement['opponent_id'], own)
        self.assertEqual(transfers.player_movement(row, row['players_a'][0]), (row['team_a'], row['team_b']))
        self.assertEqual(transfers.player_movement(row, row['players_b'][0]), (row['team_b'], row['team_a']))

    def test_team_transfer_cards_show_other_club_and_player_links(self):
        page = self.open('team', team_id=self.team_id)
        self.section(page, 'transfers')
        rows = transfers.ordered_transfers(self.world, team_id=self.team_id)
        events = [w for w in page.findChildren(__import__('PySide6').QtWidgets.QFrame) if w.property('tradeId')]
        self.assertEqual(len(events), min(4,len(rows)))
        for event, row in zip(events, rows[:4]):
            movement = transfers.team_movement(row, self.team_id)
            self.assertEqual(event.property('opponentId'), movement['opponent_id'])
            ids = {(link.kind,link.entity_id) for link in event.findChildren(EntityLink)}
            self.assertIn(('team', movement['opponent_id']),ids)
            self.assertNotIn(('team', self.team_id),ids)
            for pid in movement['incoming']+movement['outgoing']:
                self.assertIn(('player',pid),ids)

    def test_player_header_team_link_and_history_back_restore_tab(self):
        page = self.open('player', player_id=self.player_id)
        self.section(page,'transfers')
        team_id = self.world['players'][self.player_id]['team_id']
        link = next(l for l in page.findChildren(EntityLink) if l.accessibleName().startswith('所属球队'))
        QTest.mouseClick(link, Qt.LeftButton)
        QTest.qWait(40)
        self.assertEqual(self.window._current_route, ('team', {'team_id':team_id}))
        self.window.go_back();QTest.qWait(40)
        self.assertEqual(page.detail_state(self.player_id)['section'], 'transfers')

    def test_transfer_paging_refresh_and_entity_scoping(self):
        team_id = max(self.world['teams'], key=lambda tid:len(transfers.ordered_transfers(self.world,team_id=tid)))
        rows=transfers.ordered_transfers(self.world,team_id=team_id)
        self.assertGreater(len(rows),4)
        page=self.open('team',team_id=team_id);self.section(page,'transfers')
        pager=page.findChild(macos.Pagination)
        QTest.mouseClick(pager.following,Qt.LeftButton);QTest.qWait(20)
        self.assertEqual(pager.state['page'],2)
        self.window.refresh_all();QTest.qWait(40)
        self.assertEqual(page.findChild(macos.Pagination).state['page'],2)
        other=next(tid for tid in self.world['teams'] if tid!=team_id)
        page=self.open('team',team_id=other)
        self.assertEqual(page.detail_state(other)['section'],'overview')

    def test_all_numeric_columns_even_and_mixed_stats_bounded(self):
        page=self.open('player',player_id=self.player_id);self.section(page,'career')
        tables=page.findChildren(DataTable)
        # The career season table now includes the club at match time.
        numeric=page.findChild(DataTable, 'PlayerCareerTeamSeasons')
        widths=[numeric.columnWidth(i) for i,c in enumerate(numeric._columns) if c.get('numeric')]
        self.assertTrue(widths)
        self.assertLessEqual(max(widths),80)
        self.assertFalse(numeric.horizontalHeader().stretchLastSection())
        self.section(page,'matches')
        table=page.findChild(DataTable)
        assists=next(i for i,c in enumerate(table._columns) if c['key']=='assists')
        self.assertEqual(table.columnWidth(assists),60)
        self.assertEqual(table.rowCount(),10)
        pager=page.findChild(macos.Pagination)
        self.assertGreater(pager.total,20,'all match logs must remain reachable beyond prior query limit')
        old=[r['match_id'] for r in table._rows]
        QTest.mouseClick(pager.following,Qt.LeftButton)
        self.assertFalse(set(old)&{r['match_id'] for r in table._rows})
        self.window.refresh_all();QTest.qWait(40)
        self.assertEqual(page.findChild(macos.Pagination).state['page'],2)

    def test_full_team_fixtures_paged_without_losing_match_colors(self):
        page=self.open('team',team_id=self.team_id);self.section(page,'fixtures')
        pager=page.findChild(macos.Pagination)
        all_matches=sorted((m for m in self.world['season']['fixtures'] if self.team_id in (m['home_team_id'],m['away_team_id']) and m['status']=='completed'),key=lambda m:(m['week'],m['match_id']),reverse=True)
        self.assertEqual(pager.total,len(all_matches))
        visible=page.findChildren(MatchRowCard)
        self.assertEqual([r.match_id for r in visible], [m['match_id'] for m in all_matches[:5]])
        self.assertTrue(all(r.property('teamResult') for r in visible))

    def test_player_match_scores_are_from_his_recorded_team_perspective(self):
        page=self.open('player',player_id=self.player_id);self.section(page,'matches')
        table=page.findChild(DataTable)
        fixtures={m['match_id']:m for m in self.world['season']['fixtures']}
        self.assertIn('本队比分',[c['label'] for c in table._columns])
        for row in table._rows:
            match=fixtures[row['match_id']]
            home=row['team_id']==match['home_team_id']
            scores=queries.score_text(match).split('–')
            self.assertEqual(row['score'],'–'.join(scores if home else scores[::-1]))
            self.assertEqual(row['opponent_id'],match['away_team_id'] if home else match['home_team_id'])

    def test_round_selector_reaches_all_rounds_and_survives_refresh(self):
        page=self.open('competition',competition_id='premier')
        # CUI-060 把赛程放进独立分区：先切到“赛程”，再验证轮次选择器。
        tabs=page.findChild(QTabBar,'DetailSections')
        index=next(i for i in range(tabs.count()) if tabs.tabText(i)=='赛程')
        QTest.mouseClick(tabs,Qt.LeftButton,pos=tabs.tabRect(index).center());QTest.qWait(120)
        page=self.window.stack.currentWidget()
        selector=page.findChild(QComboBox,'LeagueRound')
        self.assertEqual(selector.count(),38)
        selector.setCurrentIndex(0);QTest.qWait(30)
        self.window.refresh_all();QTest.qWait(30)
        self.assertEqual(page.findChild(QComboBox,'LeagueRound').currentData(),1)
        self.assertEqual(len(page.findChildren(MatchRowCard)),10)

    def test_global_transfer_history_is_paged_and_links_teams(self):
        page=self.open('transfers')
        pager=page.findChild(macos.Pagination)
        self.assertEqual(pager.total,len(self.world['transfer_history']))
        table=page.findChild(DataTable)
        self.assertLessEqual(table.rowCount(),10)
        self.assertIn('甲队转出 → 乙队',[c['label'] for c in table._columns])
        first=table._rows[0]
        table.cell_activated.emit(first,'team_a_name')
        self.assertEqual(self.window._current_route,('team',{'team_id':first['team_a']}))

    def test_windows_keeps_original_stretch_and_unsplit_profile(self):
        with patch.object(macos,'enabled',return_value=False):
            table=DataTable();table.set_columns([{'key':'assists','label':'助','numeric':True,'width':50}])
            self.assertTrue(table.horizontalHeader().stretchLastSection())
            page=PlayerProfilePage(self.window)
            page.params={'player_id':self.player_id}
            page.refresh(self.svc.snapshot('details'),page.params)
            self.assertFalse(page.findChildren(QTabBar))
            self.assertGreaterEqual(len(page.findChildren(DataTable)),4)
            page.deleteLater();table.deleteLater()


def tearDownModule():
    support.cleanup_qt_widgets()
