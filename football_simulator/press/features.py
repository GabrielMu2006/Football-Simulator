"""Read-only feature-report material, independent of UI and automatic queues.

Snapshots must be taken at the actual event boundary. Full evidence stays local;
the writer receives selected, named facts and no Top20 scoring formula.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from functools import cached_property
import json

from football_simulator.game import rules
from football_simulator.press import PressError
from football_simulator.press.repository import digest


def stage_name(stage):
    for prefix,label in [('quarterfinal_leg_','八强'),('semifinal_leg_','半决赛'),('final_leg_','决赛'),
                         ('promotion_semifinal_leg_','升级附加赛半决赛'),('promotion_final_leg_','升级附加赛决赛')]:
        if stage.startswith(prefix):return label+('首回合' if stage.endswith('1') else '次回合')
    for prefix,label in [('swiss_','瑞士轮'),('cross_group_','组外循环'),('group_','小组赛')]:
        if stage.startswith(prefix):return label+'第'+stage[len(prefix):]+'轮'
    return rules.stage_label(stage)


def elimination_result(stage,winner,loser):
    if stage.startswith('wb_'):
        destination='进入总决赛' if stage=='wb_final' else '继续胜者组赛程'
        return f"{winner}{destination}；{loser}降入败者组，尚未出局。"
    if stage.startswith('lb_'):
        destination='进入总决赛' if stage=='lb_final' else '继续败者组赛程'
        return f"{winner}{destination}；{loser}第二败，正式出局。"
    if stage=='grand_final':return f"{winner}夺冠；{loser}获得亚军，总决赛无重置。"
    final=stage in ('final','promotion_final')
    return winner+('赢得本届赛事' if final else '赢得本轮并晋级')+f"；{loser}本届征程结束。"


def capture_snapshot(conn, completed_week, *, history_from=1):
    own_transaction=not conn.in_transaction
    if own_transaction:conn.execute('BEGIN')
    try:return _capture_snapshot(conn,completed_week,history_from)
    finally:
        if own_transaction:conn.rollback()


def _capture_snapshot(conn, completed_week, history_from):
    world = json.loads(conn.execute('SELECT data_json FROM world').fetchone()[0])
    season = int(world['season_number']); week = int(completed_week)
    boundary=52 if world['season'].get('season_complete') else int(world['season']['week'])-1
    if not 1 <= week <= 52 or week != boundary:
        raise PressError('专题需在实际事件边界冻结，不从后期世界回填早期快照。')
    # Registered rookies yet to enter the world are future material.
    world.pop('draft_pool', None); world.pop('story_events', None)
    for fixture in world['season']['fixtures']:
        for key in ('events', 'events_json', 'player_facts', 'shootout', 'shootout_json'):
            fixture.pop(key, None)
    matches = [dict(r) for r in conn.execute("SELECT * FROM matches WHERE status='completed' AND season_number>=? AND (season_number<? OR (season_number=? AND week<=?)) ORDER BY season_number,week,match_id", (history_from,season,season,week))]
    stats = [dict(r) for r in conn.execute('''SELECT p.* FROM player_match_stats p JOIN players x ON x.player_id=p.player_id
        JOIN matches m ON m.match_id=p.match_id WHERE x.is_real=1 AND m.status='completed'
        AND m.season_number>=? AND (m.season_number<? OR (m.season_number=? AND m.week<=?)) ORDER BY m.season_number,m.week,m.match_id,p.player_id''', (history_from,season,season,week))]
    from football_simulator.press.expectations import load
    # Keep long-service and old-final connections without copying every old
    # player match into each feature snapshot's three-season working window.
    service_rows=[dict(r) for r in conn.execute('''SELECT p.player_id,p.team_id,m.season_number,MIN(m.week) first_week
        FROM player_match_stats p JOIN players x ON x.player_id=p.player_id JOIN matches m ON m.match_id=p.match_id
        WHERE x.is_real=1 AND p.appeared=1 AND m.status='completed'
        AND (m.season_number<? OR (m.season_number=? AND m.week<=?))
        GROUP BY p.player_id,p.team_id,m.season_number ORDER BY p.player_id,m.season_number,p.team_id''',(season,season,week))]
    counts={r[0]:r[1] for r in conn.execute("SELECT season_number,COUNT(*) FROM matches WHERE status='completed' AND season_number<? GROUP BY season_number",(season,))}
    archived={int(a['season_number']) for a in world.get('archives',[]) if int(a['season_number'])<season}
    coverage=archived==set(range(1,season)) and all(counts.get(y)==766+sum(rules.CUP_META[c]['match_count'] for c in rules.CUP_IDS if rules.cup_active(c,y)) for y in archived)
    final_stages=('final','grand_final','final_leg_1','final_leg_2','promotion_final','promotion_final_leg_1','promotion_final_leg_2')
    placeholders=','.join('?' for _ in final_stages)
    old_finals=[dict(r) for r in conn.execute(f'''SELECT * FROM matches WHERE status='completed' AND stage IN ({placeholders})
        AND (season_number<? OR (season_number=? AND week<=?)) ORDER BY season_number,week,match_id''',(*final_stages,season,season,week))]
    final_stats=[dict(r) for r in conn.execute(f'''SELECT p.* FROM player_match_stats p JOIN players x ON x.player_id=p.player_id
        JOIN matches m ON m.match_id=p.match_id WHERE x.is_real=1 AND p.appeared=1 AND m.status='completed'
        AND m.stage IN ({placeholders}) AND (m.season_number<? OR (m.season_number=? AND m.week<=?))
        ORDER BY m.season_number,m.week,m.match_id,p.player_id''',(*final_stages,season,season,week))]
    return {'schema_version':'press.feature-sources/1','identity':{'world_id':world['world_id'],'source_world_version':world['version'],
            'season':season,'week':week},'world':world,'matches':matches,'player_match_stats':stats,
            'career_service_history':{'complete':coverage,'team_seasons':service_rows},
            'historical_finals':{'matches':old_finals,'player_match_stats':final_stats},
            'expectation_history':load(conn,season,week)}


class FeatureContext:
    def __init__(self, source):
        self.source=source;self.world=source['world'];self.season=int(source['identity']['season']);self.week=int(source['identity']['week'])
        identity=source['identity']
        if (source.get('schema_version')!='press.feature-sources/1' or identity['world_id']!=self.world['world_id']
                or identity['source_world_version']!=self.world['version'] or self.season!=self.world['season_number']
                or self.week!=(52 if self.world['season'].get('season_complete') else self.world['season']['week']-1)):
            raise PressError('专题来源身份或时点不符。')
        self.matches={r['match_id']:r for r in source['matches']};self.stats=source['player_match_stats']
        if any((m['season_number'],m['week'])>(self.season,self.week) for m in self.matches.values()):
            raise PressError('专题来源包含截止点之后的比赛。')
        if any(r['match_id'] not in self.matches or not self.world['players'].get(r['player_id'],{}).get('is_real') for r in self.stats):
            raise PressError('专题球员事实缺少比赛或真实身份。')
        if any(r['world_id']!=identity['world_id'] or r['season']!=self.season or r['before_week']>self.week
               or r['source_version']>identity['source_world_version'] for r in source.get('expectation_history',[])):
            raise PressError('赛前预期快照包含未来数据或其他世界。')
        finals=source.get('historical_finals',{})
        final_ids={m['match_id'] for m in finals.get('matches',[])}
        if any((m['season_number'],m['week'])>(self.season,self.week) for m in finals.get('matches',[])):
            raise PressError('决赛履历包含截止点之后的比赛。')
        if any(r['match_id'] not in final_ids or not self.world['players'].get(r['player_id'],{}).get('is_real') for r in finals.get('player_match_stats',[])):
            raise PressError('决赛履历缺少对应比赛或真实身份。')
        if any((r['season_number'],r['first_week'])>(self.season,self.week) for r in source.get('career_service_history',{}).get('team_seasons',[])):
            raise PressError('效力履历包含截止点之后的记录。')
        self.archives={int(a['season_number']):a for a in self.world.get('archives',[])}
        self.history_complete=set(range(1,self.season)) <= set(self.archives)
        self.stadiums={s['stadium_id']:s for s in rules.load_stadiums()}

    def team(self, tid): return self.world['teams'][tid]['club_en']
    def player(self, pid): return self.world['players'][pid]['name']

    @cached_property
    def story_notes(self):
        from football_simulator.press.story_context import game_notes
        return game_notes(self.world,list(self.matches.values()),self.stats,self.season,self.week)

    @cached_property
    def qualification_matches(self):
        from football_simulator.press.builder import walk_ties,tie_matches
        result={}
        for year,competition in {(m['season_number'],m['competition']) for m in self.matches.values()}:
            if competition in rules.LEAGUE_COMPETITIONS:continue
            edition=self.world.get('edition_archives',{}).get(f'{year}:{competition}',{})
            state=edition.get('state') or (self.world['season'].get('cups',{}).get(competition,{}) if year==self.season else {})
            for tie in walk_ties(state):
                mids=tie_matches(tie);winner=tie.get('winner')
                if winner and mids and all(mid in self.matches for mid in mids):
                    result.setdefault(mids[-1],set()).add(winner)
        return result

    def games(self, competition=None, season=None):
        number=self.season if season is None else season
        return [m for m in self.matches.values() if m['season_number']==number and (competition is None or m['competition']==competition)]

    def match(self, match, *, detailed=False):
        from football_simulator.press.builder import canonical_match
        from football_simulator.press.briefing import _goal_notes
        from football_simulator.press.semantics import goal_context,goal_caption
        m=self.matches[match] if isinstance(match,str) else match
        data=canonical_match(m,self.world['players']);score={k:data['regulation'][k]+(data['extra_time'] or {}).get(k,0) for k in ('home','away')}
        result={'competition':rules.display_name(m['competition']),'stage':stage_name(m['stage']),'week':m['week'],
                'home':self.team(m['home_team_id']),'away':self.team(m['away_team_id']),
                'score_after_extra_time_home_away':score,'regulation_home_away':data['regulation'],
                'extra_time_played':data['extra_time'] is not None}
        result.update(self.story_notes.get(m['match_id'],{}))
        if data['shootout']:
            result['shootout_home_away']=data['shootout']
            result['shootout_winner']=self.team(data['shootout_winner_team_id'])
        if m.get('stadium_id') in self.stadiums:
            s=self.stadiums[m['stadium_id']];result['stadium']=s['name_zh']+'（'+s['name_en']+'）'
        if m.get('venue_mode')=='neutral': result['venue']='中立场地'
        else: result['venue']=result['home']+'主场'
        fixture=next((f for f in self.world['season']['fixtures'] if f['match_id']==m['match_id']),{})
        result['venue_basis']=venue_basis(self,fixture or m)
        if m['competition']=='ember' and m.get('winner_team_id'):
            winner=m['winner_team_id'];loser=next(t for t in (m['home_team_id'],m['away_team_id']) if t!=winner)
            result['result_meaning']=elimination_result(m['stage'],self.team(winner),self.team(loser))
        h,a=score['home'],score['away']
        outcome=('战胜' if h>a else '负于' if h<a else '战平')
        result['result_caption']=f"{result['home']} {h}–{a} {result['away']}，{result['home']}{outcome}{result['away']}；{result['venue']}。"
        if data['shootout']:
            result['result_caption']+=f"点球{data['shootout']['home']}–{data['shootout']['away']}，{result['shootout_winner']}点球胜出。"
        if not detailed:return result
        events=m.get('events') or json.loads(m.get('events_json') or '[]')
        originals=[e for e in events if e.get('type')=='goal' and e.get('minute') is not None and e.get('team_id') in (m['home_team_id'],m['away_team_id'])]
        goals=goal_context(data,_goal_notes(data),self.team,self.player)
        for e,original in zip(goals,originals):
            pid=original.get('assist_id')
            if self.world['players'].get(pid,{}).get('is_real'):e['assister']=self.player(pid)
        if goals:result['goals']=[goal_caption(e,result['home'],result['away']) for e in goals]
        return result

    def player_card(self,pid,season=None,competition=None):
        number=self.season if season is None else season
        rows=[r for r in self.stats if r['player_id']==pid and r['season_number']==number and r['appeared'] and (competition is None or self.matches[r['match_id']]['competition']==competition)]
        fields=('goals','assists','chances_created','successful_defenses','successful_saves','clean_sheets')
        by_team=defaultdict(lambda:Counter())
        for r in rows:
            by_team[r['team_id']]['appearances']+=1
            for field in fields:by_team[r['team_id']][field]+=r[field]
        card={'player':self.player(pid),'position':self.world['players'][pid]['position'],'season':number,
              'appearances':len(rows),**{f:sum(r[f] for r in rows) for f in fields},
              'actual_teams':[{'team':self.team(t),**dict(v)} for t,v in by_team.items()]}
        from football_simulator.press.story_context import career_snapshot,arrival,named_arrival
        career=career_snapshot(self.world,self.season,self.week)
        card['recent_arrivals']=[]
        for team in by_team:
            last=max((self.matches[r['match_id']] for r in rows if r['team_id']==team),key=lambda m:(m['week'],m['match_id']))
            value=arrival(career,pid,team,number,last['week'])
            if value:card['recent_arrivals'].append(named_arrival(value,self.world))
        if competition is None:
            comps=sorted({self.matches[r['match_id']]['competition'] for r in rows})
            card['competitions']=[{'competition':rules.display_name(c),**self._totals([r for r in rows if self.matches[r['match_id']]['competition']==c])} for c in comps]
        position=self.world['players'][pid]['position']
        if position=='GK':
            best=sorted(rows,key=lambda r:(-r['successful_saves'],-r['clean_sheets'],r['match_id']))[:2]
            fields=('successful_saves','clean_sheets')
        elif position=='DF':
            best=sorted(rows,key=lambda r:(-r['successful_defenses'],-(r['goals']+r['assists']),r['match_id']))[:2]
            fields=('goals','assists','successful_defenses')
        else:
            best=sorted(rows,key=lambda r:(-(r['goals']+r['assists']),-r['goals'],r['match_id']))[:2]
            fields=('goals','assists')
        # Keep one actual final as well as the statistical leaders: a decisive
        # appearance can matter more than a higher-scoring routine league game.
        finals=[r for r in rows if self.matches[r['match_id']]['stage'] in
                ('final','grand_final','final_leg_1','final_leg_2','promotion_final','promotion_final_leg_1','promotion_final_leg_2')
                and any(r[k] for k in fields)]
        if finals:
            final=max(finals,key=lambda r:(sum(r[k] for k in fields),r['match_id']))
            if final not in best:best.append(final)
        qualifications=[r for r in rows if r['team_id'] in self.qualification_matches.get(r['match_id'],set())
                        and any(r[k] for k in fields)]
        if qualifications:
            qualifying=max(qualifications,key=lambda r:(sum(r[k] for k in fields),r['match_id']))
            if qualifying not in best:best.append(qualifying)
        card['representative_matches']=[{'player_team':self.team(r['team_id']),**{k:r[k] for k in fields},
                'qualification_secured':r['team_id'] in self.qualification_matches.get(r['match_id'],set()),
                'match':self.match(r['match_id'],detailed=True)} for r in best if any(r[k] for k in fields)]
        return card

    @staticmethod
    def _totals(rows):
        return {'appearances':sum(r['appeared'] for r in rows),'goals':sum(r['goals'] for r in rows),'assists':sum(r['assists'] for r in rows)}

    def awards(self,competition):
        edition=self.world.get('edition_archives',{}).get(f'{self.season}:{competition}',{})
        awards=edition.get('awards',{})
        result={}
        for key,label in [('top_scorer','射手王'),('assist_leader','助攻王'),('mvp','赛事MVP'),('evp','赛事EVP')]:
            result[label]=[]
            for row in awards.get(key,[]):
                card=self.player_card(row['player_id'],competition=competition)
                previous=self.world.get('edition_archives',{}).get(f'{self.season-1}:{competition}',{}).get('awards',{}).get(key,[])
                result[label].append({'player':self.player(row['player_id']),'team_at_award':self.team(row['team_id']),'value':row['value'],
                    'position':card['position'],'actual_teams_in_edition':card['actual_teams'],
                    'season_performance':{k:card[k] for k in ('position','appearances','goals','assists','successful_saves','clean_sheets','successful_defenses','chances_created')},
                    'retained_award':any(r['player_id']==row['player_id'] for r in previous)})
        return result

    def title_history(self,team,competition):
        from football_simulator.game import editions
        titles=[]
        for edition in self.world.get('edition_archives',{}).values():
            title=edition.get('championship') or {}
            if title.get('champion_team_id')==team and edition['season_number']<=self.season:
                titles.append((edition['season_number'],edition['competition_id']))
        seasons=sorted({y for y,c in titles if c==competition})
        run=0
        for year in range(self.season,0,-1):
            if year not in seasons:break
            run+=1
        result={'team':self.team(team),'competition':rules.display_name(competition),
                'known_title_seasons':seasons,'complete_history_from_first_season':self.history_complete}
        if self.history_complete:
            result['milestone']='首次赢得该赛事冠军' if len(seasons)==1 else f'第{len(seasons)}次赢得该赛事冠军'
            if run>=2:result['consecutive_titles']=run
            required={'premier',*rules.CUP_IDS};career={c for y,c in titles};current={c for y,c in titles if y==self.season}
            if required<=career:result['career_grand_slam']='队史集齐一级联赛及六杯七项冠军'
            if required<=current:result['season_grand_slam']='本赛季包揽一级联赛及六杯七项冠军'
        return result


def make_packet(ctx,kind,key,content):
    identity=ctx.source['identity']
    from football_simulator.press.team_background import backgrounds
    content['team_backgrounds']={ctx.team(t):data for t,data in backgrounds(ctx.world).items()}
    from football_simulator.press.story_context import enrich_feature
    enrich_feature(ctx,kind,content)
    return {'meta':{'schema_version':'press.feature/1','report_id':f"{kind}:{identity['world_id']}:{ctx.season}:{key}",
                    'report_type':kind,'world_id':identity['world_id'],'season':ctx.season,'completed_week':ctx.week,
                    'source_world_version':identity['source_world_version'],'cutoff':{'season':ctx.season,'week':ctx.week},'source_sha256':digest(ctx.source)},
            'entities':{'teams':{t:{'name_en':v['club_en']} for t,v in ctx.world['teams'].items()},
                        'players':{p:{'name':v['name'],'is_real':True} for p,v in ctx.world['players'].items() if v.get('is_real')},
                        'competitions':{c:{'name_en':rules.display_name(c)} for c in rules.ALL_COMPETITION_IDS},
                        'stadiums':{s:{'name_en':v['name_en'],'name_zh':v['name_zh']} for s,v in ctx.stadiums.items()}},
            'facts':[{'fact_id':'feature:'+key,'kind':kind,'time':{'season':ctx.season,'week':ctx.week},'data':content}],
            'editorial':{'topics':[]}}


def table_view(ctx, rows):
    return [{'rank':n,'team':ctx.team(r['team_id']),**{k:r.get(k,0) for k in ('played','wins','draws','losses','points','goals_for','goals_against','goal_diff')}} for n,r in enumerate(rows,1)]


def edition_identity(ctx,competition):
    first=rules.CUP_META[competition]['start_season'] if competition in rules.CUP_IDS else 1
    return {'game_season':ctx.season,'edition_number':ctx.season-first+1,'first_game_season':first}


def competition_format(competition):
    descriptions={
        'premier':'20队主客场双循环，共38轮；积分榜末三降级。',
        'second':'20队主客场双循环，共38轮；前二直升，3–6名参加升级附加赛。',
        'playoff':'4队，半决赛和决赛均为主客场两回合，总比分决胜；最终胜者升级。',
        'aureole':'16队四组双循环，前二进入八强；八强起固定半区，两回合淘汰，决赛也为两回合。',
        'confluence':'32队单场淘汰，每轮结束后重新抽签，因此没有固定上下半区；决赛中立场地。',
        'starpath':'16队瑞士轮，三胜晋级、三负淘汰；八强和半决赛两回合，决赛中立单场。',
        'ember':'8队双败固定签表；败者组决赛与总决赛是不同阶段，总决赛单场、无重置。',
        'ascendant':'12队分登峰组和涅槃组进行组外循环；登峰组前四和涅槃组第一直通八强，登峰组第五、第六与涅槃组第二至第五参加突围赛争三席，涅槃组第六出局；八强起单场淘汰，决赛中立。',
        'zenith':'8队按已结算的上季荣誉积分排种子，固定路线单场淘汰，决赛中立。',
    }
    venue=('主客场两回合，次回合由本季Second Division最终排名更高者主场。' if competition=='playoff'
           else '主客场两回合，次回合由上季排名更高者主场。' if competition=='aureole'
           else '瑞士轮由上季排名更高者主场；两回合次回合由上季排名更高者主场，决赛中立。' if competition=='starpath'
           else '胜者组及同一路径比较上季排名；败者组更晚落入者优先主场，总决赛为胜者组冠军主场。' if competition=='ember'
           else '小组组外循环每队三主三客；突围及淘汰按上季排名分配主场，决赛中立。' if competition=='ascendant'
           else '决赛外按上季排名分配主场，决赛中立。' if competition in ('confluence','zenith') else '')
    return descriptions[competition]+venue


def venue_basis(ctx,fixture,*,two_legs=False):
    if fixture.get('venue_mode')=='neutral':return '中立场地；记录中的主客顺序仅用于展示。'
    if fixture.get('venue_reason'):return fixture['venue_reason']
    comp=fixture['competition'];stage=fixture.get('stage','')
    season=int(fixture.get('season_number',ctx.season))
    h=fixture['home_team_id'];a=fixture['away_team_id']
    if comp=='playoff':
        rows=ctx.world.get('edition_archives',{}).get(f'{season}:second',{}).get('tables',{}).get('second',[])
        if not rows:rows=ctx.archives.get(season,{}).get('second_table',[])
        ranks={row['team_id']:n for n,row in enumerate(rows,1)}
        if h in ranks and a in ranks:
            return (f"第{season}赛季Second Division最终排名：{ctx.team(h)}第{ranks[h]}、{ctx.team(a)}第{ranks[a]}；"
                    '排名较高者在次回合主场。')
        return f'升级附加赛按第{season}赛季Second Division最终排名，较高者在次回合主场。'
    if comp in rules.LEAGUE_COMPETITIONS or stage.startswith(('group','cross_group')):
        return '按该阶段主客场赛程安排。'
    from football_simulator.game.competitions import previous_ranks
    if season==ctx.season:
        ranks=previous_ranks(ctx.world)
    else:
        # Historical performances must not inherit the report season's seeds.
        archive=ctx.archives.get(season-1,{})
        ranks={}
        for league,offset in (('premier',0),('second',20)):
            rows=ctx.world.get('edition_archives',{}).get(f'{season-1}:{league}',{}).get('tables',{}).get(league,[])
            order=[row['team_id'] for row in rows] if rows else archive.get(league+'_order',[])
            ranks.update({team_id:offset+n for n,team_id in enumerate(order,1)})
    previous='上季' if season==ctx.season else f'第{season-1}赛季'
    order=(f"{previous}总顺序：{ctx.team(h)}第{ranks[h]}、{ctx.team(a)}第{ranks[a]}（一级1–20，其后次级1–20）。"
           if h in ranks and a in ranks else '')
    if two_legs or '_leg_' in stage:
        return order+f'两回合由{previous}排名较高者在次回合主场，不按本届小组排名分配。'
    return order+f'本场按{previous}联赛排名确定主场。'


def bracket_view(ctx,state):
    from football_simulator.press.builder import walk_ties,tie_matches,field_score
    result=[]
    for tie in walk_ties(state):
        mids=tie_matches(tie);played=[ctx.matches[m] for m in mids if m in ctx.matches]
        item={'stage':rules.stage_label(tie.get('stage','')),'teams':[ctx.team(t) for t in tie['teams']]}
        item['format']='主客场两回合' if len(mids)==2 else '单场决胜'
        fixtures={f['match_id']:f for f in ctx.world['season']['fixtures']}
        scheduled=[fixtures[mid] for mid in mids if mid in fixtures and mid not in ctx.matches]
        if scheduled:
            item['scheduled_legs']=[{'leg':f.get('leg',1),'week':f['week'],'home':ctx.team(f['home_team_id']),
                                     'away':ctx.team(f['away_team_id']),'venue_basis':venue_basis(ctx,f,two_legs=len(mids)==2)} for f in scheduled]
        if played:
            item['matches']=[ctx.match(m) for m in played]
        if tie.get('winner'):
            winner=tie['winner'];loser=next(t for t in tie['teams'] if t!=winner)
            item['advancing_or_winning_team']=ctx.team(winner)
            item['outcome_meaning']=elimination_result(tie.get('stage',''),ctx.team(winner),ctx.team(loser))
        if len(mids)==2 and len(played)==2:
            totals=Counter()
            for match in played:
                h,a=field_score(match);totals[match['home_team_id']]+=h;totals[match['away_team_id']]+=a
            item['aggregate']=[{'team':ctx.team(t),'goals':totals[t]} for t in tie['teams']]
        if tie.get('winner') and len(played)==len(mids):
            winner=tie['winner'];loser=next(t for t in tie['teams'] if t!=winner)
            last=played[-1];h,a=field_score(last)
            gf,ga=(h,a) if last['home_team_id']==winner else (a,h)
            field='战胜' if gf>ga else '负于' if gf<ga else '战平'
            summary=f"{ctx.team(winner)}在"+('次回合' if len(mids)==2 else '本场')+f"场上{gf}–{ga}{field}{ctx.team(loser)}。"
            if item.get('aggregate'):
                first,second=item['aggregate'];summary+=f"两回合总比分{first['team']} {first['goals']}–{second['goals']} {second['team']}。"
            if last.get('home_pk') is not None:summary+=ctx.team(winner)+'通过点球决胜赢下对阵。'
            item['decision_summary']=summary+item['outcome_meaning']
        result.append(item)
    return result


def points_gap_basis(ctx,first,second):
    a,b=ctx.team(first['team_id']),ctx.team(second['team_id'])
    detail=(f"胜场积分：{a}{3*first['wins']}分，{b}{3*second['wins']}分；"
            f"平局积分：{a}{first['draws']}分，{b}{second['draws']}分；"
            f"合计{first['points']}–{second['points']}，{a}领先{first['points']-second['points']}分。")
    if first['wins']==second['wins']:
        detail+='双方胜场相同，积分差完全来自平局积分。'
    return detail


def phase_packet(source,competition,phase):
    ctx=FeatureContext(source)
    content={'competition':rules.display_name(competition),'edition':edition_identity(ctx,competition),
             'competition_format':competition_format(competition),'stage':phase}
    games=ctx.games(competition)
    if phase=='halfway':
        from football_simulator.game.calendar import standings_from_matches
        from football_simulator.game.competitions import previous_ranks
        from football_simulator.game.world import league_tables
        rows=league_tables(ctx.world)[competition]
        if len(games)!=190 or any(r['played']!=19 for r in rows):raise PressError('半程报道必须在完整第19轮结束时冻结。')
        content['halfway_table']=table_view(ctx,rows)
        content['leading_gap']=rows[0]['points']-rows[1]['points']
        content['leading_gap_basis']=points_gap_basis(ctx,rows[0],rows[1])
        content['phase_status']='仅完成半程，冠军与最终升降级尚未确定'
    else:
        state=ctx.world['season']['cups'].get(competition,{})
        if not state.get('stage_done',{}).get(phase):raise PressError('该阶段尚未结束。')
        if competition=='aureole':
            content['groups']={group:table_view(ctx,rows) for group,rows in state['group_standings'].items()}
            content['qualified']=[ctx.team(r['team_id']) for rows in state['group_standings'].values() for r in rows[:2]]
            content['eliminated']=[ctx.team(r['team_id']) for rows in state['group_standings'].values() for r in rows[2:]]
            from football_simulator.press.expectations import group_surprises
            content['group_surprises']=group_surprises(ctx,state,games)
            participants=state.get('participants',[])
            content['draw_pots']=[{'team':ctx.team(t),'pot':n//4+1} for n,t in enumerate(participants)] if len(participants)==16 else []
        elif competition=='starpath':
            content['swiss_records']=[{'team':ctx.team(t),'wins':r[0],'losses':r[1],'seed':state.get('knockout_seeds',{}).get(t)} for t,r in state['records'].items()]
            content['qualified']=[ctx.team(t) for t,r in state['records'].items() if r[0]==3]
            content['eliminated']=[ctx.team(t) for t,r in state['records'].items() if r[1]==3]
            content['record_scope']='瑞士资格胜负，含加时／点球决胜，与单场90或120分钟比分分开'
        elif competition=='ascendant':
            content['groups']={('登峰组' if group=='D' else '涅槃组'):table_view(ctx,rows) for group,rows in state['standings'].items()}
            content['direct_quarterfinalists']=[ctx.team(t) for t in state['direct']]
            content['eliminated']=[ctx.team(state['nirvana_sixth'])]
            if phase=='playin':
                content['playin_winners']=[ctx.team(t['winner']) for t in state['playin']]
                content['qualified']=content['direct_quarterfinalists']+content['playin_winners']
                content['eliminated'] += [ctx.team(team) for tie in state['playin'] for team in tie['teams'] if team!=tie['winner']]
        else:raise PressError('没有设置该类大阶段报道。')
        if competition!='aureole':
            from football_simulator.press.expectations import qualification_surprises
            content['qualification_surprises']=qualification_surprises(ctx,state,
                content.get('qualified',content.get('direct_quarterfinalists',[])),content.get('eliminated',[]),games)
        content['bracket']=bracket_view(ctx,state)
        # A phase report sees only the fixtures already decided/scheduled here.
        content['history_notes']=[]
        content['history_breakthroughs']=[]
        for tid in {t for m in games for t in (m['home_team_id'],m['away_team_id'])}:
            if ctx.team(tid) not in content.get('qualified',content.get('direct_quarterfinalists',[])):continue
            prior=[a.get('cup_placements',{}).get(competition,{}).get(tid,999) for y,a in ctx.archives.items() if y<ctx.season]
            threshold=5
            if ctx.history_complete and min(prior,default=999)>threshold:
                content['history_notes'].append(ctx.team(tid)+'首次进入该赛事八强')
                content['history_breakthroughs'].append({'team':ctx.team(tid),'achievement':'quarterfinal',
                    'first_time':True,'history_complete':True})
    # A small set of actual stars and deciding results supplements the whole table.
    pids={r['player_id'] for r in ctx.stats if r['season_number']==ctx.season and ctx.matches[r['match_id']]['competition']==competition and r['appeared']}
    cards=[ctx.player_card(p,competition=competition) for p in pids]
    cards.sort(key=lambda p:(-(p['goals']+p['assists']),-p['goals'],p['player']))
    content['players']=cards[:3]
    content['key_results']=[]
    seen=set()
    for card in cards[:3]:
        for row in card['representative_matches'][:1]:
            game=row['match'];key=(game['stage'],game['home'],game['away'])
            if key not in seen and len(content['key_results'])<2:
                content['key_results'].append(game);seen.add(key)
    return make_packet(ctx,'phase',competition+'-'+phase,content)


def competition_packet(source,competition):
    ctx=FeatureContext(source);edition=ctx.world.get('edition_archives',{}).get(f'{ctx.season}:{competition}')
    if not edition or edition['completed_week']>ctx.week or any(f['status']!='completed' for f in edition['fixtures']):
        raise PressError('赛事总报需要该届已完成并冻结的归档。')
    state=edition['state'];content={'competition':rules.display_name(competition),'edition':edition_identity(ctx,competition),
        'competition_format':competition_format(competition),'completed_matches':len(edition['fixtures'])}
    if competition in rules.LEAGUE_COMPETITIONS:
        rows=edition['tables'][competition];champion=rows[0]['team_id']
        content['final_table']=table_view(ctx,rows);content['winning_margin']=rows[0]['points']-rows[1]['points']
        content['winning_margin_basis']=points_gap_basis(ctx,rows[0],rows[1])
        content['qualification']={'relegated':[ctx.team(r['team_id']) for r in rows[-3:]]} if competition=='premier' else {'directly_promoted':[ctx.team(r['team_id']) for r in rows[:2]],'promotion_playoff':[ctx.team(r['team_id']) for r in rows[2:6]]}
        champion_games=[m for m in ctx.games(competition) if champion in (m['home_team_id'],m['away_team_id'])]
        from football_simulator.press.builder import field_score
        record=Counter();by_venue={'主场':Counter(),'客场':Counter()}
        for m in champion_games:
            h,a=field_score(m);gf,ga=(h,a) if m['home_team_id']==champion else (a,h)
            result='W' if gf>ga else 'L' if gf<ga else 'D';record[result]+=1
            by_venue['主场' if m['home_team_id']==champion else '客场'][result]+=1
        content['champion_record']=dict(record)
        content['champion_home_away_record']={label:dict(values) for label,values in by_venue.items()}
        content['key_results_scope']='以下仅为整季38场中选取的3场比赛，不是全部胜场或全部客场比赛。'
        content['key_results']=[ctx.match(m) for m in [*champion_games[:1],*champion_games[-2:]]]
    else:
        champion=state.get('champion') or state.get('winner')
        if not champion:raise PressError('冻结赛事缺少优胜球队。')
        content['bracket']=bracket_view(ctx,state)
        content['champion_route']=[ctx.match(m) for m in ctx.games(competition) if champion in (m['home_team_id'],m['away_team_id'])]
        if competition=='playoff':content['result_meaning']='升级附加赛胜者取得升级资格，不设置个人奖项'
    content['champion']=ctx.team(champion)
    from football_simulator.press.expectations import qualification_surprises
    eliminated=[ctx.team(t) for t in ctx.world['teams'] if t!=champion and any(t in (m['home_team_id'],m['away_team_id']) for m in ctx.games(competition))]
    content['campaign_surprises']=qualification_surprises(ctx,state,[ctx.team(champion)],eliminated,ctx.games(competition))
    campaign=Counter()
    from football_simulator.press.builder import field_score
    for game in ctx.games(competition):
        if champion not in (game['home_team_id'],game['away_team_id']):continue
        h,a=field_score(game);home=game['home_team_id']==champion;gf,ga=(h,a) if home else (a,h)
        campaign['matches']+=1;campaign['goals_for']+=gf;campaign['goals_against']+=ga
        campaign['W' if gf>ga else 'L' if gf<ga else 'D']+=1
        if game.get('home_pk') is not None:
            ph,pa=game['home_pk'],game['away_pk'];won=ph>pa if home else pa>ph
            campaign['shootout_wins' if won else 'shootout_losses']+=1
    content['champion_campaign']=dict(campaign)
    content['champion_campaign'].update({k:campaign[k] for k in
        ('matches','W','D','L','goals_for','goals_against','shootout_wins','shootout_losses')})
    finals=[m for m in ctx.games(competition) if m['stage'] in
        ('final','grand_final','promotion_final','final_leg_1','final_leg_2','promotion_final_leg_1','promotion_final_leg_2')]
    if finals:
        last=finals[-1]
        runner=next(t for t in (last['home_team_id'],last['away_team_id']) if t!=champion)
        content['runner_up']=ctx.team(runner)
        content['decisive_games']=[ctx.match(m) for m in finals]
        performances=[]
        for r in ctx.stats:
            if r['match_id']!=last['match_id'] or not r['appeared']:continue
            if not (r['goals'] or r['assists'] or r['successful_saves']>=3):continue
            performances.append({'player':ctx.player(r['player_id']),'team':ctx.team(r['team_id']),
                'position':ctx.world['players'][r['player_id']]['position'],
                **{k:r[k] for k in ('goals','assists','successful_saves','clean_sheets')}})
        content['decisive_performances']=performances
    if competition!='playoff':
        content['awards']=ctx.awards(competition)
        content['award_status']=edition_award_status(edition)
        content['history']=ctx.title_history(champion,competition)
    return make_packet(ctx,'competition',competition,content)


def edition_award_status(edition):
    """Distinguish absent, vacant and unawarded using this edition's frozen record."""
    values = edition.get('awards') or {}
    if values.get('rule_version') != '0.3.1' or 'evp_slots' not in values:
        return {'status': 'unknown'}
    slots = int(values['evp_slots'])
    if not slots:
        return {'status': 'not_offered', 'mvp_status': 'not_offered', 'evp_slots': 0, 'evp_count': 0}
    if 'mvp' not in values or 'evp' not in values:
        return {'status': 'incomplete'}
    count = len(values['evp'])
    return {'status': 'awarded', 'mvp_status': 'awarded' if values['mvp'] else 'vacant',
            'evp_count': count, 'evp_slots': slots, 'evp_shortfall': count < slots}


