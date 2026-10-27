"""Read-only story facts. All inputs belong to the report's frozen cutoff."""
from collections import defaultdict
from football_simulator.game import rules


def clock(season,week):return (int(season)-1)*52+int(week)


def career_snapshot(world,season,week):
    cutoff=clock(season,week);moves={};drafts={};uncertain=set()
    for row in [*world.get('transfer_history',[]),*world.get('season',{}).get('transfers',[])]:
        # Legacy completed exchanges omit status, as in game.transfers.
        if row.get('status') not in (None,'approved','已批准'):continue
        year=row.get('season',row.get('season_number'));when=row.get('week')
        if year is None or when is None or row.get('team_a') not in world['teams'] or row.get('team_b') not in world['teams']:
            uncertain.update([*(row.get('players_a') or []),*(row.get('players_b') or [])]);continue
        if clock(year,when)>cutoff:continue
        for field,a,b in [('players_a',row['team_a'],row['team_b']),('players_b',row['team_b'],row['team_a'])]:
            for pid in row.get(field) or []:
                key=(row.get('trade_id'),pid,int(year),int(when),a,b)
                moves[key]={'player_id':pid,'from_team_id':a,'to_team_id':b,
                            'season':int(year),'week':int(when),'trade_id':row.get('trade_id')}
    for log in [*[log for a in world.get('archives',[]) for log in a.get('draft',[])],*world.get('season',{}).get('draft_log',[])]:
        if int(log['season'])>season or (int(log['season'])==season and week<49):continue
        for index,row in enumerate(log.get('results',[]),1):
            drafts[row['player_id']]={'season':int(log['season']),'team_id':row['team_id'],'pick':int(row.get('pick') or index)}
    return {'moves':sorted(moves.values(),key=lambda r:(r['season'],r['week'],str(r['trade_id']),r['player_id'])),
            'drafts':drafts,'uncertain_players':sorted(uncertain)}


def arrival(career,pid,team,season,week,*,include_same_week=False):
    if pid in career.get('uncertain_players',[]):return None
    cutoff=clock(season,week)
    moves=[m for m in career.get('moves',[]) if m['player_id']==pid and
           clock(m['season'],m['week'])<cutoff+int(include_same_week)]
    if not moves or moves[-1]['to_team_id']!=team:return None
    last=moves[-1];elapsed=cutoff-clock(last['season'],last['week'])
    if not 0<=elapsed<=52:return None
    previous={m[k] for m in moves[:-1] for k in ('from_team_id','to_team_id')}
    draft=career.get('drafts',{}).get(pid)
    draft_return=bool(draft and draft['team_id']==team)
    return {**last,'weeks_since_joining':elapsed,'return_to_former_club':team in previous or draft_return,
            'return_to_draft_club':draft_return,'draft_origin':draft}


def named_arrival(value,world):
    if not value:return None
    result=dict(value)
    for key in ('from_team_id','to_team_id'):
        result[key.removesuffix('_id')]=world['teams'][value[key]]['club_en']
    return result


def champion_at(world,year,competition):
    archived=next((a for a in world.get('archives',[]) if int(a['season_number'])==year),{})
    edition=world.get('edition_archives',{}).get(f'{year}:{competition}',{})
    title=(edition.get('championship') or {}).get('champion_team_id')
    if title:return title
    if competition in rules.LEAGUE_COMPETITIONS:
        table=archived.get(competition+'_table',[])
        return table[0]['team_id'] if table else None
    return archived.get('cup_champions',{}).get(competition)


def title_background(world,season,competition):
    first=rules.CUP_META[competition]['start_season'] if competition in rules.CUP_IDS else 1
    known={y:champion_at(world,y,competition) for y in range(first,season)}
    defender=known.get(season-1)
    if not defender:return {'complete':all(known.values()),'champions':known}
    run=0;exact=True
    for year in range(season-1,first-1,-1):
        if not known[year]:exact=False;break
        if known[year]!=defender:break
        run+=1
    return {'complete':all(known.values()),'champions':known,'defending_team_id':defender,
            'defending_team':world['teams'][defender]['club_en'],'previous_consecutive_titles':run if exact else None}


