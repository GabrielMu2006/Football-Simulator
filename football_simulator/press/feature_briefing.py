"""Small, reader-facing fact briefs for feature writers.

The complete packet stays local. Here values carry their subject, time range
and unit; the writer does not have to join parallel arrays or infer scope from
a partial match sample. This module never edits generated prose.
"""
from copy import deepcopy


def edition(data):
    value = data['edition']
    return (f"第{value['game_season']}赛季，{data['competition']}第{value['edition_number']}届；"
            f"该赛事首届在第{value['first_game_season']}赛季举办。")


def record(values):
    return f"{values.get('W', 0)}胜{values.get('D', 0)}平{values.get('L', 0)}负"


def statistics(values, position):
    fields = [('appearances', '次出场')]
    if position == 'GK':
        fields += [('successful_saves', '次扑救'), ('clean_sheets', '场零封')]
    else:
        fields += [('goals', '球'), ('assists', '次助攻'), ('chances_created', '次创造机会')]
        if position in ('DF', 'MF'):
            fields.append(('successful_defenses', '次成功防守'))
    return '、'.join(str(values[key]) + unit for key, unit in fields if key in values)


def match(data):
    h, a = data['home'], data['away']
    score = data['score_after_extra_time_home_away']
    regulation = data['regulation_home_away']
    result = f"{h} {score['home']}–{score['away']} {a}"
    if data.get('extra_time_played') or score != regulation:
        result += f"（90分钟{regulation['home']}–{regulation['away']}，加时后如上）"
    else:
        result += '（场上比分，未含点球大战）'
    if data.get('shootout_home_away'):
        pk = data['shootout_home_away']
        result += f"；点球{h} {pk['home']}–{pk['away']} {a}，{data['shootout_winner']}点球胜出"
    if data.get('result_meaning'):
        result += '；' + data['result_meaning']
    venue = data['venue']
    if data.get('stadium'):
        venue += '，' + data['stadium']
    row={'赛事':data['competition'],'阶段': data['stage'], '比赛结果': result, '场地': venue,
         '场地依据': data.get('venue_basis', '')}
    return row


def profile(card, scope):
    result = {'球员': card['player'], '位置': card['position'], '统计范围': scope,
              '期间累计': statistics(card, card['position']),
              '实际参赛球队产出': [team['team'] + '：' + statistics(team, card['position'])
                                 for team in card['actual_teams']]}
    highlights = []
    for row in card.get('representative_matches', []):
        game = match(row['match'])
        game['该球员实际球队'] = row['player_team']
        game['该球员本场数据'] = statistics({**row, 'appearances': 1}, card['position'])
        goals = [line for line in row['match'].get('goals', []) if card['player'] in line]
        if goals:
            game['该球员参与的进球'] = goals
        highlights.append(game)
    if highlights:
        result['代表比赛'] = highlights
    return result


def table(rows):
    return [f"第{r['rank']} {r['team']}：{r['played']}场，{r['wins']}胜{r['draws']}平{r['losses']}负，"
            f"{r['points']}分，进{r['goals_for']}失{r['goals_against']}，净胜球{r['goal_diff']:+}"
            for r in rows]


def tie(data):
    result = {'阶段': data['stage'], '对阵': ' vs '.join(data['teams']), '赛制': data['format']}
    if data.get('matches'):
        result['已赛'] = [match(row) for row in data['matches']]
    if data.get('aggregate'):
        first, second = data['aggregate']
        result['两回合总比分'] = f"{first['team']} {first['goals']}–{second['goals']} {second['team']}"
    if data.get('advancing_or_winning_team'):
        result['本轮晋级或优胜队'] = data['advancing_or_winning_team']
    if data.get('outcome_meaning'):
        result['结果意义'] = data['outcome_meaning']
    if data.get('decision_summary'):
        result['比分与晋级结论'] = data['decision_summary']
    if data.get('scheduled_legs'):
        result['已确定的后续比赛'] = [
            {'赛程': f"第{r['week']}周，第{r['leg']}回合：{r['home']}主场对{r['away']}",
             '主场分配依据': r.get('venue_basis', '')} for r in data['scheduled_legs']]
    return result


