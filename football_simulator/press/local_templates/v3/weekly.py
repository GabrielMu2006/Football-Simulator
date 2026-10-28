"""Original weekly sports prose, rendered from build_plan's structured cards."""
from football_simulator.press.local_templates.v3.common import names,match_line,outcomes,section,article


def exciting(game):
    lines=[];flow=game.get('score_flow',{})
    if flow.get('winner') and flow.get('winner_trailing_spells',0)>0:
        lines.append(flow['winner']+'一度落后，最终逆转取胜。')
    goals=game.get('goals',[])
    key=[e for e in goals if e['field_effect'] in ('反超比分','再次取得领先','扳平比分')]
    if goals:
        last=goals[-1];field=game['field_score_home_away']
        if last['minute']>=85 and ((field['home']==field['away'] and last['field_effect']=='扳平比分') or
                (flow.get('winner')==last['team'] and last['field_effect'] in ('首开纪录','取得领先','再次取得领先','反超比分'))):
            key=[e for e in key if e is not last]+[last]
    for e in key[-2:]:
        subject=e.get('scorer') or e['team']
        text=f"第{e['minute']}分钟，{subject}为{e['team']}{e['field_effect']}" if e.get('scorer') else f"第{e['minute']}分钟，{subject}{e['field_effect']}"
        if e.get('assister'):text+=f"，{e['assister']}送出助攻"
        lines.append(text+'。')
    return ''.join(lines)