def history_events(world,season,competition,*,champion=None,eliminated=()):
    history=title_background(world,season,competition);result=[]
    defender=history.get('defending_team_id');run=history.get('previous_consecutive_titles')
    if defender and (defender in eliminated or (champion and champion!=defender)):
        result.append({'kind':'defense_ended','team':history['defending_team'],'previous_consecutive_titles':run})
    if champion and history['complete']:
        years=[y for y,t in history['champions'].items() if t==champion]
        total=len(years)+1;consecutive=1
        for y in sorted(history['champions'],reverse=True):
            if history['champions'][y]!=champion:break
            consecutive+=1
        result.append({'kind':'title','team':world['teams'][champion]['club_en'],'total_titles':total,'consecutive_titles':consecutive})
        if years and season-max(years)>=3:
            result.append({'kind':'drought_ended','team':world['teams'][champion]['club_en'],'last_title_season':max(years),'seasons_without_title':season-max(years)-1})
        sequence=[*history['champions'].values(),champion];distinct=[]
        for team in reversed(sequence):
            if not team or team in distinct:break
            distinct.append(team)
        if len(distinct)>=3:result.append({'kind':'different_champions','editions':len(distinct),'teams':[world['teams'][t]['club_en'] for t in reversed(distinct)]})
    return result


def performance_events(world,matches,stats,season,*,complete):
    """Firsts/totals need full history; bounded observed streaks need no guess."""
    games={m['match_id']:m for m in matches};state={};club={};events=defaultdict(list)
    ordered=sorted((r for r in stats if r.get('appeared') and r['match_id'] in games),
                   key=lambda r:(games[r['match_id']]['season_number'],games[r['match_id']]['week'],
                                 {'premier':0,'second':1,'playoff':2}.get(games[r['match_id']]['competition'],3),r['match_id'],r['player_id']))
    for row in ordered:
        pid=row['player_id'];player=world['players'].get(pid,{})
        if not player.get('is_real'):continue
        game=games[row['match_id']];tid=row['team_id'];goals=int(row.get('goals') or 0)
        s=state.setdefault(pid,{'apps':0,'goals':0,'hat_tricks':0,'scoring_run':0,'clean_run':0,'scoring_bounded':complete,'clean_bounded':complete})
        c=club.setdefault((pid,tid),{'apps':0,'goals':0})
        found=[]
        if complete:
            if c['apps']==0:found.append({'kind':'club_debut'})
            if c['goals']==0 and goals:found.append({'kind':'club_first_goal'})
            if goals>=3 and s['hat_tricks']==0:found.append({'kind':'first_hat_trick'})
            if (s['apps']+1)%100==0:found.append({'kind':'appearances','total':s['apps']+1})
            if s['goals']//100<(s['goals']+goals)//100:found.append({'kind':'goals','total':((s['goals']+goals)//100)*100,'after':s['goals']+goals})
        for metric,good in [('scoring',goals>0),('clean',bool(row.get('clean_sheets')) if player.get('position')=='GK' else False)]:
            if metric=='clean' and player.get('position')!='GK':continue
            before=s[metric+'_run'];bounded=s[metric+'_bounded']
            if good:
                s[metric+'_run']+=1
                if bounded and s[metric+'_run']>=3:found.append({'kind':metric+'_streak','count':s[metric+'_run'],'ended':False})
            else:
                if bounded and before>=3:found.append({'kind':metric+'_streak','count':before,'ended':True})
                s[metric+'_run']=0;s[metric+'_bounded']=True
        s['apps']+=1;s['goals']+=goals;s['hat_tricks']+=int(goals>=3);c['apps']+=1;c['goals']+=goals
        if game['season_number']==season:
            events[row['match_id']]+=[{**event,'player':player['name'],'player_id':pid,'team':world['teams'][tid]['club_en']} for event in found]
    return dict(events)


def swiss_turnarounds(world,games):
    sequences=defaultdict(list)
    for game in sorted((g for g in games if g['stage'].startswith('swiss_')),key=lambda g:(g['week'],g['match_id'])):
        winner=game.get('winner_team_id')
        if winner is None:continue
        for team in (game['home_team_id'],game['away_team_id']):sequences[team].append('W' if team==winner else 'L')
    return [{'team':world['teams'][t]['club_en'],'sequence':''.join(seq),'qualified':seq==list('LLWWW')}
            for t,seq in sequences.items() if seq in (list('LLWWW'),list('WWLLL'))]


