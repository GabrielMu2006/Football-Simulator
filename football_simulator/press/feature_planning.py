"""Testable feature requests and local assembly; no API dispatch or game writes."""
import json
from pathlib import Path

from football_simulator.press import PressError
from football_simulator.press import feature_briefing as brief
from football_simulator.press.planning import name_aliases,prepare_story_article,assemble_story_articles
from football_simulator.press.repository import digest

PROMPT='feature-author/11'
KIND_NOTES={
 'phase':'阶段收官，分析已确定的结果、晋级、出局与下一阶段路线。',
 'competition':'整届赛事已结束，回顾夺冠路线、转折、正式个人奖项与有记录的历史意义。',
 'transfer':'窗口已关闭，回顾真实完成的球员交换，重点是明星、突出表现者和历届实际前5顺位新秀。',
 'draft':'本届选秀已完成，介绍实际选中结果、球队背景，回看前两届新秀的发展。',
 'top20':'榜单排位已由游戏结算。为入选球员写赛季人物专栏，围绕表现、代表作、奖项和球队经历展开。'
}


def generation_plan(packet):
    kind=packet['meta']['report_type'];data=packet['facts'][0]['data'];fid=packet['facts'][0]['fact_id']
    system=(Path(__file__).resolve().parents[2]/'docs/press/prompts/feature-author.md').read_text(encoding='utf-8').split('\n---\n',1)[1].strip()
    pieces=[]
    def add(key,angle,content,words,heading=None):
        pieces.append({'key':key,'angle':angle,'content':content,'words':words,'heading':heading})
    if kind=='top20':
        rankings=data['rankings']
        add('overview','榜单更替回顾',brief.top20_overview(data),[300,450],heading='榜单更替')
        for player in rankings:
            content=brief.top20_player(player)
            add(f"rank-{player['rank']:02}",'赛季人物点评',content,[150,220],heading=f"第{player['rank']}名 · {player['player']}")
    elif kind=='competition':
        add('competition','整届回顾：比赛路线、奖项与历史意义',brief.competition(data),[1200,1800])
    elif kind=='draft':
        content=brief.draft_newcomers(data)
        if data['previous_two_cohorts']:
            content.update(brief.draft_development(data,packet['meta']))
        add('draft','本届选秀与前两届新秀发展',content,[1200,1800])
    elif kind=='phase':
        add('phase',KIND_NOTES[kind],brief.phase(data),[1200,1800])
    elif kind=='transfer':
        add('transfer',KIND_NOTES[kind],brief.transfer(data,packet['meta']),[1200,1800])
    else:
        add(kind,KIND_NOTES[kind],data,[1200,1800] if kind=='phase' else [800,1400])
    stories=[];units=[]
    for piece in pieces:
        sid=piece['key'];stories.append({'id':sid,'angle':piece['angle'],'content':piece['content'],'fact_ids':[fid]})
        context=KIND_NOTES[kind]
        if kind=='transfer':
            context='窗口已关闭。围绕球员产出、交换的位置构成与窗口首尾的真实阵容变化展开评论；具体交易动机仍是媒体推测。'
        elif kind=='top20':
            context='正式榜单已经结算；入选经历与名次里程碑分别整理，重返榜单指恢复入选，首次或再次登顶由历史第1名记录确定。围绕这些履历和赛季表现写人物评论。'
        if kind=='phase':
            context=('联赛第19轮收官，回顾半程走势与争冠、升级或保级形势。' if data['stage']=='halfway'
                     else '杯赛当前大阶段结束，分析已确定的晋级、出局和下一阶段对阵。')
        payload={'report_type':kind,'season':packet['meta']['season'],'completed_week':packet['meta']['completed_week'],
                 'focus':piece['angle'],'context':context,'target_words_zh':piece['words'],'content':piece['content']}
        units.append({'competition_id':sid,'story_ids':[sid],'heading':piece['heading'],'prompt_versions':[PROMPT],
                      'request':{'model':'deepseek-flash','thinking':{'type':'enabled'},'reasoning_effort':'high','max_tokens':12288,'messages':[
                        {'role':'system','content':system},{'role':'user','content':json.dumps(payload,ensure_ascii=False,separators=(',',':'))}]}})
    return {'strategy':'features/2','assembly':{'summary':'lead-only'},'sections':units,
            'packet_sha256':digest(packet),'editorial_plan':{'name_aliases':name_aliases(packet),'stories':stories}}


def prepare(raw,packet,plan,unit):
    article=prepare_story_article(raw,packet,plan,unit)
    if unit.get('heading'):
        # The actual Top20 ordering/name comes from the game, not model prose.
        blocks=[block for s in article['sections'] for block in s['blocks']]
        article['sections']=[{'heading':unit['heading'],'heading_fact_ids':[packet['facts'][0]['fact_id']],'blocks':blocks}]
    return article


def assemble(packet,plan,fragments):
    expected=[u['competition_id'] for u in plan['sections']]
    if [key for key,_ in fragments]!=expected:raise PressError('专题分段不完整或顺序不符。')
    return assemble_story_articles(packet,fragments,plan=plan)
