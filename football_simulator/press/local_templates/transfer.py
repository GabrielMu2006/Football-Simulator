"""Completed player exchanges, with explicit directions and window endpoints."""
from football_simulator.press.local_templates.common import names,stats,section,article

ROLES={'GK':'门将','DF':'后卫','MF':'中场','FW':'前锋'}


def exchange(trade):
    teams=list(dict.fromkeys(p['from_team'] for p in trade['moves']))
    if len(teams)!=2:
        return '；'.join(p['player']+'从'+p['from_team']+'前往'+p['to_team'] for p in trade['moves'])+'。'
    a,b=teams;sent_a=names(p['player'] for p in trade['moves'] if p['from_team']==a)
    sent_b=names(p['player'] for p in trade['moves'] if p['from_team']==b)
    return f"第{trade['week']}周，{a}送出{sent_a}，从{b}得到{sent_b}。"


def move_story(p,*,profile=True):
    from football_simulator.press.local_templates.narrative import season_numbers,high_pick_line
    story=p.get('transfer_story',{});name=p['player'];parts=[]
    if story.get('return_to_draft_club'):
        origin=story['draft_origin']
        parts.append(f"{name}回到了最初选中自己的{p['to_team']}。第{origin['season']}赛季，这支球队曾用第{origin['pick']}顺位将他选入。")
    elif story.get('return_to_former_club'):parts.append(name+'重返旧主'+p['to_team']+'，完成回归。')
    if story.get('first_departure_after_seasons'):
        parts.append(f"对{name}而言，这是连续{story['first_departure_after_seasons']}个赛季代表{p['from_team']}出场后的首次离队。")
    peer=story.get('former_final_opponent')
    if peer:parts.append(f"第{peer['season']}赛季的{peer['competition']}决赛中，{name}与{peer['player']}曾分属两支对阵球队，如今两人在{p['to_team']}成为队友。")
    if story.get('high_pick_teammates'):parts.append(high_pick_line(story['high_pick_teammates'],p['to_team'],name))
    if profile:
        origin=p.get('origin',{});credentials=[]
        if p.get('historical_top20'):credentials.append('曾进入年度Top20')
        if origin.get('actual_pick') is not None and origin['actual_pick']<=5 and not story.get('return_to_draft_club'):
            credentials.append(f"是第{origin['draft_season']}赛季第{origin['actual_pick']}顺位新秀")
        if credentials:parts.append(name+'，'.join(credentials)+'。')
        actual=p.get('actual_teams',[])
        subject=name if not parts else '他';scope='本季为'+actual[0]['team'] if len(actual)==1 else '本季合计'
        parts.append(season_numbers(p,subject=subject,scope=scope))
    return ''.join(parts)


def render(packet,plan):
    d=plan['content'];refs=plan['fact_ids'];label='冬季' if d['window']=='winter' else '夏季'
    title=label+'转会窗收官';deck=f"第{packet['meta']['season']}赛季{label}转会窗关闭，共有{d['completed_trades']}笔交换完成。"
    focus=d['focus_trades'];returnee=next((p for t in focus for p in t['moves'] if p.get('transfer_story',{}).get('return_to_former_club')),None)
    if returnee:title=returnee['player']+'回归'+returnee['to_team']+'，'+label+'转会窗收官'
    elif focus:
        player=focus[0]['moves'][0];title=player['player']+'转投'+player['to_team']+'，'+label+'转会窗收官'
    sections=[];stories=[]
    for trade in focus:
        stories.append(exchange(trade)+''.join(move_story(p) for p in trade['moves']))
    if stories:sections.append(section('这个窗口留下的故事',stories,refs))
    if d.get('other_trades'):
        sections.append(section('其余已完成的交换',[exchange(t)+''.join(move_story(p,profile=False) for p in t['moves']) for t in d['other_trades']],refs))
    endpoints=[]
    for team in d.get('window_team_rosters',[]):
        before=team['window_open_real_counts'];after=team['window_closed_real_counts']
        changed=[f"{ROLES[pos]}从{before[pos]}人变为{after[pos]}人" for pos in ROLES if pos in before and pos in after and before[pos]!=after[pos]]
        if changed:endpoints.append('交换完成后，'+team['team']+'的'+'，'.join(changed)+'。')
        else:endpoints.append(team['team']+'在开窗和关窗时，各位置的真实球员人数相同。')
    if endpoints:sections.append(section('阵容人数的变化',endpoints,refs))
    if not sections:sections=[section('窗口收官',[deck],refs)]
    return article(packet,title,deck,sections),[{'story_id':'transfer','template_id':'feature.transfer','fact_ids':refs}]
