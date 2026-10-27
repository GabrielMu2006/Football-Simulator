"""Official Top20 order, concise position-specific reporting and selection history."""
from football_simulator.press.local_templates.common import names,stats,match_line,section,article
from football_simulator.press.local_templates.transfer import ROLES


def selection(card):
    h=card.get('selection_history',{});lines=[];previous=card.get('previous_rank');rank=card['rank']
    total=h.get('total_selections');run=h.get('consecutive_selections');missing=h.get('missing_seasons',[])
    if h.get('status')=='首届榜单成员':lines.append('他是首届榜单成员。')
    elif total==1 and not missing:lines.append('他首次入选年度Top20。')
    elif h.get('status')=='重返Top20':lines.append('他本季重返Top20。')
    if total is not None:lines.append(f'截至本届累计入选{total}次。')
    if run is not None and run>=2:lines.append(f'已连续{run}届入选。')
    if previous is not None:
        movement=previous-rank
        lines.append(f'较上届第{previous}名'+(f'上升{movement}位。' if movement>0 else f'下降{-movement}位。' if movement<0 else '名次不变。'))
    best=h.get('previous_best_rank')
    if best is not None and rank<best:lines.append(('刷新个人历史最佳名次' if not missing else '刷新已知最佳名次')+f'，此前最高为第{best}名。')
    if rank==1 and not missing:lines.append(f"第{len(h.get('prior_number_one_seasons',[]))+1}次登顶年度Top20。")
    return ''.join(lines)


def player_paragraph(card):
    text=card['player']+'本季司职'+ROLES.get(card['position'],card['position'])+'，'+stats(card)+'。'
    teams=card.get('actual_teams',[])
    if len(teams)==1:text+='本季实际代表'+teams[0]['team']+'出场。'
    elif len(teams)>1:text+='本季分别代表'+'；'.join(t['team']+'（'+stats({**t,'position':card['position']})+'）' for t in teams)+'。'
    highlights=card.get('representative_matches',[])[:1]
    for row in highlights:
        game=row['match'];text+=game['competition']+game['stage']+'中，'+match_line(game,venue=False)
        performance=[]
        if row.get('goals'):performance.append(f"打进{row['goals']}球")
        if row.get('assists'):performance.append(f"送出{row['assists']}次助攻")
        if card['position']=='GK' and row.get('successful_saves') is not None:performance.append(f"完成{row['successful_saves']}次扑救")
        if card['position']=='DF' and row.get('successful_defenses') is not None:performance.append(f"完成{row['successful_defenses']}次成功防守")
        if performance:text+=card['player']+'在该场为'+row['player_team']+'、'.join(performance)+'。'
    awards=card.get('personal_awards',[])
    if awards:text+='本季个人奖项包括'+names(r['competition']+r['award'] for r in awards)+'。'
    titles=card.get('team_titles',[])
    if titles:text+='实际随队获得'+names(r['team_at_championship']+'的'+r['competition']+'冠军' for r in titles)+'。'
    # Results are explicitly attributed to clubs, never converted into the
    # player's personal championship collection after a transfer.
    finishes=[]
    for team in card.get('team_honor_summaries',[]):
        parts=[]
        if team.get('championship_count') is not None:parts.append(f"单季{team['championship_count']}冠")
        if team.get('honor_points') is not None:parts.append(f"球队荣誉分{team['honor_points']}分")
        if parts:finishes.append(team['team']+'的球队成绩为'+'、'.join(parts))
    if not card.get('team_honor_summaries'):
        for team in card.get('teams_season_results',[]):
            known=[r for r in team['season_results'] if r['finish'] in ('冠军','亚军')]
            if known:finishes.append(team['team']+'在部分赛事中取得'+names(r['competition']+r['finish'] for r in known))
    if finishes:text+='；'.join(finishes)+'。'
    return text+selection(card)


def render(packet,plan):
    d=plan['content'];refs=plan['fact_ids'];cards=d['rankings'];changes=d['changes'];year=packet['meta']['season']
    # Preserve the official order without re-sorting or accessing scoring data.
    if len(cards)!=20 or [p['rank'] for p in cards]!=list(range(1,21)):
        from football_simulator.press import PressError
        raise PressError('Top20 官方顺序不完整。')
    title=cards[0]['player']+'领衔第'+str(year)+'赛季Top20'
    deck='年度20人榜单已经确定，'+cards[0]['player']+'排名第一。'
    overview=[]
    if changes.get('newcomers'):overview.append(names(changes['newcomers'])+'首次入选。')
    if changes.get('returning'):overview.append(names(changes['returning'])+'重返榜单。')
    if changes.get('first_edition'):overview.append('这是这个足球世界的首届年度Top20。')
    if not overview:overview=[deck]
    sections=[section('榜单更替',overview,refs)]
    mapping=[{'story_id':'top20-overview','template_id':'feature.top20.overview','fact_ids':refs}]
    for card in cards:
        sections.append(section(f"第{card['rank']}名 · {card['player']}",[player_paragraph(card)],refs))
        mapping.append({'story_id':'top20-'+str(card['rank']),'template_id':'feature.top20.'+card['position'],'fact_ids':refs})
    # Editorial focus follows previous official position, with at most five
    # short recaps. No inferred reason for exclusion or re-scoring is offered.
    dropped=sorted(changes.get('dropped_out',[]),key=lambda r:r['previous_rank'])[:5]
    if dropped:
        paragraphs=[]
        for p in dropped:
            text=f"上届第{p['previous_rank']}名{p['player']}本届落选。"
            if p.get('last_season'):text+='上季'+stats(p['last_season'])+'；'
            text+='本季'+stats(p['this_season'])+'。'
            paragraphs.append(text)
        sections.append(section('落选回顾',paragraphs,refs))
        mapping.append({'story_id':'top20-dropped','template_id':'feature.top20.dropped','fact_ids':refs})
    return article(packet,title,deck,sections),mapping
