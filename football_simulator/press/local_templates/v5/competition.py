"""Competition retrospectives from authoritative edition archives."""
from football_simulator.press.local_templates.v5.common import names,match_line,section,article
from football_simulator.press.local_templates.v5.phase import table_paragraphs


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
    from collections import defaultdict
    from football_simulator.press.local_templates.v5.narrative import production,season_numbers,history_lines,followup_line,related_followup,arrival_line
    from football_simulator.press.expectations import surprise_paragraphs
    d=plan['content'];refs=plan['fact_ids'];comp=d['competition'];champ=d['champion'];year=d['edition']['game_season']
    title=champ+'捧起'+comp+'冠军';deck=f"第{year}赛季的{comp}属于{champ}。"
    if comp=='Promotion Playoffs':title=champ+'赢下升级附加赛';deck=champ+'拿到了本季最后一个升级名额。'
    lead=[];finals=d.get('decisive_games',[])
    if not finals and d.get('champion_route'):
        finals=[g for g in d['champion_route'] if g['stage'].startswith(('决赛','总决赛','升级附加赛决赛'))]
    runner=d.get('runner_up')
    if not runner and finals:
        last=finals[-1]
        if champ in (last['home'],last['away']):runner=last['away'] if last['home']==champ else last['home']
    if finals:
        text=''.join(g['stage']+'，'+match_line(g,venue=False) for g in finals)
        final_tie=next((t for t in d.get('bracket',[]) if t['stage'] in ('决赛','升级附加赛决赛') and t.get('aggregate')),None)
        if final_tie:text+=tie_line(final_tie)
        text+=champ+('由此拿到了升级名额。' if comp=='Promotion Playoffs' else '由此捧起'+comp+'冠军奖杯。')
        people=d.get('decisive_performances',[])
        if people:
            text+=''.join(p['player']+'为'+p['team']+production(p)+'。'+arrival_line(p['player'],finals[-1].get('player_arrivals',{}).get(p['player'])) for p in people)
        if runner:text+=runner+'最终获得亚军。'
        lead.append(text)
    else:
        rows=d.get('final_table',[])
        lead.append((f"{champ}以{rows[0]['points']}分结束赛季，拿到{comp}冠军。" if rows else deck))
    history=history_lines(d.get('narrative_history',[]))
    if 'narrative_history' not in d:history=''.join(honours(d.get('history')))
    if history:lead.append(history)
    campaign=d.get('champion_campaign',{})
    if all(k in campaign for k in ('matches','W','goals_for','goals_against')):
        record=(f"取得{campaign['W']}胜{campaign['D']}平{campaign['L']}负" if 'D' in campaign and 'L' in campaign
                else f"赢下{campaign['W']}场")
        text=(f"这座奖杯背后，{champ}在{campaign['matches']}场比赛中{record}，"
              f"打进{campaign['goals_for']}球，失{campaign['goals_against']}球。")
        if campaign.get('shootout_wins'):text+=f"他们还赢下了{campaign['shootout_wins']}次点球大战。"
        lead.append(text)
    turns=[g for g in d.get('champion_route',[]) if g.get('shootout_home_away') and g not in finals]
    if turns:
        g=turns[0];lead.append('夺冠路上，'+g['stage']+'一战走到点球决胜：'+match_line(g,venue=False))
    if runner:
        previous=[];seen=set()
        for tie in d.get('bracket',[]):
            if runner not in tie.get('teams',[]):continue
            for g in tie.get('matches',[]):
                if g['stage'].startswith(('决赛','总决赛','升级附加赛决赛')):continue
                key=(g['stage'],g['home'],g['away'])
                if key not in seen:previous.append(g);seen.add(key)
        if previous:
            notable=sorted(previous,key=lambda g:(not bool(g.get('shootout_home_away')),not g['stage'].startswith('半决赛')))[:2]
            conventional={'32强':0,'16强':1,'八强':2,'半决赛':3}
            if all(g.get('week') is not None for g in notable):notable.sort(key=lambda g:g['week'])
            elif all(g['stage'] in conventional for g in notable):notable.sort(key=lambda g:conventional[g['stage']])
            lead.append('回看'+runner+'此前的征程：'+''.join(g['stage']+'，'+match_line(g,venue=False) for g in notable))
    surprises=surprise_paragraphs(d.get('campaign_surprises',[]),campaign=True)
    if surprises:lead.append(''.join(surprises))
    followup=related_followup(d,[a for g in finals for a in g.get('player_arrivals',{}).values()])
    if followup:lead.append(followup_line(followup))
    sections=[section('这座奖杯如何到来',lead,refs)]
    award_people=defaultdict(list)
    for label,winners in d.get('awards',{}).items():
        for p in winners:award_people[p['player']].append((label,p))
    awards=[]
    for player,items in award_people.items():
        descriptions=[]
        for label,p in items:
            if label=='射手王':descriptions.append(f"以{p['value']}粒进球获得射手王")
            elif label=='助攻王':descriptions.append(f"以{p['value']}次助攻获得助攻王")
            elif label=='赛事EVP':descriptions.append('入选本届赛事EVP')
            else:descriptions.append('获评赛事MVP')
            if p.get('retained_award') and label in ('射手王','助攻王'):descriptions[-1]+='，连续两届赢得这一奖项'
        text=player+'、'.join(descriptions)+'。'
        if len(items)>1:text+='他的本届赛事以'+str(len(items))+'项正式个人奖收官。'
        performance=items[0][1].get('season_performance')
        if performance:text+=season_numbers(performance,scope='在本届赛事')
        teams=items[0][1].get('actual_teams_in_edition',[])
        if len(teams)>1:
            text+='这些表现来自他在'+names(t['team'] for t in teams)+'的效力经历。'
        awards.append(text)
    if awards:sections.append(section('奖杯之外的个人收获',awards,refs))
    route=[]
    games=d.get('champion_route',d.get('key_results',[]))
    for game in games:route.append(game['stage']+'：'+match_line(game))
    for tie in d.get('bracket',[]):
        if champ in tie['teams'] and tie.get('aggregate'):route.append(tie_line(tie))
    if route:sections.append(section('夺冠路线' if d.get('champion_route') else '赛季关键场次',route,refs))
    if d.get('final_table'):
        paragraphs=table_paragraphs(d['final_table'])
        for key,verb in [('directly_promoted','直接升级'),('promotion_playoff','进入升级附加赛'),('relegated','降级')]:
            if d.get('qualification',{}).get(key):paragraphs.append(names(d['qualification'][key])+verb+'。')
        sections.append(section('最终排名与资格',paragraphs,refs))
    return article(packet,title,deck,sections),[{'story_id':'competition','template_id':'feature.competition','fact_ids':refs}]
