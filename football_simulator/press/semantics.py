"""Resolve sporting meanings before writing; no prose inspection or game writes."""


def achievement_context(data, team, competition):
    """Best-finish equality proves a repeat, even when labels differ:
    'runner-up' is a previous final, not evidence of a first final now.
    """
    achievement = data['current_achievement']
    repeat = data['comparison'] == 'equalled'
    verb = '赢得冠军' if achievement == '冠军' else '进入' + achievement
    milestone = ('再次' if repeat else '首次') + verb
    previous = data.get('previous_best')
    summary = f'{team}本次在{competition}{milestone}。'
    if previous:
        summary += f'此前该赛事最佳成绩为{previous}；' + ('本次追平这一最佳成绩。' if repeat else '本次超过此前最佳成绩。')
    return {'team': team, 'milestone': milestone, 'historical_summary': summary}


def goal_context(data, notes, team_name, player_name):
    """Bind each scorer, before/after score and field effect in one event.

    Effects describe this match only, not a two-leg aggregate. Event list order
    is preserved when two goals have the same recorded minute.
    """
    if not notes['goal_timeline_complete']:
        return []
    result, before = [], {'home': 0, 'away': 0}
    led, behind_since_lead = set(), set()
    for event in notes['score_progression_in_recorded_order']:
        after = event['score_after_event']
        side = 'home' if event['scoring_team_id'] == data['home_team_id'] else 'away'
        opposite = 'away' if side == 'home' else 'home'
        if sum(before.values()) == 0:
            effect = '首开纪录'
        elif after[side] == after[opposite]:
            effect = '扳平比分'
        elif before[side] == before[opposite]:
            effect = '反超比分' if side in behind_since_lead else '再次取得领先' if side in led else '取得领先'
        elif before[side] < before[opposite]:
            effect = '缩小分差'
        else:
            effect = '扩大领先'
        result.append({'minute': event['minute'], 'team': team_name(event['scoring_team_id']),
                       **({'scorer': player_name(event['player_id'])} if event['player_id'] else {}),
                       'field_score_before_home_away': dict(before), 'field_score_after_home_away': dict(after),
                       'field_effect': effect})
        for current, rival in (('home','away'), ('away','home')):
            if after[current] > after[rival]:
                led.add(current); behind_since_lead.discard(current)
            elif after[current] < after[rival]:
                behind_since_lead.add(current)
        before = after
    return result


def goal_caption(event, home, away):
    """An unambiguous author-facing event, including unknown-scorer goals."""
    subject = event['scorer'] + '为' + event['team'] if event.get('scorer') else event['team']
    after = event['field_score_after_home_away']
    text = f"第{event['minute']}分钟，{subject}{event['field_effect']}，场上比分{home} {after['home']}–{after['away']} {away}"
    if event.get('assister'): text += '，助攻者为' + event['assister']
    return text + '。'
