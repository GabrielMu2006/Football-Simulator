"""Editorial priorities and truthful fallbacks for the fifth local template."""
import unittest
from unittest.mock import patch

from football_simulator.press.features import FeatureContext
from football_simulator.press.planning import select_candidates
from football_simulator.press.local_templates.engine import freeze_plan,render
from football_simulator.press.local_templates.narrative import best_highlight,stage_text


def packet(kind,content):
    return {'meta':{'report_id':'revision5:'+kind,'report_type':kind,'season':4,'source_sha256':'source'},
            'facts':[{'fact_id':'feature:'+kind,'data':content}]}


def text(article):
    return article['summary']+'\n'+'\n'.join(b['text'] for s in article['sections'] for b in s['blocks'])


class NarrativeRevisionFiveTests(unittest.TestCase):
    def test_weekly_groups_ordinary_qualifications_without_losing_required_facts(self):
        def item(key,priority,mandatory,competition):
            return {'key':key,'priority':priority,'mandatory':mandatory,'kind':'match_story','angle':'story',
                    'content':{'matches':[{'competition':competition,'fixture':key}]},'fact_ids':[key]}
        rows=[item('q1',90,True,'Cup'),item('q2',90,True,'Cup'),item('q3',90,True,'Cup'),
              item('champion',230,True,'Another Cup'),item('race',135,False,'Premier Division')]
        chosen=select_candidates(rows,local_extra_story=True)
        required={f for s in chosen if s['mandatory'] for f in s['fact_ids']}
        self.assertEqual(required,{'q1','q2','q3','champion'})
        self.assertIn('race',{s['key'] for s in chosen})
        roundup=next(s for s in chosen if s['kind']=='qualification_roundup')
        self.assertEqual({g['fixture'] for g in roundup['content']['matches']},{'q1','q2','q3'})
        self.assertNotIn('race',{s['key'] for s in select_candidates(rows)})

    def test_mandatory_results_from_different_cups_leave_room_for_major_race(self):
        rows=[{'key':str(i),'priority':90,'mandatory':True,'kind':'match_story','angle':'story',
               'content':{'matches':[{'competition':'Cup'+str(i)}]},'fact_ids':[str(i)]} for i in range(4)]
        rows.append({'key':'race','priority':135,'mandatory':False,'kind':'match_story','angle':'race',
                     'content':{'matches':[{'competition':'Premier Division'}]},'fact_ids':['race']})
        chosen=select_candidates(rows,local_extra_story=True)
        self.assertEqual(len(chosen),5);self.assertEqual({f for s in chosen if s['mandatory'] for f in s['fact_ids']},set('0123'))
        self.assertEqual(chosen[0]['key'],'race')

    def test_final_or_official_qualification_is_selected_from_actual_appearances(self):
        league={'match':{'stage':'联赛'},'goals':5,'assists':1}
        final={'match':{'stage':'总决赛'},'goals':3,'assists':1}
        self.assertIs(best_highlight([league,final]),final)
        qualifying={'match':{'stage':'小组赛第6轮'},'goals':1,'qualification_secured':True}
        self.assertIs(best_highlight([league,qualifying]),qualifying)
        self.assertEqual(stage_text('group_4'),'小组赛第4轮')

    def test_qualification_requires_decided_tie_and_both_legs(self):
        ctx=FeatureContext.__new__(FeatureContext);ctx.season=4
        ctx.world={'edition_archives':{},'season':{'cups':{'starpath':{'quarterfinals':[
            {'teams':['A','B'],'legs':['one','two'],'winner':'A'}]}}}}
        ctx.matches={m:{'season_number':4,'competition':'starpath'} for m in ('one','two')}
        self.assertEqual(ctx.qualification_matches,{'two':{'A'}})
        ctx.__dict__.pop('qualification_matches');ctx.matches.pop('two')
        self.assertEqual(ctx.qualification_matches,{})

    def test_legacy_competition_uses_recorded_final_and_partial_totals(self):
        g={'stage':'决赛','home':'Runner','away':'Champion','score_after_extra_time_home_away':{'home':0,'away':5},'stadium':'Actual Ground'}
        d={'competition':'Destiny Cup','champion':'Champion','edition':{'game_season':4},
           'champion_route':[g],'champion_campaign':{'matches':5,'W':5,'goals_for':20,'goals_against':3}}
        p=packet('competition',d);a,_=render(p,freeze_plan(p));body=text(a)
        self.assertIn('决赛，Runner以0–5不敌Champion',body)
        self.assertIn('5场比赛中赢下5场',body);self.assertIn('Actual Ground',body)
        self.assertNotIn('0平0负',body)

    def test_runner_up_rounds_follow_real_week_not_bracket_order(self):
        def game(stage,week,shootout=False):
            value={'stage':stage,'week':week,'home':'Runner','away':'Opponent',
                   'score_after_extra_time_home_away':{'home':1,'away':1}}
            if shootout:value.update(shootout_home_away={'home':4,'away':3},shootout_winner='Runner')
            return value
        final={'stage':'决赛','home':'Runner','away':'Champion','score_after_extra_time_home_away':{'home':0,'away':1}}
        semifinal=game('半决赛',40);opening=game('32强',20,True)
        d={'competition':'Destiny Cup','champion':'Champion','edition':{'game_season':4},
           'champion_route':[final],'champion_campaign':{},'bracket':[
               {'stage':'半决赛','teams':['Runner','Opponent'],'matches':[semifinal]},
               {'stage':'32强','teams':['Runner','Opponent'],'matches':[opening]}]}
        p=packet('competition',d);body=text(render(p,freeze_plan(p))[0])
        self.assertLess(body.index('32强，Runner'),body.index('半决赛，Runner'))

    def test_legacy_draft_team_finish_and_multi_club_appearances(self):
        d={'picks':[{'actual_pick':1,'team':'A','player':'Rookie','position':'FW','team_season_finish':'A上季排名第20。'}],
           'previous_two_cohorts':[{'player':'Old Rookie','draft_season':2,'actual_pick':1,'draft_team':'A',
              'by_season':[{'season':3,'position':'FW','appearances':2,'goals':2,'assists':0,
                 'actual_teams':[{'team':'A','appearances':1,'goals':1,'assists':0},{'team':'B','appearances':1,'goals':1,'assists':0}]}]}]}
        p=packet('draft',d);a,_=render(p,freeze_plan(p));body=text(a)
        self.assertIn('A上季排名第20',body);self.assertIn('曾代表A、B出场',body)
        self.assertIn('为A出场1次',body);self.assertIn('为B出场1次',body)

    def test_standout_is_based_on_frozen_marker(self):
        d={'window':'winter','completed_trades':1,'focus_trades':[{'week':25,'moves':[
           {'player':'Standout','from_team':'A','to_team':'B','current_season_standout':True,'position':'FW',
            'appearances':10,'goals':9,'assists':0,'actual_teams':[{'team':'A'}]},
           {'player':'Other','from_team':'B','to_team':'A','position':'FW','appearances':10,'goals':1,'assists':0,'actual_teams':[{'team':'B'}]}]}]}
        p=packet('transfer',d);body=text(render(p,freeze_plan(p))[0])
        self.assertIn('Standout本季为A出场10次，打进9球，表现抢眼',body)
        self.assertEqual(body.count('表现抢眼'),1)

    def test_missing_legacy_top20_history_does_not_invent_first_number_one(self):
        from football_simulator.press.local_templates.top20 import player_paragraph
        card={'player':'Player','position':'FW','rank':1,'previous_rank':3,'appearances':4,'goals':3,'assists':1}
        body=player_paragraph(card)
        self.assertNotIn('首次登顶',body);self.assertNotIn('首次入选',body)
        card['selection_history']={'total_selections':2,'consecutive_selections':2,'missing_seasons':[],
                                   'prior_number_one_seasons':[]}
        self.assertIn('首次',player_paragraph(card))

    def test_frozen_v4_uses_its_own_renderer(self):
        p=packet('transfer',{'window':'summer','completed_trades':0,'focus_trades':[]})
        plan=freeze_plan(p,version='local-news/4');expected=render(p,plan)
        with patch('football_simulator.press.local_templates.transfer.render',side_effect=AssertionError('wrong version')):
            self.assertEqual(render(p,plan),expected)
