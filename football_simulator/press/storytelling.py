"""Small editorial cards and per-competition request previews.

This is an explicit experimental path, not an extra automatic game/API call.
Numbers and formal names in the cards come from frozen facts, not another LLM.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from football_simulator.game import rules
from football_simulator.press.briefing import writing_view
from football_simulator.press.validation import validate_packet
from football_simulator.press import PressError


def _rank_topics(packet, competition):
    index = {f['fact_id']: f for f in packet['facts']}
    ranked = []
    for topic in packet['editorial']['topics']:
        facts = [index[fid] for fid in topic['required_fact_ids']]
        if not any(f['competition_id'] == competition for f in facts):
            continue
        weight = 50 if topic['priority'] == 'primary' else 10
        for f in facts:
            d = f['data']
            if f['kind'] == 'title_clinched' or topic['event_type'] == 'defender_exit':
                weight = max(weight, 100)
            elif f['kind'] == 'competition_status':
                weight = max(weight, {'champion': 100, 'qualified': 85, 'eliminated': 80,
                                     'entered_losers_bracket': 75}.get(d['status'], 20))
            elif f['kind'] == 'player_match':
                weight = max(weight, 90 if d['goals'] >= 4 else 80 if d['goals'] >= 3 else 55)
            elif f['kind'] == 'table_row' and d.get('lead_context'):
                weight = max(weight, 85 if d['lead_context']['leader_changed'] else 70)
            elif f['kind'] == 'streak':
                weight = max(weight, 75 if d['ended'] and d['before'] >= 6 else 45)
        ranked.append((weight, topic))
    return [t for _, t in sorted(ranked, key=lambda x: -x[0])]


def cards(packet, competition, *, max_games=3):
    """A reading budget, not a prose validity gate. Full evidence stays frozen."""
    index = {f['fact_id']: f for f in packet['facts']}
    topics, games = [], set()
    for topic in _rank_topics(packet, competition):
        if len(topics) >= 6:
            break
        mids = {mid for fid in topic['required_fact_ids'] for mid in index[fid]['match_ids']}
        if len(games | mids) > max_games and not mids <= games:
            continue
        topic = deepcopy(topic)
        # Older frozen packets include this provisional hint even for a winner.
        # Correct the writing hint, preserving the original packet and facts.
        if any(index[fid]['kind'] == 'record' and index[fid]['data'].get('current_achievement') == '冠军'
               for fid in topic['required_fact_ids']):
            topic['angle'] = '回顾夺冠历程；如有上届冠军事实，说明卫冕。'
        topics.append(topic); games.update(mids)
    refs = {fid for topic in topics for fid in topic['required_fact_ids'] + topic['context_fact_ids']}
    teams = {tid for f in packet['facts'] if f['kind'] == 'match_result' and f['data']['match_id'] in games for tid in f['team_ids']}
    chosen = set(refs)
    for f in packet['facts']:
        if f['competition_id'] != competition:
            continue
        if f['kind'] == 'competition_status' or games.intersection(f['match_ids']) or (f['kind'] in ('table_row', 'historical_title') and teams.intersection(f['team_ids'])):
            chosen.add(f['fact_id'])
    view = writing_view(packet)
    by_id = {f['fact_id']: f for f in view['facts']}
    match_fids = {f['data']['match_id']: f['fact_id'] for f in view['facts'] if f['kind'] == 'match_result'}
    # Keep all context used to derive a statement, including a later cup match.
    for fid in list(chosen):
        chosen.update(by_id[fid].get('reading_notes', {}).get('supporting_fact_ids', []))
        if by_id[fid]['kind'] == 'tie_result':
            chosen.update(match_fids[mid] for mid in by_id[fid]['data']['leg_match_ids'])
    lines = [{'fact_id': f['fact_id'], 'text': fact_line(f, packet)} for f in view['facts'] if f['fact_id'] in chosen]
    outcomes = {}
    for f in packet['facts']:
        if f['kind'] == 'competition_status' and f['competition_id'] == competition:
            outcomes.setdefault(f['data']['status'], []).append(packet['entities']['teams'][f['data']['team_id']]['name_en'])
    stage_context = ''
    swiss = [f for f in packet['facts'] if f['kind'] == 'swiss_record' and f['competition_id'] == competition]
    rounds = [int(f['data']['stage'].split('_')[-1]) for f in packet['facts']
              if f['kind'] == 'match_result' and f['competition_id'] == competition
              and f['time'] == packet['meta']['cutoff'] and f['data']['stage'].startswith('swiss_')]
    if rounds:
        complete = bool(swiss) and all(f['data']['wins'] == 3 or f['data']['losses'] == 3 for f in swiss)
        stage_context = f"瑞士轮第{max(rounds)}轮已结束；" + ('整个瑞士阶段已结束。' if complete else '整个瑞士阶段尚未结束，仍有球队待争夺资格。')
    return {'competition_id': competition, 'competition_name': packet['entities']['competitions'][competition]['name_en'],
            'stage_context': stage_context,
            'topics': topics, 'verified_statements': lines, 'all_week_outcomes': outcomes}


def fact_line(fact, packet):
    entities = packet['entities']; d = fact['data']; kind = fact['kind']
    team = lambda tid: entities['teams'].get(tid, {}).get('name_en', tid)
    player = lambda pid: entities['players'].get(pid, {}).get('name', pid)
    comp = entities['competitions'].get(fact['competition_id'], {}).get('name_en', fact['competition_id']) or '跨赛事背景'
    if kind == 'match_result':
        notes = fact['reading_notes']; score = notes['field_score_excluding_shootout']
        when = fact.get('time', packet['meta']['cutoff'])
        stage = rules.stage_label(d['stage'])
        winner = d['home_team_id'] if score['home'] > score['away'] else d['away_team_id'] if score['away'] > score['home'] else None
        text = f"第{when['season']}赛季第{when['week']}周，{comp} {stage}；"
        if winner:
            loser = d['away_team_id'] if winner == d['home_team_id'] else d['home_team_id']
            text += f"{team(winner)}以{max(score.values())}–{min(score.values())}战胜{team(loser)}"
        else:
            text += f"{team(d['home_team_id'])}与{team(d['away_team_id'])}在场上战成{score['home']}–{score['away']}"
        text += f"；主队={team(d['home_team_id'])}，客队={team(d['away_team_id'])}"
        if d['extra_time']:
            text += '（含加时）'
        if d['shootout']:
            text += f"；另有点球大战 {d['shootout']['home']}–{d['shootout']['away']}，不计入场上比分或球员进球"
        if winner:
            if notes['goal_timeline_complete']:
                spells = notes['field_winner_trailing_spells']
                text += f'；获胜队曾有{spells}段连续落后局面，后来反超' if spells else '；获胜队从未落后，不可写逆转'
        stadium = entities['stadiums'].get(d.get('stadium_id'))
        if stadium:
            text += f"；球场 {stadium['name_zh']}（{stadium['name_en']}）"
        if notes['goal_timeline_complete']:
            for lo, hi, label in ((1, 45, '上半场'), (46, 90, '下半场'), (91, 120, '加时')):
                goals = [e for e in d['goal_events'] if lo <= e['minute'] <= hi]
                text += f"；{label}进球：" + '、'.join(f"{team(tid)} {sum(e['team_id'] == tid for e in goals)}球" for tid in (d['home_team_id'], d['away_team_id']))
        return text
    if kind == 'player_match':
        label = {2: '梅开二度', 3: '帽子戏法', 4: '单场四球'}.get(d['goals'], '')
        text = (f"{comp} 的比赛 {d['match_id']}：{player(d['player_id'])} 代表 {team(d['team_id'])}；"
                f"本场进球 {d['goals']}" + (f'（{label}）' if label else '') +
                f"，助攻 {d['assists']}，扑救 {d['successful_saves']}，成功防守 {d['successful_defenses']}")
        if 'chances_created' in d:
            text += f"，创造机会 {d['chances_created']}"
        if 'clean_sheets' in d:
            text += f"，零封 {d['clean_sheets']}"
        return text
    if kind == 'table_row':
        if d['timing'] != 'end_of_week':
            return f"{comp}，{team(d['team_id'])} 单场赛前第{d['rank']}，{d['points']}分；不是周初排名"
        before = d.get('week_comparison', {})
        text = f"{comp}，{team(d['team_id'])}：周末第{d['rank']}、{d['points']}分"
        if before.get('before_rank') is not None:
            text = f"{comp}，{team(d['team_id'])}：周初第{before['before_rank']}、{before['before_points']}分 → 周末第{d['rank']}、{d['points']}分"
        notes = fact.get('reading_notes') or {}
        for key, label in (('before_week', '周初榜首/次席'), ('after_week', '周末榜首/次席')):
            entry = notes.get(key)
            if entry:
                text += f"；{label}：{team(entry['leader_team_id'])} {entry['leader_points']}分 / {team(entry['runner_up_team_id'])} {entry['runner_up_points']}分，整体领先{entry['gap_to_runner_up']}分"
        direct = notes.get('current_leader_against_current_runner_up')
        if direct:
            text += f"；单独对 {team(direct['opponent_team_id'])} 的积分差：周初{direct['gap_before_week']} → 周末{direct['gap_after_week']}（正数为领先，负数为落后）"
        return text
    if kind == 'streak':
        scope = {'all_official': '所有正式比赛', 'league_all_levels': '联赛', 'competition': comp}[d['scope']]
        label = {'wins': '连胜', 'losses': '连败', 'unbeaten': '连续不败', 'winless': '连续不胜'}[d['kind']]
        end = fact.get('reading_notes', {}).get('current_count_at_week_end', d['after'])
        return f"{team(d['team_id'])}，范围={scope}：本场前{label}{d['before']}场，本场后{d['after']}场；旧纪录本场是否终结={d['ended']}；截至本周该范围最后一场，当前{label}计数={end}"
    if kind == 'competition_status':
        status = {'qualified': '已晋级下一阶段', 'eliminated': '已淘汰', 'champion': '已夺冠',
                  'entered_losers_bracket': '本次失利后转入败者组，仍在赛事中',
                  'awaiting_tie_second_leg': '两回合尚未结束', 'in_competition': '仍在赛事中'}[d['status']]
        if d['status'] == 'qualified' and d['next_stage'] in ('grand_final', 'final'):
            status = '已锁定' + rules.stage_label(d['next_stage']) + '席位，接下来争夺冠军，不是继续争夺决赛资格'
        elif d['status'] == 'eliminated':
            original = next(f for f in packet['facts'] if f['fact_id'] == fact['fact_id'])
            matches = [f for f in packet['facts'] if f['kind'] == 'match_result' and f['data']['match_id'] in original['match_ids']]
            if any(f['data']['stage'] in ('final', 'grand_final', 'final_leg_2') for f in matches):
                status = '决赛落败，获得亚军'
        return f"{comp}：{team(d['team_id'])} {status}；下一阶段={rules.stage_label(d['next_stage']) if d['next_stage'] else '无'}"
    if kind == 'swiss_record':
        return f"{comp}：{team(d['team_id'])} 瑞士轮资格战绩为{d['wins']}胜{d['losses']}负；这是对阵资格战绩，不足以证明相同场次的场上全胜"
    if kind == 'historical_title':
        return f"{team(d['team_id'])}在第{d['season']}赛季夺得{comp}冠军，是本届该赛事的卫冕冠军。"
    if kind == 'record':
        change = {'first': '首次达到', 'broken': '刷新队史最佳至', 'equalled': '追平队史最佳'}[d['comparison']]
        text = f"{team(d['entity_id'])}在{comp}{change}{d['current_achievement']}；此前最佳={d['previous_best'] or '无该赛事参赛成绩'}"
        return text + ('；本届最终成绩为冠军。' if d['current_achievement'] == '冠军' else '；本届仍可能继续晋级。')
    if kind == 'tie_result':
        text = f"{comp}：{team(d['team_a_id'])} 对 {team(d['team_b_id'])} 两回合累计{d['team_a_goals']}–{d['team_b_goals']}；是否两回合结束={d['complete']}；晋级球队={team(d['advancing_team_id']) if d['advancing_team_id'] else '尚未产生'}；无客场进球规则"
        notes = fact.get('reading_notes', {})
        for i, leg in enumerate(notes.get('leg_field_scores', []), 1):
            text += f"；第{i}回合 {team(leg['home_team_id'])} {leg['home']}–{leg['away']} {team(leg['away_team_id'])}"
        behind = notes.get('advancing_team_trailing_on_aggregate_during_second_leg')
        if behind is not None:
            text += f"；晋级球队在次回合总比分{'曾落后后追上' if behind else '从未落后，不能称总比分逆转'}"
        return text
    if kind == 'former_club':
        when = d['transfer_effective']
        return (f"{player(d['player_id'])}本场代表{team(d['current_team_id'])}面对老东家{team(d['former_team_id'])}；"
                f"曾为老东家正式出场={d['appeared_for_former']}；是否紧接上一家俱乐部={d['is_immediate_previous_club']}；"
                f"相关转会在第{when['season']}赛季第{when['week']}周生效")
    return comp + '：' + json.dumps(d, ensure_ascii=False)


def section_preview(packet, sources, competition):
    validate_packet(packet, sources)
    brief = cards(packet, competition)
    system = (Path(__file__).resolve().parents[2] / 'docs/press/prompts/weekly-section.md').read_text(encoding='utf-8').split('\n---\n', 1)[1].strip()
    ids = {f['fact_id'] for f in brief['verified_statements']}
    selected = [f for f in packet['facts'] if f['fact_id'] in ids]
    related = {'teams': {tid for f in selected for tid in f['team_ids']},
               'players': {pid for f in selected for pid in f['player_ids']},
               'stadiums': {f['data']['stadium_id'] for f in selected if f['kind'] == 'match_result'},
               'competitions': {competition, *[f['competition_id'] for f in selected]}}
    entities = {kind: {eid: value for eid, value in packet['entities'][kind].items() if eid in related[kind]}
                for kind in related}
    user = {'meta': packet['meta'], 'entities': entities, 'story_cards': brief}
    return {'prompt_versions': ['weekly-section-draft-6'], 'facts': len(brief['verified_statements']), 'request': {
        'model': 'deepseek-flash', 'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'},
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(user, ensure_ascii=False, separators=(',', ':'))}]}}


def prepare_requested_fragment(raw, packet, competition, request):
    """Check references against what this section actually received."""
    from copy import deepcopy
    payload = json.loads(request['messages'][1]['content'])['story_cards']
    allowed = {item['fact_id'] for item in payload['verified_statements']}
    subset = deepcopy(packet)
    subset['facts'] = [f for f in packet['facts'] if f['fact_id'] in allowed]
    subset['editorial']['topics'] = payload['topics']
    return prepare_fragment(raw, subset, competition)


def phase_heading(packet, competition):
    """A completed round is not necessarily the end of a tournament phase."""
    comp = packet['entities']['competitions'][competition]['name_en']
    stages = list(dict.fromkeys(f['data']['stage'] for f in packet['facts'] if f['kind'] == 'match_result'
                               and f['competition_id'] == competition and f['time'] == packet['meta']['cutoff']))
    def name(stage):
        if stage.startswith('swiss_'):
            return '瑞士轮第' + stage.rsplit('_', 1)[1] + '轮'
        if stage.endswith(('_leg_1', '_leg_2')):
            base, _, leg = stage.rsplit('_', 2)
            return rules.stage_label(base) + ('首回合' if leg == '1' else '次回合')
        return rules.stage_label(stage)
    return comp if not stages or stages == ['league'] else comp + ' · ' + ' / '.join(name(s) for s in stages)


def prepare_fragment(raw, packet, competition):
    """Use paragraphs, not disposable model-generated headers or topic bookkeeping.

    Unknown body references and wrong report identity still fail the ordinary
    structure check. This neither edits prose nor attests its semantic accuracy.
    """
    from copy import deepcopy
    from football_simulator.press.validation import validate_article_basics
    if raw.get('schema_version') != 'press.article/0.1-draft' or raw.get('report_id') != packet['meta']['report_id']:
        raise PressError('文章报道ID或结构版本不符')
    blocks = [deepcopy(block) for section in raw.get('sections', []) for block in section.get('blocks', [])]
    refs = list(dict.fromkeys(fid for block in blocks for fid in block['fact_ids']))
    topics = [t['topic_id'] for t in packet['editorial']['topics']
              if t['required_fact_ids'] and set(t['required_fact_ids']) <= set(refs)]
    heading = phase_heading(packet, competition)
    result = {'schema_version': raw['schema_version'], 'report_id': raw['report_id'],
              'title': heading, 'title_fact_ids': refs, 'summary': heading, 'summary_fact_ids': refs,
              'used_topic_ids': topics, 'sections': [{'heading': heading, 'heading_fact_ids': refs, 'blocks': blocks}]}
    validate_article_basics(result, packet)
    return result


def compose_sections(packet, fragments):
    """Ground volatile titles in cited facts; preserve generated body paragraphs.

    This avoids another model pass introducing new facts while summarizing.
    Raw fragment titles remain in their own immutable call receipts.
    """
    from copy import deepcopy
    refs = {fid for _, article in fragments for fid in article['title_fact_ids'] + article['summary_fact_ids']}
    refs.update(fid for _, article in fragments for section in article['sections'] for fid in section['heading_fact_ids'])
    refs.update(fid for _, article in fragments for section in article['sections'] for b in section['blocks'] for fid in b['fact_ids'])
    cited_ties = [f for f in packet['facts'] if f['fact_id'] in refs and f['kind'] == 'tie_result' and f['data']['complete']]
    for tie in cited_ties:
        refs.update(f['fact_id'] for f in packet['facts'] if f['kind'] == 'competition_status'
                    and f['competition_id'] == tie['competition_id'] and f['data']['team_id'] == tie['data']['advancing_team_id']
                    and f['data']['status'] == 'qualified' and set(f['match_ids']).intersection(tie['match_ids']))
    facts = [f for f in packet['facts'] if f['fact_id'] in refs]
    teams = packet['entities']['teams']; players = packet['entities']['players']
    comp_name = lambda cid: packet['entities']['competitions'][cid]['name_en']
    candidates = []
    for fact in facts:
        d = fact['data']; cid = fact['competition_id']
        if fact['kind'] == 'title_clinched':
            candidates.append((130, f"{teams[d['team_id']]['name_en']}提前夺得{comp_name(cid)}冠军", [fact['fact_id']], cid))
        elif fact['kind'] == 'competition_status' and d['status'] in ('champion', 'qualified'):
            team = teams[d['team_id']]['name_en']
            if d['status'] == 'champion':
                candidates.append((120, f'{team}夺得{comp_name(cid)}', [fact['fact_id']], cid))
                continue
            stage = d['next_stage']
            if not stage:
                continue
            score = {'grand_final': 105, 'final': 103, 'wb_final': 100, 'semifinal': 98, 'quarterfinal': 95}.get(stage, 80)
            text = f'{team}晋级{comp_name(cid)}{rules.stage_label(stage)}'
            ids = [fact['fact_id']]
            performers = [f for f in facts if f['kind'] == 'player_match' and f['data']['team_id'] == d['team_id']
                          and f['data']['match_id'] in fact['match_ids'] and f['data']['goals'] >= 3]
            if performers:
                best = max(performers, key=lambda f: f['data']['goals'])
                text += f"，{players[best['data']['player_id']]['name']}单场{best['data']['goals']}球"
                ids.append(best['fact_id']); score += min(best['data']['goals'], 4)
            candidates.append((score, text, ids, cid))
        elif fact['kind'] == 'table_row' and d.get('lead_context'):
            lead = d['lead_context']; team = teams[d['team_id']]['name_en']
            text = f'{team}领跑{comp_name(cid)}'
            if lead['leader_changed']:
                text = f'{team}登上{comp_name(cid)}榜首'
            candidates.append((85 if lead['leader_changed'] else 60, text, [fact['fact_id']], cid))
    if not candidates:
        competition, article = fragments[0]
        chosen = (0, f"第{packet['meta']['season']}赛季 · 第{packet['meta']['completed_week']}周赛场观察", article['title_fact_ids'], competition)
    else:
        chosen = max(candidates, key=lambda item: item[0])
    ordered = sorted(fragments, key=lambda item: item[0] != chosen[3])
    facts_by_id = {f['fact_id']: f for f in facts}
    summaries, summary_refs, covered = [], [], set()
    for candidate in sorted(candidates, key=lambda item: -item[0]):
        if candidate[3] in covered:
            continue
        covered.add(candidate[3]); fact = facts_by_id[candidate[2][0]]; d = fact['data']
        if fact['kind'] == 'table_row':
            lead = d['lead_context']; team = teams[d['team_id']]['name_en']
            sentence = f"{team}以{d['points']}分领跑{comp_name(candidate[3])}"
            if lead['after_gap'] == 0:
                sentence += '，与第二名同分'
            elif lead['leader_changed'] is False and lead['same_leader_gap_change'] == 0:
                sentence += f"，整体领先优势仍为{lead['after_gap']}分"
            elif lead['leader_changed'] is False and lead['before_gap'] is not None:
                sentence += f"，整体领先优势由{lead['before_gap']}分变为{lead['after_gap']}分"
            elif lead['after_gap'] is not None:
                sentence += f"，领先第二名{lead['after_gap']}分"
        else:
            sentence = candidate[1]
        summaries.append(sentence + '。'); summary_refs.extend(candidate[2])
        if len(covered) >= 3:
            break
    if not summaries:
        summaries = [ordered[0][1]['summary']]
        summary_refs = ordered[0][1]['summary_fact_ids']
    summary = ' '.join(summaries)
    sections, topic_ids = [], []
    for competition, article in ordered:
        blocks = [deepcopy(block) for section in article['sections'] for block in section['blocks']]
        stage_refs = [f['fact_id'] for f in facts if f['kind'] == 'match_result' and f['competition_id'] == competition and f['time'] == packet['meta']['cutoff']]
        if not stage_refs:
            stage_refs = article['sections'][0]['heading_fact_ids']
        sections.append({'heading': phase_heading(packet, competition), 'heading_fact_ids': stage_refs, 'blocks': blocks})
        topic_ids.extend(article['used_topic_ids'])
    return {'schema_version': 'press.article/0.1-draft', 'report_id': packet['meta']['report_id'],
            'title': chosen[1], 'title_fact_ids': chosen[2], 'summary': summary, 'summary_fact_ids': list(dict.fromkeys(summary_refs)),
            'used_topic_ids': list(dict.fromkeys(topic_ids)), 'sections': sections}
