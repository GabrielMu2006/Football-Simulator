"""Sporting meanings and lossless recovery of the actual failed returns."""
from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

from football_simulator.press.briefing import _goal_notes
from football_simulator.press.semantics import achievement_context, goal_context
from football_simulator.press import provider
from football_simulator.press.context import Context


class SemanticsTests(unittest.TestCase):
    def test_previous_runner_up_is_a_repeat_final_while_best_semifinal_is_first_final(self):
        for comparison, previous, expected in [('equalled', '亚军', '再次进入决赛'), ('broken', '四强', '首次进入决赛'), ('first', None, '首次进入决赛')]:
            with self.subTest(comparison=comparison):
                result = achievement_context({'comparison': comparison, 'current_achievement': '决赛', 'previous_best': previous}, 'Void Relics', 'Apex Cup')
                self.assertEqual(result['milestone'], expected)
                self.assertIn(expected, result['historical_summary'])
        again = achievement_context({'comparison': 'equalled', 'current_achievement': '冠军', 'previous_best': '冠军'}, 'Club', 'Cup')
        self.assertEqual(again['milestone'], '再次赢得冠军')

    def test_goal_effects_bind_scorer_score_and_extra_time_in_recorded_order(self):
        data = {'home_team_id': 'A', 'away_team_id': 'B', 'regulation': {'home': 3, 'away': 3}, 'extra_time': {'home': 1, 'away': 0},
                'goal_events': [{'minute': minute, 'team_id': team, 'player_id': player} for minute, team, player in [
                    (10,'B','Piotr'),(23,'A','Rabiot'),(43,'A','Benzema'),(51,'B','Alexis'),(59,'A','Benzema'),(89,'B',None),(119,'A','Benzema')]]}
        before = deepcopy(data)
        events = goal_context(data, _goal_notes(data), lambda t: t, lambda p: p)
        self.assertEqual([e['field_effect'] for e in events], ['首开纪录','扳平比分','反超比分','扳平比分','再次取得领先','扳平比分','再次取得领先'])
        self.assertEqual(events[1]['scorer'], 'Rabiot')
        self.assertEqual(events[1]['field_score_before_home_away'], {'home':0,'away':1})
        self.assertEqual(events[1]['field_score_after_home_away'], {'home':1,'away':1})
        self.assertNotIn('scorer', events[5]); self.assertEqual(events[-1]['minute'],119)
        data['goal_events'][1]['minute'] = 10
        same_minute = goal_context(data, _goal_notes(data), lambda t:t, lambda p:p)
        self.assertEqual([e['field_effect'] for e in same_minute[:2]], ['首开纪录','扳平比分'])
        self.assertEqual(before['goal_events'][1]['minute'],23)
        self.assertEqual(goal_context(data, {'goal_timeline_complete':False}, str,str), [])

    def test_actual_failed_returns_recover_without_changing_any_string_token(self):
        root = Path(__file__).parent/'fixtures_press/format_recovery'
        for path in sorted(root.glob('*.json')):
            with self.subTest(case=path.stem):
                response = json.loads(path.read_text()); before = deepcopy(response)
                metadata = provider.response_metadata(response)
                article = provider.parse_article(response,format_receipt=metadata)
                text = response['choices'][0]['message']['content']
                tokens = lambda text: re.findall(r'"(?:[^"\\]|\\.)*"', text)
                self.assertEqual(tokens(text), tokens(json.dumps(article,ensure_ascii=False,separators=(',',':'))))
                self.assertFalse(metadata['article_format_recovery']['content_values_invented'])
                self.assertEqual(response,before)

    def test_assisters_remain_bound_to_their_own_goals_even_at_the_same_minute(self):
        cutoff={'season':3,'week':42}
        events=[{'type':'goal','minute':23,'team_id':'A','player_id':None,'assist_id':'D'},
                {'type':'goal','minute':83,'team_id':'B','player_id':None,'assist_id':None},
                {'type':'goal','minute':83,'team_id':'A','player_id':'N','assist_id':'L'}]
        data={'match_id':'m','stage':'final','home_team_id':'A','away_team_id':'B','regulation':{'home':2,'away':1},
              'extra_time':None,'shootout':None,'stadium_id':None,
              'goal_events':[{k:e[k] for k in ('minute','team_id','player_id')} for e in events]}
        packet={'meta':{'cutoff':cutoff},'week_summary':{},'editorial':{},
                'entities':{'teams':{'A':{'name_en':'A'},'B':{'name_en':'B'}},'players':{p:{'name':n} for p,n in [('D','Douglas Luiz'),('L','Luis Suarez'),('N','Nick Woltemade')]},
                            'stadiums':{},'competitions':{'zenith':{'name_en':'Aeon Cup'}}},
                'facts':[{'fact_id':'match:m','kind':'match_result','competition_id':'zenith','time':cutoff,'team_ids':['A','B'],'match_ids':['m'],'data':data}]}
        source={'records':{'match:m':{'value':{'events':events}}}}
        goals=Context(packet,source).match('m')['goals']
        self.assertEqual(goals[0]['assister'],'Douglas Luiz'); self.assertNotIn('scorer',goals[0])
        self.assertNotIn('assister',goals[1]); self.assertEqual(goals[2]['assister'],'Luis Suarez')
        self.assertEqual(goals[2]['scorer'],'Nick Woltemade')
        self.assertIn('A先取得领先',Context(packet,source).match('m')['summary'])
        self.assertNotIn('逆转',Context(packet,source).match('m')['summary'])

    def test_leader_switch_keeps_each_gap_bound_to_the_correct_leader(self):
        cutoff={'season':3,'week':37}
        rows=[]
        for tid,rank,points,old_rank,old_points in [('R',1,55,2,52),('D',2,53,1,53)]:
            data={'team_id':tid,'rank':rank,'points':points,'played':33,'timing':'end_of_week',
                  'week_comparison':{'before_rank':old_rank,'before_points':old_points,'rank_change':old_rank-rank,'points_change':points-old_points}}
            if tid=='R':data['lead_context']={'before_leader_team_id':'D','leader_changed':True,'before_gap':1,'after_gap':2,'same_leader_gap_change':None}
            rows.append({'fact_id':tid,'kind':'table_row','competition_id':'second','time':cutoff,'data':data})
        packet={'meta':{'cutoff':cutoff},'week_summary':{},'editorial':{},'facts':rows,
                'entities':{'teams':{'R':{'name_en':'Ravenhill'},'D':{'name_en':'Doom Knights'}},'players':{},'stadiums':{},'competitions':{'second':{'name_en':'Second Division'}}}}
        race=Context(packet,{}).league_context('second')
        self.assertIn('周初榜首Doom Knights53分',race['race_summary'])
        self.assertIn('周末Ravenhill55分',race['race_summary'])
        self.assertIn('本周榜首易主',race['race_summary']);self.assertNotIn('lead_change',race)

    def test_author_view_keeps_goal_totals_numeric_and_uses_role_bound_event_sentences(self):
        from football_simulator.press.planning import author_content
        data={'players':[{'player':'Player','goals':3}], 'goals':[{'description':'第4分钟球队首开纪录，助攻者为Player。','assister':'Player'}]}
        view=author_content(data)
        self.assertEqual(view['players'][0]['goals'],3)
        self.assertEqual(view['goals'],['第4分钟球队首开纪录，助攻者为Player。'])
        self.assertIsInstance(data['goals'][0],dict)

    def test_author_view_anchors_aggregate_comeback_to_second_leg(self):
        from football_simulator.press.authoring import author_content
        tie={'team_a':'Chaos Overture','team_b':'Void Relics','aggregate_a_b':[6,7],
             'advancing_team':'Void Relics','advancing_team_was_behind_on_aggregate':True,
             'legs':[{'home':'Void Relics','away':'Chaos Overture','field_score_home_away':{'home':4,'away':3}},
                     {'home':'Chaos Overture','away':'Void Relics','field_score_home_away':{'home':3,'away':3}}]}
        before=deepcopy(tie);facts=author_content(tie)
        self.assertIn('第1回合：Void Relics 4–3 Chaos Overture。',facts)
        self.assertIn('Void Relics在次回合比赛进行中一度总比分落后，之后重新取得总比分优势。',facts)
        self.assertEqual(tie,before)

    def test_two_cups_in_one_week_get_distinct_writing_units_and_evidence(self):
        from football_simulator.press.planning import split_cross_competition_stories
        packet={'entities':{'competitions':{'a':{'name_en':'Aeon Cup'},'b':{'name_en':'Ascendance Cup'}}},
                'facts':[{'fact_id':'fa','competition_id':'a','match_ids':['ma']},
                         {'fact_id':'fb','competition_id':'b','match_ids':['mb']}]}
        editorial={'stories':[{'id':'S1','angle':'双线一周','fact_ids':['fa','fb'],
          'content':{'matches':[{'competition':'Aeon Cup'},{'competition':'Ascendance Cup'}],
                     'phase':{'competition':'Ascendance Cup'},'other_competition_phases':[{'competition':'Aeon Cup'}],
                     'champion_honours':{'team':'Club','consecutive_titles':2}}}]}
        before=deepcopy(packet)
        split_cross_competition_stories(editorial,packet)
        first,second=editorial['stories']
        self.assertEqual(first['fact_ids'],['fb']);self.assertEqual(second['fact_ids'],['fa'])
        self.assertEqual([g['competition'] for g in first['content']['matches']],['Ascendance Cup'])
        self.assertIn('champion_honours',first['content']);self.assertNotIn('champion_honours',second['content'])
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(packet,before)

    def test_incomplete_or_ambiguous_content_still_fails_without_inventing_text(self):
        from football_simulator.press.json_output import decode_article
        invalid = ['{"title":"t","deck":"d","body":"unfinished',
                   '{"title":"a","title":"b","deck":"d","body":"text",}',
                   '{"title":"t","deck":"d","body":"b","extra":"unknown",}',
                   '{"title":"t","deck":"d","body":NaN}',
                   '{"title":"t","deck":"d","body":"b"}<script>extra</script>']
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError): decode_article(text)
        raw = json.loads((Path(__file__).parent/'fixtures_press/format_recovery/H-W22-v6.json').read_text())
        raw['choices'][0]['finish_reason']='length'
        with self.assertRaises(provider.ProviderError): provider.parse_article(raw)
        body='## 标题\n\n0–1与原始引号“原话”。'
        wrapped='```json\n'+json.dumps({'title':'t','deck':'d','body':body},ensure_ascii=False)+'\n```'
        self.assertEqual(decode_article(wrapped)[0]['body'],body)
