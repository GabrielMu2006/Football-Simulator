"""Small, lossless writing view of frozen facts, with explicit comparisons.

Proof/source records stay in the local frozen package. They are validated before
this view is made, but repeating hundreds of source keys in prose input adds no
sporting information. No game state or stored fact is changed here.
"""
from __future__ import annotations

from copy import deepcopy


VERSION = 'press.writing/1'


def score(data):
    return {side: int(data['regulation'][side]) + int((data.get('extra_time') or {}).get(side, 0))
            for side in ('home', 'away')}


def match_order(fact):
    return (fact['time']['season'], fact['time']['week'],
            {'premier': 0, 'second': 1, 'playoff': 2}.get(fact['competition_id'], 3), fact['data']['match_id'])


def result_for(fact, team):
    points = score(fact['data'])
    gf, ga = (points['home'], points['away']) if team == fact['data']['home_team_id'] else (points['away'], points['home'])
    return 'W' if gf > ga else 'L' if gf < ga else 'D'


def _goal_notes(data):
    field = score(data)
    events = data.get('goal_events', [])
    counted = {side: sum(e['team_id'] == data[side + '_team_id'] for e in events) for side in ('home', 'away')}
    complete = counted == field
    notes = {'field_score_excluding_shootout': field, 'goal_timeline_complete': complete}
    if complete:
        winner = data['home_team_id'] if field['home'] > field['away'] else data['away_team_id'] if field['away'] > field['home'] else None
        # The game stores integer match minutes; avoid inventing stoppage time.
        notes['recorded_goal_minutes'] = {
            'minutes_1_to_45': [e['minute'] for e in events if 1 <= e['minute'] <= 45],
            'minutes_46_to_90': [e['minute'] for e in events if 46 <= e['minute'] <= 90],
            'minutes_over_90': [e['minute'] for e in events if e['minute'] > 90],
        }
        home, away = 0, 0
        timeline = []
        for event in events:
            home += int(event['team_id'] == data['home_team_id'])
            away += int(event['team_id'] == data['away_team_id'])
            timeline.append({'minute': event['minute'], 'scoring_team_id': event['team_id'],
                             'player_id': event['player_id'], 'score_after_event': {'home': home, 'away': away}})
        notes['score_progression_in_recorded_order'] = timeline
        if winner:
            behind, spells = False, 0
            for event in timeline:
                points = event['score_after_event']
                now_behind = (points['home'] - points['away']) * (1 if winner == data['home_team_id'] else -1) < 0
                spells += int(now_behind and not behind)
                behind = now_behind
            notes['field_winner_team_id'] = winner
            notes['field_winner_trailing_spells'] = spells
    return notes


def _lead_notes(facts, leader):
    rows = [f for f in facts if f['kind'] == 'table_row' and f['competition_id'] == leader['competition_id']
            and f['data']['timing'] == 'end_of_week']
    before = {f['data'].get('week_comparison', {}).get('before_rank'): f for f in rows
              if f['data'].get('week_comparison', {}).get('before_rank') is not None}
    after = {f['data']['rank']: f for f in rows}
    if 1 not in after or 2 not in after:
        return None

    def entry(first, second, old=False):
        def points(f):
            return f['data']['week_comparison']['before_points'] if old else f['data']['points']
        return {'leader_team_id': first['data']['team_id'], 'runner_up_team_id': second['data']['team_id'],
                'leader_points': points(first), 'runner_up_points': points(second),
                'gap_to_runner_up': points(first) - points(second)}

    previous = entry(before[1], before[2], True) if 1 in before and 2 in before else None
    current = entry(after[1], after[2])
    runner = after[2]['data']
    notes = {'before_week': previous, 'after_week': current,
             'runner_up_changed': previous['runner_up_team_id'] != current['runner_up_team_id'] if previous else None,
             'supporting_fact_ids': [f['fact_id'] for f in rows if f in (before.get(1), before.get(2), after[1], after[2])]}
    if previous:
        notes['current_leader_against_current_runner_up'] = {
            'leader_team_id': leader['data']['team_id'], 'opponent_team_id': runner['team_id'],
            'gap_before_week': leader['data']['week_comparison']['before_points'] - runner['week_comparison']['before_points'],
            'gap_after_week': leader['data']['points'] - runner['points'],
        }
    return notes


def _streak_notes(fact, weekly):
    data = fact['data']
    def in_scope(match):
        if data['scope'] == 'competition':
            return match['competition_id'] == data['scope_competition_id']
        if data['scope'] == 'league_all_levels':
            return match['competition_id'] in ('premier', 'second')
        return True
    matches = [f for f in weekly if data['team_id'] in f['team_ids']
               and in_scope(f)]
    matches.sort(key=match_order)
    where = next((i for i, f in enumerate(matches) if f['data']['match_id'] == data['affected_match_id']), None)
    if where is None:
        return None
    later = matches[where + 1:]
    good = {'wins': {'W'}, 'losses': {'L'}, 'unbeaten': {'W', 'D'}, 'winless': {'L', 'D'}}[data['kind']]
    current = data['after']
    for match in later:
        current = current + 1 if result_for(match, data['team_id']) in good else 0
    return {'is_last_match_of_week_in_this_scope': not later,
            'current_count_at_week_end': current,
            'later_match_fact_ids_in_same_scope': [f['fact_id'] for f in later],
            'supporting_fact_ids': [fact['fact_id'], *[f['fact_id'] for f in later]]}


def _tie_notes(fact, facts):
    data = fact['data']
    by_match = {f['data']['match_id']: f for f in facts if f['kind'] == 'match_result'}
    legs = [by_match[mid] for mid in data['leg_match_ids'] if mid in by_match]
    notes = {'leg_field_scores': [{'match_id': f['data']['match_id'],
             'home_team_id': f['data']['home_team_id'], 'away_team_id': f['data']['away_team_id'],
             **score(f['data'])} for f in legs], 'supporting_fact_ids': [f['fact_id'] for f in legs]}
    winner = data['advancing_team_id']
    if len(legs) == 2 and winner and all(_goal_notes(f['data'])['goal_timeline_complete'] for f in legs):
        total = 0
        first = legs[0]['data']; points = score(first)
        total += (points['home'] - points['away']) * (1 if first['home_team_id'] == winner else -1)
        trailing = total < 0
        for event in legs[1]['data']['goal_events']:
            total += 1 if event['team_id'] == winner else -1
            trailing |= total < 0
        notes['advancing_team_trailing_on_aggregate_during_second_leg'] = trailing
    return notes


def writing_view(packet):
    facts = packet['facts']
    weekly = [f for f in facts if f['kind'] == 'match_result' and f['time'] == packet['meta']['cutoff']]
    result = {key: deepcopy(packet[key]) for key in ('meta', 'entities', 'week_summary', 'editorial')}
    result['writing_view_version'] = VERSION
    result['facts'] = []
    for fact in facts:
        item = {key: deepcopy(fact[key]) for key in ('fact_id', 'kind', 'competition_id', 'data')}
        if fact['time'] != packet['meta']['cutoff']:
            item['time'] = deepcopy(fact['time'])
        notes = None
        if fact['kind'] == 'match_result':
            notes = _goal_notes(fact['data'])
        elif fact['kind'] == 'table_row' and fact['data'].get('lead_context') is not None:
            notes = _lead_notes(facts, fact)
        elif fact['kind'] == 'streak':
            notes = _streak_notes(fact, weekly)
        elif fact['kind'] == 'tie_result':
            notes = _tie_notes(fact, facts)
        if notes:
            item['reading_notes'] = notes
        result['facts'].append(item)
    return result
