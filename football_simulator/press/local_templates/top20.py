"""Official Top20 order, concise position-specific reporting and selection history."""
from football_simulator.press.local_templates.common import names,stats,match_line,section,article
from football_simulator.press.local_templates.transfer import ROLES


def selection(card,skip=()):
    h=card.get('selection_history',{});lines=[];previous=card.get('previous_rank');rank=card['rank']
    total=h.get('total_selections');run=h.get('consecutive_selections');missing=h.get('missing_seasons',[])
    if 'identity' not in skip:
        if h.get('status')=='首届榜单成员':lines.append('他是首届榜单成员。')
        elif total==1 and not missing:lines.append('他首次入选年度Top20。')
        elif h.get('status')=='重返Top20':lines.append('他本季重返Top20。')
    if 'counts' not in skip and total is not None and total>1:
        text=f'他已累计入选{total}次'
        if run is not None and run>=2:text+=f'，且已连续{run}届上榜'
        lines.append(text+'。')
    elif 'counts' not in skip and run is not None and run>=2:lines.append(f'他已连续{run}届上榜。')
    if previous is not None:
        movement=previous-rank
        if movement:lines.append(f'从上届第{previous}名'+(f'升至第{rank}名，上升{movement}位。' if movement>0 else f'降至第{rank}名，下降{-movement}位。'))
        else:lines.append(f'排名与上届相同，仍列第{rank}名。')
    best=h.get('previous_best_rank')
    if best is not None and rank<best:lines.append(('刷新个人历史最佳名次' if not missing else '刷新已知最佳名次')+f'，此前最高为第{best}名。')
    if rank==1 and not missing and 'number_one' not in skip:
        times=len(h.get('prior_number_one_seasons',[]))+1
        lines.append('这也是他'+('首次' if times==1 else f'第{times}次')+'登顶年度Top20。')
    return ''.join(lines)


def player_paragraph(card):
    from football_simulator.press.local_templates.narrative import season_numbers,production,arrival_line
    h=card.get('selection_history',{});rank=card['rank'];previous=card.get('previous_rank');name=card['player']
    skip=set();total=h.get('total_selections');run=h.get('consecutive_selections')
    if rank==1 and total and not h.get('missing_seasons'):
        text=f"这是{name}第{total}次入选年度Top20"
        if run and run>=2:text+=f"，且已连续{run}届上榜"
        first=not h.get('prior_number_one_seasons');text+='。这一届，他'+('首次' if first else '再次')+'站上榜首。'
        if not first:text+=f"这是他第{len(h['prior_number_one_seasons'])+1}次登顶。"
        skip.update(('counts','identity','number_one'))
    elif rank==1:text=name+'站上了这一季年度Top20的榜首。'
    elif h.get('status')=='重返Top20':text=name+f'重新回到年度Top20，这次位列第{rank}名。';skip.add('identity')
    elif h.get('total_selections')==1 and not h.get('missing_seasons'):text=name+f'第一次进入年度Top20，便排在第{rank}名。';skip.add('identity')
    elif previous is not None and previous-rank>=5:text=f'从上届第{previous}名来到第{rank}名，{name}在榜单上向前迈了一大步。'
    else:text=name+f'以第{rank}名结束本届年度Top20评选。'
    highlights=card.get('representative_matches',[])[:1]
    if highlights:
        row=highlights[0];game=row['match']
        text+='回看'+game['competition']+game['stage']+'，'+match_line(game,venue=False)
        line=production({**row,'position':card['position']})
        if line:text+=name+'那场为'+row['player_team']+line+'。'
    teams=card.get('actual_teams',[])
    scope='代表'+teams[0]['team'] if len(teams)==1 else '合计'
    text+=season_numbers(card,scope='本季'+scope)
    if len(teams)>1:
        text+='这段赛季经历横跨'+names(t['team'] for t in teams)+'。'
        text+=''.join(season_numbers({**t,'position':card['position']},subject='他',scope='在'+t['team']) for t in teams)
    arrivals=card.get('recent_arrivals',[])
    if arrivals:text+=arrival_line(name,arrivals[-1])
    awards=card.get('personal_awards',[])
    if awards:
        text+='他的个人奖杯中还有'+names(r['competition']+r['award'] for r in awards)+'。'
        if len(awards)>=2:text+=f'这一季共有{len(awards)}项正式个人奖记在他名下。'
    title_teams={}
    for row in card.get('team_titles',[]):title_teams.setdefault(row['team_at_championship'],[]).append(row['competition'])
    for team,comps in title_teams.items():text+='在'+team+'，他也随队捧起'+names(comps)+'冠军奖杯。'
    for team in card.get('team_honor_summaries',[]):
        count=team.get('championship_count');points=team.get('honor_points')
        if count is None and points is None:continue
        prefix=team['team']+'这一季'
        if count is not None:prefix+=(f'共拿到{count}座冠军' if count else '没有夺冠')
        if points is not None:prefix+=('，' if count is not None else '')+f'球队荣誉积分为{points}分'
        text+=prefix+'。'
    if not card.get('team_honor_summaries'):
        for team in card.get('teams_season_results',[]):
            known=[r for r in team['season_results'] if r['finish'] in ('冠军','亚军')]
            if known:text+=team['team']+'取得了'+names(r['competition']+r['finish'] for r in known)+'。'
    return text+selection(card,skip)


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
    for group in d.get('club_top20_groups',[]):
        overview.append('本季代表过'+group['team']+'的球员中，有'+str(len(group['players']))+'人进入榜单：'+names(group['players'])+'。')
    for row in d.get('draft_class_contrasts',[]):
        text=f"第{row['season']}赛季第{row['pick']}顺位新秀{row['player']}已来到榜单第{row['rank']}名。"
        text+=(f"同届第{row['first_pick']}顺位的{row['first_player']}本届排第{row['first_rank']}名。" if row['first_rank'] is not None else f"同届第{row['first_pick']}顺位的{row['first_player']}本届没有入选。")
        overview.append(text)
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
            run=p.get('previous_consecutive_selections',0)
            if run>=2:text+=('此前连续' if p.get('previous_run_exact') else '此前至少连续')+str(run)+'届上榜的纪录就此结束。'
            if p.get('last_season'):text+='上季'+stats(p['last_season'])+'；'
            text+='本季'+stats(p['this_season'])+'。'
            paragraphs.append(text)
        sections.append(section('落选回顾',paragraphs,refs))
        mapping.append({'story_id':'top20-dropped','template_id':'feature.top20.dropped','fact_ids':refs})
    return article(packet,title,deck,sections),mapping