def draft_logs(ctx):
    logs={int(log['season']):log for a in ctx.archives.values() for log in a.get('draft',[])}
    logs.update({int(log['season']):log for log in ctx.world['season'].get('draft_log',[])})
    return logs


def origin(ctx,pid):
    for year,log in sorted(draft_logs(ctx).items()):
        for n,row in enumerate(log.get('results',[]),1):
            if row['player_id']==pid:
                return {'draft_season':year,'actual_pick':int(row.get('pick') or n),'draft_team':ctx.team(row['team_id'])}
    return {'origin':'初始球员'}


def compact_card(card):
    """Transfers/cohort reviews need a season profile, not two full match logs
    per player. Keep detailed representative games for the Top20 profiles."""
    return {k:v for k,v in card.items() if k not in ('representative_matches','competitions')}


def player_titles(ctx,pid):
    result=[]
    for e in ctx.world.get('edition_archives',{}).values():
        title=e.get('championship') or {}
        if e['season_number']!=ctx.season or pid not in title.get('roster_player_ids',[]):continue
        result.append({'competition':rules.display_name(e['competition_id']),'team_at_championship':ctx.team(title['champion_team_id']),
                       'decided_week':e.get('completed_week'),'basis':'championship_roster'})
    return result


def personal_awards(ctx,pid):
    result=[]
    for e in ctx.world.get('edition_archives',{}).values():
        if e['season_number']!=ctx.season:continue
        for key,label in [('top_scorer','射手王'),('assist_leader','助攻王'),('mvp','赛事MVP'),('evp','赛事EVP')]:
            if any(r['player_id']==pid for r in e.get('awards',{}).get(key,[])):
                result.append({'competition':rules.display_name(e['competition_id']),'award':label})
    return result


