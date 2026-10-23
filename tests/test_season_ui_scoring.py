"""Champion-weighted saves and the five macOS season-observer changes."""
import copy
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QComboBox, QTabBar
from tests import support
from football_simulator import runtime
from football_simulator.app import queries, records, profile_history
from football_simulator.app.service import GameService
from football_simulator.game import rules, honor_scoring, world as game_world
from football_simulator.persistence_v2 import store
from football_simulator.ui.components import BracketNode, CompactMatchRow, MatchRowCard, DataTable, node_venue_lines, node_venue_height, venue_text_height
from football_simulator.ui.competition_boards import GraphPanel
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.presentation import bracket
from football_simulator.ui.theme import apply_theme

FIXTURES = support.PROJECT_ROOT / 'tests/fixtures_vnext'


def non_scoring(world):
    value = copy.deepcopy(world)
    value.pop('version', None)
    def strip(obj):
        if isinstance(obj, dict):
            obj.pop('honor_scoring_profile', None)
            obj.pop('honor_totals', None)
            for row in obj.get('honor_rows') or []:
                row.pop('points', None)
            for item in obj.values():
                strip(item)
        elif isinstance(obj, list):
            for item in obj:
                strip(item)
    strip(value)
    return value


class SeasonScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='fs_season_scoring_tests_')
        support.install_test_data_root(cls.tmp)
        store.import_package(FIXTURES / 's3-finalized.fsarchive', 'final')
        cls.original = store.load_world('final', readonly=True)

    @classmethod
    def tearDownClass(cls):
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def test_new_ratios_prestige_and_legacy_profile_boundary(self):
        values = rules.honor_points(rules.HONOR_PROFILE)
        for comp in rules.CUP_IDS:
            self.assertEqual(values[comp]['runner_up'] * 2, values[comp]['champion'])
            semi = values[comp].get('semifinal', values[comp].get('third'))
            self.assertLessEqual(abs(semi * 3 - values[comp]['champion']), 10)
        self.assertEqual([values[c]['champion'] for c in ('aureole','starpath','ascendant','ember','confluence','zenith')], [180,180,160,140,130,90])
        self.assertEqual(rules.premier_honor_points(1, rules.HONOR_PROFILE), 240)
        self.assertEqual(rules.premier_honor_points(2, rules.HONOR_PROFILE), 120)
        self.assertEqual(rules.premier_honor_points(1), 120)
        self.assertEqual(rules.honor_points_for('zenith', 1), 45)
        self.assertEqual(values['second']['all_places'], 0)

    def test_all_saved_seasons_reweight_only_honor_values_and_keep_future_calculation_consistent(self):
        before = copy.deepcopy(self.original)
        after = honor_scoring.reweight(before)
        self.assertEqual(before, self.original)
        self.assertEqual(non_scoring(before), non_scoring(after))
        for edition in [*after['archives'], after['season']]:
            expected = {tid: 0 for tid in after['teams']}
            for row in edition['honor_rows']:
                comp, rank = row['competition'], row['rank']
                points = (rules.premier_honor_points(rank, rules.HONOR_PROFILE) if comp == 'premier' else 0 if comp == 'second' else rules.honor_points_for(comp, rank, rules.HONOR_PROFILE))
                self.assertEqual(row['points'], points)
                expected[row['team_id']] += points
            self.assertEqual(edition['honor_totals'], expected)
        previous = after['season']['previous']
        prior = next(a for a in after['archives'] if a['season_number']==after['season_number']-1)
        self.assertEqual(previous['honor_rows'], prior['honor_rows'])
        self.assertEqual(previous['honor_totals'], prior['honor_totals'])
        rows, total = game_world.compute_honors(after)
        self.assertEqual(rows, after['season']['honor_rows'])
        self.assertEqual(total, after['season']['honor_totals'])
        self.assertEqual(honor_scoring.reweight(after), after)
        summary = records.cumulative_team_honors(after)
        by_season = {a['season_number']: a['honor_totals'] for a in after['archives']}
        for row in summary['rows']:
            self.assertEqual(row['points'], sum(v.get(row['team_id'],0) for v in by_season.values()))

    def test_backup_failure_never_writes_then_success_preserves_match_sql_and_is_idempotent(self):
        path = store.database_path('final')
        digest = lambda: hashlib.sha256(path.read_bytes()).hexdigest()
        before = digest()
        service = GameService(honor_profile=rules.HONOR_PROFILE)
        with patch.object(store, 'export_package', side_effect=OSError('backup unavailable')):
            with self.assertRaises(OSError):
                service.prepare_save('final')
        self.assertEqual(before, digest())
        def facts():
            conn = store.open_readonly('final')
            try:
                return {table: list(conn.execute(f'SELECT * FROM {table} ORDER BY 1, 2, 3')) for table in ('matches','player_match_stats','awards')}
            finally:
                conn.close()
        before_facts = {k: [tuple(r) for r in v] for k,v in facts().items()}
        receipt = service.prepare_save('final')
        self.assertTrue(Path(receipt['backup']).is_file())
        store.import_package(Path(receipt['backup']), 'original-backup')
        self.assertEqual(store.load_world('original-backup', readonly=True), self.original)
        self.assertEqual(non_scoring(store.load_world('final', readonly=True)), non_scoring(self.original))
        self.assertEqual({k:[tuple(r) for r in v] for k,v in facts().items()}, before_facts)
        after = digest()
        self.assertIsNone(service.prepare_save('final'))
        self.assertEqual(digest(), after)

    def test_import_command_upgrades_before_first_display(self):
        service = GameService(honor_profile=rules.HONOR_PROFILE)
        service.import_save(FIXTURES / 's3-finalized.fsarchive', 'profile-import')
        loaded = service.load_world('profile-import')
        self.assertEqual(loaded['honor_scoring_profile'], rules.HONOR_PROFILE)
        self.assertEqual(non_scoring(loaded), non_scoring(self.original))

    def test_corrupt_rows_abort_before_claiming_profile(self):
        world = copy.deepcopy(self.original)
        world['archives'][0]['honor_rows'].append(copy.deepcopy(world['archives'][0]['honor_rows'][0]))
        with self.assertRaises(ValueError):
            honor_scoring.reweight(world)
        world = copy.deepcopy(self.original)
        world['archives'][0]['honor_rows'] = []
        with self.assertRaises(ValueError):
            honor_scoring.reweight(world)


class SeasonObserverUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='fs_season_observer_tests_')
        support.install_test_data_root(cls.tmp)
        cls.app = support.ensure_qapp()
        apply_theme(cls.app)
        for save, fixture in (('final','s3-finalized'),('winter','s2-week25')):
            store.import_package(FIXTURES / f'{fixture}.fsarchive', save)
        cls.service = GameService(honor_profile=rules.HONOR_PROFILE)
        cls.window = MainWindow(cls.service)
        cls.window.setFixedSize(1280, 800)
        cls.window.show()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def open(self, route, save='final', **params):
        self.window.open_save(save)
        self.window.navigate(route, **params)
        QTest.qWait(35)
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        return self.window.stack.currentWidget()

    def test_matrix_default_one_row_per_season_all_events_and_cell_navigation(self):
        page = self.open('team', team_id='team-glorious-vision')
        page.set_detail_section('team-glorious-vision','history')
        QTest.qWait(40)
        table = page.findChild(DataTable,'TeamSeasonMatrix')
        self.assertEqual(table._rows, profile_history.team_season_matrix(self.window.world(),'final','team-glorious-vision'))
        self.assertEqual([r['season'] for r in table._rows], [3,2,1])
        self.assertEqual(len(table._columns), 11)
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        table.cell_activated.emit(table._rows[-1], 'premier')
        QTest.qWait(40)
        self.assertEqual(self.window._current_route, ('history_competition',{'competition_id':'premier','season':1}))
        self.window.go_back()
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(30)
        self.assertIsNotNone(self.window.stack.currentWidget().findChild(DataTable,'TeamSeasonMatrix'))

    def test_annual_page_preserves_saved_ranks_crests_and_season_on_f5(self):
        page = self.open('annual_top20', season=3)
        table = page.findChild(DataTable, 'AnnualTop20')
        raw = next(a['top20'] for a in self.window.world()['archives'] if a['season_number']==3)
        self.assertEqual([r['rank'] for r in table._rows], [r['rank'] for r in raw])
        self.assertEqual(len({r['player_id'] for r in table._rows}), len(raw))
        self.assertTrue(all(not table.item(i,2).icon().isNull() for i in range(len(raw))))
        self.assertTrue(all('score' not in row and 'components' not in row for row in table._rows))
        self.assertTrue(all(row['team']==self.window.world()['teams'][row['team_id']]['club_en'] for row in table._rows))
        box = page.findChild(QComboBox, 'AnnualTop20Season')
        box.setCurrentIndex(box.findData(1))
        QTest.qWait(40)
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(40)
        self.assertEqual(page.findChild(QComboBox, 'AnnualTop20Season').currentData(),1)
        history = self.open('history')
        self.assertIsNone(history.findChild(DataTable,'AnnualTop20'))
        self.assertFalse(any([c['key'] for c in tab._columns]==['rank','label','team'] for tab in history.findChildren(DataTable)))

    def test_home_has_only_completed_matches_of_observed_week_with_stadiums(self):
        page = self.open('dashboard', save='winter')
        world = self.window.world()
        data = queries.dashboard(world, 'winter')
        self.assertEqual(data['played_week'],24)
        expected = {f['match_id'] for f in world['season']['fixtures'] if f['week']==24 and f['status']=='completed'}
        self.assertEqual({r['match_id'] for r in data['played_results']},expected)
        rows = page.findChildren(CompactMatchRow)
        self.assertTrue(rows)
        self.assertTrue(all(row.match_id in expected for row in rows))
        self.assertTrue(all(row.findChildren(QLabel,'MatchVenue') for row in rows))
        labels = [w.text() for w in page.findChildren(QLabel)]
        self.assertIn('本周已赛', labels)
        self.assertNotIn('下一场', labels)
        for week in (1,26,53):
            copied = copy.deepcopy(world)
            copied['season']['week'] = week
            dto = queries.dashboard(copied,'winter')
            self.assertEqual(dto['played_week'],max(0,week-1))
            self.assertEqual(dto['played_results'],[])

    def test_all_six_cups_graph_and_round_cards_have_actual_stadiums_without_path_footers(self):
        for comp in rules.CUP_IDS:
            page = self.open('competition', competition_id=comp)
            if comp=='ember':
                page.detail_state(comp)['bracket_view']='panorama'
                self.window.refresh_all()
                QTest.qWait(30)
            graphs = [p.view for p in page.findChildren(GraphPanel) if not p.view._bucket_graph]
            self.assertTrue(graphs, comp)
            for graph in graphs:
                for node in graph._nodes:
                    if node.get('match_id'):
                        self.assertTrue(node.get('venues'), (comp,node['id']))
                    height = sum(venue_text_height(text,node['w']-24) for text in node.get('venues') or [])
                    base = 2*26+16+(24 if node.get('legs') else 0)+(18 if node.get('badge') else 0)
                    self.assertGreaterEqual(node['h'],base+height)
            model = bracket.bracket_for(self.window.world(),comp,queries.competition_detail(self.window.world(),'final',comp))
            models = [n for col in model.get('columns') or [] for n in col['nodes']] or [n for g in model['groups'] for col in g['rounds'] for n in col['nodes']]
            card = BracketNode(self.window.world(),models[0])
            actual = [w.text() for w in card.findChildren(QLabel,'MatchVenue')]
            self.assertEqual(actual,node_venue_lines(models[0]))
            self.assertFalse(any('来源：' in w.text() or '下一站：' in w.text() for w in card.findChildren(QLabel)))
            card.deleteLater()