def complete_match_history(world,matches,season):
    archived={int(a['season_number']) for a in world.get('archives',[]) if int(a['season_number'])<season}
    return archived==set(range(1,season)) and all(sum(m['season_number']==y for m in matches)==
        766+sum(rules.CUP_META[c]['match_count'] for c in rules.CUP_IDS if rules.cup_active(c,y)) for y in archived)


def game_notes(world,matches,stats,season,week,*,target_week=None):
    """Freeze connections, without later player ownership or reconstructed quotes."""
    career=career_snapshot(world,season,week);by_game=defaultdict(list);by_player=defaultdict(list)
    cutoff=clock(season,week)
    games=[g for g in matches if clock(g['season_number'],g['week'])<=cutoff]
    game_by_id={g['match_id']:g for g in games};complete=complete_match_history(world,games,season)
    def game_order(g):return (g['season_number'],g['week'],{'premier':0,'second':1,'playoff':2}.get(g['competition'],3),g['match_id'])
    metadata={f['match_id']:f for a in world.get('edition_archives',{}).values() for f in a.get('fixtures',[])}
    metadata.update({f['match_id']:f for f in world.get('season',{}).get('fixtures',[])})
    ties=defaultdict(list)
    for g in games:
        key=g.get('tie_id') or metadata.get(g['match_id'],{}).get('tie_id')
        if key:ties[key].append(g)
    for row in stats:
        if row.get('appeared') and world['players'].get(row['player_id'],{}).get('is_real'):
            by_game[row['match_id']].append(row);by_player[row['player_id']].append(row)
    milestones=performance_events(world,games,stats,season,complete=complete)
    result={}
    for game in games:
        if game['season_number']!=season or (target_week is not None and game['week']!=target_week):continue
        mid=game['match_id'];rows=by_game[mid];arrivals={}
        for row in rows:
            value=arrival(career,row['player_id'],row['team_id'],season,game['week'])
            if value:arrivals[world['players'][row['player_id']]['name']]=named_arrival(value,world)
        teams={game['home_team_id'],game['away_team_id']}
        prior=[g for g in games if g['season_number']==season and g['competition']==game['competition']
               and {g['home_team_id'],g['away_team_id']}==teams and g['week']<game['week']]
        meeting=None
        if prior:
            last=max(prior,key=lambda g:(g['week'],g['match_id']))
            from football_simulator.press.builder import field_score
            h,a=field_score(last)
            meeting={'week':last['week'],'home':world['teams'][last['home_team_id']]['club_en'],
                     'away':world['teams'][last['away_team_id']]['club_en'],'home_goals':h,'away_goals':a,
                     'winner':world['teams'][last['winner_team_id']]['club_en'] if last.get('winner_team_id') else None}
        duels=[]
        traded=defaultdict(list)
        for row in rows:
            value=arrival(career,row['player_id'],row['team_id'],season,game['week'])
            if value and {value['from_team_id'],value['to_team_id']}==teams:
                traded[value['trade_id']].append({'player':world['players'][row['player_id']]['name'],
                    'team':world['teams'][row['team_id']]['club_en'],'team_id':row['team_id']})
        for trade,participants in traded.items():
            if len({p['team_id'] for p in participants})==2:duels.append({'trade_id':trade,'players':participants})
        opponent_runs=[]
        for row in rows:
            if not row.get('goals'):continue
            pid=row['player_id'];opponent=game['away_team_id'] if row['team_id']==game['home_team_id'] else game['home_team_id']
            own=[r for r in by_player[pid] if r['match_id'] in game_by_id and r['team_id']!=opponent and
                 opponent in (game_by_id[r['match_id']]['home_team_id'],game_by_id[r['match_id']]['away_team_id']) and
                 game_order(game_by_id[r['match_id']])<=game_order(game)]
            own.sort(key=lambda r:game_order(game_by_id[r['match_id']]))
            run=0
            for r in reversed(own):
                if not r.get('goals'):break
                run+=1
            if run>=3 and (complete or all(game_by_id[r['match_id']]['season_number']==season for r in own[-3:])):
                opponent_runs.append({'player':world['players'][pid]['name'],'opponent':world['teams'][opponent]['club_en'],'last_meetings':3})
        head_to_head=[g for g in games if {g['home_team_id'],g['away_team_id']}==teams and
                     (g['season_number'],g['week'])<(season,game['week'])]
        winner=game.get('winner_team_id');history=[]
        if winner and game['competition'] in rules.LEAGUE_COMPETITIONS:
            own_prior=[g for g in head_to_head if g['season_number']==season and g['competition']==game['competition']]
            if len(own_prior)==1 and own_prior[0].get('winner_team_id')==winner:
                history.append({'kind':'league_double','team':world['teams'][winner]['club_en'],'opponent':world['teams'][next(t for t in teams if t!=winner)]['club_en']})
        if winner:
            without=[]
            for previous in sorted(head_to_head,key=lambda g:(g['season_number'],g['week'],g['match_id']),reverse=True):
                if previous.get('winner_team_id')==winner:break
                without.append(previous)
            if len(without)>=3 and (complete or all(g['season_number']==season for g in without[:3])):
                history.append({'kind':'head_to_head_break','team':world['teams'][winner]['club_en'],
                    'opponent':world['teams'][next(t for t in teams if t!=winner)]['club_en'],'previous_winless':len(without),
                    'first_win':len(without)==len(head_to_head) and complete})
        contributors=[]
        tie_key=game.get('tie_id') or metadata.get(mid,{}).get('tie_id')
        legs=sorted(ties.get(tie_key,[]),key=game_order)
        if len(legs)==2 and mid==legs[-1]['match_id']:
            first={r['player_id']:r for r in by_game[legs[0]['match_id']]}
            for r in rows:
                a=first.get(r['player_id'])
                if a and a['team_id']==r['team_id'] and (a.get('goals') or a.get('assists')) and (r.get('goals') or r.get('assists')):
                    contributors.append({'player':world['players'][r['player_id']]['name'],'team':world['teams'][r['team_id']]['club_en'],
                        'first_goals':a.get('goals',0),'first_assists':a.get('assists',0),'second_goals':r.get('goals',0),'second_assists':r.get('assists',0)})
        old_final=[g for g in head_to_head if g['season_number']==season-1 and g['competition']==game['competition']
                   and g['stage'] in ('final','grand_final','final_leg_1','final_leg_2')]
        previous_final=None
        old_champion=champion_at(world,season-1,game['competition'])
        if old_final and old_champion:previous_final={'season':season-1,'champion':world['teams'][old_champion]['club_en'],
            'teams':[world['teams'][game['home_team_id']]['club_en'],world['teams'][game['away_team_id']]['club_en']]}
        result[mid]={'player_arrivals':arrivals,'player_events':milestones.get(mid,[]),
                     'previous_meeting_this_edition':meeting,'exchange_duels':duels,
                     'opponent_scoring_runs':opponent_runs,'meeting_events':history,
                     'two_leg_contributors':contributors,'previous_final':previous_final}
    return result


