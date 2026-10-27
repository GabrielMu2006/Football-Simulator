"""Actual picks and the prior two cohorts, with recorded sporting outcomes."""
from football_simulator.press.local_templates.common import names,stats,section,article
from football_simulator.press.local_templates.transfer import ROLES


def team_result(row,player=None):
    parts=[];finish=row.get('league_finish')
    if finish:parts.append(f"在{finish['competition']}位列第{finish['rank']}")
    if row.get('championship_count') is not None:
        count=row['championship_count'];parts.append(f"全年拿到{count}座冠军" if count else '全年未能夺冠')
    if row.get('honor_points') is not None:parts.append(f"球队荣誉积分为{row['honor_points']}分")
    text=row['team']+'，'.join(parts)+'。' if parts else ''
    if row.get('team_championships') and set(row['team_championships'])!=set(row.get('player_roster_titles',[])):
        text+='球队赢得了'+names(row['team_championships'])+'冠军。'
    if row.get('player_roster_titles'):text+=(player or '他')+'随队捧起的奖杯包括'+names(row['player_roster_titles'])+'冠军。'
    return text


def render(packet,plan):
    d=plan['content'];refs=plan['fact_ids'];year=packet['meta']['season']
    picks=sorted(d['picks'],key=lambda r:r['actual_pick'])
    title=f'第{year}赛季选秀落定'
    deck=(f"{picks[0]['team']}用第{picks[0]['actual_pick']}顺位选中{picks[0]['player']}，本届共有{len(picks)}名球员被选中。" if picks else '本届选秀已经完成，没有球队选中球员。')
    sections=[];first=[];others=[]
    for p in picks:
        text=f"第{p['actual_pick']}顺位，{p['team']}选中{ROLES.get(p['position'],p['position'])}{p['player']}。"
        if p['actual_pick']<=5:
            text+=team_result(p.get('receiving_team_record',{'team':p['team']}));first.append(text)
        else:others.append(text)
    if first:sections.append(section('本届前五顺位',first,refs))
    if others:sections.append(section('其余顺位',others,refs))
    progress=[]
    for p in d.get('previous_two_cohorts',[]):
        lines=[f"{p['player']}在第{p['draft_season']}赛季由{p['draft_team']}以第{p['actual_pick']}顺位选中。"]
        if p.get('current_registered_team'):lines.append('目前效力于'+p['current_registered_team']+'。')
        for year_card in p['by_season']:
            text=f"第{year_card['season']}赛季，他"
            if year_card.get('top20_rank') is not None:text+=f"在年度Top20排名第{year_card['top20_rank']}，"
            elif year_card.get('top20_known'):text+='未入选年度Top20，'
            text+=stats(year_card)+'。'
            lines.append(text)
            lines += [team_result(r,p['player']) for r in year_card.get('team_records',[]) if team_result(r)]
        progress.append(''.join(lines))
    if progress:sections.append(section('前两届新秀回顾',progress,refs))
    if d.get('unselected'):
        sections.append(section('未选中的候选',[names(r['player'] for r in d['unselected'])+'本届未被选中，不列入顺位名单。'],refs))
    if not sections:sections=[section('本届结果',[deck],refs)]
    return article(packet,title,deck,sections),[{'story_id':'draft','template_id':'feature.draft','fact_ids':refs}]
