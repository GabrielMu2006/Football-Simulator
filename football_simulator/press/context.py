"""Typed writing context derived only from a frozen packet and its evidence.

Legacy exports remain immutable. In particular their unanchored before facts
are not copied into the new author view; tables are reconstructed at the right
boundary using their original matches and the game's own ordering rules.
"""
from __future__ import annotations

import json

from football_simulator.game import rules
from football_simulator.game.calendar import standings_from_matches
from football_simulator.press.briefing import score, writing_view
from football_simulator.press.standings import equivalent_streaks
from football_simulator.press.semantics import achievement_context, goal_context, goal_caption


class Context:
    def __init__(self, packet, sources):
        self.packet, self.book = packet, sources.get('records', {})
        self.facts = {f['fact_id']: f for f in packet['facts']}
        self.view = {f['fact_id']: f for f in writing_view(packet)['facts']}
        self.week = packet['meta']['cutoff']
        self.games = {f['data']['match_id']: f for f in packet['facts'] if f['kind'] == 'match_result'}
        self.weekly = {mid: f for mid, f in self.games.items() if f['time'] == self.week}
        self.tables = {cid: sorted([f for f in packet['facts'] if f['kind'] == 'table_row'
                       and f['competition_id'] == cid and f['data']['timing'] == 'end_of_week'], key=lambda f: f['data']['rank'])
                       for cid in rules.LEAGUE_COMPETITIONS}
        self.equivalents = {}
        for group in equivalent_streaks(packet['facts']):
            for fid in group['equivalent_fact_ids']:
                self.equivalents[fid] = group['preferred_fact_id']

    def team(self, tid):
        return self.packet['entities']['teams'][tid]['name_en']

    def player(self, pid):
        return self.packet['entities']['players'][pid]['name']

    def competition(self, cid):
        return self.packet['entities']['competitions'].get(cid, {}).get('name_en') or rules.display_name(cid)

    def state(self, cid):
        return self.book.get('cup-state:' + cid, {}).get('value', {}).get('state', {})

    def league_of(self, tid):
        return next((cid for cid, rows in self.tables.items() if any(f['data']['team_id'] == tid for f in rows)), None)

    def row(self, cid, tid):
        return next((f['data'] for f in self.tables.get(cid, []) if f['data']['team_id'] == tid), None)

    def table_row(self, cid, data):
        result = {'team': self.team(data['team_id']), 'rank': data['rank'], 'points': data['points'], 'played': data['played']}
        if data.get('season_record'):
            result['goal_difference'] = data['season_record']['goal_diff']
        change = data.get('week_comparison', {})
        if change.get('before_rank') is not None:
            result['week_change'] = {k: change[k] for k in ('before_rank', 'before_points', 'rank_change', 'points_change')}
            result['rank_movement'] = ('上升' if change['rank_change'] > 0 else '下降') + str(abs(change['rank_change'])) + '位' if change['rank_change'] else '名次不变'
            if cid == 'second':
                def zone(rank): return '直接升级区' if rank <= 2 else '附加赛席位区' if rank <= 6 else '前六以外'
                old_zone, new_zone = zone(change['before_rank']), zone(data['rank'])
                result['promotion_zone_change'] = ('本周前后均在' + new_zone) if old_zone == new_zone else ('本周从' + old_zone + '变为' + new_zone)
        return result

    def closed(self, cid):
        rows = self.tables.get(cid, [])
        return len(rows) == 20 and all(f['data']['played'] == 38 for f in rows)

    def league_context(self, cid):
        rows = self.tables[cid]
        ended = self.closed(cid)
        result = {'competition': self.competition(cid), 'league_finished': ended,
                  'rounds_total': 38, 'rounds_completed': min((f['data']['played'] for f in rows), default=0)}
        if not rows:
            return result
        first = rows[0]['data']
        result['leader_and_runner_up'] = [self.table_row(cid, f['data']) for f in rows[:2]]
        if first.get('lead_context'):
            lead = first['lead_context']
            result['leader_changed_this_week'] = lead['leader_changed']
            old_top = sorted([f['data'] for f in rows if f['data'].get('week_comparison', {}).get('before_rank') is not None], key=lambda d:d['week_comparison']['before_rank'])
            leader = self.team(first['team_id'])
            end = f"周末{leader}{first['points']}分排名第一，领先当前次席{self.team(rows[1]['data']['team_id'])}{lead['after_gap']}分。"
            if len(old_top) >= 2:
                old_first, old_second = old_top[:2]
                start = f"周初榜首{self.team(old_first['team_id'])}{old_first['week_comparison']['before_points']}分，领先当时次席{self.team(old_second['team_id'])}{lead['before_gap']}分。"
                result['race_summary'] = start + end + ('本周榜首易主。' if lead['leader_changed'] else '本周榜首未变。')
            else:
                result['race_summary'] = end
            old = first.get('week_comparison', {})
            result['leader_week_change'] = {'points_before': old.get('before_points'), 'points_after': first['points']}
        if cid == 'second':
            result['places'] = {'direct_promotion': [1, 2], 'promotion_playoff': [3, 4, 5, 6]}
        else:
            result['places'] = {'relegation': [18, 19, 20]}
        if ended:
            result['champion'] = self.team(first['team_id'])
            old_first = first.get('week_comparison', {})
            # A final round is not necessarily the round that decided a title.
            # Prove an earlier clinch from the frozen round-start points bound.
            before_points = old_first.get('before_points')
            already = before_points is not None and all(before_points > f['data'].get('week_comparison', {}).get('before_points', 999)
                + 3 * (38 - f['data'].get('week_comparison', {}).get('before_played', 0)) for f in rows[1:])
            result['champion_was_already_confirmed_before_week'] = already
            result['champion_decided_this_week'] = not already
            result['clinched_early_this_week'] = False
            result['decisive_places'] = [self.table_row(cid, f['data']) for f in (rows[:8] if cid == 'second' else rows[-4:])]
            groups = {}
            for fact in rows:
                row = fact['data']; groups.setdefault(row['points'], []).append(self.table_row(cid, row))
            # Only ties affecting a title/qualification boundary are news here;
            # two already-relegated teams' mutual order adds distracting detail.
            def outcome(rank):
                return 'champion' if rank == 1 else ('direct' if rank <= 2 else 'playoff' if rank <= 6 else 'outside') if cid == 'second' else ('relegated' if rank >= 18 else 'safe')
            result['teams_tied_on_points'] = [{'points': points, 'teams_in_official_order': tied} for points, tied in groups.items()
                if len(tied) > 1 and len({outcome(r['rank']) for r in tied}) > 1]
            result['all_final_round_scores'] = [{'home': self.team(f['data']['home_team_id']), 'away': self.team(f['data']['away_team_id']),
                'score_home_away': score(f['data'])} for f in self.weekly.values() if f['competition_id'] == cid]
            if cid == 'second':
                result['promoted_directly'] = [self.team(f['data']['team_id']) for f in rows[:2]]
                result['qualified_for_promotion_playoff'] = [self.team(f['data']['team_id']) for f in rows[2:6]]
            else:
                result['relegated'] = [self.team(f['data']['team_id']) for f in rows[-3:]]
        return result

    def boundary_rows(self, match):
        d = match['data']; result = []
        for tid in match['team_ids']:
            cid = self.league_of(tid)
            table = self.book.get('table:' + str(cid), {}).get('value')
            if not table:
                continue
            raw = [self.book[k]['value'] for k in table['match_keys']]
            is_league = match['competition_id'] in rules.LEAGUE_COMPETITIONS
            before = [r for r in raw if r['week'] < match['time']['week'] or (not is_league and r['week'] == match['time']['week'])]
            rows = standings_from_matches(table['team_ids'], before, previous_rank=table['previous_rank'])
            rank, row = next((i, r) for i, r in enumerate(rows, 1) if r['team_id'] == tid)
            result.append({'team': self.team(tid), 'league': self.competition(cid), 'rank': rank, 'points': row['points'],
                           'played': row['played'], 'basis': 'round_start' if is_league else 'league_table_before_cup_match'})
        return result

    def streaks(self, mid):
        selected, seen = [], set()
        facts = [f for f in self.facts.values() if f['kind'] == 'streak' and mid in f['match_ids']]
        facts.sort(key=lambda f: (not f['data']['ended'], f['data']['kind'] not in ('wins', 'losses'), -max(f['data']['before'], f['data']['after'])))
        for fact in facts:
            key = self.equivalents.get(fact['fact_id'], fact['fact_id'])
            if key in seen:
                continue
            seen.add(key); f = self.facts[key]; d = f['data']
            # An ended streak is the story; suppress a weaker continuing one
            # for the same team, rather than explaining every possible scope.
            if any(x['team'] == self.team(d['team_id']) for x in selected):
                continue
            entry = {'team': self.team(d['team_id']), 'scope': {'league_all_levels': '联赛', 'all_official': '各项正式赛事', 'competition': self.competition(f['competition_id']) if f['competition_id'] else '本赛事'}[d['scope']],
                             'kind': d['kind'], 'before_match': d['before'], 'after_match': d['after'], 'ended': d['ended'],
                             'crosses_seasons': d['crosses_seasons'], 'history_complete': d['history_complete']}
            notes = self.view[key].get('reading_notes', {})
            if not notes.get('is_last_match_of_week_in_this_scope', True):
                entry['later_match_in_same_scope_this_week'] = True
                entry['count_at_week_end'] = notes['current_count_at_week_end']
            selected.append(entry)
        return selected

    def match(self, mid):
        from football_simulator.press.features import stage_name
        f = self.games[mid]; d = f['data']; notes = self.view[f['fact_id']]['reading_notes']
        result = {'competition': self.competition(f['competition_id']), 'stage': stage_name(d['stage']),
                  'home': self.team(d['home_team_id']), 'away': self.team(d['away_team_id']),
                  'field_score_home_away': score(d)}
        result['leg']=d.get('leg',1)
        story=self.book.get('story-context',{}).get('value',{})
        result.update(story.get('games',{}).get(mid,{}))
        background=story.get('title_backgrounds',{}).get(f['competition_id'],{})
        if background.get('defending_team_id') in f['team_ids']:
            result['defending_title_run']=background
        if d['extra_time'] is not None:
            result['regulation_home_away'] = d['regulation']; result['extra_time_goals_home_away'] = d['extra_time']
        if d['shootout']:
            result['shootout_home_away'] = d['shootout']; result['shootout_winner'] = self.team(d['shootout_winner_team_id'])
        stadium = self.packet['entities']['stadiums'].get(d['stadium_id'])
        if stadium:
            result['stadium'] = stadium['name_zh'] + '（' + stadium['name_en'] + '）'
        if d.get('venue_mode') == 'neutral':
            result['neutral_venue'] = True
        result['league_background'] = self.boundary_rows(f)
        if f['competition_id'] in rules.LEAGUE_COMPETITIONS:
            before = result['league_background']
            if len(before) == 2 and all(r['rank'] <= 4 for r in before):
                result['race_meeting'] = before
        if f['competition_id'] in rules.LEAGUE_COMPETITIONS:
            result['league_week_end'] = [self.table_row(f['competition_id'], self.row(f['competition_id'], tid)) for tid in f['team_ids']]
        statuses, records, former, performances = [], [], [], []
        for related in self.facts.values():
            if mid not in related['match_ids']:
                continue
            rd = related['data']; kind = related['kind']
            if kind == 'competition_status':
                statuses.append({'team': self.team(rd['team_id']), 'status': rd['status'],
                                 **({'next_stage': rules.stage_label(rd['next_stage'])} if rd['next_stage'] else {})})
            elif kind == 'swiss_record' and d['stage'].startswith('swiss_'):
                winner = d['shootout_winner_team_id'] or d['on_field_winner_team_id']
                won = winner == rd['team_id']
                result.setdefault('swiss_qualification_records', []).append({'team': self.team(rd['team_id']),
                    'before_match': {'wins': rd['wins'] - int(won), 'losses': rd['losses'] - int(not won)},
                    'after_match': {'wins': rd['wins'], 'losses': rd['losses']},
                    'qualified_in_this_match': won and rd['wins'] == 3, 'eliminated_in_this_match': not won and rd['losses'] == 3})
            elif kind == 'record':
                records.append(achievement_context(rd, self.team(rd['entity_id']), self.competition(f['competition_id'])))
            elif kind == 'former_club':
                performance = self.facts.get(f"performance:{mid}:{rd['player_id']}", {}).get('data', {})
                if performance.get('goals') or performance.get('assists'):
                    former.append({'player': self.player(rd['player_id']), 'current_team': self.team(rd['current_team_id']), 'former_team': self.team(rd['former_team_id']),
                                   'appeared_for_former': rd['appeared_for_former'], 'immediate_previous_club': rd['is_immediate_previous_club']})
            elif kind == 'player_match':
                # A list of routine defensive/creation counts encourages a
                # writer to invent links to unnamed goals. Keep actual impact
                # and genuinely exceptional specialist performances only.
                values = {k: rd[k] for k in ('goals', 'assists') if rd.get(k)}
                if rd.get('successful_saves', 0) >= 5:
                    values['successful_saves'] = rd['successful_saves']
                    if rd.get('clean_sheets'): values['clean_sheets'] = rd['clean_sheets']
                if rd.get('successful_defenses', 0) >= 6: values['successful_defenses'] = rd['successful_defenses']
                if rd.get('chances_created', 0) >= 5: values['chances_created'] = rd['chances_created']
                if values:
                    importance = rd['goals'] * 5 + rd['assists'] * 3 + rd['successful_saves'] * .45 + rd['successful_defenses'] * .35 + rd['chances_created'] * .3 + rd['clean_sheets']
                    performances.append((importance, {'player': self.player(rd['player_id']), 'team': self.team(rd['team_id']), **values}))
        if performances:
            result['players'] = [p for _, p in sorted(performances, key=lambda p: -p[0])[:3]]
        if statuses: result['qualification_outcomes'] = statuses
        if records: result['club_records'] = records
        if former: result['former_clubs'] = former
        result['streak_events'] = self.streaks(mid)
        defenders = [f for f in self.facts.values() if f['kind'] == 'historical_title' and f['competition_id'] == self.games[mid]['competition_id'] and f['data']['team_id'] in self.games[mid]['team_ids']]
        if defenders:
            result['defending_champions'] = [self.team(f['data']['team_id']) for f in defenders]
        # Give a bounded, ordered score history when it actually explains a
        # narrow result or comeback. Missing scorers stay unassigned.
        if notes['goal_timeline_complete'] and (abs(score(d)['home'] - score(d)['away']) <= 1 or notes.get('field_winner_trailing_spells', 0)):
            result['goals'] = goal_context(d, notes, self.team, self.player)
            raw = self.book.get('match:' + mid, {}).get('value', {})
            events = raw.get('events') or json.loads(raw.get('events_json') or '[]')
            goals = [event for event in events if event.get('type') == 'goal' and event.get('minute') is not None
                     and event.get('team_id') in f['team_ids']]
            if len(goals) == len(result['goals']):
                # Join by original event position, not minute: equal-minute
                # goals can belong to different teams and different assisters.
                for original, event in zip(goals, result['goals']):
                    pid = original.get('assist_id')
                    if (original['minute'] == event['minute'] and self.team(original['team_id']) == event['team']
                            and pid in self.packet['entities']['players']):
                        event['assister'] = self.player(pid)
            for event in result['goals']:
                event['description'] = goal_caption(event, result['home'], result['away'])
            if notes.get('field_winner_trailing_spells'):
                result['field_winner_trailing_spells'] = notes['field_winner_trailing_spells']
        if notes['goal_timeline_complete']:
            result['halftime_score_home_away'] = {side: sum(e['team_id'] == d[side + '_team_id'] and e['minute'] <= 45 for e in d['goal_events']) for side in ('home', 'away')}
            field = score(d); winner = notes.get('field_winner_team_id')
            if winner:
                loser = d['away_team_id'] if winner == d['home_team_id'] else d['home_team_id']
                spells = notes['field_winner_trailing_spells']
                result['summary'] = (f'{self.team(winner)}曾{spells}次陷入落后，最终逆转战胜{self.team(loser)}。' if spells
                                     else f'{self.team(winner)}先取得领先，最终战胜{self.team(loser)}。')
            else:
                result['summary'] = f"{self.team(d['home_team_id'])}与{self.team(d['away_team_id'])}在场上战成{field['home']}–{field['away']}。"
            equalizers = {}
            leads = {}
            for event in goal_context(d, notes, self.team, self.player):
                if event['field_effect'] == '扳平比分': equalizers[event['team']] = equalizers.get(event['team'], 0) + 1
                if event['field_effect'] in ('首开纪录', '取得领先', '反超比分', '再次取得领先'):
                    leads[event['team']] = leads.get(event['team'], 0) + 1
            result['score_flow'] = {'winner': self.team(winner) if winner else None,
                                    'winner_trailing_spells': notes.get('field_winner_trailing_spells', 0),
                                    'equalizers': equalizers}
            repeated_leads = {team: count for team, count in leads.items() if count >= 2}
            if repeated_leads:
                result['summary'] += '本场取得领先的次数：' + '；'.join(f'{team}{count}次' for team,count in repeated_leads.items()) + '。'
            if equalizers:
                result['summary'] += '本场扳平比分次数：' + '；'.join(f'{team}{count}次' for team,count in equalizers.items()) + '。'
        ties = [f for f in self.facts.values() if f['kind'] == 'tie_result' and mid in f['match_ids']]
        for tie in ties:
            td = tie['data']; tn = self.view[tie['fact_id']]['reading_notes']
            result['two_leg_tie'] = {'team_a': self.team(td['team_a_id']), 'team_b': self.team(td['team_b_id']),
                'aggregate_a_b': [td['team_a_goals'], td['team_b_goals']], 'complete': td['complete'],
                'advancing_team': self.team(td['advancing_team_id']) if td['advancing_team_id'] else None,
                'legs': [{'home': self.team(leg['home_team_id']), 'away': self.team(leg['away_team_id']), 'field_score_home_away': {k: leg[k] for k in ('home', 'away')}} for leg in tn['leg_field_scores']]}
            if 'advancing_team_trailing_on_aggregate_during_second_leg' in tn:
                result['two_leg_tie']['advancing_team_was_behind_on_aggregate'] = tn['advancing_team_trailing_on_aggregate_during_second_leg']
        for tid in f['team_ids']:
            row = self.row(f['competition_id'], tid)
            if row and row.get('season_record', {}).get('first_loss_match_id') == mid:
                result.setdefault('season_first_losses', []).append(self.team(tid))
        baseline=self.book.get('expectations:week',{}).get('value')
        winner=d.get('shootout_winner_team_id') or d.get('on_field_winner_team_id')
        if baseline and winner and winner in baseline['teams']:
            from copy import deepcopy
            from football_simulator.press.expectations import compare
            loser=next(t for t in f['team_ids'] if t!=winner)
            profiles=deepcopy(baseline['teams'])
            for row in result['league_background']:
                tid=next((t for t in f['team_ids'] if self.team(t)==row['team']),None)
                if tid:profiles[tid]['current_league']={'competition':row['league'],**{k:row[k] for k in ('rank','points','played')}}
            result['expectation_comparison']=compare(profiles[winner],profiles[loser])
            result['expectation_source_key']='expectations:week'
        if baseline:
            result['club_backgrounds']={self.team(t):baseline['teams'][t]['heritage'] for t in f['team_ids']
                                       if baseline['teams'].get(t,{}).get('heritage')}
        return result

    def honours(self, team, cid):
        value = self.book.get('honours-context', {}).get('value')
        if not value or not value['history_complete']:
            return None
        background=self.book.get('story-context',{}).get('value',{}).get('title_backgrounds',{}).get(cid)
        if background is not None and not background.get('complete'):return None
        titles = {(r['season'], r['competition'], r['team_id']) for r in value['titles']}
        season = self.week['season']; required = {'premier', *rules.CUP_IDS}
        current = {c for y, c, t in titles if y == season and t == team and c in required}
        career = {c for y, c, t in titles if t == team and c in required}
        seasons = {y for y, c, t in titles if c == cid and t == team}
        consecutive = 0
        for year in range(season, 0, -1):
            if year not in seasons: break
            consecutive += 1
        return {'team': self.team(team), 'titles_in_this_competition': len(seasons),
                **({'consecutive_titles': consecutive} if consecutive >= 2 else {}),
                **({'season_grand_slam_completed': True} if current == required else {}),
                **({'career_grand_slam_completed': True} if career == required else {})}
