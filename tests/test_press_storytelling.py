"""Small fact-card contract checks; model quality is reviewed from real returns."""
import json
from pathlib import Path
import unittest

from football_simulator.press.repository import canonical
from football_simulator.press.briefing import writing_view
from football_simulator.press.storytelling import cards, compose_sections, fact_line, phase_heading, prepare_fragment, section_preview


class StoryCardTests(unittest.TestCase):
    def test_swiss_round_context_and_champion_history_are_explicit(self):
        cutoff = {'season': 3, 'week': 18}
        p = {'meta': {'cutoff': cutoff}, 'editorial': {'topics': []}, 'facts': [],
             'entities': {'teams': {'A': {'name_en': 'Team A'}}, 'players': {}, 'stadiums': {},
                          'competitions': {'starpath': {'name_en': 'Apex Cup'}}}}
        game = {'fact_id': 'g', 'kind': 'match_result', 'competition_id': 'starpath',
                'time': cutoff, 'team_ids': ['A'], 'player_ids': [], 'match_ids': ['m'],
                'data': {'stage': 'swiss_3', 'match_id': 'm'}}
        record = {'fact_id': 'r', 'kind': 'swiss_record', 'competition_id': 'starpath',
                  'team_ids': ['A'], 'player_ids': [], 'match_ids': [], 'data': {'wins': 2, 'losses': 1}}
        # No selected match needs score expansion in this context-only case.
        p['facts'] = [record]
        from unittest.mock import patch
        with patch('football_simulator.press.storytelling.writing_view', return_value={'facts': []}):
            p['facts'].append(game)
            self.assertIn('整个瑞士阶段尚未结束', cards(p, 'starpath', max_games=0)['stage_context'])
            game['data']['stage'] = 'swiss_5'; record['data'].update(wins=3, losses=2)
            self.assertIn('整个瑞士阶段已结束', cards(p, 'starpath', max_games=0)['stage_context'])
        text = fact_line({'kind': 'record', 'competition_id': 'starpath', 'data': {
            'entity_id': 'A', 'comparison': 'equalled', 'current_achievement': '冠军', 'previous_best': '冠军'}}, p)
        self.assertIn('本届最终成绩为冠军', text)
        self.assertNotIn('提高', text)

    def test_former_club_background_without_competition_is_readable(self):
        p = {'entities': {'teams': {'A': {'name_en': 'Current Club'}, 'B': {'name_en': 'Former Club'}},
                         'players': {'x': {'name': 'Player Name'}}, 'competitions': {}}}
        text = fact_line({'kind': 'former_club', 'competition_id': None, 'data': {
            'player_id': 'x', 'current_team_id': 'A', 'former_team_id': 'B', 'appeared_for_former': True,
            'is_immediate_previous_club': True, 'transfer_effective': {'season': 3, 'week': 27}}}, p)
        self.assertIn('Player Name本场代表Current Club面对老东家Former Club', text)

    def test_section_keeps_required_references_without_mutating_frozen_data(self):
        root = Path(__file__).parent / 'fixtures_press/second_weekly'
        p = json.loads((root / 'weekly.json').read_text())
        s = json.loads((root / 'sources.json').read_text())
        before = canonical(p), canonical(s)
        brief = cards(p, 'premier')
        allowed = {f['fact_id'] for f in brief['verified_statements']}
        for topic in brief['topics']:
            self.assertTrue(set(topic['required_fact_ids']) <= allowed)
        request = section_preview(p, s, 'premier')
        self.assertEqual(request['prompt_versions'], ['weekly-section-draft-6'])
        sent = json.loads(request['request']['messages'][1]['content'])
        wanted_stadiums = {f['data']['stadium_id'] for f in p['facts']
                           if f['fact_id'] in allowed and f['kind'] == 'match_result'}
        self.assertEqual(set(sent['entities']['stadiums']), wanted_stadiums)
        self.assertEqual((canonical(p), canonical(s)), before)

    def test_away_winner_has_one_score_direction_and_all_outcomes_are_available(self):
        p = {'meta': {'cutoff': {'season': 3, 'week': 37}}, 'week_summary': {}, 'editorial': {'topics': []},
             'entities': {'teams': {'A': {'name_en': 'Team A'}, 'B': {'name_en': 'Team B'}},
                          'players': {}, 'stadiums': {}, 'competitions': {'ember': {'name_en': 'Resurgence Cup'}}}, 'facts': []}
        for tid in ('A', 'B'):
            p['facts'].append({'fact_id': 'out:' + tid, 'kind': 'competition_status', 'competition_id': 'ember',
                              'time': p['meta']['cutoff'], 'team_ids': [tid], 'player_ids': [], 'match_ids': [],
                              'data': {'team_id': tid, 'status': 'eliminated', 'next_stage': None}})
        brief = cards(p, 'ember', max_games=0)
        self.assertEqual(brief['all_week_outcomes']['eliminated'], ['Team A', 'Team B'])
        game = {'fact_id': 'match:g', 'kind': 'match_result', 'competition_id': 'ember', 'time': p['meta']['cutoff'],
                'team_ids': ['A', 'B'], 'player_ids': [], 'match_ids': ['g'], 'data': {
                    'match_id': 'g', 'home_team_id': 'A', 'away_team_id': 'B', 'stage': 'lb_round_1',
                    'regulation': {'home': 0, 'away': 3}, 'extra_time': None, 'shootout': None, 'stadium_id': None,
                    'goal_events': [{'minute': n, 'team_id': 'B', 'player_id': None} for n in (4, 23, 75)]}}
        p['facts'].append(game)
        text = fact_line(writing_view(p)['facts'][-1], p)
        self.assertIn('Team B以3–0战胜Team A', text)
        self.assertNotIn('0–3', text)

    def test_two_players_in_one_match_get_their_own_goal_labels(self):
        p = {'entities': {'teams': {'A': {'name_en': 'Team A'}},
                         'players': {'x': {'name': 'First Player'}, 'y': {'name': 'Second Player'}},
                         'competitions': {'premier': {'name_en': 'Premier Division'}}}}
        def line(pid, goals):
            return fact_line({'kind': 'player_match', 'competition_id': 'premier', 'data': {
                'match_id': 'one-game', 'team_id': 'A', 'player_id': pid, 'goals': goals,
                'assists': 0, 'successful_saves': 0, 'successful_defenses': 0}}, p)
        self.assertIn('First Player', line('x', 2))
        self.assertIn('进球 2（梅开二度）', line('x', 2))
        self.assertIn('Second Player', line('y', 3))
        self.assertIn('进球 3（帽子戏法）', line('y', 3))

    def test_composition_uses_actual_margin_and_round_without_rewriting_body(self):
        cutoff = {'season': 3, 'week': 18}
        p = {'meta': {'report_id': 'weekly:test:3:18', 'season': 3, 'completed_week': 18, 'cutoff': cutoff},
             'entities': {'teams': {'A': {'name_en': 'Team A'}}, 'players': {},
                          'competitions': {'premier': {'name_en': 'Premier Division'},
                                           'starpath': {'name_en': 'Apex Cup'}}},
             'facts': [{'fact_id': 'lead', 'kind': 'table_row', 'competition_id': 'premier',
                        'data': {'team_id': 'A', 'points': 41, 'lead_context': {
                            'before_gap': 2, 'after_gap': 2, 'leader_changed': False, 'same_leader_gap_change': 0}}},
                       {'fact_id': 'game', 'kind': 'match_result', 'competition_id': 'starpath',
                        'time': cutoff, 'data': {'stage': 'swiss_3'}}]}
        body = {'kind': 'body', 'label': None, 'text': '对当前次席的分差由4分缩小到2分。', 'fact_ids': ['lead']}
        raw = {'title': 'untrusted title', 'title_fact_ids': ['lead'],
               'summary': '整体领先优势从4分缩小到2分。', 'summary_fact_ids': ['lead'],
               'used_topic_ids': [], 'sections': [{'heading': 'untrusted', 'heading_fact_ids': ['lead'], 'blocks': [body]}]}
        before = canonical(raw)
        result = compose_sections(p, [('premier', raw)])
        self.assertEqual(result['summary'], 'Team A以41分领跑Premier Division，整体领先优势仍为2分。')
        self.assertEqual(result['sections'][0]['blocks'], [body])
        self.assertEqual(phase_heading(p, 'starpath'), 'Apex Cup · 瑞士轮第3轮')
        self.assertEqual(canonical(raw), before)

    def test_fragment_body_survives_missing_disposable_header_references(self):
        root = Path(__file__).parent / 'fixtures_press/second_weekly'
        p = json.loads((root / 'weekly.json').read_text())
        raw = json.loads((root / 'response.json').read_text())
        raw.pop('title_fact_ids')
        expected = [b for section in raw['sections'] for b in section['blocks']]
        prepared = prepare_fragment(raw, p, 'premier')
        self.assertEqual(prepared['sections'][0]['blocks'], expected)
        raw['report_id'] = 'different-save'
        with self.assertRaises(Exception):
            prepare_fragment(raw, p, 'premier')