def team_results_for_player(ctx,pid):
    from football_simulator.app.profile_history import FINISH_LABELS
    appearances=[r for r in ctx.stats if r['player_id']==pid and r['season_number']==ctx.season and r['appeared']]
    teams={r['team_id'] for r in appearances}
    moves=[]
    for r in ctx.world.get('transfer_history',[]):
        if r['season']!=ctx.season or r['week']>ctx.week or r['status']!='approved':continue
        if pid in r['players_a']:a,b=r['team_a'],r['team_b']
        elif pid in r['players_b']:a,b=r['team_b'],r['team_a']
        else:continue
        moves.append({'week':r['week'],'from_team':ctx.team(a),'to_team':ctx.team(b)})
    result=[]
    for tid in sorted(teams):
        finishes=[]
        for e in ctx.world.get('edition_archives',{}).values():
            if e['season_number']!=ctx.season:continue
            comp=e['competition_id']
            finish=None
            if comp in rules.LEAGUE_COMPETITIONS:
                row=next((n for n,r in enumerate(e['tables'][comp],1) if r['team_id']==tid),None)
                if row:finish=f'第{row}名'
            elif comp in rules.CUP_IDS:
                rank=e['state'].get('placements',{}).get(tid)
                if rank:finish=FINISH_LABELS.get(rules.honor_key_for_rank(comp,rank),str(rank))
            if finish is None:continue
            item={'competition':rules.display_name(comp),'finish':finish,'result_owner':'team'}
            title=e.get('championship') or {}
            if title.get('champion_team_id')==tid:
                roster=title.get('roster_player_ids')
                item['player_championship_credit']={'decided_week':e.get('completed_week'),
                    'awarded_to_player':pid in roster if isinstance(roster,list) else None,'basis':'championship_roster'}
            finishes.append(item)
        weeks=[ctx.matches[r['match_id']]['week'] for r in appearances if r['team_id']==tid]
        result.append({'team':ctx.team(tid),'first_appearance_week':min(weeks),'last_appearance_week':max(weeks),
                       'season_transfers':[m for m in moves if ctx.team(tid) in (m['from_team'],m['to_team'])],
                       'season_results':finishes})
    return result


