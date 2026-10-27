"""Frozen pre-week comparisons for cautious upset/dark-horse editorial labels.

No sporting ranks, match results or game RNG are changed. Separate evidence
axes are compared directly; there is no new team score or ranking model.
"""
import json
from football_simulator.press.repository import canonical,digest


def team_profiles(world):
    from football_simulator.game.world import league_tables
    previous=world['season'].get('previous') or {}
    old_order=[*previous.get('premier_order',[]),*previous.get('second_order',[])]
    current={r['team_id']:{'competition':comp,'rank':n,'points':r['points'],'played':r['played']}
             for comp,rows in league_tables(world).items() for n,r in enumerate(rows,1)}
    profiles={}
    from football_simulator.press.team_background import backgrounds
    heritage=backgrounds(world)
    participants=world['season'].get('cups',{}).get('aureole',{}).get('participants',[])
    for tid,team in world['teams'].items():
        real=[world['players'][r['player_id']] for r in team['roster'] if world['players'][r['player_id']].get('is_real')]
        values=[p.get('market_value') for p in real]
        profiles[tid]={'team':team['club_en'],'previous_overall_rank':old_order.index(tid)+1 if tid in old_order else None,
                       'current_league':current.get(tid),'total_market_value':sum(values) if values and all(v is not None for v in values) else None,
                       'real_players':len(real),'reign_draw_pot':participants.index(tid)//4+1 if len(participants)==16 and tid in participants else None}
        profiles[tid]['heritage']=heritage.get(tid)
    return profiles


def capture_next(conn,world):
    columns={r[1] for r in conn.execute('PRAGMA table_info(press_activation)')}
    if 'engine_id' not in columns:return
    active=conn.execute('SELECT engine_id FROM press_activation WHERE singleton=1').fetchone()
    if not active or active[0]!='local-template' or world['season'].get('season_complete'):return
    year,week=world['season_number'],world['season']['week']
    if any(f['status']=='completed' and f['week']==week for f in world['season']['fixtures']):return
    conn.execute('''CREATE TABLE IF NOT EXISTS press_expectations (
        season INTEGER NOT NULL,week INTEGER NOT NULL,world_id TEXT NOT NULL,source_version INTEGER NOT NULL,
        data_json TEXT NOT NULL,sha TEXT NOT NULL,PRIMARY KEY(season,week))''')
    data={'season':year,'before_week':week,'world_id':world['world_id'],'source_version':world['version'],
          'teams':team_profiles(world)}
    conn.execute('INSERT OR REPLACE INTO press_expectations VALUES(?,?,?,?,?,?)',
                 (year,week,world['world_id'],world['version'],canonical(data),digest(data)))


def load(conn,season,through_week):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='press_expectations'").fetchone():return []
    result=[]
    for r in conn.execute('SELECT * FROM press_expectations WHERE season=? AND week<=? ORDER BY week',(season,through_week)):
        data=json.loads(r['data_json'])
        if digest(data)!=r['sha']:raise ValueError('expectation snapshot checksum mismatch')
        result.append(data)
    return result


def compare(winner,opponent):
    """At least two adverse evidence axes, without a clear conflicting axis.

    Draw pot is displayed, but does not double-count the previous league rank
    from which Reign Cup's pots were formed. Missing values remain unknown.
    """
    adverse=[];favorable=[];basis=[]
    def record(axis,relation,text):
        basis.append({'axis':axis,'relation':relation,'text':text})
        if relation=='lower':adverse.append(axis)
        if relation=='higher':favorable.append(axis)
    a,b=winner.get('previous_overall_rank'),opponent.get('previous_overall_rank')
    if a is not None and b is not None:
        record('previous', 'lower' if a-b>=4 else 'higher' if b-a>=4 else 'close',
               f"上季联赛总顺序{winner['team']}第{a}、{opponent['team']}第{b}（一级在前、次级在后）")
    a,b=winner.get('current_league'),opponent.get('current_league')
    if a and b and a['competition']==b['competition'] and min(a['played'],b['played'])>0:
        relation=('lower' if a['rank']-b['rank']>=7 else 'higher' if b['rank']-a['rank']>=7 else 'close') if min(a['played'],b['played'])>=5 else 'close'
        record('current',relation,
               f"本季赛前联赛{winner['team']}{a['played']}场{a['points']}分排第{a['rank']}、{opponent['team']}{b['played']}场{b['points']}分排第{b['rank']}")
    a,b=winner.get('total_market_value'),opponent.get('total_market_value')
    if a is not None and b is not None and a>0 and b>0:
        record('value','lower' if a<=b*.75 else 'higher' if b<=a*.75 else 'close',
               f"赛前真实球员总身价{winner['team']}为{a:.2f}、{opponent['team']}为{b:.2f}（游戏估值）")
    return {'winner':winner['team'],'opponent':opponent['team'],'is_upset':len(adverse)>=2 and not favorable,
            'adverse_axes':adverse,'conflicting_axes':favorable,'basis':basis,'method':'evidence-comparison/1'}


def group_surprises(ctx,state,phase_games):
    """Group pot and qualification are authoritative; expectations are pre-phase."""
    history=ctx.source.get('expectation_history',[])
    first_week=min((m['week'] for m in phase_games),default=None)
    baseline=next((r for r in history if r['season']==ctx.season and r['before_week']==first_week),None)
    result=[];participants=state.get('participants',[])
    if len(participants)!=16:return result
    pots={tid:n//4+1 for n,tid in enumerate(participants)}
    for group,rows in state.get('group_standings',{}).items():
        for row in rows[:2]:
            tid=row['team_id']
            if pots[tid]<3:continue
            higher=[r['team_id'] for r in rows[2:] if pots[r['team_id']]<pots[tid]]
            item={'team':ctx.team(tid),'group':group,'pot':pots[tid],'qualified':True,
                  'wins':row['wins'],'comparisons':[],'baseline_available':baseline is not None,
                  'sustained':row['wins']>=3,'is_dark_horse':False}
            if baseline:
                item['comparisons']=[compare(baseline['teams'][tid],baseline['teams'][other]) for other in higher]
                item['is_dark_horse']=item['sustained'] and any(c['is_upset'] for c in item['comparisons'])
            result.append(item)
    return result


def qualification_surprises(ctx,state,qualified,eliminated,games):
    first_week=min((m['week'] for m in games),default=None)
    baseline=next((r for r in ctx.source.get('expectation_history',[]) if r['before_week']==first_week),None)
    if not baseline:return []
    ids={ctx.team(t):t for t in ctx.world['teams']}
    profiles=baseline['teams'];result=[]
    for name in qualified:
        tid=ids[name];prior=profiles[tid].get('previous_overall_rank')
        if prior is None:continue
        others=[ids[n] for n in eliminated if profiles[ids[n]].get('previous_overall_rank') is not None and profiles[ids[n]]['previous_overall_rank']<prior]
        comparisons=[compare(profiles[tid],profiles[t]) for t in others]
        surprising=[c for c in comparisons if c['is_upset']]
        if not surprising:continue
        wins=sum(m.get('winner_team_id')==tid for m in games)
        result.append({'team':name,'comparisons':surprising[:2],'sustained':wins>=3,'is_dark_horse':wins>=3,'baseline_available':True})
    return result


def surprise_paragraphs(rows):
    result=[]
    for r in rows:
        label='本阶段的黑马' if r.get('is_dark_horse') else '制造出线冷门' if any(c['is_upset'] for c in r.get('comparisons',[])) else '晋级'
        prefix=(f"{r['group']}组第{r['pot']}档球队" if r.get('pot') else '')
        result.append(prefix+r['team']+('成为'+label if label=='本阶段的黑马' else label)+'。')
        for comparison in r.get('comparisons',[]):
            if comparison['is_upset']:result.append('；'.join(b['text'] for b in comparison['basis'])+'。')
    return result
