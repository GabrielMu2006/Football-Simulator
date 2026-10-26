"""Necessary semantics and writer-contract checks; no network or user data."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from football_simulator.press import PressError
from football_simulator.press.context import Context
from football_simulator.press.planning import generation_plan, prepare_story_article
from football_simulator.press.repository import canonical


class PlanningTests(unittest.TestCase):
    def material(self):
        root = Path(__file__).parent / 'fixtures_press/second_weekly'
        return json.loads((root/'weekly.json').read_text()), json.loads((root/'sources.json').read_text())

    def test_league_match_background_uses_round_start_not_processing_order(self):
        packet, sources = self.material(); ctx = Context(packet, sources)
        checked = 0
        for match in ctx.weekly.values():
            if match['competition_id'] not in ('premier', 'second'): continue
            for row in ctx.boundary_rows(match):
                tid = next(t for t in match['team_ids'] if ctx.team(t) == row['team'])
                before = ctx.row(match['competition_id'], tid)['week_comparison']
                self.assertEqual((row['points'], row['played']), (before['before_points'], before['before_played']))
                if before['before_rank'] is not None: self.assertEqual(row['rank'], before['before_rank'])
                self.assertEqual(row['basis'], 'round_start'); checked += 1
        self.assertGreater(checked, 0)

    def test_final_tied_points_mean_champion_and_playoff_qualification_are_decided(self):
        cutoff = {'season': 3, 'week': 42}
        packet = {'meta': {'cutoff': cutoff}, 'editorial': {}, 'week_summary': {}, 'facts': [],
                  'entities': {'teams': {}, 'players': {}, 'stadiums': {}, 'competitions': {'second': {'name_en': 'Second Division'}}}}
        for i in range(1, 21):
            tid = 'T'+str(i); packet['entities']['teams'][tid] = {'name_en': tid}
            data = {'team_id': tid, 'timing': 'end_of_week', 'rank': i, 'played': 38, 'points': 66 if i <= 2 else 55,
                    'season_record': {'goal_diff': 19 if i == 1 else 10}, 'week_comparison': {'before_played': 37}}
            packet['facts'].append({'fact_id': tid, 'kind': 'table_row', 'time': cutoff, 'competition_id': 'second', 'data': data})
        result = Context(packet, {}).league_context('second')
        self.assertTrue(result['league_finished']); self.assertEqual(result['champion'], 'T1')
        self.assertEqual(result['promoted_directly'], ['T1', 'T2'])
        self.assertEqual(result['qualified_for_promotion_playoff'], ['T3','T4','T5','T6'])
        self.assertFalse(result['clinched_early_this_week']); self.assertNotIn('relegation', result['places'])

    def test_author_receives_named_stories_and_headers_are_preserved_without_world_ids(self):
        packet, sources = self.material(); before = canonical(packet), canonical(sources)
        plan = generation_plan(packet, sources); unit = plan['sections'][0]
        sent = json.loads(unit['request']['messages'][1]['content'])
        self.assertNotIn(packet['meta']['world_id'], canonical(sent))
        self.assertNotIn('fact_ids', sent['stories'][0]); self.assertNotIn('suggested_angle', canonical(sent))
        raw = {'title': '编辑型主标题', 'deck': '本周有明确变化。', 'sections': [
            {'heading': '一个故事小标题', 'story_ids': [unit['story_ids'][0]], 'paragraphs': ['完整正文。']}]}
        article = prepare_story_article(raw, packet, plan, unit)
        self.assertEqual(article['title'], raw['title']); self.assertEqual(article['summary'], raw['deck'])
        self.assertEqual(article['sections'][0]['heading'], raw['sections'][0]['heading'])
        raw['sections'][0]['story_ids'] = ['not-provided']
        with self.assertRaises(PressError): prepare_story_article(raw, packet, plan, unit)
        flat = {'title': '水晶玫瑰本周观察', 'deck': '导语。', 'body': '## 第一段\n\n正文甲。\n\n## 第二段\n\n正文乙。'}
        normalized = prepare_story_article(flat, packet, plan, unit)
        self.assertEqual([s['heading'] for s in normalized['sections']], ['第一段', '第二段'])
        self.assertEqual(normalized['title'], 'Crystal Roses本周观察')
        self.assertEqual(flat['title'], '水晶玫瑰本周观察')  # raw return remains intact
        full = {'title':'Mac Allister的表现','deck':'导语。','body':'## 小标题\n\nAlexis Mac Allister与Mac Allister。'}
        from football_simulator.press.planning import name_aliases
        alias_plan=deepcopy(plan)
        alias_plan['editorial_plan']['name_aliases']=name_aliases({'entities':{'teams':{},'competitions':{},'stadiums':{},'players':{'P':{'name':'Alexis Mac Allister'}}}})
        expanded = prepare_story_article(full,packet,alias_plan,unit)
        self.assertEqual(expanded['title'],'Alexis Mac Allister的表现')
        self.assertNotIn('Mac Alexis',expanded['sections'][0]['blocks'][0]['text'])
        alias_plan['editorial_plan']['name_aliases']={'Kingsmeadow Park':'王庭草甸球场（Kingsmeadow Park）',
            '王庭草甸球场':'王庭草甸球场（Kingsmeadow Park）','王庭草甸球场（Kingsmeadow Park）':'王庭草甸球场（Kingsmeadow Park）'}
        stadium=prepare_story_article({'title':'球场','deck':'导语','body':'在Kingsmeadow Park（王庭草甸球场）进行。'},packet,alias_plan,unit)
        self.assertEqual(stadium['sections'][0]['blocks'][0]['text'],'在王庭草甸球场（Kingsmeadow Park）进行。')
        team=prepare_story_article({'title':'Crownfield领先','deck':'导语','body':'Crownfield与Crownfield Athletic。'},packet,plan,unit)
        self.assertEqual(team['title'],'Crownfield Athletic领先')
        self.assertEqual(team['sections'][0]['blocks'][0]['text'],'Crownfield Athletic与Crownfield Athletic。')
        self.assertEqual((canonical(packet), canonical(sources)), before)

    def test_new_assembly_uses_one_lead_and_old_frozen_plans_keep_their_contract(self):
        from football_simulator.press.planning import assemble_story_articles
        packet,sources=self.material();plan=generation_plan(packet,sources)
        fragments=[]
        for n,unit in enumerate(plan['sections'][:2],1):
            article=prepare_story_article({'title':f'标题{n}','deck':f'导语{n}','body':f'正文{n}。'},packet,plan,unit)
            fragments.append((unit['competition_id'],article))
        current=assemble_story_articles(packet,fragments,plan=plan)
        old=assemble_story_articles(packet,fragments,plan={'strategy':plan['strategy']})
        self.assertEqual(current['summary'],'导语1')
        self.assertEqual(old['summary'],'导语1 导语2')
        self.assertEqual(current['sections'],old['sections'])

    def test_author_material_keeps_comeback_but_omits_routine_counting_and_background(self):
        from football_simulator.press.authoring import match_view
        material={'competition':'Premier Division','stage':'league','home':'A','away':'B',
          'field_score_home_away':{'home':3,'away':2},'summary':'A先破门后曾落后，最终逆转；本场扳平1次。',
          'score_flow':{'winner':'A','winner_trailing_spells':1,'equalizers':{'A':1}},
          'streak_events':[{'team':'A','scope':'各项正式赛事','kind':'wins','before_match':2,'after_match':3,'ended':False,
                            'later_match_in_same_scope_this_week':True,'count_at_week_end':0}],
          'league_background':[{'team':'A','league':'Premier Division','rank':8,'points':20}],
          'league_week_end':[{'team':'A','rank':8,'points':23,'goal_difference':2,'week_change':{'before_rank':8,'rank_change':0,'before_points':20}}]}
        before=deepcopy(material);view=match_view(material)
        self.assertIn('A一度落后，最终逆转取胜。',view['result'])
        self.assertNotIn('league_background',view);self.assertNotIn('league_week_end',view)
        self.assertNotIn('streaks',view)
        self.assertNotIn('1次',canonical(view));self.assertNotIn('先失球',canonical(view))
        self.assertEqual(material,before)
