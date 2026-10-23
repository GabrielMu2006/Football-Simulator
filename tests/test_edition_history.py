"""Edition freezing, honest legacy draws, draft history and recorded team splits."""
import copy,hashlib,random,shutil,tempfile,unittest
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox,QLabel,QPushButton
from tests import support
from football_simulator.app import edition_history,profile_history,queries,records
from football_simulator.app.service import GameService
from football_simulator.game import editions,rules,world as game_world
from football_simulator.persistence_v2 import store
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.components import DataTable,BracketBoard
from football_simulator.ui.competition_boards import GraphPanel
from football_simulator.ui.presentation import bracket
from football_simulator.ui.theme import apply_theme
FIXTURES=support.PROJECT_ROOT/'tests/fixtures_vnext'


class EditionDataTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.mkdtemp(prefix='fs_edition_data_');support.install_test_data_root(cls.tmp)
  for name,fixture in [('final','s3-finalized'),('winter','s2-week25')]:store.import_package(FIXTURES/f'{fixture}.fsarchive',name)
  cls.service=GameService(edition_archives=True,rng_factory=lambda:random.Random(219))
  cls.before=store.load_world('final',readonly=True)
  cls.receipt=cls.service.prepare_save('final');cls.world=cls.service.load_world('final')
 @classmethod
 def tearDownClass(cls):
  support.reset_test_data_root();shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))
 def test_backfill_is_backed_up_idempotent_and_preserves_existing_frozen_awards_and_rankings(self):
  self.assertTrue(Path(self.receipt['backup']).is_file())
  after=copy.deepcopy(self.world);before=copy.deepcopy(self.before)
  for obj in (before,after):
   for key in ('version','edition_archives','edition_archive_version','_fixture_index'):obj.pop(key,None)
  self.assertEqual(after,before)
  self.assertEqual(len(self.world['edition_archives']),20)
  path=store.database_path('final');digest=hashlib.sha256(path.read_bytes()).hexdigest()
  self.assertIsNone(self.service.prepare_save('final'))
  self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
 def test_every_legacy_cup_reproduces_all_played_ties_scores_stadiums_and_winners(self):
  w=self.world
  for year in (2,3):
   archive=next(a for a in w['archives'] if a['season_number']==year)
   for comp in rules.CUP_IDS:
    if not rules.cup_active(comp,year):continue
    view=edition_history.display_world(w,'final',comp,year)
    detail=queries.competition_detail(view,'final',comp,live_standings=True)
    model=bracket.bracket_for(view,comp,detail)
    columns=model.get('columns') or [r for g in model['groups'] for r in g['rounds']]
    nodes=[n for col in columns for n in col['nodes']]
    self.assertEqual(len(nodes),14 if comp=='ember' else 31 if comp=='confluence' else 7,(year,comp))
    self.assertEqual(detail['champion_id'],archive['cup_champions'][comp])
    raw={f['match_id']:f for f in store.fetch_matches('final',season=year,competition=comp)}
    for node in nodes:
     self.assertTrue(node['winner_id'])
     for mid in node['match_ids']:self.assertIn(mid,raw)
     self.assertTrue(node['venues'])
     scores={tid:0 for tid in node['team_ids']}
     for mid in node['match_ids']:
      f=raw[mid]
      for side in ('home','away'):scores[f[side+'_team_id']]+=int(f[side+'_goals'] or 0)+int(f[side+'_goals_et'] or 0)
     self.assertEqual({t['team_id']:t['score'] for t in node['teams']},scores)
  self.assertIsNone(edition_history.display_world(w,'final','zenith',1))
 def test_swiss_group_results_and_buchholz_use_actual_matches_not_new_draws(self):
  view=edition_history.display_world(self.world,'final','starpath',2)
  state=view['season']['cups']['starpath']
  self.assertEqual(len(state['qualified']),8);self.assertEqual(len(state['eliminated']),8)
  self.assertEqual(sum(sum(r) for r in state['records'].values()),66)
  for tid in state['participants']:
   opponents={f['away_team_id'] if f['home_team_id']==tid else f['home_team_id'] for f in view['season']['fixtures'] if f['stage'].startswith('swiss_') and tid in (f['home_team_id'],f['away_team_id'])}
   self.assertEqual(state['bu'][tid],sum(state['records'][other][0]-state['records'][other][1] for other in opponents))
  for comp in ('aureole','ascendant'):
   v=edition_history.display_world(self.world,'final',comp,2);groups=v['season']['cups'][comp]['groups']
   self.assertEqual(sorted(len(x) for x in groups.values()),[4]*4 if comp=='aureole' else [6]*2)
 def test_final_archives_are_frozen_immediately_at_week_30_before_season_archive(self):
  service=self.service;service.prepare_save('winter');service.set_mode('winter','manual')
  while service.load_world('winter')['season']['week']<=30:
   pending=service.load_world('winter')['season']['pending']
   if pending.get('transfer_review'):
    service.apply_transfer_reviews('winter',{})
   service.advance_week('winter')
  world=service.load_world('winter');identifier='2:confluence'
  self.assertIn(identifier,world['edition_archives']);self.assertFalse(any(a['season_number']==2 for a in world['archives']))
  entry=copy.deepcopy(world['edition_archives'][identifier]);self.assertEqual(entry['completed_week'],30)
  self.assertEqual(len(entry['fixtures']),31);self.assertTrue(entry['awards'])
  self.assertEqual(world['season']['awards']['competitions']['confluence'],entry['awards'])
  clean=copy.deepcopy(world);clean['edition_archives']={}
  self.assertEqual(game_world.compute_competition_awards(clean,competition_ids=['confluence'])['confluence'],entry['awards'])
  leaders=records.leaderboards('winter',scope='edition',competition_id='confluence',season_number=2,include_in_progress=False)
  self.assertFalse(leaders['empty'])
  self.assertEqual(editions.capture_finished(world),[])
  service.advance_week('winter');self.assertEqual(service.load_world('winter')['edition_archives'][identifier],entry)
 def test_draft_origin_uses_saved_actual_pick_and_initial_players_are_distinct(self):
  logs=profile_history.draft_history(self.world);self.assertEqual([log['season'] for log in logs],[3,2,1])
  drafted=set()
  for log in logs:
   for pick,row in enumerate(log['results'],1):
    drafted.add(row['player_id']);origin=profile_history.player_origin(self.world,row['player_id'])
    self.assertEqual(origin['pick'],pick);self.assertEqual(origin['season'],log['season']);self.assertIn('顺位',origin['label']);self.assertNotIn('种子',origin['label'])
  initial=next(pid for pid,p in self.world['players'].items() if p['is_real'] and pid not in drafted)
  self.assertEqual(profile_history.player_origin(self.world,initial)['label'],'初始球员')
 def test_team_split_stats_match_historical_sql_and_honor_matrix_uses_final_totals(self):
  conn=store.open_readonly('final')
  try:
   pid=conn.execute("SELECT player_id FROM player_match_stats WHERE appeared=1 GROUP BY player_id,season_number HAVING COUNT(DISTINCT team_id)>1 ORDER BY player_id LIMIT 1").fetchone()[0]
   expected=[tuple(r) for r in conn.execute("SELECT p.season_number,m.competition,p.team_id,COUNT(*),SUM(p.goals),SUM(p.assists) FROM player_match_stats p JOIN matches m ON m.match_id=p.match_id WHERE p.player_id=? AND p.appeared=1 GROUP BY p.season_number,m.competition,p.team_id",(pid,))]
  finally:conn.close()
  rows=profile_history.player_team_seasons(self.world,'final',pid,by_competition=True)
  self.assertEqual(set(expected),{(r['season'],r['competition'],r['team_id'],r['appearances'],r['goals'],r['assists']) for r in rows})
  self.assertTrue(any(len({r['team_id'] for r in rows if r['season']==season})>1 for season in (1,2,3)))
  for row in rows:self.assertEqual(row['team'],self.world['teams'][row['team_id']]['club_en'])
  for r in profile_history.team_season_matrix(self.world,'final','team-glorious-vision'):
   archive=next(a for a in self.world['archives'] if a['season_number']==r['season']);self.assertEqual(r['honor_points'],archive['honor_totals']['team-glorious-vision'])

 def test_z_all_competitions_archive_at_their_own_finish_week_and_never_rewrite(self):
  service=self.service
  while service.load_world('winter')['season']['week']<=49:
   pending=service.load_world('winter')['season']['pending']
   if pending.get('transfer_review'):service.apply_transfer_reviews('winter',{})
   service.advance_week('winter')
  w=service.load_world('winter')
  expected={'premier':42,'second':42,'playoff':49,'aureole':47,'confluence':30,'starpath':49,'ember':48,'ascendant':43}
  for comp,week in expected.items():
   entry=w['edition_archives'][f'2:{comp}'];self.assertEqual(entry['completed_week'],week)
  self.assertNotIn('2:zenith',w['edition_archives'])
  self.assertFalse(any(a['season_number']==2 for a in w['archives']))
  original=copy.deepcopy(w['edition_archives']);self.assertEqual(editions.capture_finished(w),[]);self.assertEqual(w['edition_archives'],original)
  pending=w['season']['pending']
  if pending.get('ability_review'):service.apply_ability_review('winter',{row['player_id']:False for row in pending['ability_review']})
  result=service.apply_draft('winter')
  self.assertTrue(result['drafted'])
  for pick,row in enumerate(result['drafted'],1):
   self.assertEqual(row['pick'],pick)
   origin=profile_history.player_origin(result['world'],row['player_id'])
   self.assertEqual((origin['season'],origin['pick']),(2,pick))
   self.assertEqual(result['world']['players'][row['player_id']]['draft_origin']['pick'],pick)
  self.assertEqual(result['world']['edition_archives'],original)
 def test_new_exact_draw_snapshot_does_not_alias_live_state(self):
  w=copy.deepcopy(self.before);w['edition_archive_version']=1;w['edition_archives']={};editions.capture_finished(w)
  original=copy.deepcopy(w['edition_archives']['3:aureole'])
  self.assertEqual(original['state'],w['season']['cups']['aureole'])
  w['season']['cups']['aureole']['champion']='changed-for-test'
  w['season']['fixtures'][0]['home_goals']=999
  self.assertEqual(w['edition_archives']['3:aureole'],original)

 def test_zz_start_next_season_keeps_old_exact_draw_and_stats(self):
  before=copy.deepcopy(self.service.load_world('final')['edition_archives'])
  self.service.start_next_season('final');world=self.service.load_world('final')
  self.assertEqual(world['season_number'],4);self.assertEqual(world['edition_archive_version'],1)
  self.assertEqual(world['edition_archives'],before)
  view=edition_history.display_world(world,'final','aureole',3)
  self.assertEqual(len(view['season']['fixtures']),62);self.assertEqual(view['_edition_source'],'decision')


class EditionUiTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.mkdtemp(prefix='fs_edition_ui_');support.install_test_data_root(cls.tmp);cls.app=support.ensure_qapp();apply_theme(cls.app)
  store.import_package(FIXTURES/'s3-finalized.fsarchive','final');cls.service=GameService(edition_archives=True);cls.window=MainWindow(cls.service);cls.window.setFixedSize(1280,800);cls.window.show();cls.window.open_save('final')
 @classmethod
 def tearDownClass(cls):
  cls.window.close();cls.window.deleteLater();support.cleanup_qt_widgets();support.reset_test_data_root();shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))
 def open(self,route,**params):
  self.window.navigate(route,**params);QTest.qWait(35);self.assertFalse(self.window.error_label.text(),self.window.error_label.text());return self.window.stack.currentWidget()
 def test_edition_selector_routes_to_past_bracket_and_f5_retains_year_and_round(self):
  page=self.open('competition',competition_id='ember',season=2)
  self.assertEqual(page.findChild(QComboBox,'CompetitionSeason').currentData(),2)
  model=bracket.bracket_for(page._display_world,'ember',queries.competition_detail(page._display_world,'final','ember',live_standings=True))
  state=page.detail_state('ember:bracket:2');page._focus_bracket_stage({'id':'ember'},state,'lb_final');QTest.qWait(40)
  board=page.findChild(BracketBoard);self.assertIsNotNone(board);self.assertEqual(board.selection(),('lb','lb_final'))
  QTest.keyClick(self.window,Qt.Key_F5);QTest.qWait(40);self.assertEqual(page.findChild(BracketBoard).selection(),('lb','lb_final'))
  self.assertEqual(page._display_world['season_number'],2)
  box=page.findChild(QComboBox,'CompetitionSeason');box.setCurrentIndex(box.findData(3));QTest.qWait(40);self.assertEqual(self.window._current_route,('competition',{'competition_id':'ember','season':3}))
 def test_history_deeplink_uses_same_full_bracket_without_nested_scroll(self):
  page=self.open('history_competition',competition_id='aureole',season=2)
  self.assertTrue(page.findChildren(GraphPanel));self.assertEqual(page._display_world['season_number'],2)
  self.assertEqual(page.scroll.horizontalScrollBar().maximum(),0)
  self.assertEqual(len(page.findChildren(type(page.scroll))),1)
 def test_draft_year_switch_and_readable_player_origin_with_links(self):
  page=self.open('draft',season=1);table=page.findChild(DataTable,'DraftResults');self.assertIsNotNone(table)
  self.assertFalse(page.findChild(QPushButton,'SubmitDraft').isEnabled())
  pick=table._rows[0];table.cell_activated.emit(pick,'player');QTest.qWait(40);player=self.window.stack.currentWidget()
  origin=player.findChild(QLabel,'PlayerOrigin');self.assertIn('第 1 赛季选秀',origin.text());self.assertIn('第 1 顺位',origin.text());self.assertGreaterEqual(origin.font().pixelSize(),16)
  self.window.go_back();QTest.keyClick(self.window,Qt.Key_F5);QTest.qWait(40);self.assertEqual(self.window.stack.currentWidget().findChild(QComboBox,'DraftHistorySeason').currentData(),1)
 def test_player_stats_and_team_matrix_are_readable_navigable_and_frozen(self):
  page=self.open('player',player_id='real::darwin-nunez');page.set_detail_section('real::darwin-nunez','season');QTest.qWait(40)
  table=page.findChild(DataTable,'PlayerSeasonStats');self.assertIn('team',[c['key'] for c in table._columns])
  for index,row in enumerate(table._rows):
   if row['team_id']:self.assertFalse(table.item(index,1).icon().isNull())
  page=self.open('team',team_id='team-glorious-vision');page.set_detail_section('team-glorious-vision','history');QTest.qWait(40)
  table=page.findChild(DataTable,'TeamSeasonMatrix');self.assertEqual(table._columns[-1]['key'],'honor_points');self.assertEqual(len(table._columns),11)
  self.assertEqual(table.horizontalScrollBar().maximum(),0);self.assertEqual(page.scroll.horizontalScrollBar().maximum(),0)