def top20_packet(source):
    ctx=FeatureContext(source);rows=ctx.world['season'].get('awards',{}).get('top20',[])
    if not ctx.world['season'].get('finalized') or len(rows)!=20 or [r['rank'] for r in rows]!=list(range(1,21)):
        raise PressError('Top20需使用已结算的完整20人榜单。')
    previous=ctx.archives.get(ctx.season-1,{}).get('top20',[]);prev={r['player_id']:r['rank'] for r in previous}
    cards=[]
    for row in rows:
        pid=row['player_id'];card=ctx.player_card(pid)
        history=top20_selection_history(ctx,pid,rows)
        card.update(rank=row['rank'],history_status=history['status'],selection_history=history,previous_rank=prev.get(pid),origin=origin(ctx,pid),
                    personal_awards=personal_awards(ctx,pid),team_titles=player_titles(ctx,pid),
                    teams_season_results=team_results_for_player(ctx,pid), award_policy=ctx.world.get('awards_rule_version'))
        tids=sorted({r['team_id'] for r in ctx.stats if r['player_id']==pid and r['season_number']==ctx.season and r['appeared']})
        card['team_honor_summaries']=[official_team_summary(ctx,t,ctx.season) for t in tids]
        cards.append(card)
    current={r['player_id'] for r in rows}
    changes={'first_edition':ctx.season==1,'newcomers':[c['player'] for c in cards if c['history_status']=='首次入选Top20'],
             'returning':[c['player'] for c in cards if c['history_status']=='重返Top20'],
             'dropped_out':[{'player':ctx.player(r['player_id']),'previous_rank':r['rank'],
                            'last_season':compact_card(ctx.player_card(r['player_id'],season=ctx.season-1)),
                            'this_season':compact_card(ctx.player_card(r['player_id']))} for r in previous if r['player_id'] not in current]}
    return make_packet(ctx,'top20','season-top20',{'rankings':cards,'changes':changes,'ranking_status':'游戏已结算的最终排名；文字只分析表现，不提供综合分与计算公式'})