def transfer_notes(world,matches,stats,move,season,week,*,service_history=None,final_history=None):
    career=career_snapshot(world,season,week);pid=move['player_id'];target=move['to_team_id']
    value=named_arrival(arrival(career,pid,target,move['season'],move['week'],include_same_week=True),world) or {}
    prior=[m for m in career['moves'] if m['player_id']==pid and clock(m['season'],m['week'])<clock(move['season'],move['week'])]
    by_id={m['match_id']:m for m in matches}
    appearances=[r for r in stats if r['player_id']==pid and r.get('appeared') and r['team_id']==move['from_team_id']
                 and r['match_id'] in by_id and clock(by_id[r['match_id']]['season_number'],by_id[r['match_id']]['week'])<=clock(move['season'],move['week'])]
    years={by_id[r['match_id']]['season_number'] for r in appearances};run=0
    if service_history:
        years={r['season_number'] for r in service_history['team_seasons'] if r['player_id']==pid and r['team_id']==move['from_team_id']
               and (r['season_number']<move['season'] or (r['season_number']==move['season'] and r['first_week']<=move['week']))}
    for year in range(move['season'],0,-1):
        if year not in years:break
        run+=1
    covered=service_history['complete'] if service_history is not None else complete_match_history(world,matches,season)
    if run>=3 and not prior and pid not in career.get('uncertain_players',[]) and covered:value['first_departure_after_seasons']=run
    final_matches=final_history['matches'] if final_history is not None else matches
    final_rows=final_history['player_match_stats'] if final_history is not None else stats
    finals=[m for m in final_matches if m['stage'] in ('final','grand_final','final_leg_1','final_leg_2','promotion_final','promotion_final_leg_1','promotion_final_leg_2')
            and clock(m['season_number'],m['week'])<clock(move['season'],move['week'])]
    for game in sorted(finals,key=lambda m:(m['season_number'],m['week']),reverse=True):
        own=next((r for r in final_rows if r['match_id']==game['match_id'] and r['player_id']==pid and r.get('appeared')),None)
        if own is None:continue
        peers=[r for r in final_rows if r['match_id']==game['match_id'] and r.get('appeared') and r['team_id']!=own['team_id']
               and world['players'].get(r['player_id'],{}).get('team_id')==target]
        if peers:
            other=max(peers,key=lambda r:(r.get('goals',0)+r.get('assists',0),r['player_id']))
            value['former_final_opponent']={'player':world['players'][other['player_id']]['name'],
                'season':game['season_number'],'competition':rules.display_name(game['competition'])};break
    draft=career['drafts'].get(pid)
    if draft and draft['pick']<=5:
        peers=[{'player':world['players'][other]['name'],**origin} for other,origin in career['drafts'].items()
               if other!=pid and origin['season']!=draft['season'] and origin['pick']<=5
               and world['players'].get(other,{}).get('team_id')==target]
        if peers:value['high_pick_teammates']=peers
    return value