def awards(data):
    result = []
    for name, winners in data.get('awards', {}).items():
        for row in winners:
            unit = '球' if name == '射手王' else '次助攻' if name == '助攻王' else '赛事评分'
            value = f"赛事评分{row['value']}" if name in ('赛事MVP', '赛事EVP') else f"{row['value']}{unit}"
            # Counts/rating are whole-edition results, not production at the
            # club registered when the game settles the award.
            teams = row.get('actual_teams_in_edition', [])
            metric = 'goals' if name == '射手王' else 'assists' if name == '助攻王' else None
            production = [f"{t['team']}：{t.get(metric, 0)}{unit}" if metric else
                          f"{t['team']}：{t['appearances']}次出场" for t in teams]
            result.append({'正式奖项': f"{row['player']}获得{name}，整届结果为{value}。",
                           '领奖时登记球队': row['team_at_award'],
                           '本届实际参赛球队及产出': production})
    return result


def title_history(data):
    history = data.get('history')
    if not history:
        return []
    subject = history['team'] + '在' + history['competition']
    result = [subject + '的已知夺冠赛季：' + '、'.join(map(str, history['known_title_seasons'])) + '。']
    if history.get('milestone'):
        result.append(subject + '：' + history['milestone'] + '。')
    if history.get('consecutive_titles'):
        result.append(subject + f"已达{history['consecutive_titles']}连冠。")
    for key in ('career_grand_slam', 'season_grand_slam'):
        if history.get(key):
            result.append(history['team'] + '：' + history[key] + '。')
    return result


def competition(data):
    result = {'本届': edition(data), '赛制与场地规则': data['competition_format'],
              '整届已完成场数': data['completed_matches'], '最终优胜队': data['champion']}
    campaign=data['champion_campaign']
    result['冠军本届完整赛程统计']=(f"{campaign['matches']}场，场上{record(campaign)}，"
        f"进{campaign.get('goals_for',0)}失{campaign.get('goals_against',0)}；"
        f"另有点球胜出{campaign.get('shootout_wins',0)}次、点球落败{campaign.get('shootout_losses',0)}次。"
        '场上纪录按90分钟及加时比分，点球决胜另列。')
    if data.get('final_table'):
        result['最终积分榜'] = table(data['final_table'])
        result['冠军领先亚军'] = f"{data['winning_margin']}分"
        result['争冠分差来源']=data['winning_margin_basis']
        result['冠军整季38场纪录'] = record(data['champion_record'])
        result['冠军整季主客场纪录'] = {k: record(v) for k, v in data['champion_home_away_record'].items()}
        result['最终资格'] = deepcopy(data['qualification'])
        result['选取的3场比赛'] = {'范围': data['key_results_scope'],
                                  '比赛': [match(row) for row in data['key_results']]}
    else:
        bracket = data['bracket']
        # Retain both finalists' knockout paths, rather than resending every
        # tie plus a duplicate copy of the champion's matches.
        finals = [t for t in bracket if t['stage'] in ('决赛', '总决赛', '升级附加赛决赛')]
        finalists = set(finals[-1]['teams']) if finals else {data['champion']}
        paths=[t for t in bracket if finalists.intersection(t['teams'])]
        result['两位决赛队的逐轮对阵'] = [tie(t) for t in paths]
        included={(m['stage'],m['home'],m['away']) for t in paths for m in t.get('matches',[])}
        early=[row for row in data['champion_route'] if (row['stage'],row['home'],row['away']) not in included]
        if early:result['冠军此前的非淘汰阶段比赛']=[match(row) for row in early]
    result['正式个人奖项'] = awards(data)
    status = data.get('award_status') or {}
    if status.get('status') == 'not_offered':
        result['本届MVP与EVP'] = '本赛事本届不设MVP和EVP。'
    elif status.get('status') == 'awarded':
        mvp = '本届MVP空缺。' if status.get('mvp_status') == 'vacant' else '本届MVP已评出，得主以正式个人奖项为准。'
        result['本届MVP与EVP'] = mvp + f"本届实际评出{status['evp_count']}名EVP，名额为{status['evp_slots']}名。"
    elif status.get('status') == 'unknown':
        result['本届MVP与EVP'] = 'MVP获奖信息以正式个人奖项为准；该届EVP评选情况和名额未记录。'
    elif status:
        result['本届MVP与EVP'] = '本届奖项记录不完整，不推断未列得主。'
    result['本届冠军的历史坐标'] = title_history(data)
    if data.get('result_meaning'):
        result['赛事结果意义'] = data['result_meaning']
    return result