def top20_selection_history(ctx,pid,current_rows):
    """Counts come from saved editions, not from the word 'consecutive'.

    A missing edition is unknown, rather than evidence of a missed selection.
    A current season also present in archives is counted only once.
    """
    editions={y:a['top20'] for y,a in ctx.archives.items()
              if y<ctx.season and isinstance(a.get('top20'),list)}
    editions[ctx.season]=current_rows
    ranks={y:next((r['rank'] for r in rows if r['player_id']==pid),None) for y,rows in editions.items()}
    missing=[y for y in range(1,ctx.season+1) if y not in ranks]
    selected=sorted(y for y,rank in ranks.items() if rank is not None)
    run=0;exact_run=True
    for y in range(ctx.season,0,-1):
        if y not in ranks:exact_run=False;break
        if ranks[y] is None:break
        run+=1
    previous=ranks.get(ctx.season-1)
    status=('首届榜单成员' if ctx.season==1 else '连续入选' if previous is not None
            else '重返Top20' if ctx.season-1 in ranks and any(y<ctx.season for y in selected)
            else '首次入选Top20' if not missing and len(selected)==1 else '在已知历史中入选')
    prior=[rank for y,rank in ranks.items() if y<ctx.season and rank is not None]
    current=ranks[ctx.season]
    number_ones=sorted(y for y,rank in ranks.items() if y<ctx.season and rank==1)
    best=min(prior) if prior else None
    milestones=[]
    if current==1:
        milestones.append(f'第{len(number_ones)+1}次登顶Top20' if number_ones and not missing else
                          '首次登顶Top20' if not missing else '本届排名第1，历史登顶次数尚无法确认')
    if best is not None and current<best:
        milestones.append(('刷新个人历史最佳名次' if not missing else '刷新已知记录中的最佳名次')+f'：第{current}名（此前最佳第{best}名）')
    elif best==current:
        milestones.append(f'追平此前最佳名次：第{current}名')
    return {'status':status,'editions':[{'season':y,'rank':ranks[y]} for y in sorted(ranks)],
            'rank_milestones':milestones,'previous_best_rank':best,'prior_number_one_seasons':number_ones,
            'missing_seasons':missing,'selection_seasons':selected,
            'total_selections':len(selected) if not missing else None,'known_selections':len(selected),
            'consecutive_selections':run if exact_run else None,'known_consecutive_selections':run,
            'first_selection_season':selected[0] if not any(y<selected[0] for y in missing) else None}