def game_paragraphs(game,background_seen=None):
    from football_simulator.press.local_templates.v3.narrative import production,arrival_line,milestone_lines,history_lines
    from football_simulator.press.local_templates.v3.background import comparison_sentence
    players=game.get('players',[])[:2]
    statuses=game.get('qualification_outcomes',[])
    ordered=dict(game)
    ordered['qualification_outcomes']=sorted(statuses,key=lambda r:{'champion':0,'qualified':1,'eliminated':2}.get(r['status'],3))
    ordered['swiss_qualification_records']=sorted(game.get('swiss_qualification_records',[]),key=lambda r:not (r['qualified_in_this_match'] or r['eliminated_in_this_match']))
    impact=outcomes(ordered)
    important=any(r['status'] in ('champion','qualified') or
                  (r['status']=='eliminated' and r['team'] in game.get('defending_champions',[])) for r in statuses)
    lead=(impact+f"在{game['competition']}{game['stage']}中，"+match_line(game,venue=False) if important else
          f"在{game['competition']}{game['stage']}中，"+match_line(game,venue=False)+impact)
    if players:
        star=players[0];lead+=star['player']+'在这场比赛中'+production(star)+'。'
        if len(players)>1:
            other=players[1]
            if other.get('goals',0)>=2 or other.get('assists',0)>=2 or other.get('successful_saves',0)>=5:
                lead+=other['player']+'则'+production(other)+'。'
        noteworthy=star.get('goals',0)>=2 or star.get('assists',0)>=2 or star.get('successful_saves',0)>=5 or star.get('successful_defenses',0)>=6
        if noteworthy:lead+=arrival_line(star['player'],game.get('player_arrivals',{}).get(star['player']))
    action=exciting(game)
    if action:lead+=action
    if game.get('stadium'):lead+='本场比赛在'+game['stadium']+'进行。'
    lines=[lead];context=[]
    for p in game.get('former_clubs',[]):
        context.append(p['player']+'这次面对的是旧主'+p['former_team']+'。')
    for r in game.get('streak_events',[]):
        label={'wins':'连胜','losses':'连败','unbeaten':'不败','winless':'不胜'}[r['kind']]
        span='跨赛季的' if r.get('crosses_seasons') else ''
        text=(f"{r['team']}{span}{r['scope']}{r['before_match']}场{label}就此终结" if r['ended'] else
              f"{r['team']}将{span}{r['scope']}{label}延续到{r['after_match']}场")
        if r.get('later_match_in_same_scope_this_week'):text+='（截至本场结束）'
        context.append(text+'。')
    for team in game.get('season_first_losses',[]):context.append(team+'也尝到了本赛季联赛首败。')
    for r in game.get('club_records',[]):context.append(r['team']+'在'+game['competition']+r['milestone']+'。')
    background=game.get('defending_title_run',{})
    if background.get('defending_team') and any(r['team']==background['defending_team'] and r['status']=='eliminated' for r in statuses):
        context.append(history_lines([{'kind':'defense_ended','team':background['defending_team'],'previous_consecutive_titles':background.get('previous_consecutive_titles')}]))
    comparison=game.get('expectation_comparison')
    if comparison and comparison['is_upset']:context.append(comparison_sentence(comparison)+'这次取胜，为本轮添上了一场冷门。')
    elif game.get('race_meeting') or game.get('upset_basis'):
        rows=game.get('race_meeting') or game['upset_basis']
        context.append('两队赛前的联赛位置分别是'+'、'.join(f"{r['team']}第{r['rank']}（{r['points']}分）" for r in rows)+'。')
    meaningful=[e for e in game.get('player_events',[]) if e['kind'] not in ('club_debut','club_first_goal') or
                e['player'] in {p['player'] for p in players}]
    context.append(milestone_lines(meaningful[:3]))
    for r in game.get('opponent_scoring_runs',[]):context.append(f"{r['player']}近{r['last_meetings']}次出场对阵{r['opponent']}都有进球，成了这支球队的克星。")
    for r in game.get('meeting_events',[]):
        if r['kind']=='league_double':context.append(r['team']+'本季两次在联赛击败'+r['opponent']+'，完成赛季双杀。')
        else:context.append(r['team']+('首次击败' if r['first_win'] else '结束近'+str(r['previous_winless'])+'次交锋不胜，击败了')+r['opponent']+'。')
    meeting=game.get('previous_meeting_this_edition')
    if meeting and (game['stage']!='联赛' or any(r['kind']=='league_double' for r in game.get('meeting_events',[]))):
        context.append(f"本届第{meeting['week']}周两队曾交手，{meeting['home']}与{meeting['away']}当时的比分是{meeting['home_goals']}–{meeting['away_goals']}。")
    for duel in game.get('exchange_duels',[]):
        context.append(names(p['player'] for p in duel['players'])+'曾在同一笔交换中互换球队，如今分别代表新东家出场交锋。')
    if game.get('previous_final'):
        context.append(history_lines([{'kind':'previous_final',**game['previous_final']}]))
    for e in meaningful:
        if e['kind']=='scoring_streak' and not e['ended']:
            own=next((p for p in game.get('players',[]) if p['player']==e['player']),None)
            score=game['field_score_home_away'];won=(e['team']==game['home'] and score['home']>score['away']) or (e['team']==game['away'] and score['away']>score['home'])
            winless=next((r for r in game.get('streak_events',[]) if r['team']==e['team'] and r['kind']=='winless' and not r['ended'] and r['after_match']>=3),None)
            if own and not won and winless:context.append(f"{e['player']}的进球仍在继续，{e['team']}却已在{winless['scope']}连续{winless['after_match']}场未能取胜。")
    if any(context):lines.append(''.join(context))
    tie=game.get('two_leg_tie')
    if tie and tie.get('complete') and game.get('leg')==2 and tie.get('advancing_team'):
        winner=tie['advancing_team'];score=game['field_score_home_away']
        lost=(winner==game['home'] and score['home']<score['away']) or (winner==game['away'] and score['away']<score['home'])
        if lost:lines.append(winner+'虽然输掉了这一回合，仍赢下两回合对阵'+('并捧杯。' if any(r['team']==winner and r['status']=='champion' for r in statuses) else '并晋级。'))
        if tie.get('legs'):
            first=tie['legs'][0];fs=first['field_score_home_away'];first_winner=first['home'] if fs['home']>fs['away'] else first['away'] if fs['away']>fs['home'] else None
            if first_winner and first_winner!=winner:lines.append(first_winner+'首回合曾经取胜，最终仍未能赢下这轮对阵。')
    for p in game.get('two_leg_contributors',[]):
        lines.append(p['player']+'在两回合都为'+p['team']+'留下了贡献：首回合'+production({'goals':p['first_goals'],'assists':p['first_assists']})+'，次回合'+production({'goals':p['second_goals'],'assists':p['second_assists']})+'。')
    return lines


