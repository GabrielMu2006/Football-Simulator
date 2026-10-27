"""Stage reports: the full outcome first, then performances and known routes."""
from football_simulator.press.local_templates.common import names,match_line,stats,section,article


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


def render(packet,plan):
    from football_simulator.press.local_templates.narrative import history_lines,season_numbers,arrival_line,followup_line,related_followup
    from football_simulator.press.expectations import surprise_paragraphs
    d=plan['content'];refs=plan['fact_ids'];comp=d['competition'];stage=d['stage']
    label={'halfway':'半程','groups':'小组阶段','swiss':'瑞士轮','playin':'突围赛'}[stage]
    title=comp+label+'收官';lead=[];standings=[];qualified=d.get('qualified',d.get('direct_quarterfinalists',[]))
    history=history_lines(d.get('narrative_history',[]))
    surprises=surprise_paragraphs(d.get('group_surprises',[])+d.get('qualification_surprises',[]))
    if d.get('halfway_table'):
        first=d['halfway_table'][0];title=first['team']+'领跑'+comp+'半程'
        text=f"{first['team']}带着{first['points']}分结束前半程，暂居{comp}榜首。"
        if len(d['halfway_table'])>1:
            second=d['halfway_table'][1];gap=first['points']-second['points']
            text+=second['team']+('与其同分。' if gap==0 else f"以{gap}分之差紧随其后。")
        text+=('最终冠军和升级资格仍待后半程揭晓。' if comp=='Second Division' else '最终冠军和降级名单仍待后半程揭晓。')
        lead.append(text);standings+=table_paragraphs(d['halfway_table'])
    else:
        lead.append(f"{comp}{label}结束，"+(f"{len(qualified)}支球队继续向前。" if qualified else '本阶段的晋级与出局名单已经落定。'))
        if history:lead.append(history)
        if surprises:lead.append(''.join(surprises))
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
        text='';highlights=p.get('representative_matches',[])
        if index==0 and highlights:
            from football_simulator.press.local_templates.narrative import production
            highlight=highlights[0];g=highlight['match'];seen_games.add(game_key(g))
            text=g['stage']+'，'+match_line(g,venue=False)+p['player']+'为'+highlight['player_team']+production(highlight)+'。'
        text+=season_numbers(p,subject=p['player'],scope=('在'+team+'的本届征程中' if team else '截至本阶段结束'))
        arrivals=p.get('recent_arrivals',[])
        if arrivals:text+=arrival_line(p['player'],arrivals[-1])
        people.append(text)
    if people:lead.append(''.join(people))
    breakthroughs=[r['team'] for r in d.get('history_breakthroughs',[]) if r.get('first_time') and r.get('history_complete')]
    if breakthroughs:lead.append('对于'+names(breakthroughs)+'，这还是队史第一次进入'+comp+'八强。')
    games=[g for g in d.get('key_results',[]) if game_key(g) not in seen_games]
    if games:
        lead.append('回看这一阶段，'+''.join(g['stage']+'，'+match_line(g,venue=False) for g in games))
    followup=related_followup(d,[a for p in d.get('players',[]) for a in p.get('recent_arrivals',[])])
    if followup:lead.append(followup_line(followup))
    sections=[section(label+'回顾',lead,refs)]
    if standings:sections.append(section('完整排名与晋级名单',standings,refs))
    route=routes(d.get('bracket',[]))
    if route:sections.append(section('接下来的对阵',route,refs))
    return article(packet,title,lead[0],sections),[{'story_id':'phase','template_id':'feature.phase.'+stage,'fact_ids':refs}]