def transfer_roster_context(ctx,trades):
    """Reconstruct the window opening from its closing real-player rosters.

    These are whole-window endpoints. They do not pretend to be the state
    immediately after each selected trade, nor count default placeholders.
    """
    from football_simulator.game.models import POSITIONS
    affected={r[k] for r in trades for k in ('team_a','team_b')}
    closing={t:Counter(ctx.world['players'][r['player_id']]['position']
                       for r in ctx.world['teams'][t]['roster'] if ctx.world['players'][r['player_id']]['is_real']) for t in affected}
    opening={t:c.copy() for t,c in closing.items()}
    for row in trades:
        a,b=row['team_a'],row['team_b']
        sent_a=Counter(ctx.world['players'][p]['position'] for p in row['players_a'])
        sent_b=Counter(ctx.world['players'][p]['position'] for p in row['players_b'])
        for pos in POSITIONS:
            opening[a][pos]+=sent_a[pos]-sent_b[pos]
            opening[b][pos]+=sent_b[pos]-sent_a[pos]
    return [{'team':ctx.team(t),'window_open_real_counts':{p:opening[t][p] for p in POSITIONS},
             'window_closed_real_counts':{p:closing[t][p] for p in POSITIONS}} for t in sorted(affected)]


def transfer_packet(source,window):
    from football_simulator.game.transfers import window_key
    ctx=FeatureContext(source);closed=27 if window=='winter' else 52 if window=='summer' else None
    if ctx.week!=closed or ctx.world['season']['pending'].get('transfer_review'):raise PressError('转会窗报道需在窗口关闭且审核完成后冻结。')
    rows={r['trade_id']:r for r in ctx.world.get('transfer_history',[]) if r['season']==ctx.season and window_key(r)==window and r['status']=='approved' and r['week']<=ctx.week}
    historical={r['player_id'] for y,a in ctx.archives.items() if y<=ctx.season for r in a.get('top20',[])}
    historical.update(r['player_id'] for r in ctx.world['season'].get('awards',{}).get('top20',[]))
    rookie_ids={r['player_id'] for log in draft_logs(ctx).values() for n,r in enumerate(log.get('results',[]),1) if int(r.get('pick') or n)<=5}
    production=defaultdict(Counter)
    for row in ctx.stats:
        if row['season_number']==ctx.season and row['appeared']:
            for field in ('goals','assists','successful_saves'):production[row['player_id']][field]+=row[field]
    outstanding=set()
    for field,count in [('goals',5),('assists',5),('successful_saves',3)]:
        outstanding.update(sorted(production,key=lambda p:(-production[p][field],p))[:count])
    completed=[]
    for row in rows.values():
        moved=[]
        for key,from_id,to_id in [('players_a',row['team_a'],row['team_b']),('players_b',row['team_b'],row['team_a'])]:
            for pid in row[key]:
                card=compact_card(ctx.player_card(pid));card.update(from_team=ctx.team(from_id),to_team=ctx.team(to_id),origin=origin(ctx,pid),
                    historical_top20=pid in historical,current_season_standout=pid in outstanding)
                from football_simulator.press.story_context import transfer_notes
                card['transfer_story']=transfer_notes(ctx.world,list(ctx.matches.values()),ctx.stats,
                    {'player_id':pid,'from_team_id':from_id,'to_team_id':to_id,'season':row['season'],'week':row['week']},ctx.season,ctx.week,
                    service_history=ctx.source.get('career_service_history'),final_history=ctx.source.get('historical_finals'))
                moved.append(card)
        positions_a=Counter(ctx.world['players'][p]['position'] for p in row['players_a'])
        positions_b=Counter(ctx.world['players'][p]['position'] for p in row['players_b'])
        completed.append({'trade_id':row['trade_id'],'week':row['week'],'moves':moved,'position_exchange':[
                          {'team':ctx.team(row['team_a']),'outgoing':dict(positions_a),'incoming':dict(positions_b)},
                          {'team':ctx.team(row['team_b']),'outgoing':dict(positions_b),'incoming':dict(positions_a)}],
                          'estimated_package_values':[
                          {'team':ctx.team(row['team_a']),'estimated_value':row['value_a']},
                          {'team':ctx.team(row['team_b']),'estimated_value':row['value_b']}],
                          '_priority':sum(3*(p in historical)+2*(p in rookie_ids)+2*(p in outstanding) for p in row['players_a']+row['players_b'])})
    completed.sort(key=lambda r:(-r['_priority'],r['week']))
    for row in completed:row.pop('_priority')
    label='冬季' if window=='winter' else '夏季'
    return make_packet(ctx,'transfer',window,{'window':window,'completed_trades':len(rows),
        'window_summary':f'第{ctx.season}赛季{label}转会窗共完成{len(rows)}笔球员交换，计数仅限这一次转会窗。','focus_trades':completed[:6],
        'other_trades':[{'trade_id':r['trade_id'],'week':r['week'],'moves':[{k:p[k] for k in ('player','from_team','to_team','position','transfer_story')} for p in r['moves']]} for r in completed[6:]],
        'window_team_rosters':transfer_roster_context(ctx,list(rows.values())),
        'recorded_position_roles':{'GK':'门将','DF':'后卫','MF':'中场','FW':'前锋'},
        'unrecorded_attributes':['年龄','细分站位和技术风格','俱乐部谈判与交易动机','身价差的因素分解'],
        'transaction_type':'球员交换；身价是估值，不是现金转会费','attention':'历届Top20、本季突出表现者及各届实际前5顺位新秀'})


