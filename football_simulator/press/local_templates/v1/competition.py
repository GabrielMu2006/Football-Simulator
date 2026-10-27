"""Competition retrospectives from authoritative edition archives."""
from football_simulator.press.local_templates.v1.common import names,match_line,section,article
from football_simulator.press.local_templates.v1.phase import table_paragraphs


def tie_line(tie):
    text=tie['stage']+'，'+'对阵'.join(tie['teams'])+'。'
    if tie.get('aggregate'):
        a,b=tie['aggregate'];text+=f"两回合总比分{a['team']} {a['goals']}–{b['goals']} {b['team']}。"
    if tie.get('advancing_or_winning_team'):text+=tie['advancing_or_winning_team']+'赢下对阵。'
    return text


def honours(history):
    if not history or not history.get('complete_history_from_first_season'):return []
    team=history['team'];years=history.get('known_title_seasons',[]);lines=[]
    if years:lines.append(team+('首次赢得' if len(years)==1 else f'第{len(years)}次赢得')+history['competition']+'冠军。')
    if history.get('consecutive_titles'):lines.append(f"{team}已在这项赛事完成{history['consecutive_titles']}连冠。")
    if history.get('career_grand_slam'):lines.append(team+'已在队史中集齐一级联赛和六项杯赛冠军。')
    if history.get('season_grand_slam'):lines.append(team+'本赛季包揽一级联赛和六项杯赛冠军。')
    return lines


def render(packet,plan):
    d=plan['content'];refs=plan['fact_ids'];comp=d['competition'];champ=d['champion']
    title=champ+'赢得'+comp+'冠军';year=d['edition']['game_season'];edition=d['edition']['edition_number']
    if comp=='Promotion Playoffs':title=champ+'通过升级附加赛取得升级资格'
    deck=f"第{year}赛季{comp}第{edition}届结束，{champ}成为最终优胜者。"
    campaign=d['champion_campaign'];intro=[champ+'完成本届夺冠征程。']
    keys=('matches','W','D','L','goals_for','goals_against')
    if all(k in campaign for k in keys):
        intro=[f"{champ}在本届{campaign['matches']}场比赛中取得场上{campaign['W']}胜{campaign['D']}平{campaign['L']}负，进{campaign['goals_for']}球、失{campaign['goals_against']}球。"]
    if campaign.get('shootout_wins'):intro.append(f"其中另有{campaign['shootout_wins']}次点球大战胜出。")
    if d.get('runner_up'):intro.append(d['runner_up']+'获得本届亚军。')
    sections=[section('冠军的赛季',intro+honours(d.get('history')),refs)]
    from football_simulator.press.local_templates.v1.background import paragraph
    background=paragraph(champ,d.get('team_backgrounds',{}).get(champ))
    if background:sections[0]['blocks'].append({'kind':'body','label':None,'text':background,'fact_ids':refs})
    from football_simulator.press.local_templates.v1.background import surprise_paragraphs
    surprises=surprise_paragraphs(d.get('campaign_surprises',[]))
    if surprises:sections.append(section('冷门与黑马',surprises,refs))
    route=[]
    if d.get('champion_route'):
        for game in d['champion_route']:route.append(game['stage']+'，'+match_line(game,venue=False))
        # Aggregate advancement is separate from a potentially lost second leg.
        for tie in d.get('bracket',[]):
            if champ in tie['teams'] and tie.get('aggregate'):route.append(tie_line(tie))
    else:
        route=[game['stage']+'，'+match_line(game) for game in d.get('key_results',[])]
    if route:sections.append(section('冠军路线' if d.get('champion_route') else '赛季关键场次',route,refs))
    performances=[]
    for p in d.get('decisive_performances',[]):
        parts=[]
        if p.get('goals'):parts.append(f"打进{p['goals']}球")
        if p.get('assists'):parts.append(f"送出{p['assists']}次助攻")
        if p.get('position')=='GK' and p.get('successful_saves') is not None:parts.append(f"完成{p['successful_saves']}次扑救")
        if parts:performances.append(p['player']+'在决胜场为'+p['team']+'、'.join(parts)+'。')
    if performances:sections.append(section('决胜场人物',performances,refs))
    awards=[]
    for label,winners in d.get('awards',{}).items():
        for p in winners:
            text=p['player']+'获得'+label
            if label=='射手王':text+=f"，整届打进{p['value']}球"
            if label=='助攻王':text+=f"，整届送出{p['value']}次助攻"
            awards.append(text+'。')
            teams=p.get('actual_teams_in_edition',[])
            if len(teams)>1:
                metric='goals' if label=='射手王' else 'assists' if label=='助攻王' else 'appearances'
                unit='球' if metric=='goals' else '次助攻' if metric=='assists' else '次出场'
                awards.append('本届代表球队分项：'+'；'.join(f"{t['team']} {t[metric]}{unit}" for t in teams if metric in t)+'。')
    if awards:sections.append(section('正式个人奖项',awards,refs))
    if d.get('final_table'):
        paragraphs=table_paragraphs(d['final_table'])
        for key,verb in [('directly_promoted','直接升级'),('promotion_playoff','进入升级附加赛'),('relegated','降级')]:
            if d.get('qualification',{}).get(key):paragraphs.append(names(d['qualification'][key])+verb+'。')
        sections.append(section('最终排名与资格',paragraphs,refs))
    return article(packet,title,deck,sections),[{'story_id':'competition','template_id':'feature.competition','fact_ids':refs}]
