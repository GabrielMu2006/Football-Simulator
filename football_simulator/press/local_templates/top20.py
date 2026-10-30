"""Official Top20 order, concise position-specific reporting and selection history."""
from football_simulator.press.local_templates.common import names,stats,match_line,section,article
from football_simulator.press.local_templates.transfer import ROLES


def selection(card,skip=()):
    h=card.get('selection_history',{});lines=[];previous=card.get('previous_rank');rank=card['rank']
    total=h.get('total_selections');run=h.get('consecutive_selections');missing=h.get('missing_seasons')
    if 'identity' not in skip:
        if h.get('status')=='首届榜单成员':lines.append('他是首届榜单成员。')
        elif total==1 and missing==[]:lines.append('他首次入选年度Top20。')
        elif h.get('status')=='重返Top20':lines.append('他本季重返Top20。')
    if 'counts' not in skip and total is not None and total>1:
        text=f'他已累计入选{total}次'
        if run is not None and run>=2:text+=f'，且已连续{run}届上榜'
        lines.append(text+'。')
    elif 'counts' not in skip and run is not None and run>=2:lines.append(f'他已连续{run}届上榜。')
    if previous is not None and 'movement' not in skip:
        movement=previous-rank
        if movement:lines.append(f'从上届第{previous}名'+(f'升至第{rank}名，上升{movement}位。' if movement>0 else f'降至第{rank}名，下降{-movement}位。'))
        else:lines.append(f'排名与上届相同，仍列第{rank}名。')
    best=h.get('previous_best_rank')
    if best is not None and rank<best:lines.append(('刷新个人历史最佳名次' if missing==[] else '刷新已知最佳名次')+f'，此前最高为第{best}名。')
    if rank==1 and missing==[] and 'prior_number_one_seasons' in h and 'number_one' not in skip:
        times=len(h.get('prior_number_one_seasons',[]))+1
        lines.append('这也是他'+('首次' if times==1 else f'第{times}次')+'登顶年度Top20。')
    return ''.join(lines)


def player_paragraph(card):
    from football_simulator.press.local_templates.narrative import season_numbers,production,arrival_line,best_highlight,stage_text
    h=card.get('selection_history',{});rank=card['rank'];previous=card.get('previous_rank');name=card['player']
    skip=set();total=h.get('total_selections');run=h.get('consecutive_selections')
    if rank==1 and total and h.get('missing_seasons')==[] and 'prior_number_one_seasons' in h:
        text=f"这是{name}第{total}次入选年度Top20"
        if run and run>=2:text+=f"，且已连续{run}届上榜"
        first=not h.get('prior_number_one_seasons');text+='。这一届，他'+('首次' if first else '再次')+'站上榜首。'
        if not first:text+=f"这是他第{len(h['prior_number_one_seasons'])+1}次登顶。"
        skip.update(('counts','identity','number_one'))
    elif rank==1:text=name+'站上了这一季年度Top20的榜首。'
    elif h.get('status')=='重返Top20':text=name+f'重新回到年度Top20，这次位列第{rank}名。';skip.add('identity')
    elif h.get('total_selections')==1 and h.get('missing_seasons')==[]:text=name+f'第一次进入年度Top20，便排在第{rank}名。';skip.add('identity')
    elif previous is not None and previous-rank>=5:
        text=f'从上届第{previous}名来到第{rank}名，{name}在榜单上向前迈了一大步。';skip.add('movement')
    else:text=''
    row=best_highlight(card.get('representative_matches',[]))
    if row:
        game=row['match']
        text+=('回看' if text else '')+game['competition']+stage_text(game['stage'])+'，'+match_line(game,venue=False)
        line=production({**row,'position':card['position']})
        if line:text+=name+'那场为'+row['player_team']+line+'。'
    elif not text:text=name+f'以第{rank}名结束本届年度Top20评选。'
    teams=card.get('actual_teams',[])
    scope='代表'+teams[0]['team'] if len(teams)==1 else '合计'
    text+=season_numbers(card,subject='这一季，他',scope=scope)
    if len(teams)>1:
        spells=[season_numbers({**t,'position':card['position']},subject='',scope='代表'+t['team']).rstrip('。') for t in teams]
        text+='其中，他'+'；'.join(spells)+'。'
    arrivals=card.get('recent_arrivals',[])
    if arrivals:text+=arrival_line(name,arrivals[-1])
    awards=card.get('personal_awards',[])
    if awards:
        mvps=[r for r in awards if r['award']=='赛事MVP']
        evps=[r for r in awards if r['award']=='赛事EVP']
        other=[r for r in awards if r['award'] not in ('赛事MVP','赛事EVP')]
        honours=[]
        if mvps:honours.append(f"{len(mvps)}次获评赛事MVP（"+names(r['competition'] for r in mvps)+'）')
        if evps:honours.append(f"{len(evps)}次入选赛事EVP（"+names(r['competition'] for r in evps)+'）')
        if other:honours.append('获得'+names(r['competition']+r['award'] for r in other))
        if honours:
            text+='个人奖项方面，他'+honours[0]
            for extra in honours[1:]:text+='，还'+extra
            text+='。'
    title_teams={}
    for row in card.get('team_titles',[]):title_teams.setdefault(row['team_at_championship'],[]).append(row['competition'])
    summaries={row['team']:row for row in card.get('team_honor_summaries',[])}
    for team in dict.fromkeys([*title_teams,*summaries]):
        summary=summaries.get(team,{})
        count=summary.get('championship_count');points=summary.get('honor_points')
        titles=title_teams.get(team,[])
        if count is None and points is None and not titles:continue
        if titles:
            sentence='他随'+team+'捧起'+names(titles)+'冠军奖杯'
            if count is not None:sentence+=f'，球队这一季共获得{count}座冠军'
        else:
            sentence=team+'这一季'+(f'获得{count}座冠军' if count else '没有夺冠') if count is not None else team+'这一季'
        if points is not None:sentence+=('，' if count is not None or titles else '')+f'球队荣誉积分为{points}分'
        text+=sentence+'。'
    if not card.get('team_honor_summaries'):
        for team in card.get('teams_season_results',[]):
            known=[r for r in team['season_results'] if r['finish'] in ('冠军','亚军')
                   and not (r['finish']=='冠军' and r['competition'] in title_teams.get(team['team'],[]))]
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