def draft_packet(source):
    ctx=FeatureContext(source);logs=draft_logs(ctx);log=logs.get(ctx.season)
    if ctx.week<49 or log is None or ctx.world['season']['pending'].get('draft',{}).get('status')=='awaiting_input':raise PressError('选秀尚未完成。')
    picks=[]
    from football_simulator.game.world import league_tables
    standings=ctx.world['season'].get('final_tables') or league_tables(ctx.world)
    ranks={row['team_id']:{'competition':rules.display_name(comp),'rank':n}
           for comp,rows in standings.items() for n,row in enumerate(rows,1)}
    for n,row in enumerate(log.get('results',[]),1):
        pick=int(row.get('pick') or n);team=ctx.team(row['team_id']);finish=ranks[row['team_id']]
        picks.append({'player':ctx.player(row['player_id']),'actual_pick':pick,'team':team,
                      'selection_result':f"实际第{pick}顺位：{team}选中{ctx.player(row['player_id'])}。",
                      'team_season_finish':f"{team}在刚结束的第{ctx.season}赛季{finish['competition']}排名第{finish['rank']}。",
                      'position':row['position'],'registered_ability_at_pick':row['ability'],
                      'receiving_team_record':official_team_summary(ctx,row['team_id'],ctx.season)})
    progress=[]
    for year in range(max(1,ctx.season-2),ctx.season):
        old=logs.get(year)
        if old is None:continue
        for n,row in enumerate(old.get('results',[]),1):
            if int(row.get('pick') or n)>5:continue
            pid=row['player_id'];p=ctx.world['players'][pid]
            development=[]
            for y in range(year+1,ctx.season+1):
                card=compact_card(ctx.player_card(pid,season=y))
                edition=ctx.world['season'] if y==ctx.season else ctx.archives.get(y,{})
                top=edition.get('awards',{}).get('top20') if y==ctx.season else edition.get('top20')
                card['top20_known']=isinstance(top,list)
                card['top20_rank']=next((r['rank'] for r in (top or []) if r['player_id']==pid),None)
                tids=sorted({r['team_id'] for r in ctx.stats if r['player_id']==pid and r['season_number']==y and r['appeared']})
                card['team_records']=[official_team_summary(ctx,t,y,pid=pid) for t in tids]
                development.append(card)
            progress.append({'player':ctx.player(pid),'draft_season':year,'actual_pick':int(row.get('pick') or n),
                'draft_team':ctx.team(row['team_id']),'current_registered_team':ctx.team(p['team_id']) if p.get('team_id') else None,
                'by_season':development,
                'ability_at_selection':row['ability'],'ability_at_report':p['ability']})
    reasons={'user_skipped':'本届未选择参与球队分配','no_open_slot':'没有符合位置上限的空位'}
    return make_packet(ctx,'draft','draft',{'picks':picks,'unselected':[{'player':r['name'],'position':r['position'],'reason':reasons.get(r['reason'],r['reason'])} for r in log.get('undrafted',[])],
        'previous_two_cohorts':progress,'timing':'本届选秀在赛季比赛结束后进行；新秀尚未代表新队参加本届已结束的比赛',
        'selection_order':'actual_pick为实际选中顺位，未选中者没有顺位'})


