"""Necessary writing-adapter checks; no prose regex rules or network calls."""
import json
from pathlib import Path
import unittest

from football_simulator.press.briefing import writing_view
from football_simulator.press.editorial import request_preview
from football_simulator.press.repository import canonical


def packet(facts):
    return {'meta': {'cutoff': {'season': 3, 'week': 18}}, 'entities': {},
            'week_summary': {}, 'editorial': {}, 'facts': facts}


def fact(fid, kind, data, *, comp='premier', teams=()):
    return {'fact_id': fid, 'kind': kind, 'competition_id': comp,
            'time': {'season': 3, 'week': 18}, 'team_ids': list(teams), 'player_ids': [],
            'match_ids': [], 'sources': [{'key': 'local-proof'}], 'data': data}


def match(mid, h, a, *, comp='premier', goals=(), shootout=None):
    return fact('match:' + mid, 'match_result', {'match_id': mid, 'home_team_id': 'A', 'away_team_id': 'B',
                'regulation': {'home': h, 'away': a}, 'extra_time': None, 'shootout': shootout,
                'goal_events': list(goals)}, comp=comp, teams=('A', 'B'))


class BriefingTests(unittest.TestCase):
    def test_compact_view_keeps_all_facts_and_original_packet_immutable(self):
        root = Path(__file__).parent / 'fixtures_press/second_weekly'
        original = json.loads((root / 'weekly.json').read_text())
        sources = json.loads((root / 'sources.json').read_text())
        before = canonical(original), canonical(sources)
        view = writing_view(original)
        self.assertEqual({f['fact_id'] for f in original['facts']}, {f['fact_id'] for f in view['facts']})
        self.assertEqual([f['data'] for f in original['facts']], [f['data'] for f in view['facts']])
        self.assertTrue(all('sources' not in f for f in view['facts']))
        preview = request_preview(original, sources)
        self.assertEqual(preview['prompt_versions'], ['editorial-system-draft-5', 'weekly-user-draft-5'])
        self.assertLess(preview['writing_view']['bytes'], len(json.dumps(original, ensure_ascii=False).encode()))
        self.assertEqual((canonical(original), canonical(sources)), before)

    def test_gap_tracks_replacement_runner_up_and_same_opponent_separately(self):
        def row(team, before_rank, before_points, after_rank, points):
            data = {'team_id': team, 'timing': 'end_of_week', 'rank': after_rank, 'points': points,
                    'week_comparison': {'before_rank': before_rank, 'before_points': before_points}}
            if after_rank == 1:
                data['lead_context'] = {'before_gap': 2, 'after_gap': 3}
            return fact('table:' + team, 'table_row', data)
        p = packet([row('A', 1, 21, 1, 24), row('B', 2, 19, 4, 19), row('C', 5, 18, 2, 21)])
        notes = writing_view(p)['facts'][0]['reading_notes']
        self.assertEqual(notes['before_week']['runner_up_team_id'], 'B')
        self.assertEqual(notes['after_week']['runner_up_team_id'], 'C')
        self.assertEqual((notes['before_week']['gap_to_runner_up'], notes['after_week']['gap_to_runner_up']), (2, 3))
        direct = notes['current_leader_against_current_runner_up']
        self.assertEqual((direct['gap_before_week'], direct['gap_after_week']), (3, 3))

    def test_goal_minutes_and_score_progression_are_explicit(self):
        game = match('g', 2, 0, goals=[{'minute': n, 'team_id': 'A', 'player_id': None} for n in (4, 23)])
        notes = writing_view(packet([game]))['facts'][0]['reading_notes']
        self.assertEqual(notes['recorded_goal_minutes']['minutes_1_to_45'], [4, 23])
        self.assertEqual(notes['recorded_goal_minutes']['minutes_46_to_90'], [])
        self.assertEqual([e['score_after_event'] for e in notes['score_progression_in_recorded_order']], [{'home': 1, 'away': 0}, {'home': 2, 'away': 0}])
        first = match('first', 1, 1, goals=[{'minute': 4, 'team_id': 'A', 'player_id': None}, {'minute': 23, 'team_id': 'B', 'player_id': None}])
        second = match('second', 2, 1, goals=[{'minute': n, 'team_id': t, 'player_id': None} for n, t in ((10, 'B'), (29, 'A'), (89, 'A'))])
        tie = fact('tie', 'tie_result', {'leg_match_ids': ['first', 'second'], 'advancing_team_id': 'A'})
        tie_notes = writing_view(packet([first, second, tie]))['facts'][2]['reading_notes']
        self.assertTrue(tie_notes['advancing_team_trailing_on_aggregate_during_second_leg'])
        self.assertEqual(writing_view(packet([second]))['facts'][0]['reading_notes']['field_winner_trailing_spells'], 1)

    def test_later_cup_shootout_win_does_not_extend_field_wins(self):
        league = match('league', 3, 0)
        cup = match('cup', 1, 1, comp='starpath', shootout={'home': 5, 'away': 4})
        def streak(scope):
            return fact('streak:' + scope, 'streak', {'team_id': 'A', 'kind': 'wins',
                        'scope': scope, 'scope_competition_id': 'premier' if scope == 'competition' else None,
                        'affected_match_id': 'league', 'before': 4, 'after': 5}, teams=('A',))
        swiss = fact('swiss:A', 'swiss_record', {'team_id': 'A', 'wins': 3, 'losses': 0,
                     'basis': 'swiss_ties_including_shootout_wins'}, comp='starpath')
        view = writing_view(packet([league, cup, streak('all_official'), streak('league_all_levels'), streak('competition'), swiss]))
        self.assertEqual(view['facts'][2]['reading_notes']['current_count_at_week_end'], 0)
        self.assertEqual(view['facts'][3]['reading_notes']['current_count_at_week_end'], 5)
        self.assertEqual(view['facts'][1]['reading_notes']['field_score_excluding_shootout'], {'home': 1, 'away': 1})
        self.assertEqual(view['facts'][4]['reading_notes']['current_count_at_week_end'], 5)
        self.assertEqual(view['facts'][5]['data']['wins'], 3)