def phase(data):
    stage={'halfway':'联赛第19轮结束','swiss':'瑞士轮结束','playin':'突围赛结束',
           'groups':'组外循环结束' if data['competition']=='Ascendance Cup' else '小组赛结束'}[data['stage']]
    result = {'本届': edition(data), '当前阶段': stage, '赛制与场地规则': data['competition_format']}
    if data.get('halfway_table'):
        result['本次为19轮半程积分榜'] = table(data['halfway_table'])
        result['榜首对第二名的领先'] = f"{data['leading_gap']}分"
        result['当前分差来源']=data['leading_gap_basis']
        result['当前结果性质'] = data['halfway_table'][0]['team']+'已确定是半程榜首；'+data['phase_status']
    if data.get('groups'):
        result['本阶段分组结果'] = {group: table(rows) for group, rows in data['groups'].items()}
    for key, label in [('qualified', '已确定晋级'), ('eliminated', '已确定出局'),
                       ('direct_quarterfinalists', '直通八强'), ('playin_winners', '突围赛晋级')]:
        if key in data:
            result[label] = data[key]
    if data.get('swiss_records'):
        result['瑞士资格战绩及最终种子'] = [
            f"{r['team']}：{r['wins']}胜{r['losses']}负，" +
            (f"八强第{r['seed']}种子" if r.get('seed') else '已淘汰') for r in data['swiss_records']]
        result['资格战绩范围'] = data['record_scope']
    if data.get('bracket'):
        result['本阶段已赛与下一阶段对阵'] = [tie(row) for row in data['bracket']]
    if data.get('history_notes'):
        result['本项赛事历史节点'] = data['history_notes']
    scope=data['competition']+'本届截至'+stage+'累计'
    if data['stage']=='playin':scope+='，含组外循环和突围赛'
    result['球员'] = [profile(p,scope) for p in data['players']]
    representatives={(m['match']['competition'],m['match']['stage'],m['match']['home'],m['match']['away'])
                     for p in data['players'] for m in p.get('representative_matches',[])}
    result['球员代表比赛的选材范围']=f"{len(data['players'])}名球员，{len(representatives)}场不同的代表比赛；累计数据另列。"
    return result


def transfer(data, meta):
    roles=data.get('recorded_position_roles',{'GK':'门将','DF':'后卫','MF':'中场','FW':'前锋'})
    def counts(values):return '、'.join(f'{roles[p]}{n}人' for p,n in values.items())
    result = {'窗口已关闭': data['window_summary'], '交易性质': data['transaction_type'],
              '游戏记录的位置':roles,'未记录的人物与交易信息':data.get('unrecorded_attributes',[]),'焦点交易': []}
    for row in data['focus_trades']:
        group = {'发生周': row['week'], '双方送出球员包的身价估值': row['estimated_package_values'], '球员流向': []}
        for p in row['moves']:
            card = profile(p, f"截至第{meta['season']}赛季第{meta['completed_week']}周，各项正式赛事累计")
            card['位置']=roles[p['position']]
            card['此次交换'] = f"{p['player']}从{p['from_team']}转入{p['to_team']}。"
            card['履历'] = p['origin']
            card['历届Top20入选者'] = p['historical_top20']
            card['本季突出表现者'] = p['current_season_standout']
            group['球员流向'].append(card)
        if row.get('position_exchange'):
            group['本笔交换的位置构成']=[f"{r['team']}送出{counts(r['outgoing'])}，收到{counts(r['incoming'])}。" for r in row['position_exchange']]
        result['焦点交易'].append(group)
    focus_teams={p[k] for r in data['focus_trades'] for p in r['moves'] for k in ('from_team','to_team')}
    if data.get('window_team_rosters'):
        result['焦点球队整个窗口的阵容变化']={'统计对象':'真实球员位置人数，未占满的阵容槽位由默认球员补位；为整个窗口首尾，不是单笔交易时点。',
            '球队':[f"{r['team']}：开窗{counts(r['window_open_real_counts'])}；关窗{counts(r['window_closed_real_counts'])}。"
                    for r in data['window_team_rosters'] if r['team'] in focus_teams]}
    return result


def draft_newcomers(data):
    return {'选秀时间': data['timing'],
            '本届选中结果': [{k: p[k] for k in ('selection_result', 'team_season_finish', 'position', 'registered_ability_at_pick')}
                            for p in data['picks']], '未参与或未选中者': data['unselected']}


