"""Completed player exchanges, with explicit directions and window endpoints."""
from football_simulator.press.local_templates.v1.common import names,stats,section,article

ROLES={'GK':'门将','DF':'后卫','MF':'中场','FW':'前锋'}


def exchange(trade):
    teams=list(dict.fromkeys(p['from_team'] for p in trade['moves']))
    if len(teams)!=2:
        return '；'.join(p['player']+'从'+p['from_team']+'前往'+p['to_team'] for p in trade['moves'])+'。'
    a,b=teams;sent_a=names(p['player'] for p in trade['moves'] if p['from_team']==a)
    sent_b=names(p['player'] for p in trade['moves'] if p['from_team']==b)
    return f"第{trade['week']}周，{a}送出{sent_a}，从{b}得到{sent_b}。"


def render(packet,plan):
    d=plan['content'];refs=plan['fact_ids'];label='冬季' if d['window']=='winter' else '夏季'
    title=label+'转会窗落幕：'+str(d['completed_trades'])+'笔交换完成'
    deck=f"第{packet['meta']['season']}赛季{label}转会窗关闭，已完成的球员交换改变了相关球队的阵容。" if d['completed_trades'] else label+'转会窗已经关闭，本窗没有完成的球员交换。'
    sections=[];focus=[]
    for trade in d['focus_trades']:
        focus.append(exchange(trade))
        for p in trade['moves']:
            note=p['player']+'司职'+ROLES.get(p['position'],p['position'])+'。'
            origin=p.get('origin',{})
            if origin.get('actual_pick') is not None and origin['actual_pick']<=5:
                note+=f"他是第{origin['draft_season']}赛季实际第{origin['actual_pick']}顺位新秀。"
            if p.get('historical_top20'):note+='此前曾入选年度Top20。'
            if p.get('appearances') is not None:note+='截至关窗，本赛季代表各队累计'+stats(p)+'。'
            focus.append(note)
    if focus:sections.append(section('重点交换',focus,refs))
    if d.get('other_trades'):sections.append(section('其余已完成交易',[exchange(r) for r in d['other_trades']],refs))
    endpoints=[]
    for team in d.get('window_team_rosters',[]):
        before=team['window_open_real_counts'];after=team['window_closed_real_counts']
        changed=[f"{ROLES[pos]}由{before[pos]}人变为{after[pos]}人" for pos in ROLES if pos in before and pos in after and before[pos]!=after[pos]]
        text=team['team']+'：'+('，'.join(changed) if changed else '窗口首尾各位置真实球员人数相同')+'。'
        endpoints.append(text)
    if endpoints:sections.append(section('关窗后的阵容',endpoints,refs))
    if not sections:sections=[section('窗口结束',[deck],refs)]
    return article(packet,title,deck,sections),[{'story_id':'transfer','template_id':'feature.transfer','fact_ids':refs}]