def enrich_feature(ctx,kind,content):
    """Add proved article connections while still inside the frozen material."""
    world=ctx.world;season=ctx.season;names_to_ids={ctx.team(t):t for t in world['teams']}
    competition=next((c for c in rules.ALL_COMPETITION_IDS if rules.display_name(c)==content.get('competition')),None)
    if competition and kind in ('phase','competition'):
        champion=names_to_ids.get(content.get('champion'))
        eliminated=[names_to_ids[n] for n in content.get('eliminated',[]) if n in names_to_ids]
        events=history_events(world,season,competition,champion=champion,eliminated=eliminated)
        for event in events:
            if event['kind']!='defense_ended':continue
            defender=names_to_ids[event['team']]
            played=[m for m in ctx.games(competition) if defender in (m['home_team_id'],m['away_team_id'])]
            if competition in rules.LEAGUE_COMPETITIONS:event['league_title']=True
            elif played:
                from football_simulator.press.features import stage_name
                last=max(played,key=lambda m:(m['week'],m['match_id']))
                event['exit_stage']=stage_name(last['stage'])
            elif kind=='competition':event['not_entered']=True
        old_finals=[m for m in ctx.games(competition,season-1) if m['stage'] in ('final','grand_final','final_leg_1','final_leg_2')]
        if old_finals:
            previous=old_finals[-1];pair={previous['home_team_id'],previous['away_team_id']};old_winner=champion_at(world,season-1,competition)
            if old_winner and any({m['home_team_id'],m['away_team_id']}==pair for m in ctx.games(competition)):
                events.append({'kind':'previous_final','season':season-1,'champion':ctx.team(old_winner),
                               'teams':[ctx.team(previous['home_team_id']),ctx.team(previous['away_team_id'])]})
        if champion and competition=='ember' and world['season']['cups']['ember'].get('loss_stage',{}).get(champion) is not None:
            events.append({'kind':'losers_champion','team':ctx.team(champion)})
        if champion and competition=='playoff':
            rows=world['season'].get('final_tables',{}).get('second',[])
            rank=next((n for n,r in enumerate(rows,1) if r['team_id']==champion),None)
            if rank:events.append({'kind':'playoff_low_seed','team':ctx.team(champion),'league_rank':rank})
        if competition=='premier' and kind=='competition':
            previous=next((a for a in world.get('archives',[]) if a['season_number']==season-1),{})
            lower={r['team_id'] for r in previous.get('second_table',[])}
            for row in content.get('final_table',[]):
                if names_to_ids.get(row['team']) in lower:events.append({'kind':'promoted_finish','team':row['team'],'rank':row['rank']})
        content['narrative_history']=events
        if competition=='starpath':content['swiss_turnarounds']=swiss_turnarounds(world,ctx.games(competition))
    if kind=='top20':
        from football_simulator.press.features import draft_logs
        groups=defaultdict(list);ranks={p['player']:p['rank'] for p in content['rankings']};contrasts=[]
        for p in content['rankings']:
            for team in p.get('actual_teams',[]):
                if team.get('appearances',0)>0:groups[team['team']].append(p['player'])
        content['club_top20_groups']=[{'team':team,'players':players} for team,players in groups.items() if len(players)>=3]
        for year,log in draft_logs(ctx).items():
            picked=sorted(log.get('results',[]),key=lambda r:int(r.get('pick') or log['results'].index(r)+1))
            if not picked:continue
            first=picked[0];first_rank=ranks.get(ctx.player(first['player_id']))
            for n,r in enumerate(log.get('results',[]),1):
                pick=int(r.get('pick') or n);rank=ranks.get(ctx.player(r['player_id']))
                if pick>5 and rank and (first_rank is None or rank<first_rank):
                    contrasts.append({'season':year,'player':ctx.player(r['player_id']),'pick':pick,'rank':rank,
                                      'first_player':ctx.player(first['player_id']),'first_pick':int(first.get('pick') or 1),'first_rank':first_rank})
        content['draft_class_contrasts']=contrasts
        for p in content['changes'].get('dropped_out',[]):
            run=0;exact=True
            for year in range(season-1,0,-1):
                archive=ctx.archives.get(year,{})
                if not isinstance(archive.get('top20'),list):exact=False;break
                if not any(ctx.player(r['player_id'])==p['player'] for r in archive['top20']):break
                run+=1
            p['previous_consecutive_selections']=run;p['previous_run_exact']=exact
    if kind=='draft':
        from football_simulator.press.features import draft_logs
        career=career_snapshot(world,season,ctx.week)
        for pick in content['picks']:
            target=names_to_ids[pick['team']]
            pick['high_pick_teammates']=[{'player':ctx.player(pid),**origin} for pid,origin in career['drafts'].items()
                if origin['season']!=season and origin['pick']<=5 and world['players'].get(pid,{}).get('team_id')==target]
        official=world['season'].get('awards',{}).get('top20',[])
        content['draft_class_contrasts']=[]
        if len(official)==20 and [r['rank'] for r in official]==list(range(1,21)):
            ranks={r['player_id']:r['rank'] for r in official}
            for year,log in draft_logs(ctx).items():
                picks=[(int(r.get('pick') or n),r) for n,r in enumerate(log.get('results',[]),1)]
                if not picks:continue
                first_pick,first=min(picks,key=lambda p:p[0]);first_rank=ranks.get(first['player_id'])
                for pick,row in picks:
                    rank=ranks.get(row['player_id'])
                    if pick>5 and rank and (first_rank is None or rank<first_rank):
                        content['draft_class_contrasts'].append({'season':year,'player':ctx.player(row['player_id']),
                            'pick':pick,'rank':rank,'first_player':ctx.player(first['player_id']),'first_pick':first_pick,'first_rank':first_rank})
    if kind in ('phase','competition'):
        career=career_snapshot(world,season,ctx.week);followups=[]
        trades={m['trade_id']:m for m in career['moves'] if 1<=clock(season,ctx.week)-clock(m['season'],m['week'])<=26}
        for move in trades.values():
            elapsed=clock(season,ctx.week)-clock(move['season'],move['week']);teams=[]
            for team in (move['from_team_id'],move['to_team_id']):
                split={}
                for label,low,high in [('before',clock(move['season'],move['week'])-elapsed,clock(move['season'],move['week'])),
                                       ('after',clock(move['season'],move['week']),clock(season,ctx.week))]:
                    rows=[g for g in ctx.matches.values() if low<clock(g['season_number'],g['week'])<=high and team in (g['home_team_id'],g['away_team_id'])]
                    from football_simulator.press.builder import field_score
                    w=d=l=0
                    for game in rows:
                        h,a=field_score(game);gf,ga=(h,a) if team==game['home_team_id'] else (a,h)
                        w+=gf>ga;d+=gf==ga;l+=gf<ga
                    split[label]={'matches':len(rows),'wins':w,'draws':d,'losses':l}
                teams.append({'team':ctx.team(team),**split})
            if all(t['before']['matches'] and t['after']['matches'] for t in teams):
                followups.append({'trade_id':move['trade_id'],'season':move['season'],'week':move['week'],'teams':teams})
        content['trade_followups']=followups
