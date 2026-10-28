"""Small, evidence-bound sentences used inside report narratives, not a prose DSL."""
from football_simulator.press.local_templates.v4.common import names


def production(card):
    g=card.get('goals',0);a=card.get('assists',0);parts=[]
    if g==3:parts.append('上演帽子戏法')
    elif g>=4:parts.append(f'独进{g}球')
    elif g:parts.append(f'打进{g}球')
    if a:parts.append(f'送出{a}次助攻')
    if card.get('successful_saves'):parts.append(f"完成{card['successful_saves']}次扑救")
    if card.get('clean_sheets'):parts.append('保持零封')
    if card.get('successful_defenses') and not (g or a):parts.append(f"完成{card['successful_defenses']}次成功防守")
    if card.get('chances_created') and not (g or a):parts.append(f"创造{card['chances_created']}次机会")
    return '，并'.join(parts)


def arrival_line(player,data):
    if not data:return ''
    date=f"第{data['season']}赛季第{data['week']}周"
    if data.get('return_to_draft_club'):
        origin=data['draft_origin']
        return (f"{player}在{date}重返{data['to_team']}。早在第{origin['season']}赛季，"
                f"正是这支球队用第{origin['pick']}顺位选中了他。")
    if data.get('return_to_former_club'):
        return f"这是{player}回到旧主{data['to_team']}后的表现；他在{date}由{data['from_team']}回归。"
    return f"{player}在{date}才从{data['from_team']}加盟{data['to_team']}，来到这支球队还不满一个赛季。" if data['weeks_since_joining']<52 else f"{player}从{data['from_team']}加盟{data['to_team']}刚好一个赛季。"


def milestone_lines(events,*,players=None):
    result=[]
    for e in events:
        if players is not None and e['player'] not in players:continue
        p=e['player'];kind=e['kind'];team=e['team']
        if kind=='club_debut':text=p+'首次代表'+team+'参加正式比赛'
        elif kind=='club_first_goal':text=p+'收获了在'+team+'的首粒正式比赛进球'
        elif kind=='first_hat_trick':text='这也是'+p+'在本存档正式比赛中的首个帽子戏法'
        elif kind=='appearances':text=f"{p}完成个人正式比赛第{e['total']}次出场"
        elif kind=='goals':text=f"{p}的正式比赛进球跨过{e['total']}球关口，本场结束后累计{e['after']}球"
        elif kind in ('scoring_streak','clean_streak'):
            event='进球' if kind=='scoring_streak' else '保持零封'
            text=(f"{p}此前连续{e['count']}次正式比赛出场{event}的走势就此结束" if e['ended'] else
                  f"{p}已连续{e['count']}次正式比赛出场{event}")
        else:continue
        result.append(text+'。')
    # Debut + first goal can share a paragraph without repeating the player.
    return ''.join(result)


def history_lines(events):
    lines=[]
    for e in events:
        kind=e['kind'];team=e.get('team','')
        if kind=='defense_ended':
            if e.get('league_title'):text='上季冠军'+team+'未能完成卫冕'
            elif e.get('not_entered'):text='上届冠军'+team+'本届没有参赛，未能延续卫冕'
            elif e.get('exit_stage'):text='卫冕冠军'+team+'止步'+e['exit_stage']+'，未能卫冕'
            else:text='卫冕冠军'+team+'的本届征程已经结束'
            if e.get('previous_consecutive_titles',0) and e['previous_consecutive_titles']>=2:
                text+=f"，此前的{e['previous_consecutive_titles']}连冠也到此为止"
        elif kind=='title':
            if e['consecutive_titles']>=2:text=f"{team}完成{e['consecutive_titles']}连冠，这也是球队第{e['total_titles']}次赢得这项赛事"
            elif e['total_titles']==1:text=team+'首次捧起这项赛事的冠军奖杯'
            else:text=f"{team}将这项赛事的队史冠军数增加到{e['total_titles']}座"
        elif kind=='drought_ended':text=f"距离第{e['last_title_season']}赛季上一次夺冠，{team}经历了{e['seasons_without_title']}个无冠赛季，如今再度捧杯"
        elif kind=='different_champions':text=f"这项赛事最近{e['editions']}届出现了{e['editions']}支不同的冠军球队："+names(e['teams'])
        elif kind=='promoted_finish':text=f"升入一级联赛的首个赛季，{team}以第{e['rank']}名收官"+('，保住了一级联赛席位' if e['rank']<=17 else '，赛季结束后再次降级')
        elif kind=='losers_champion':text=team+'曾落入败者组，最终一路走到总决赛并赢得冠军'
        elif kind=='previous_final':text=names(e.get('teams',[]))+f"曾在第{e['season']}赛季的决赛相遇，当时由{e['champion']}夺冠"
        elif kind=='playoff_low_seed':text=f"{team}以次级联赛第{e['league_rank']}名进入附加赛，最终拿到升级名额"
        else:continue
        lines.append(text+'。')
    return ''.join(lines)


def season_numbers(card,*,subject='他',scope='本季'):
    position=card.get('position');apps=card.get('appearances')
    prefix=subject+scope
    if apps==0:return subject+('本季' if scope.startswith('本季') else scope)+'尚无正式比赛出场记录。'
    if position=='GK':
        text=(f"出场{apps}次，" if apps is not None else '')
        parts=[]
        if card.get('clean_sheets') is not None:parts.append(f"其中{card['clean_sheets']}场保持零封")
        if card.get('successful_saves') is not None:parts.append(f"累计完成{card['successful_saves']}次扑救")
        return prefix+text+'，'.join(parts)+'。' if parts else ''
    goals=card.get('goals');assists=card.get('assists');parts=[]
    if goals is not None and goals>0:parts.append(f"打进{goals}球")
    if assists is not None and assists>0:parts.append(f"送出{assists}次助攻")
    if not parts and apps is not None:parts.append('尚无进球或助攻' if goals==0 and assists==0 else '')
    text=(f"出场{apps}次，" if apps is not None else '')+'、'.join(p for p in parts if p)
    extra=[]
    if position=='DF' and card.get('successful_defenses') is not None:extra.append(f"完成{card['successful_defenses']}次成功防守")
    if position=='MF' and card.get('chances_created') is not None:extra.append(f"创造{card['chances_created']}次机会")
    if extra:text+='，'+ '、'.join(extra)
    return prefix+text+'。' if text else ''


def followup_line(data):
    if not data:return ''
    parts=[]
    for team in data['teams']:
        before=team['before'];after=team['after']
        parts.append(f"{team['team']}在交换前{before['matches']}场正式比赛中取得{before['wins']}胜{before['draws']}平{before['losses']}负，"
                     f"交换后至本次报道的{after['matches']}场则为{after['wins']}胜{after['draws']}平{after['losses']}负")
    return f"回看第{data['season']}赛季第{data['week']}周的那笔交换，"+'；'.join(parts)+'。'


def high_pick_line(players,team=None,player=None):
    if not players:return ''
    peers=names(f"第{p['season']}赛季第{p['pick']}顺位新秀{p['player']}" for p in players)
    return (('在'+team+'，' if team else '')+(player or '他')+'与'+peers+'成为队友。')


def related_followup(data,arrivals):
    ids={a.get('trade_id') for a in arrivals if a}
    return next((r for r in data.get('trade_followups',[]) if r['trade_id'] in ids and all(t['after']['matches']>=3 for t in r['teams'])),None)
