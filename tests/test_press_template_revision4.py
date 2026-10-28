"""Changed article content and frozen-version boundaries, without a provider."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from football_simulator.press.local_templates.engine import freeze_plan,render


def packet(kind,content):
    return {'meta':{'report_id':'revision4:'+kind,'report_type':kind,'season':3,'source_sha256':'source'},
            'facts':[{'fact_id':'feature:'+kind,'data':content}]}


def body(article):
    return '\n'.join(b['text'] for s in article['sections'] for b in s['blocks'])


class RevisionFourTests(unittest.TestCase):
    def test_high_pot_exits_are_in_phase_narrative_even_without_upset_evidence(self):
        content={'competition':'Reign Cup','stage':'groups','groups':{'C':[
            {'team':'Second','rank':1,'points':13},{'team':'Third','rank':2,'points':10},
            {'team':'Fourth','rank':3,'points':7},{'team':'First','rank':4,'points':4}]},
            'draw_pots':[{'team':t,'pot':p} for p,t in enumerate(('First','Second','Third','Fourth'),1)],
            'qualified':['Second','Third'],'eliminated':['Fourth','First']}
        p=packet('phase',content);before=deepcopy(p);a,_=render(p,freeze_plan(p))
        narrative=''.join(b['text'] for b in a['sections'][0]['blocks'])
        self.assertIn('第1档球队First以4分位列C组第4名，未能晋级',narrative)
        self.assertNotIn('爆冷',narrative);self.assertNotIn('黑马',narrative)
        self.assertEqual(before,p)
        p['facts'][0]['data']['draw_pots']=[]
        self.assertNotIn('第1档球队',body(render(p,freeze_plan(p))[0]))

    def test_pot_exit_and_surprise_evidence_are_not_repeated(self):
        comparison={'winner':'Third','opponent':'First','is_upset':True,'adverse_axes':['previous','value']}
        content={'competition':'Reign Cup','stage':'groups','groups':{'C':[
            {'team':'Third','rank':1,'points':10},{'team':'First','rank':3,'points':4}]},
            'draw_pots':[{'team':'First','pot':1},{'team':'Third','pot':3}],
            'qualified':['Third'],'eliminated':['First'],
            'group_surprises':[{'team':'Third','group':'C','pot':3,'is_dark_horse':True,'comparisons':[comparison]}]}
        p=packet('phase',content);text=body(render(p,freeze_plan(p))[0])
        self.assertIn('上季联赛排名不及First',text)
        self.assertIn('第1档球队First以4分位列C组第3名，未能晋级',text)
        self.assertNotIn('First则未能过关',text)

    def test_champion_route_keeps_actual_venues_and_distinct_shootout_score(self):
        game={'competition':'Destiny Cup','stage':'八强','home':'A','away':'B',
              'score_after_extra_time_home_away':{'home':0,'away':0},'regulation_home_away':{'home':0,'away':0},
              'extra_time_played':True,'shootout_home_away':{'home':5,'away':3},'shootout_winner':'A',
              'stadium':'实际球场（Recorded Ground）','venue':'中立场地'}
        p=packet('competition',{'competition':'Destiny Cup','champion':'A','edition':{'game_season':3},
                 'champion_campaign':{},'champion_route':[game]})
        a,_=render(p,freeze_plan(p));route=next(s for s in a['sections'] if s['heading']=='夺冠路线')
        text=''.join(b['text'] for b in route['blocks'])
        for expected in ('实际球场（Recorded Ground）','0–0','点球大战5–3'):self.assertIn(expected,text)
        self.assertNotIn('5–3击败',text)
        old,_=render(p,freeze_plan(p,version='local-news/3'))
        self.assertNotIn('Recorded Ground',body(old))
        del game['stadium'];self.assertNotIn('Recorded Ground',body(render(p,freeze_plan(p))[0]))

    def test_transfer_omits_roster_counts_but_frozen_v3_keeps_original_article(self):
        content={'window':'summer','completed_trades':0,'focus_trades':[],'other_trades':[],
                 'window_team_rosters':[{'team':'A','window_open_real_counts':{'FW':2},'window_closed_real_counts':{'FW':1}}]}
        p=packet('transfer',content);old_plan=freeze_plan(p,version='local-news/3');old=render(p,old_plan)
        self.assertIn('前锋从2人变为1人',body(old[0]))
        with patch('football_simulator.press.local_templates.transfer.render',side_effect=AssertionError('new renderer called')):
            self.assertEqual(render(p,old_plan),old)
        new,_=render(p,freeze_plan(p))
        self.assertNotIn('阵容人数',body(new));self.assertNotIn('前锋从2人变为1人',body(new))
        self.assertNotIn('阵容人数的变化',[s['heading'] for s in new['sections']])