def draft_development(data, meta):
    result = []
    for row in data['previous_two_cohorts']:
        result.append({'球员及选秀经历': f"{row['player']}，第{row['draft_season']}赛季实际第{row['actual_pick']}顺位，"
                                        f"由{row['draft_team']}选中。",
                       '截至报道时登记球队': row['current_registered_team'],
                       '选中时能力值': row['ability_at_selection'],
                       '截至报道时能力值': row['ability_at_report'],
                       '逐季实际表现': [profile(card, f"第{card['season']}赛季各项正式赛事累计") for card in row['by_season']]})
    return {'报道时点': f"第{meta['season']}赛季第{meta['completed_week']}周", '前两届实际前五顺位的发展': result}


def selection_history(card):
    history=card.get('selection_history')
    if not history:return {'状态':card['history_status']}
    total=history['total_selections'];run=history['consecutive_selections']
    return {'状态':history['status'],
            '本届名次里程碑':history.get('rank_milestones',[]),
            '累计入选':f"{total}次" if total is not None else f"已知{history['known_selections']}次，缺少历史届次，完整次数未确定",
            '截至本届连续入选':f"{run}届" if run is not None else f"至少{history['known_consecutive_selections']}届，完整连续次数未确定",
            '历届名次':[f"第{r['season']}赛季第{r['rank']}名" if r['rank'] is not None else f"第{r['season']}赛季未入选"
                        for r in history['editions']],
            '缺少记录的赛季':history['missing_seasons']}


def top20_player(card):
    result = profile(card, f"第{card['season']}赛季各项正式赛事累计")
    result['游戏正式最终名次'] = f"{card['player']}：赛季Top20第{card['rank']}名。"
    result['入选经历'] = selection_history(card)
    result['上季名次'] = card['previous_rank']
    result['选秀或初始球员经历'] = card['origin']
    player=card['player']
    unknown_roster=any(r.get('player_championship_credit',{}).get('awarded_to_player','known') is None
                       for team in card['teams_season_results'] for r in team['season_results'])
    result['本季球员已获荣誉']={
        '个人奖项':[f"{player}获得{a['competition']}{a['award']}。" for a in card['personal_awards']] or ['无'],
        '实际随队冠军':[f"{player}随{a['team_at_championship']}获得{a['competition']}冠军。" for a in card['team_titles']]
                      or (['夺冠阵容记录不完整，尚无法确认'] if unknown_roster else ['无'])}
    if card.get('award_policy') == '0.3.1':
        personal = card.get('personal_awards', [])
        result['本季赛事MVP与EVP次数'] = {
            'MVP': sum(a['award'] == '赛事MVP' for a in personal),
            'EVP': sum(a['award'] == '赛事EVP' for a in personal),
        }
    contexts=[]
    for team in card['teams_season_results']:
        facts=[]
        for r in team['season_results']:
            fact=f"{team['team']}在{r['competition']}最终获得{r['finish']}。"
            credit=r.get('player_championship_credit')
            if credit is not None:
                when=f"第{credit['decided_week']}周" if credit.get('decided_week') is not None else '该届决出冠军时'
                awarded=credit['awarded_to_player']
                fact+= (f"{when}的夺冠阵容包含{player}，{player}获得此项随队冠军。" if awarded is True else
                        f"{when}的夺冠阵容不含{player}，{player}未获得此项随队冠军。" if awarded is False else
                        f"夺冠阵容记录不足，无法确认{player}是否获得此项随队冠军。")
            facts.append(fact)
        context={'球队':team['team'],'球队最终成绩':facts}
        if team.get('first_appearance_week') is not None:
            context['该球员实际出场范围']=f"第{team['first_appearance_week']}至第{team['last_appearance_week']}周"
        if team.get('season_transfers'):
            context['本季实际转会']=[f"第{m['week']}周，{player}从{m['from_team']}转入{m['to_team']}。" for m in team['season_transfers']]
        contexts.append(context)
    result['曾效力球队的最终成绩（球队背景）']=contexts
    return result


def top20_overview(data):
    changes=data['changes']
    return {'游戏正式最终榜单':[{'名次':p['rank'],'球员':p['player'],'入选经历':selection_history(p)} for p in data['rankings']],
            '是否首届':changes['first_edition'],'首次入选':changes['newcomers'],'回归榜单':changes['returning'],
            '上季入选本季未入选':[{'球员':p['player'],'上季正式名次':p['previous_rank'],
                '上季表现':profile(p['last_season'],f"第{p['last_season']['season']}赛季各项正式赛事累计"),
                '本季表现':profile(p['this_season'],f"第{p['this_season']['season']}赛季各项正式赛事累计")}
                for p in changes['dropped_out']]}
