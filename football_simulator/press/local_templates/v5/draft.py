"""Actual picks and the prior two cohorts, with recorded sporting outcomes."""
from football_simulator.press.local_templates.v5.common import names,stats,section,article
from football_simulator.press.local_templates.v5.transfer import ROLES


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
    from football_simulator.press.local_templates.v5.narrative import season_numbers,high_pick_line
    d=plan['content'];refs=plan['fact_ids'];year=packet['meta']['season'];picks=sorted(d['picks'],key=lambda r:r['actual_pick'])
    title=f'第{year}赛季选秀落定'
    deck='本届选秀结束，没有球员被选中。'
    first=[];others=[]
    if picks:
        head=picks[0];title=head['team']+'选中'+head['player']
        deck=f"{head['team']}用第{head['actual_pick']}顺位写下{head['player']}的名字，第{year}赛季共有{len(picks)}名球员被选中。"
    for p in picks:
        role=ROLES.get(p['position'],p['position']);record=p.get('receiving_team_record',{});finish=record.get('league_finish')
        text=(f"在{finish['competition']}以第{finish['rank']}名结束赛季的{p['team']}，" if finish and p['actual_pick']<=5 else p['team'])
        text+=f"用第{p['actual_pick']}顺位选中了{role}{p['player']}。"
        if p['actual_pick']<=5:
            # Club results belong to the ended season, before this rookie played.
            text+=team_result({k:v for k,v in record.items() if k!='league_finish'}) if record.get('team') else ''
            if not finish and p.get('team_season_finish'):text+=p['team_season_finish']
            text+=high_pick_line(p.get('high_pick_teammates',[]),p['team'],p['player'])
            first.append(text)
        else:others.append(text)
    sections=[]
    if first:sections.append(section('新名字进入球队',first,refs))
    if others:sections.append(section('其余选中结果',others,refs))
    progress=[]
    for p in d.get('previous_two_cohorts',[]):
        text=p['player']+f"是第{p['draft_season']}赛季由{p['draft_team']}选中的第{p['actual_pick']}顺位新秀。"
        cards=p['by_season'];ranked=[c for c in cards if c.get('top20_rank') is not None]
        if len(ranked)>=2:
            text+='从'+f"第{ranked[0]['season']}赛季的Top20第{ranked[0]['top20_rank']}名，到第{ranked[-1]['season']}赛季的第{ranked[-1]['top20_rank']}名，他在两个赛季都进入了年度榜单。"
        elif ranked:
            last=ranked[-1];text+=f"第{last['season']}赛季，他在年度Top20排名第{last['top20_rank']}。"
        elif cards and all(c.get('top20_known') for c in cards):text+='回看这两个赛季，他还没有进入年度Top20。' if len(cards)==2 else '刚结束的赛季，他尚未进入年度Top20。'
        if p.get('current_registered_team'):text+='目前他效力于'+p['current_registered_team']+'。'
        for card in cards:
            actual=card.get('actual_teams',[])
            if len(actual)>1:
                text+=f"第{card['season']}赛季，他曾代表"+names(t['team'] for t in actual)+'出场。'
            records=card.get('team_records',[])
            text+=season_numbers(card,scope=f"在第{card['season']}赛季")
            if len(actual)>1:
                for team in actual:
                    text+=season_numbers({**team,'position':card.get('position')},scope='为'+team['team'])
                    record=next((r for r in records if r['team']==team['team']),None)
                    if record:text+=team_result(record,p['player'])
            else:text+=''.join(team_result(r,p['player']) for r in records)
        progress.append(text)
    if progress:sections.append(section('曾经的高顺位新秀，如今走到哪里',progress,refs))
    contrasts=[]
    for r in d.get('draft_class_contrasts',[]):
        text=f"第{r['season']}赛季第{r['pick']}顺位的{r['player']}本季进入Top20，排在第{r['rank']}名。"
        text+=(f"同届第{r['first_pick']}顺位的{r['first_player']}排在第{r['first_rank']}名。" if r['first_rank'] is not None else f"同届第{r['first_pick']}顺位的{r['first_player']}本季未进入榜单。")
        contrasts.append(text)
    if contrasts:sections.append(section('同届新秀的不同轨迹',contrasts,refs))
    if d.get('unselected'):sections.append(section('本届未选中的球员',[names(r['player'] for r in d['unselected'])+'本届未被选中，不列入实际顺位。'],refs))
    if not sections:sections=[section('本届选秀',[deck],refs)]
    return article(packet,title,deck,sections),[{'story_id':'draft','template_id':'feature.draft','fact_ids':refs}]
