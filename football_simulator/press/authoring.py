"""Small author-facing views. Typed facts and full evidence stay in the plan."""
from __future__ import annotations


def table_line(row, *, goal_difference=False):
    text = f"{row['team']}：第{row['rank']}，{row['points']}分"
    if goal_difference and 'goal_difference' in row:
        text += f"，净胜球{row['goal_difference']}"
    change = row.get('week_change', {})
    if change.get('before_rank') is not None:
        if change['before_rank'] != row['rank']:
            text += f"，从第{change['before_rank']}升至此位" if change['before_rank'] > row['rank'] else f"，从第{change['before_rank']}降至此位"
    if row.get('promotion_zone_change', '').startswith('本周从'):
        text += '；' + row['promotion_zone_change']
    return text + '。'


def league_view(value):
    facts = []
    if value.get('race_summary'):
        facts.append(value['race_summary'])
    if value.get('league_finished'):
        timing = '本周前已确定冠军' if value['champion_was_already_confirmed_before_week'] else '本周末轮后确定冠军'
        facts.append(f"{value['competition']}已结束，{value['champion']}{timing}。")
        for key, label in [('promoted_directly', '直接升级'), ('qualified_for_promotion_playoff', '进入升级附加赛'), ('relegated', '降级')]:
            if value.get(key): facts.append(label + '：' + '、'.join(value[key]) + '。')
    rows = {row['team']: row for row in value.get('leader_and_runner_up', []) + value.get('decisive_places', [])}
    tied_teams = {row['team'] for group in value.get('teams_tied_on_points', []) for row in group['teams_in_official_order']}
    if value.get('race_summary'):
        # The race already names the top two and attaches the before/after gap.
        for row in value.get('leader_and_runner_up', []):
            if row['team'] not in tied_teams: rows.pop(row['team'], None)
    return {'competition': value['competition'], 'confirmed_outcomes': facts,
            'relevant_standings': [table_line(row, goal_difference=row['team'] in tied_teams) for row in rows.values()]}


def tie_view(value):
    facts = []
    for number, leg in enumerate(value['legs'], 1):
        score = leg['field_score_home_away']
        facts.append(f"第{number}回合：{leg['home']} {score['home']}–{score['away']} {leg['away']}。")
        if number == 1 and score['home'] != score['away']:
            leading = leg['home'] if score['home'] > score['away'] else leg['away']
            facts.append(f"首回合结束时，{leading}已建立{abs(score['home'] - score['away'])}球总比分优势。")
    a, b = value['aggregate_a_b']
    facts.append(f"两回合总比分：{value['team_a']} {a}–{b} {value['team_b']}。")
    if value.get('advancing_team'):
        facts.append(value['advancing_team'] + '晋级。')
        if value.get('advancing_team_was_behind_on_aggregate'):
            facts.append(value['advancing_team'] + '在次回合比赛进行中一度总比分落后，之后重新取得总比分优势。')
    return facts


def author_content(value):
    if isinstance(value, dict):
        if 'competition' in value and 'field_score_home_away' in value:
            return match_view(value)
        if 'leader_and_runner_up' in value:
            return league_view(value)
        if 'aggregate_a_b' in value:
            return tie_view(value)
        if 'historical_summary' in value:
            return value['historical_summary']
        return {key: [event['description'] for event in data]
                if key == 'goals' and isinstance(data, list) and all(isinstance(e, dict) and 'description' in e for e in data)
                else author_content(data) for key, data in value.items()}
    if isinstance(value, list):
        return [author_content(item) for item in value]
    return value


def match_view(value):
    home, away = value['home'], value['away']
    score = value['field_score_home_away']
    facts = [f"场上赛果：{home} {score['home']}–{score['away']} {away}。"]
    if 'regulation_home_away' in value:
        regulation = value['regulation_home_away']
        facts.append(f"90分钟：{home} {regulation['home']}–{regulation['away']} {away}；之后进行30分钟加时，加时结束比分为{home} {score['home']}–{score['away']} {away}。")
    flow = value.get('score_flow', {})
    if flow.get('winner') and flow.get('winner_trailing_spells'):
        winner = flow['winner']; count = flow['winner_trailing_spells']
        facts.append(f'{winner}两度落后后逆转取胜。' if count == 2 else
                     f'{winner}曾{count}次落后后逆转取胜。' if count > 2 else f'{winner}一度落后，最终逆转取胜。')
    for team, count in flow.get('equalizers', {}).items():
        if count >= 2: facts.append(f'{team}本场{count}次扳平比分。')
    if value.get('shootout_home_away'):
        p = value['shootout_home_away']
        facts.append(f"点球大战最终比分：{home} {p['home']}–{p['away']} {away}；{value['shootout_winner']}晋级。")
    result = {'competition': value['competition'], 'stage': value['stage'], 'result': facts}
    if value.get('stadium'): result['stadium'] = value['stadium']
    if value.get('neutral_venue'): result['venue'] = '中立场地'
    goals = value.get('goals', [])
    # Keep a few newsworthy details, not a parallel live-text commentary. Full
    # ordered events remain local in the typed editorial plan for verification.
    indices = {0, len(goals) - 1} if goals else set()
    equality = next((i for i, e in enumerate(goals) if e['field_effect'] == '扳平比分'), None)
    if equality is not None: indices.add(equality)
    if indices: result['key_goals'] = [goals[i]['description'] for i in sorted(indices)]
    for key in ('qualification_outcomes', 'club_records', 'two_leg_tie', 'defending_champions', 'season_first_losses', 'swiss_qualification_records'):
        if value.get(key): result[key] = author_content(value[key])
    players = value.get('players', [])
    former = {p['player'] for p in value.get('former_clubs', [])}
    selected = [p for p in players if p.get('goals', 0) >= 2 or p.get('assists', 0) >= 2 or p['player'] in former
                or any(p.get(k, 0) >= 5 for k in ('successful_saves', 'successful_defenses', 'chances_created'))]
    if players: result['players'] = selected or players[:1]
    if former:
        result['former_clubs'] = [f"{p['player']}本场代表{p['current_team']}对阵曾效力的{p['former_team']}。" for p in value['former_clubs']]
    background = value.get('league_background', [])
    if background and value.get('upset_basis'):
        result['league_background'] = [f"{r['team']}：{r['league']}第{r['rank']}，{r['points']}分；此为本场比赛前的联赛背景。" for r in background]
    moved = [r for r in value.get('league_week_end', [])
             if abs(r.get('week_change', {}).get('rank_change', 0)) >= 2 or r.get('promotion_zone_change', '').startswith('本周从')]
    if moved: result['league_week_end'] = [table_line(r) for r in moved]
    if value.get('streak_events'):
        kinds = {'wins': '连胜', 'losses': '连败', 'unbeaten': '连续不败', 'winless': '连续不胜'}
        lines = []
        for e in value['streak_events']:
            # Short runs are routine context, especially when already ended
            # again later in the same week. Keep them in local facts, not prose.
            if max(e['before_match'], e['after_match']) < 4:
                continue
            kind = kinds[e['kind']]
            line = f"{e['team']}的{e['scope']}{kind}"
            line += f"{e['before_match']}场在本场终结。" if e['ended'] else f"在本场后延续到{e['after_match']}场。"
            if e.get('later_match_in_same_scope_this_week'):
                line += f"本周其后还有比赛；截至周末，该范围{kind}为{e['count_at_week_end']}场。"
            lines.append(line)
        if lines: result['streaks'] = lines
    return result
