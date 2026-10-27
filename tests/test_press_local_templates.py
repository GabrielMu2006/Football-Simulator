"""Necessary local expression and persistence semantics, no fake API replies."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from football_simulator.persistence_v2 import store
from football_simulator.press import PressError,repository
from football_simulator.press.local_library import LocalLibrary,LocalFeatureLibrary,activate
from football_simulator.press.local_templates.engine import freeze_plan,render
from football_simulator.press.local_templates.common import match_line,outcomes
from football_simulator.press.local_templates.weekly import exciting,league_paragraphs,game_paragraphs
from tests.test_press_export import PressSaveCase


def offline_guards():
    stack=ExitStack()
    for target in ('football_simulator.press.credentials.Credentials.get',
                   'football_simulator.press.credentials.Keychain.__init__',
                   'football_simulator.press.provider.send_once','urllib.request.urlopen','socket.create_connection'):
        stack.enter_context(patch(target,side_effect=AssertionError('offline boundary called: '+target)))
    return stack


class LocalSemanticsTests(unittest.TestCase):
    def test_field_shootout_and_aggregate_are_distinct(self):
        game={'competition':'Apex Cup','home':'A','away':'B','field_score_home_away':{'home':1,'away':1},
              'regulation_home_away':{'home':0,'away':0},'extra_time_goals_home_away':{'home':1,'away':1},
              'shootout_home_away':{'home':4,'away':3},'shootout_winner':'A',
              'two_leg_tie':{'team_a':'B','team_b':'A','aggregate_a_b':[2,2],'complete':True,'advancing_team':'A'}}
        text=match_line(game)+outcomes(game)
        for expected in ('1–1','90分钟0–0','点球大战4–3','总比分为B 2–2 A','A赢下对阵'):self.assertIn(expected,text)
        self.assertNotIn('5–4',text)
        from football_simulator.press.local_templates.weekly import heading_for
        self.assertEqual(heading_for({'kind':'match_story','content':{'matches':[game]}}),'A点球胜出')

    def test_swiss_and_double_elimination_use_authoritative_outcomes(self):
        for status,expected,absent in [('entered_losers_bracket','降入败者组','本届出局'),('eliminated','本届出局','降入败者组')]:
            with self.subTest(status=status):
                text=outcomes({'competition':'Ember Cup','qualification_outcomes':[{'team':'A','status':status}]})
                self.assertIn(expected,text);self.assertNotIn(absent,text)
        for wins,losses,qualified,eliminated in [(2,1,False,False),(3,1,True,False),(2,3,False,True)]:
            with self.subTest(wins=wins,losses=losses):
                text=outcomes({'swiss_qualification_records':[{'team':'A','after_match':{'wins':wins,'losses':losses},
                               'qualified_in_this_match':qualified,'eliminated_in_this_match':eliminated}]})
                self.assertEqual('淘汰赛资格' in text,qualified);self.assertEqual('本届出局' in text,eliminated)
                self.assertNotIn('种子',text)

    def test_exciting_match_requires_ordered_events_and_final_effect(self):
        self.assertEqual(exciting({'field_score_home_away':{'home':1,'away':0}}),'')
        game={'field_score_home_away':{'home':2,'away':1},'score_flow':{'winner':'A','winner_trailing_spells':1},
              'goals':[{'minute':88,'scorer':'Player A','team':'A','field_effect':'反超比分'}]}
        text=exciting(game);self.assertIn('逆转',text);self.assertIn('第88分钟',text);self.assertIn('Player A',text)

    def test_second_division_closure_has_full_outcomes_without_relegation(self):
        data={'competition':'Second Division','champion':'A','leader_and_runner_up':[
              {'team':'A','points':66},{'team':'B','points':66}],
              'promoted_directly':['A','B'],'qualified_for_promotion_playoff':['C','D','E','F']}
        text=''.join(league_paragraphs(data,closure=True))
        for expected in ('同分','A、B直接升级','C、D、E、F进入升级附加赛'):self.assertIn(expected,text)
        self.assertNotIn('提前',text);self.assertNotIn('降级',text)

    def test_streak_scope_count_and_cross_season_are_preserved(self):
        game={'competition':'Premier Division','stage':'联赛','home':'A','away':'B','field_score_home_away':{'home':0,'away':1},
              'streak_events':[{'team':'A','kind':'unbeaten','scope':'联赛','before_match':60,'after_match':0,'ended':True,'crosses_seasons':True}]}
        text=''.join(game_paragraphs(game))
        self.assertIn('跨赛季的联赛60场不败',text);self.assertNotIn('各项正式赛事60',text)


class LocalStorageTests(PressSaveCase):
    def enabled(self):return activate('Press')

    def test_weekly_auto_queued_once_zero_keys_network_and_frozen_after_upgrade(self):
        with offline_guards():
            lib=self.enabled();self.advance()
            item=lib.next_local();self.assertEqual(item['kind'],'weekly')
            weekly=LocalLibrary(lib.path);job=weekly.reserve(1,1)
            before=repository.digest(self.world())
            self.assertTrue(weekly.generate_local(job))
            article=weekly.read(1,1,mark_read=False)
            self.assertEqual(repository.digest(self.world()),before)
            self.advance();store.write_world('Press',self.world())
            with patch('football_simulator.press.local_library.TEMPLATE_VERSION','future/2'),patch('football_simulator.press.planning.build_plan',side_effect=AssertionError('must not rewrite')):
                self.assertIsNone(weekly.reserve(1,1,retry=True))
                self.assertEqual(weekly.read(1,1),article)
            with lib.connection() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM press_articles WHERE season=1 AND week=1').fetchone()[0],1)
                metadata=json.loads(conn.execute('SELECT metadata_json FROM press_articles WHERE week=1').fetchone()[0])
            self.assertEqual(metadata['engine_id'],'local-template')
            self.assertTrue(metadata['story_templates'])
            self.assertTrue({'model','tokens','provider','usage'}.isdisjoint(metadata))

    def test_paused_retry_uses_frozen_plan_and_template(self):
        lib=self.enabled();self.advance();weekly=LocalLibrary(lib.path);job=weekly.reserve(1,1)
        with offline_guards():
            self.assertFalse(weekly.generate_local(job,should_stop=lambda:True))
            with patch('football_simulator.press.local_library.TEMPLATE_VERSION','future/2'),patch('football_simulator.press.planning.build_plan',side_effect=AssertionError('must reuse')):
                resumed=weekly.reserve(1,1);self.assertEqual(resumed['template_version'],'local-news/1')
                self.assertTrue(weekly.generate_local(resumed))

    def test_archive_preserves_source_and_rejects_stale_imported_identity(self):
        lib=self.enabled();self.advance();weekly=LocalLibrary(lib.path)
        with offline_guards():
            self.assertTrue(weekly.generate_local(weekly.reserve(1,1)))
            article=weekly.read(1,1,mark_read=False)
            self.advance();stale=weekly.reserve(1,2)
            package=Path(self.tmp.name)/'local.fsarchive';store.export_package('Press',package);store.import_package(package,'Imported')
            copied=LocalLibrary(store.database_path('Imported'));copied.install()
            with copied.connection() as conn:before=[dict(r) for r in conn.execute('SELECT * FROM press_articles')]
            self.assertFalse(copied.generate_local(stale))
            with copied.connection() as conn:self.assertEqual([dict(r) for r in conn.execute('SELECT * FROM press_articles')],before)
            self.assertEqual(copied.read(1,1),article)
            self.assertEqual(copied.source(season=1,week=1)['engine_id'],'local-template')

    def test_old_cloud_success_readable_unfinished_jobs_are_untouched(self):
        from football_simulator.press.library import activate as legacy_activate
        legacy_activate('Press',features=True);self.advance(19)
        weekly=LocalLibrary(store.database_path('Press'))
        # Store a readable article through the old record shape, without an API.
        material=repository.load_week('Press',1,1)
        article,_=render(material['packet'],freeze_plan(material['packet'],material['sources']))
        with weekly.connection(write=True) as conn:
            conn.execute('''INSERT INTO press_articles(season,week,world_id,report_id,packet_sha,source_sha,status,article_json,article_sha,created_at)
                VALUES(1,1,?,?,?,?, 'ready',?,?, 'legacy')''',(material['packet']['meta']['world_id'],material['packet']['meta']['report_id'],
                repository.digest(material['packet']),repository.digest(material['sources']),repository.canonical(article),repository.digest(article)))
            feature_before=[dict(r) for r in conn.execute('SELECT * FROM press_features')]
        with offline_guards():
            local=LocalFeatureLibrary(weekly.path);local.install();local.recover()
            self.assertIsNone(local.next_local())
            self.assertEqual(local.read(1,1),article)
            self.assertEqual(local.source(season=1,week=1)['engine_id'],'deepseek')
            with local.connection() as conn:self.assertEqual([dict(r) for r in conn.execute('SELECT * FROM press_features')],feature_before)
            for row in feature_before:
                with self.assertRaises(PressError):local.reserve_feature(row['report_id'],retry=True)
            rid=feature_before[0]['report_id']
            for state in ('queued','generating','paused','failed'):
                with self.subTest(legacy_state=state):
                    with local.connection(write=True) as conn:conn.execute('UPDATE press_features SET status=? WHERE report_id=?',(state,rid))
                    local.recover();self.assertIsNone(local.next_local())
                    with local.connection() as conn:self.assertEqual(conn.execute('SELECT status FROM press_features WHERE report_id=?',(rid,)).fetchone()[0],state)


class LocalEditorialContextTests(unittest.TestCase):
    def test_upset_requires_two_axes_and_conflicts_prevent_label(self):
        from football_simulator.press.expectations import compare
        a={'team':'A','previous_overall_rank':14,'total_market_value':50,
           'current_league':{'competition':'premier','rank':15,'points':12,'played':10}}
        b={'team':'B','previous_overall_rank':2,'total_market_value':100,
           'current_league':{'competition':'premier','rank':2,'points':25,'played':10}}
        self.assertTrue(compare(a,b)['is_upset'])
        missing={**a,'total_market_value':None,'current_league':None}
        self.assertFalse(compare(missing,b)['is_upset'])
        conflict={**a,'total_market_value':200}
        self.assertFalse(compare(conflict,b)['is_upset'])
        self.assertIn('value',compare(conflict,b)['conflicting_axes'])

    def test_heritage_uses_official_honors_recent_three_and_ties(self):
        from football_simulator.press.team_background import backgrounds
        teams={t:{} for t in 'ABCD'}
        totals=[{'A':1000,'B':0,'C':200,'D':0}, {'A':0,'B':0,'C':0,'D':0},
                {'A':0,'B':0,'C':0,'D':0}, {'A':0,'B':100,'C':200,'D':100}]
        world={'season_number':5,'season':{},'teams':teams,'archives':[
            {'season_number':i,'honor_totals':r} for i,r in enumerate(totals,1)]}
        result=backgrounds(world)
        self.assertEqual(result['A']['label'],'处于低谷的传统强队')
        # Expand the cohort so ties on the quartile boundary receive the same label.
        for t in 'EFGH':
            world['teams'][t]={}
            for a in world['archives']:a['honor_totals'][t]=0
        result=backgrounds(world)
        self.assertEqual(result['B']['label'],result['D']['label'])
        self.assertEqual(result['B']['label'],'新锐')
        self.assertEqual(result['C']['label'],'传统强队')
        self.assertEqual(result['A']['recent_seasons'],[2,3,4])
        world['archives']=world['archives'][-3:]
        self.assertEqual(backgrounds(world),{}) # missing history is unknown

    def test_reign_low_pot_qualification_combines_pot_with_pre_phase_evidence(self):
        from types import SimpleNamespace
        from football_simulator.press.expectations import group_surprises
        ids=['T'+str(n) for n in range(1,17)]
        profiles={t:{'team':t,'previous_overall_rank':n,'total_market_value':100,
                     'current_league':{'competition':'premier','played':4,'points':8,'rank':n}} for n,t in enumerate(ids,1)}
        profiles['T9']['total_market_value']=50
        source={'expectation_history':[{'season':3,'before_week':5,'teams':profiles}]}
        ctx=SimpleNamespace(source=source,season=3,team=lambda t:t)
        state={'participants':ids,'group_standings':{'A':[
            {'team_id':'T9','wins':3},{'team_id':'T1','wins':3},{'team_id':'T5','wins':1},{'team_id':'T13','wins':0}]}}
        before=deepcopy(state)
        rows=group_surprises(ctx,state,[{'week':5}]);self.assertEqual(rows[0]['pot'],3)
        self.assertTrue(rows[0]['is_dark_horse']);self.assertTrue(rows[0]['comparisons'][0]['is_upset'])
        self.assertEqual(state,before)
        ctx.source={'expectation_history':[]}
        self.assertFalse(group_surprises(ctx,state,[{'week':5}])[0]['is_dark_horse'])

    def test_halfway_second_division_has_no_relegation_exit(self):
        from football_simulator.press.local_templates.phase import render as phase
        packet={'meta':{'report_id':'phase:test'}}
        plan={'fact_ids':['feature:test'],'content':{'competition':'Second Division','stage':'halfway',
              'halfway_table':[{'team':'A','rank':1,'points':30}]}}
        article,_=phase(packet,plan)
        text=article['summary']+''.join(b['text'] for sec in article['sections'] for b in sec['blocks'])
        self.assertIn('升级资格',text);self.assertNotIn('降级',text)

    def test_top20_history_count_and_transferred_title_are_separate(self):
        from football_simulator.press.local_templates.top20 import player_paragraph,selection
        card={'player':'Player','position':'FW','rank':4,'previous_rank':7,'appearances':30,'goals':20,'assists':3,
              'selection_history':{'total_selections':4,'consecutive_selections':4,'missing_seasons':[],
                                   'previous_best_rank':5,'prior_number_one_seasons':[]},
              'team_titles':[],'teams_season_results':[{'team':'Former Club','season_results':[{'competition':'Apex Cup','finish':'冠军'}]}]}
        text=player_paragraph(card)
        for expected in ('累计入选4次','连续4届','上升3位','此前最高为第5名','Former Club'):self.assertIn(expected,text)
        self.assertNotIn('实际随队获得',text)
        unknown={**card,'selection_history':{'total_selections':None,'consecutive_selections':None,'missing_seasons':[2]}}
        self.assertNotIn('累计入选',selection(unknown))

    def test_draft_emphasizes_official_top20_and_team_honors_without_ability(self):
        from football_simulator.press.local_templates.draft import render as draft
        packet={'meta':{'report_id':'draft:test','season':4}}
        content={'picks':[],'previous_two_cohorts':[{'player':'Rookie','draft_season':2,'actual_pick':3,'draft_team':'A',
            'ability_at_selection':70,'ability_at_report':99,'current_registered_team':'B','by_season':[
             {'season':3,'position':'FW','appearances':20,'goals':10,'top20_rank':8,'top20_known':True,
              'team_records':[{'team':'A','championship_count':2,'honor_points':180,'team_championships':['Reign Cup'],
                               'player_roster_titles':[]}]}]}]}
        article,_=draft(packet,{'content':content,'fact_ids':['feature:test']})
        text=''.join(b['text'] for s in article['sections'] for b in s['blocks'])
        for expected in ('Top20排名第8','全季2冠','荣誉分180','Rookie'):self.assertIn(expected,text)
        self.assertNotIn('99',text);self.assertNotIn('能力',text)


class SixLocalReportTests(PressSaveCase):
    def test_all_six_engines_generate_without_transport_keychain_and_archive_sources(self):
        from football_simulator.game import rules
        self.service.edition_archives=True;self.service.honor_profile=rules.HONOR_PROFILE
        self.service.prepare_save('Press')
        lib=activate('Press');self.service.set_mode('Press','auto')
        with offline_guards():
            self.advance(49)
            kinds={}
            for row in lib.feature_rows():kinds.setdefault(row['kind'],row)
            self.assertEqual(set(kinds),{'phase','competition','transfer','draft','top20'})
            weekly=LocalLibrary(lib.path);self.assertTrue(weekly.generate_local(weekly.reserve(1,1)))
            for kind in ('phase','competition','transfer','draft','top20'):
                with self.subTest(kind=kind):
                    row=kinds[kind];job=lib.reserve_feature(row['report_id'])
                    self.assertTrue(lib.generate_local(job))
                    self.assertIsNone(lib.reserve_feature(row['report_id']))
                    self.assertEqual(lib.source(report_id=row['report_id'])['engine_id'],'local-template')
            with lib.connection() as conn:
                empty=conn.execute("SELECT COUNT(*) FROM press_weeks WHERE json_extract(packet_json,'$.meta.input_validity')='no_matches'").fetchone()[0]
                fabricated=conn.execute("SELECT COUNT(*) FROM press_weeks w JOIN press_articles a USING(season,week) WHERE json_extract(w.packet_json,'$.meta.input_validity')='no_matches'").fetchone()[0]
            self.assertGreater(empty,0);self.assertEqual(fabricated,0)
            package=Path(self.tmp.name)/'all-six.fsarchive';store.export_package('Press',package)
            store.import_package(package,'SixImported')
            copied=LocalFeatureLibrary(store.database_path('SixImported'));copied.install()
            for row in kinds.values():
                self.assertEqual(copied.read_feature(row['report_id'],mark_read=False),lib.read_feature(row['report_id'],mark_read=False))
                self.assertEqual(copied.source(report_id=row['report_id']),lib.source(report_id=row['report_id']))
            self.assertEqual(copied.read(1,1,mark_read=False),lib.read(1,1,mark_read=False))
