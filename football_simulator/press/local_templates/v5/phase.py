"""Stage reports: the full outcome first, then performances and known routes."""
from football_simulator.press.local_templates.v5.common import names,match_line,stats,section,article


def table_paragraphs(rows):
    return ['；'.join(f"第{r['rank']}名{r['team']}，{r['points']}分" for r in rows[i:i+5])+'。' for i in range(0,len(rows),5)]


def routes(bracket):
    lines=[]
    for tie in bracket:
        scheduled=tie.get('scheduled_legs',[])
        if not scheduled:continue
        text=tie['stage']+'，'+'对阵'.join(tie['teams'])+'，'+tie['format']+'。'
        text+=''.join(f"第{r['week']}周第{r['leg']}回合，{r['home']}主场迎战{r['away']}。" for r in scheduled)
        lines.append(text)
    return lines


def high_pot_exits(data):
    pots={r['team']:r['pot'] for r in data.get('draw_pots',[])}
    eliminated=set(data.get('eliminated',[]));exits=[]
    for group,rows in data.get('groups',{}).items():
        for row in rows:
            team=row['team'];pot=pots.get(team)
            if team in eliminated and pot in (1,2):
                exits.append(f"第{pot}档球队{team}以{row['points']}分位列{group}组第{row['rank']}名，未能晋级")
    return '；'.join(exits)+'。' if exits else ''


def render(packet,plan):
    from football_simulator.press.local_templates.v5.narrative import history_lines,season_numbers,arrival_line,followup_line,related_followup,best_highlight,stage_text
    from football_simulator.press.expectations import surprise_paragraphs
    d=plan['content'];refs=plan['fact_ids'];comp=d['competition'];stage=d['stage']
    label={'halfway':'半程','groups':'小组阶段','swiss':'瑞士轮','playin':'突围赛'}[stage]
    title=comp+label+'收官';lead=[];standings=[];qualified=d.get('qualified',d.get('direct_quarterfinalists',[]))
    deck=f"{comp}{label}结束，"+(f"{len(qualified)}支球队继续向前。" if qualified else '本阶段的结果已经确定。')
    history=history_lines(d.get('narrative_history',[]))
    group_teams={row['team'] for rows in d.get('groups',{}).values() for row in rows}
    reported_exits={r['team'] for r in d.get('draw_pots',[]) if r['pot'] in (1,2)
                    and r['team'] in d.get('eliminated',[]) and r['team'] in group_teams}
    surprises=surprise_paragraphs(d.get('group_surprises',[])+d.get('qualification_surprises',[]),
                                  opponent_exits_reported=reported_exits)
    if d.get('halfway_table'):
        deck=comp+'已经完成半程，接下来的比赛仍将决定最终名次。'
        first=d['halfway_table'][0];title=first['team']+'领跑'+comp+'半程'
        text=f"{first['team']}带着{first['points']}分结束前半程，暂居{comp}榜首。"
        if len(d['halfway_table'])>1:
            second=d['halfway_table'][1];gap=first['points']-second['points']
            text+=second['team']+('与其同分。' if gap==0 else f"以{gap}分之差紧随其后。")
        text+=('最终冠军和升级资格仍待后半程揭晓。' if comp=='Second Division' else '最终冠军和降级名单仍待后半程揭晓。')
        lead.append(text);standings+=table_paragraphs(d['halfway_table'])
    else:
        unbeaten=[r['team'] for r in d.get('swiss_records',[]) if r['wins']==3 and r['losses']==0]
        if unbeaten:lead.append(names(unbeaten)+'均以3胜0负的战绩取得淘汰赛资格。' if len(unbeaten)>1 else unbeaten[0]+'以3胜0负的战绩取得淘汰赛资格。')
        if history:lead.append(history)
        if surprises:lead.append(''.join(surprises))
        exits=high_pot_exits(d)
        if exits:lead.append(exits)
        dark=next((r for r in d.get('group_surprises',[])+d.get('qualification_surprises',[]) if r.get('is_dark_horse')),None)
        if dark:title=dark['team']+'突围，'+comp+label+'收官'
        for r in d.get('swiss_turnarounds',[]):
            lead.append(r['team']+('从0胜2负起步，随后连赢三场，以3胜2负晋级。' if r['qualified'] else '曾取得2胜0负的开局，却在接下来三场连败，以2胜3负结束本届征程。'))
    for group,rows in d.get('groups',{}).items():
        standings.append(group+('排名如下。' if group in ('登峰组','涅槃组') else '组排名如下。'))
        standings+=table_paragraphs(rows)
    if d.get('swiss_records'):
        ordered=sorted(d['swiss_records'],key=lambda r:(r.get('seed') is None,r.get('seed') or 0))
        standings+=['；'.join(f"{r['team']}{r['wins']}胜{r['losses']}负"+(f"，第{r['seed']}号种子" if r.get('seed') else '') for r in ordered[i:i+4])+'。' for i in range(0,len(ordered),4)]
    if d.get('draw_pots'):
        standings.append('本届抽签分档：'+'；'.join(f"第{pot}档为"+names(r['team'] for r in d['draw_pots'] if r['pot']==pot) for pot in range(1,5))+'。')
    for key,verb in [('qualified','晋级'),('direct_quarterfinalists','直通八强'),('playin_winners','通过突围赛进入八强'),('eliminated','本阶段出局')]:
        if d.get(key):standings.append(names(d[key])+verb+'。')
    people=[];seen_games=set()
    def game_key(g):return (g['competition'],g['stage'],g['home'],g['away'])
    for index,p in enumerate(d.get('players',[])):
        teams=p.get('actual_teams',[]);team=teams[0]['team'] if len(teams)==1 else None
        text=season_numbers(p,subject=p['player'],scope=('在'+team+'的本届征程中' if team else '截至本阶段结束'))
        highlight=best_highlight(p.get('representative_matches',[])) if index<2 else None
        if highlight:
            from football_simulator.press.local_templates.v5.narrative import production
            g=highlight['match'];seen_games.add(game_key(g));performance=production(highlight)
            if performance:text+='在'+stage_text(g['stage'])+'，'+match_line(g,venue=False)+p['player']+'为'+highlight['player_team']+performance+'。'
        arrivals=p.get('recent_arrivals',[])
        if arrivals:text+=arrival_line(p['player'],arrivals[-1])
        people.append(text)
    if people:lead.extend(people)
    breakthroughs=[r['team'] for r in d.get('history_breakthroughs',[]) if r.get('first_time') and r.get('history_complete')]
    if breakthroughs:lead.append('对于'+names(breakthroughs)+'，这还是队史第一次进入'+comp+'八强。')
    elif not d.get('history_breakthroughs'):
        for note in d.get('history_notes',[]):lead.append(note.replace('该赛事',comp)+'。')
    games=[g for g in d.get('key_results',[]) if game_key(g) not in seen_games]
    if games:
        lead.append('回看这一阶段，'+''.join(g['stage']+'，'+match_line(g,venue=False) for g in games))
    followup=related_followup(d,[a for p in d.get('players',[]) for a in p.get('recent_arrivals',[])])
    if followup:lead.append(followup_line(followup))
    if not lead:lead=[deck]
    sections=[section(label+'回顾',lead,refs)]
    if standings:sections.append(section('完整排名与晋级名单',standings,refs))
    route=routes(d.get('bracket',[]))
    if route:sections.append(section('接下来的对阵',route,refs))
    return article(packet,title,deck,sections),[{'story_id':'phase','template_id':'feature.phase.'+stage,'fact_ids':refs}]