def build_feature(source,kind,**kwargs):
    return {'phase':phase_packet,'competition':competition_packet,'transfer':transfer_packet,'draft':draft_packet,'top20':top20_packet}[kind](source,**kwargs)


def official_team_summary(ctx,tid,year,*,pid=None):
    """Team achievements stay distinct from a player's championship credit."""
    edition=ctx.world['season'] if year==ctx.season else ctx.archives.get(year,{})
    final=edition.get('finalized',False) if year==ctx.season else year in ctx.archives
    tables=edition.get('final_tables') or {'premier':edition.get('premier_table',[]),'second':edition.get('second_table',[])}
    finish=next(({'competition':rules.display_name(c),'rank':n} for c,rows in tables.items()
                 for n,r in enumerate(rows,1) if r['team_id']==tid),None)
    recorded=edition.get('championships')
    titles=[t for t in (recorded or {}).values() if t['champion_team_id']==tid]
    result={'team':ctx.team(tid),'season':year,'finalized':bool(final),'league_finish':finish,
            'honor_points':edition.get('honor_totals',{}).get(tid) if final else None,
            'championship_count':len(titles) if isinstance(recorded,dict) and final else None,
            'team_championships':[rules.display_name(t['competition_id']) for t in titles]}
    if pid is not None:
        result['player_roster_titles']=[rules.display_name(t['competition_id']) for t in titles if isinstance(t.get('roster_player_ids'),list) and pid in t['roster_player_ids']]
    return result
