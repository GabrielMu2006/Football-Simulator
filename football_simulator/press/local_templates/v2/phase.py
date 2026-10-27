"""Stage reports: the full outcome first, then performances and known routes."""
from football_simulator.press.local_templates.v2.common import names,match_line,stats,section,article


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
    d=plan['content'];refs=plan['fact_ids'];comp=d['competition'];sections=[]
    stage=d['stage'];label={'halfway':'半程','groups':'小组阶段','swiss':'瑞士轮','playin':'突围赛'}[stage]
    title=comp+label+'收官'
    paragraphs=[]
    if d.get('halfway_table'):
        first=d['halfway_table'][0]
        title=first['team']+'领跑'+comp+'半程'
        pending='最终冠军和升级资格尚未确定' if comp=='Second Division' else '最终冠军和降级名单尚未确定'
        paragraphs.append(f"{first['team']}以{first['points']}分领跑半程积分榜。联赛仍有后半程赛程，{pending}。")
        paragraphs+=table_paragraphs(d['halfway_table'])
    for group,rows in d.get('groups',{}).items():
        paragraphs.append(group+'组排名：' if group not in ('登峰组','涅槃组') else group+'排名：')
        paragraphs+=table_paragraphs(rows)
    if d.get('swiss_records'):
        ordered=sorted(d['swiss_records'],key=lambda r:(r.get('seed') is None,r.get('seed') or 0))
        paragraphs+=['；'.join(f"{r['team']}{r['wins']}胜{r['losses']}负"+(f"，最终第{r['seed']}号种子" if r.get('seed') else '') for r in ordered[i:i+4])+'。' for i in range(0,len(ordered),4)]
    if d.get('draw_pots'):
        paragraphs.append('回看小组抽签分档，'+'；'.join(f"第{pot}档为"+names(r['team'] for r in d['draw_pots'] if r['pot']==pot) for pot in range(1,5))+'。')
    for key,verb in [('qualified','晋级'),('direct_quarterfinalists','直通八强'),('playin_winners','通过突围赛进入八强'),('eliminated','本阶段出局')]:
        if d.get(key):paragraphs.append(names(d[key])+verb+'。')
    sections.append(section(label+'结果',paragraphs,refs))
    route=routes(d.get('bracket',[]))
    if route:sections.append(section('下一阶段对阵',route,refs))
    history=[r['team']+'首次进入'+comp+'八强。' for r in d.get('history_breakthroughs',[]) if r.get('first_time') and r.get('history_complete')]
    if history:sections.append(section('队史突破',history,refs))
    from football_simulator.press.local_templates.v2.background import surprise_paragraphs
    surprises=surprise_paragraphs(d.get('group_surprises',[])+d.get('qualification_surprises',[]))
    if surprises:sections.append(section('冷门与黑马观察',surprises,refs))
    time='半程过后' if stage=='halfway' else label+'结束时'
    people=[f"{time}，{p['player']}已在本届赛事{stats(p)}。" for p in d.get('players',[])]
    if people:sections.append(section('阶段人物',people,refs))
    games=[g['stage']+'，'+match_line(g) for g in d.get('key_results',[])]
    if games:sections.append(section('代表比赛',games,refs))
    deck=paragraphs[0] if d.get('halfway_table') else comp+label+'结束，'+(f"{len(d['qualified'])}支球队拿到晋级名额。" if d.get('qualified') else '本阶段结果与后续赛程如下。')
    return article(packet,title,deck,sections),[{'story_id':'phase','template_id':'feature.phase.'+stage,'fact_ids':refs}]