def league_paragraphs(data, *, closure=False):
    leaders=data.get('leader_and_runner_up',[]);lines=[]
    if leaders:
        first=leaders[0];text=f"{data['competition']}"+('最终由' if closure else '目前由')+f"{first['team']}以{first['points']}分"+('夺冠' if closure else '领跑')
        if len(leaders)>1:
            second=leaders[1];gap=first['points']-second['points']
            text+=(f"，与第二名{second['team']}同分" if gap==0 else f"，领先第二名{second['team']}{gap}分")
        lines.append(text+'。')
    for key,label in [('promoted_directly','直接升级'),('qualified_for_promotion_playoff','进入升级附加赛'),('relegated','降级')]:
        if data.get(key):lines.append(names(data[key])+label+'。')
    if closure:
        for r in data.get('decisive_places',[]):
            change=r.get('week_change',{})
            if change.get('before_rank') is not None and change['before_rank']!=r['rank']:
                lines.append(f"{r['team']}从周初第{change['before_rank']}升至第{r['rank']}，最终积{r['points']}分。" if change['before_rank']>r['rank'] else
                             f"{r['team']}从周初第{change['before_rank']}降至第{r['rank']}，最终积{r['points']}分。")
    return lines


def phase_paragraphs(data):
    lines=[]
    for key,label in [('qualified','晋级'),('eliminated','出局'),('still_competing','继续瑞士轮赛程'),
                      ('direct_quarterfinalists','直接进入八强'),('play_in_winners','通过突围赛进入八强')]:
        values=data.get('qualification_summary',{}).get(key,data.get(key,[]))
        if values:lines.append(names(values)+label+'。')
    if data.get('group_qualifiers'):
        lines.append('；'.join(group+'组：'+names(teams) for group,teams in data['group_qualifiers'].items())+'，以上球队进入八强。')
    if data.get('knockout_seeding_final') and data.get('knockout_seeds'):
        lines.append('最终种子顺序为'+names(f"第{r['seed']}号{r['team']}" for r in data['knockout_seeds'])+'。')
    pairs=data.get('quarterfinal_draw',{}).get('pairings') or data.get('play_in_pairs',[])
    if pairs:lines.append(('八强对阵为' if data.get('quarterfinal_draw') else '突围赛对阵为')+'；'.join('对阵'.join(pair) for pair in pairs)+'。')
    elif data.get('upcoming_drawn_ties'):
        lines.append('已确定的后续对阵：'+'；'.join(p['stage']+'，'+'对阵'.join(p['teams']) for p in data['upcoming_drawn_ties'])+'。')
    return lines


