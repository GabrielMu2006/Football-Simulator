"""In-progress group tables must exist before qualification is finalized."""
from __future__ import annotations
import copy, hashlib, json, shutil, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox
from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.components import DataTable
from football_simulator.ui.main_window import MainWindow
from tests import support


class LiveStandingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=Path(tempfile.mkdtemp(prefix='fs_live_groups_'))
        support.install_test_data_root(str(cls.tmp))
        cls.app=support.ensure_qapp();cls.svc=GameService()
        cls.svc.import_save(Path(__file__).parent/'fixtures_vnext/s3-finalized.fsarchive','groups')
        cls.finished=store.load_world('groups')
        cls.window=MainWindow(cls.svc);cls.window.setFixedSize(1440,900);cls.window.show();cls.window.open_save('groups')

    @classmethod
    def tearDownClass(cls):
        cls.window.close();support.reset_test_data_root();shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def at_week(self,competition,week):
        world=copy.deepcopy(self.finished)
        state=world['season']['cups'][competition]
        state['stage_done']={};state['champion']=None;state['runner_up']=None
        state['group_standings' if competition=='aureole' else 'standings']={}
        world['season']['week']=week;world['season']['finalized']=False;world['season']['awards']={}
        for match in world['season']['fixtures']:
            if match['competition']==competition and match['week']>=week:match['status']='scheduled'
        return world

    def verify(self,world,competition):
        dto=queries.competition_detail(world,'groups',competition,live_standings=True)
        state=world['season']['cups'][competition]
        for group in dto['groups']:
            key=group['name'] if competition=='aureole' else 'D' if group['name']=='登峰组' else 'N'
            ids=state['groups'][key]
            self.assertEqual({r['team_id'] for r in group['table']},set(ids))
            oracle={tid:dict(played=0,points=0,goals_for=0,goals_against=0) for tid in ids}
            prefix='group_' if competition=='aureole' else 'cross_group_'
            for m in world['season']['fixtures']:
                if m['competition']!=competition or m['status']!='completed' or not m.get('stage','').startswith(prefix):continue
                hg=int(m.get('home_goals') or 0)+int(m.get('home_goals_et') or 0);ag=int(m.get('away_goals') or 0)+int(m.get('away_goals_et') or 0)
                for tid,gf,ga in ((m['home_team_id'],hg,ag),(m['away_team_id'],ag,hg)):
                    if tid not in oracle:continue
                    row=oracle[tid];row['played']+=1;row['goals_for']+=gf;row['goals_against']+=ga;row['points']+=3 if gf>ga else 1 if gf==ga else 0
            for row in group['table']:
                for field,value in oracle[row['team_id']].items():self.assertEqual(row[field],value,(competition,key,field))
        return dto

    def test_reign_week_nine_has_16_teams_after_16_matches(self):
        world=self.at_week('aureole',9)
        played=[m for m in world['season']['fixtures'] if m['competition']=='aureole' and m['status']=='completed']
        self.assertEqual(len(played),16)
        dto=self.verify(world,'aureole')
        self.assertEqual([len(g['table']) for g in dto['groups']],[4,4,4,4])
        self.assertTrue(all(r['played']==2 for g in dto['groups'] for r in g['table']))
        self.assertFalse(any('已晋级' in r['advance'] for g in dto['groups'] for r in g['table']))

    def test_both_cups_zero_games_still_show_seeded_teams(self):
        for competition in ('aureole','ascendant'):
            dto=self.verify(self.at_week(competition,1),competition)
            self.assertEqual(sum(len(g['table']) for g in dto['groups']),16 if competition=='aureole' else 12)
            self.assertTrue(all(r['played']==r['points']==0 for g in dto['groups'] for r in g['table']))

    def test_cross_group_matches_update_both_group_tables(self):
        world=self.at_week('ascendant',15)
        dto=self.verify(world,'ascendant')
        self.assertEqual([len(g['table']) for g in dto['groups']],[6,6])
        self.assertTrue(all(r['played']>0 for g in dto['groups'] for r in g['table']))
        self.assertTrue(all(r['advance'].startswith('暂列') for g in dto['groups'] for r in g['table']))

    def test_cross_group_live_and_frozen_qualification_use_wins_tiebreak(self):
        import random
        from football_simulator.game import competitions
        from tests.test_v2_competitions import AscendantScheduleTests
        world = self.at_week('ascendant', 20)
        state = world['season']['cups']['ascendant']
        d_group, n_group, matches = AscendantScheduleTests.tied_cross_group_fixture()
        ids = dict(zip(d_group + n_group, state['groups']['D'] + state['groups']['N']))
        original = [m for m in world['season']['fixtures'] if m['competition'] == 'ascendant' and m['stage'].startswith('cross_group_')]
        replacements = {}
        for template, match in zip(original, matches):
            match['home_team_id'] = ids[match['home_team_id']]
            match['away_team_id'] = ids[match['away_team_id']]
            replacements[template['match_id']] = {**template, **match, 'home_goals_et': 0, 'away_goals_et': 0}
        world['season']['fixtures'] = [replacements.get(m['match_id'], m) for m in world['season']['fixtures']]
        state['playin'] = []
        before = self.verify(world, 'ascendant')
        live_order = [row['team_id'] for row in before['groups'][0]['table']]
        self.assertEqual(live_order[3:5], [ids['D1'], ids['D0']])
        competitions.progress_ascendant(world, state, 19, random.Random(3))
        after = queries.competition_detail(world, 'groups', 'ascendant', live_standings=True)
        self.assertEqual(live_order, [row['team_id'] for row in after['groups'][0]['table']])
        self.assertIn(ids['D1'], state['direct'])
        self.assertNotIn(ids['D0'], state['direct'])

    def test_read_does_not_mutate_world_or_database_and_frozen_table_is_kept(self):
        path=store.database_path('groups');digest=hashlib.sha256(path.read_bytes()).hexdigest()
        for competition in ('aureole','ascendant'):
            world=self.at_week(competition,9);before=json.dumps(world,sort_keys=True)
            self.verify(world,competition)
            self.assertEqual(json.dumps(world,sort_keys=True),before)
            final=queries.competition_detail(self.finished,'groups',competition,live_standings=True)
            legacy=queries.competition_detail(self.finished,'groups',competition)
            self.assertEqual([[r['team_id'] for r in g['table']] for g in final['groups']],[[r['team_id'] for r in g['table']] for g in legacy['groups']])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)

    def test_legacy_default_contract_unchanged(self):
        world=self.at_week('aureole',9)
        self.assertTrue(all(not group['table'] for group in queries.competition_detail(world,'groups','aureole')['groups']))

    def test_live_player_production_matches_per_game_facts(self):
        world=self.at_week('aureole',9)
        rows=queries.competition_players(world,'aureole')
        self.assertTrue(rows)
        for row in rows:
            facts=[m.get('player_facts',{}).get(row['player_id'],{}) for m in world['season']['fixtures'] if m['competition']=='aureole' and m['status']=='completed']
            appeared=[f for f in facts if f.get('appeared')]
            self.assertEqual(row['appearances'],len(appeared))
            self.assertEqual(row['goals'],sum(f.get('goals',0) for f in appeared))
            self.assertEqual(row['assists'],sum(f.get('assists',0) for f in appeared))
            self.assertTrue(world['players'][row['player_id']]['is_real'])

    @unittest.skipUnless(sys.platform=='darwin','macOS observer sections')
    def test_cup_sections_show_real_schedule_players_and_pending_awards(self):
        world=self.at_week('aureole',9)
        with patch.object(self.window,'world',return_value=world):
            self.window.navigate('competition',competition_id='aureole')
            page=self.window.stack.currentWidget();key=f"aureole:{world['season_number']}"
            for section in ('matches','players','awards','history','overview'):
                page.set_detail_section(key,section);QTest.qWait(20)
                self.assertFalse(self.window.error_label.text())
                if section in ('matches','players'):
                    self.assertTrue(page.findChild(DataTable)._rows)
                if section=='awards':
                    text=' '.join(l.text() for l in page.findChildren(QLabel))
                    self.assertIn('待赛事结算',text);self.assertNotIn('空缺（',text)

    @unittest.skipUnless(sys.platform=='darwin','macOS promotion detail')
    def test_promotion_playoffs_are_reachable_with_real_ties(self):
        self.window.navigate('competition',competition_id='playoff');QTest.qWait(30)
        page=self.window.stack.currentWidget()
        self.assertFalse(self.window.error_label.text())
        text=' '.join(l.text() for l in page.findChildren(QLabel))
        self.assertIn('升级成功',text);self.assertNotIn('未知赛事',text)
        self.assertEqual(queries.competition_detail(self.finished,'groups','playoff',live_standings=True)['kind'],'playoff')

    @unittest.skipUnless(sys.platform=='darwin','macOS completed-season week')
    def test_season_end_shows_week_52_and_opens_its_report(self):
        self.window.open_save('groups')
        self.assertTrue(self.window.world()['season']['season_complete'])
        self.assertIn('已完成第 52 周', self.window.status_line.text())
        page = self.window.stack.currentWidget()
        self.assertEqual(page.weekly_button.text(), '查看第 52 周赛况')
        QTest.mouseClick(page.weekly_button, Qt.LeftButton)
        self.assertEqual(self.window._current_route, ('weekly', {'week': 52}))

    @unittest.skipUnless(sys.platform=='darwin','macOS archived paging')
    def test_y_all_40_honor_rows_are_reachable_without_hidden_scroll(self):
        self.window.navigate('history');QTest.qWait(30)
        page=self.window.stack.currentWidget()
        table=next(t for t in page.findChildren(DataTable) if [c['key'] for c in t._columns]==['team','points'])
        from football_simulator.ui import macos_presentation as macos
        pager=page.findChild(macos.Pagination)
        self.assertEqual(pager.total,40)
        found=[]
        while True:
            found.extend(r['team_id'] for r in table._rows)
            self.assertEqual(table.verticalScrollBar().maximum(),0)
            if not pager.following.isEnabled():break
            pager.following.click()
        self.assertEqual(len(found),40);self.assertEqual(len(set(found)),40)

    @unittest.skipUnless(sys.platform=='darwin','macOS archived paging')
    def test_y_historical_player_table_is_bounded(self):
        self.window.navigate('history_competition',competition_id='premier',season=2);QTest.qWait(30)
        page=self.window.stack.currentWidget()
        page.set_detail_section('premier:2','players');QTest.qWait(30)
        table=next(t for t in page.findChildren(DataTable) if any(c['key']=='player' for c in t._columns))
        self.assertLessEqual(table.rowCount(),10)

    @unittest.skipUnless(sys.platform=='darwin','macOS native lifecycle')
    def test_z_repeated_native_season_confirmation_with_cached_pages(self):
        if self.app.platformName()!='cocoa':self.skipTest('requires real Cocoa dialog/event lifecycle')
        for index in range(3):
            name=f'cached-native-{index}'
            self.svc.import_save(Path(__file__).parent/'fixtures_vnext/s3-finalized.fsarchive',name)
            self.window.open_save(name)
            world=self.window.world();team=next(iter(world['teams']));player=next(pid for pid,p in world['players'].items() if p.get('is_real'))
            for route,params in [('dashboard',{}),('matches',{}),('players',{}),('teams',{}),('history',{}),('records',{}),('honors',{}),('team',{'team_id':team}),('player',{'player_id':player}),('competition',{'competition_id':'aureole'}),('competition',{'competition_id':'ascendant'}),('season',{})]:
                self.window.navigate(route,**params);self.app.processEvents()
            self.assertGreaterEqual(len(self.window._pages),10)
            def confirm():
                dialog=next(w for w in QApplication.topLevelWidgets() if isinstance(w,QMessageBox) and w.isVisible())
                self.assertIn('本赛季成绩已冻结',dialog.text())
                dialog.button(QMessageBox.Yes).click()
            QTimer.singleShot(30,confirm)
            QTest.mouseClick(self.window.primary,Qt.LeftButton);QTest.qWait(60)
            self.assertEqual(self.window.world()['season_number'],4)
            self.assertEqual(set(self.window._pages),{'dashboard'})
            self.assertFalse(self.window.error_label.text())

    @unittest.skipUnless(sys.platform=='darwin','macOS live tables')
    def test_actual_group_rows_and_team_navigation(self):
        world=self.at_week('aureole',9)
        with patch.object(self.window,'world',return_value=world):
            self.window.navigate('competition',competition_id='aureole');QTest.qWait(50)
            self.assertFalse(self.window.error_label.text())
            # CUI-040 移除了“分组路径”显示列；四组仍各 4 队，前二为晋级区。
            tables=[t for t in self.window.stack.currentWidget().findChildren(DataTable) if len(t._rows)==4]
            self.assertEqual([t.rowCount() for t in tables],[4,4,4,4])
            self.assertTrue(all('advance' not in [c['key'] for c in t._columns] for t in tables))
            self.assertEqual([sum(1 for zone in t._zone_rows if zone) for t in tables],[2,2,2,2])
            table=tables[0];column=next(i for i,c in enumerate(table._columns) if c['key']=='team');tid=table._rows[0]['team_id']
            QTest.mouseClick(table.viewport(),Qt.LeftButton,pos=table.visualItemRect(table.item(0,column)).center());QTest.qWait(30)
            self.assertEqual(self.window._current_route,('team',{'team_id':tid}))


def tearDownModule():support.cleanup_qt_widgets()
