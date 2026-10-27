"""Original weekly sports prose, rendered from build_plan's structured cards."""
from football_simulator.press.local_templates.v1.common import names,match_line,outcomes,section,article


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


def game_paragraphs(game):
    lines=[f"{game['competition']} · {game['stage']}，"+match_line(game)+outcomes(game)]
    action=exciting(game)
    if action:lines.append(action)
    players=[]
    for p in game.get('players',[]):
        production=[]
        for k,unit in [('goals','球'),('assists','次助攻'),('successful_saves','次扑救'),('clean_sheets','场零封')]:
            if p.get(k):production.append(f"{p[k]}{unit}")
        if production:players.append(f"{p['player']}为{p['team']}贡献"+'、'.join(production)+'。')
    for p in game.get('former_clubs',[]):
        players.append(f"{p['player']}本场代表{p['current_team']}面对曾效力的{p['former_team']}。")
    if players:lines.append(''.join(players))
    record=[]
    for r in game.get('streak_events',[]):
        kind={'wins':'连胜','losses':'连败','unbeaten':'不败','winless':'不胜'}[r['kind']]
        prefix='跨赛季的' if r.get('crosses_seasons') else ''
        if r['ended']:text=f"{r['team']}{prefix}{r['scope']}{r['before_match']}场{kind}在本场结束"
        else:text=f"本场之后，{r['team']}{prefix}{r['scope']}{kind}延续至{r['after_match']}场"
        if r.get('later_match_in_same_scope_this_week'):text+='（本场结束时）'
        record.append(text+'。')
    for team in game.get('season_first_losses',[]):record.append(team+'遭遇本赛季联赛首败。')
    # Milestones are already resolved from structured records; never parse the
    # historical_summary sentence back into numeric or identity fields.
    for r in game.get('club_records',[]):record.append(r['team']+'在'+game['competition']+r['milestone']+'。')
    if record:lines.append(''.join(record))
    if game.get('upset_basis') or game.get('race_meeting'):
        rows=game.get('upset_basis') or game['race_meeting']
        lines.append('赛前，'+'；'.join(f"{r['team']}在{r['league']}排第{r['rank']}，积{r['points']}分" for r in rows)+'。')
    comparison=game.get('expectation_comparison')
    if comparison and comparison['is_upset']:
        lines.append(comparison['winner']+'取得了一场冷门胜利。'+'；'.join(b['text'] for b in comparison['basis'])+'。')
    from football_simulator.press.local_templates.v1.background import paragraph
    if game.get('club_backgrounds'):
        lines += [paragraph(team,data) for team,data in game['club_backgrounds'].items() if paragraph(team,data)]
    return lines


def league_paragraphs(data, *, closure=False):
    leaders=data.get('leader_and_runner_up',[]);lines=[]
    if leaders:
        first=leaders[0];text=f"{data['competition']}，{first['team']}以{first['points']}分"+('夺冠' if closure else '位居榜首')
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
            if r['status']=='eliminated' and r['team'] in game.get('defending_champions',[]):return r['team']+'卫冕之旅结束'
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
    sections=[];mapping=[];stories=plan['editorial']['stories']
    for story in stories:
        d=story['content'];kind=story['kind'];paragraphs=[]
        if kind in ('league_closure','early_champion'):
            paragraphs=league_paragraphs(d['league'],closure=kind=='league_closure')
            if kind=='early_champion':paragraphs.insert(0,d['champion']+'已提前锁定本赛季'+d['league']['competition']+'冠军。')
            for g in d.get('decisive_matches',[]):paragraphs+=game_paragraphs(g)
        elif kind=='phase_closure':paragraphs=phase_paragraphs(d)
        else:
            for g in d['matches']:paragraphs+=game_paragraphs(g)
            if d.get('league_race'):paragraphs+=league_paragraphs(d['league_race'])
            if d.get('promoted_via_playoff'):paragraphs.append(d['promoted_via_playoff']+'通过升级附加赛取得升级资格。')
        if not paragraphs:continue
        sections.append(section(heading_for(story),paragraphs,story['fact_ids']))
        mapping.append({'story_id':story['id'],'template_id':'weekly.'+kind,'fact_ids':story['fact_ids'],
                        'source_keys':sorted({g['expectation_source_key'] for g in d.get('matches',[]) if g.get('expectation_source_key')})})
    title=heading_for(stories[0])
    deck=f"第{plan['editorial']['issue']['season']}赛季第{plan['editorial']['issue']['completed_week']}周，"+title+'。'
    return article(packet,title,deck,sections),mapping
