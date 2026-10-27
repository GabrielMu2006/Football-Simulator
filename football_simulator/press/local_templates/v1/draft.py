"""Actual picks and the prior two cohorts, with recorded sporting outcomes."""
from football_simulator.press.local_templates.v1.common import names,stats,section,article
from football_simulator.press.local_templates.v1.transfer import ROLES


def team_result(row):
    parts=[];finish=row.get('league_finish')
    if finish:parts.append(f"{finish['competition']}第{finish['rank']}名")
    if row.get('championship_count') is not None:parts.append(f"全季{row['championship_count']}冠")
    if row.get('honor_points') is not None:parts.append(f"单季球队荣誉分{row['honor_points']}分")
    text=row['team']+'取得'+'、'.join(parts)+'。' if parts else ''
    if row.get('team_championships'):text+='球队冠军来自'+names(row['team_championships'])+'。'
    if row.get('player_roster_titles'):text+='该球员在夺冠阵容中实际随队获得的冠军为'+names(row['player_roster_titles'])+'。'
    return text


def render(packet,plan):
    d=plan['content'];refs=plan['fact_ids'];year=packet['meta']['season']
    picks=sorted(d['picks'],key=lambda r:r['actual_pick'])
    title=f'第{year}赛季选秀落定'
    deck=(f"{picks[0]['team']}以实际第{picks[0]['actual_pick']}顺位选中{picks[0]['player']}，本届共有{len(picks)}人被选中。" if picks else '本届选秀已经完成，没有球员被实际选中。')
    sections=[];first=[];others=[]
    for p in picks:
        text=f"第{p['actual_pick']}顺位，{p['team']}选中{ROLES.get(p['position'],p['position'])}{p['player']}。"
        if p['actual_pick']<=5:
            text+=team_result(p.get('receiving_team_record',{'team':p['team']}));first.append(text)
        else:others.append(text)
    if first:sections.append(section('本届前五顺位',first,refs))
    if others:sections.append(section('其余实际选中结果',others,refs))
    progress=[]
    for p in d.get('previous_two_cohorts',[]):
        lines=[f"第{p['draft_season']}赛季第{p['actual_pick']}顺位新秀{p['player']}当年由{p['draft_team']}选中。"]
        if p.get('current_registered_team'):lines.append('报道时登记球队为'+p['current_registered_team']+'。')
        for year_card in p['by_season']:
            text=f"第{year_card['season']}赛季，"+stats(year_card)+'。'
            if year_card.get('top20_rank') is not None:text+=f"年度Top20排名第{year_card['top20_rank']}。"
            elif year_card.get('top20_known'):text+='没有入选该季Top20。'
            lines.append(text)
            lines += [team_result(r) for r in year_card.get('team_records',[]) if team_result(r)]
        progress.append(''.join(lines))
    if progress:sections.append(section('前两届新秀回顾',progress,refs))
    if d.get('unselected'):
        sections.append(section('未选中的候选',[names(r['player'] for r in d['unselected'])+'本届未被选中，没有实际选中顺位。'],refs))
    if not sections:sections=[section('本届结果',[deck],refs)]
    return article(packet,title,deck,sections),[{'story_id':'draft','template_id':'feature.draft','fact_ids':refs}]
