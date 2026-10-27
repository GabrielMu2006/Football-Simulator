"""Small expression primitives; all sporting decisions arrive as data."""


def names(values):
    return '、'.join(values)


def score(values):
    return f"{values['home']}–{values['away']}"


def match_line(game, *, venue=True):
    field=game.get('field_score_home_away',game.get('score_after_extra_time_home_away'))
    h,a=field['home'],field['away']
    if h==a:text=f"{game['home']}与{game['away']}战成{h}–{a}"
    elif h>a:text=f"{game['home']}以{h}–{a}击败{game['away']}"
    else:text=f"{game['home']}以{h}–{a}不敌{game['away']}"
    regulation=game.get('regulation_home_away')
    if regulation is not None and (game.get('extra_time_played') or 'extra_time_goals_home_away' in game):
        text+=f"（90分钟{score(regulation)}，加时后{score(field)}）"
    if game.get('shootout_home_away'):
        text+=f"；点球大战{score(game['shootout_home_away'])}，{game['shootout_winner']}点球胜出"
    text+='。'
    if venue and game.get('stadium'):
        text+=f"比赛在{game['stadium']}进行。"
    return text


def stats(card):
    position=card.get('position')
    fields=[('appearances','出场{}次')]
    fields+=([('successful_saves','完成{}次扑救'),('clean_sheets','零封{}场')] if position=='GK' else
             [('goals','打进{}球'),('assists','送出{}次助攻')])
    if position=='DF':fields.append(('successful_defenses','完成{}次成功防守'))
    if position=='MF':fields.append(('chances_created','创造{}次机会'))
    parts=[]
    for key,phrase in fields:
        if card.get(key) is None:continue
        if key=='goals' and card[key]==0:parts.append('未取得进球')
        elif key=='assists' and card[key]==0:parts.append('未送出助攻')
        else:parts.append(phrase.format(card[key]))
    return '，'.join(parts)


def section(heading, paragraphs, refs):
    return {'heading':heading,'heading_fact_ids':list(refs),'blocks':[
        {'kind':'body','label':None,'text':p,'fact_ids':list(refs)} for p in paragraphs if p]}


def article(packet,title,deck,sections):
    refs=sections[0]['heading_fact_ids']
    return {'schema_version':'press.article/0.1-draft','report_id':packet['meta']['report_id'],
            'title':title,'title_fact_ids':list(refs),'summary':deck,'summary_fact_ids':list(refs),
            'used_topic_ids':[],'sections':sections}


def outcomes(game):
    result=[]
    final_decided=any(r['status']=='champion' for r in game.get('qualification_outcomes',[]))
    swiss={r['team'] for r in game.get('swiss_qualification_records',[])}
    for row in game.get('qualification_outcomes',[]):
        team=row['team'];state=row['status']
        if team in swiss:continue
        if state=='champion':text=(team+'赢下升级附加赛，取得升级资格' if game['competition']=='Promotion Playoffs' else team+'赢得'+game['competition']+'冠军')
        elif state=='qualified':text=team+'晋级'+row.get('next_stage','下一阶段')
        elif state=='eliminated':
            text=(team+('未能通过附加赛升级' if game.get('competition')=='Promotion Playoffs' else '获得本届亚军') if final_decided else team+'本届出局')
        elif state=='entered_losers_bracket':text=team+'降入败者组，仍有后续赛程'
        else:continue
        if state=='eliminated' and team in game.get('defending_champions',[]):text='卫冕冠军'+text
        result.append(text+'。')
    for row in game.get('swiss_qualification_records',[]):
        after=row['after_match'];record=f"{after['wins']}胜{after['losses']}负"
        if row['qualified_in_this_match']:text=row['team']+'以'+record+'取得淘汰赛资格'
        elif row['eliminated_in_this_match']:text=row['team']+'以'+record+'结束瑞士轮，本届出局'
        else:text=row['team']+'的瑞士轮战绩为'+record
        result.append(text+'。')
    tie=game.get('two_leg_tie')
    if tie:
        a,b=tie['aggregate_a_b']
        text=f"两回合总比分为{tie['team_a']} {a}–{b} {tie['team_b']}"
        if tie['complete'] and tie.get('advancing_team'):text+=f"，{tie['advancing_team']}赢下对阵"
        elif not tie['complete']:text+='，对阵尚未结束'
        result.append(text+'。')
    return ''.join(result)