def heading_for(story):
    d=story['content'];kind=story['kind']
    if kind=='league_closure':return d['league']['champion']+'收获'+d['league']['competition']+'冠军'
    if kind=='early_champion':return d['champion']+'提前夺冠'
    if kind=='phase_closure':return d['competition']+'阶段收官'
    race=d.get('league_race')
    if race and race.get('leader_changed_this_week'):
        return race['leader_and_runner_up'][0]['team']+'登上'+race['competition']+'榜首'
    games=d.get('matches',[]);g=games[0]
    for game in games:
        for r in game.get('qualification_outcomes',[]):
            if r['status']=='champion':return r['team']+'赢得'+game['competition']
    for game in games:
        for r in game.get('qualification_outcomes',[]):
            if r['status']=='eliminated' and r['team'] in game.get('defending_champions',[]):return '卫冕冠军'+r['team']+'提前出局' if not game['stage'].startswith('决赛') else r['team']+'卫冕之旅结束'
    for game in reversed(games):
        if game.get('shootout_winner'):return game['shootout_winner']+'点球胜出'
        tie=game.get('two_leg_tie')
        if tie and tie['complete'] and tie.get('advancing_team'):return tie['advancing_team']+'赢下两回合对阵'
    established=[r['team'] for r in g.get('league_background',[]) if r['team'] in g.get('season_first_losses',[]) and r['played']>=5]
    if established:return names(established)+'联赛首败'
    if g.get('score_flow',{}).get('winner_trailing_spells',0):return g['score_flow']['winner']+'逆转取胜'
    star=next((p for p in g.get('players',[]) if p.get('goals',0)>=3),None)
    if star:return star['player']+'单场打进'+str(star['goals'])+'球'
    field=g['field_score_home_away']
    if field['home']==field['away']:return g['home']+'与'+g['away']+'握手言和'
    winner=g['home'] if field['home']>field['away'] else g['away']
    loser=g['away'] if field['home']>field['away'] else g['home']
    return winner+'击败'+loser


def render(packet,plan):
    sections=[];mapping=[];stories=plan['editorial']['stories'];background_seen=set()
    for story in stories:
        d=story['content'];kind=story['kind'];paragraphs=[]
        if kind in ('league_closure','early_champion'):
            paragraphs=league_paragraphs(d['league'],closure=kind=='league_closure')
            if kind=='early_champion':paragraphs.insert(0,d['champion']+'已提前锁定本赛季'+d['league']['competition']+'冠军。')
            for g in d.get('decisive_matches',[]):paragraphs+=game_paragraphs(g,background_seen)
        elif kind=='phase_closure':paragraphs=phase_paragraphs(d)
        else:
            for g in d['matches']:paragraphs+=game_paragraphs(g,background_seen)
            if d.get('league_race'):paragraphs+=league_paragraphs(d['league_race'])
            if d.get('promoted_via_playoff'):paragraphs.append(d['promoted_via_playoff']+'通过升级附加赛取得升级资格。')
        if not paragraphs:continue
        honours=d.get('champion_honours')
        if honours:
            from football_simulator.press.local_templates.v3.narrative import history_lines
            paragraphs.append(history_lines([{'kind':'title','team':honours['team'],'total_titles':honours['titles_in_this_competition'],
                'consecutive_titles':honours.get('consecutive_titles',1)}]))
            if honours.get('career_grand_slam_completed'):paragraphs.append(honours['team']+'至此已集齐一级联赛和六项杯赛的冠军。')
            if honours.get('season_grand_slam_completed'):paragraphs.append(honours['team']+'本季包揽了一级联赛和六项杯赛冠军。')
        sections.append(section(heading_for(story),paragraphs,story['fact_ids']))
        mapping.append({'story_id':story['id'],'template_id':'weekly.'+kind,'fact_ids':story['fact_ids'],
                        'source_keys':sorted({g['expectation_source_key'] for g in d.get('matches',[]) if g.get('expectation_source_key')} |
                            ({'story-context'} if any(g.get('player_arrivals') or g.get('player_events') or g.get('meeting_events') or g.get('defending_title_run') for g in d.get('matches',[])) else set()))})
    title=heading_for(stories[0])
    first=stories[0];content=first['content']
    if first['kind'] in ('league_closure','early_champion'):
        lead=''.join(league_paragraphs(content['league'],closure=first['kind']=='league_closure')[:1])
        if first['kind']=='early_champion':lead=content['champion']+'提前锁定'+content['league']['competition']+'冠军。'+lead
    elif first['kind']=='phase_closure':lead=''.join(phase_paragraphs(content)[:1])
    else:
        game=content['matches'][0];lead=match_line(game,venue=False)+outcomes(game)
        if content.get('league_race'):lead+=''.join(league_paragraphs(content['league_race'])[:1])
    deck=f"第{plan['editorial']['issue']['season']}赛季第{plan['editorial']['issue']['completed_week']}周，"+lead
    return article(packet,title,deck,sections),mapping
