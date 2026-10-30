"""A week-wide editorial plan and a small, versioned author contract.

All complete facts and evidence remain local. The writer receives named story
objects, not a flat collection of facts plus duplicated editorial instructions.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from football_simulator.press import PressError
from football_simulator.press.context import Context
from football_simulator.press.authoring import author_content
from football_simulator.press.repository import digest
from football_simulator.press.validation import validate_article_basics, validate_packet

VERSION = 'weekly-stories/1'
PROMPT = 'weekly-issue-draft-21'


def team_name_aliases(packet):
    names = {t['name_en'] for t in packet['entities']['teams'].values()}
    prefixes = {}
    for name in names:
        first = name.split()[0]
        if len(first) >= 6: prefixes.setdefault(first, set()).add(name)
    aliases = {prefix: next(iter(full)) for prefix, full in prefixes.items() if len(full) == 1}
    # Protect complete names from prefix expansion inside the canonical name.
    return {**aliases, **{name: name for name in names}}


def name_aliases(packet):
    """Freeze only known display aliases, never guesses from article prose."""
    from football_simulator import runtime
    config = json.loads(runtime.bundled_shared_config_path().read_text(encoding='utf-8'))
    english = {t['name_en'] for t in packet['entities']['teams'].values()}
    aliases = {zh: en for en, zh in config.get('team_chinese_names', {}).items() if en in english and len(zh) >= 3}
    from football_simulator.game import rules
    for cid, entity in packet['entities']['competitions'].items():
        if entity['name_en'] == rules.display_name(cid): aliases[rules.display_name(cid, 'zh')] = entity['name_en']
    for stadium in packet['entities']['stadiums'].values():
        en, zh = stadium['name_en'], stadium['name_zh']; both = zh + '（' + en + '）'
        for key in (en, zh, both, zh + '(' + en + ')'):
            aliases[key] = both
    by_surname = {}
    for player in packet['entities']['players'].values():
        full = player['name']; words = full.split()
        # Match compound surnames as a whole: 'Mac Allister' must not become
        # 'Mac Alexis Mac Allister' when expanding 'Allister'.
        for start in range(1, len(words)):
            by_surname.setdefault(' '.join(words[start:]), set()).add(full)
        aliases[full] = full
    for surname, full in by_surname.items():
        if len(full) == 1 and len(surname) >= 4: aliases[surname] = next(iter(full))
    return aliases


def phase_context(ctx, cid):
    games = [f for f in ctx.weekly.values() if f['competition_id'] == cid]
    stages = list(dict.fromkeys(f['data']['stage'] for f in games))
    from football_simulator.game import rules
    result = {'competition': ctx.competition(cid), 'stages_this_week': [rules.stage_label(s) for s in stages],
              'matches_this_week': len(games)}
    state = ctx.state(cid)
    # Publish already-drawn upcoming ties even between ordinary knockout
    # rounds; a missing major-stage summary does not mean no draw exists.
    future = []
    for key in ('quarterfinals', 'semifinals', 'final'):
        ties = state.get(key) or []
        if isinstance(ties, dict): ties = [ties]
        for tie in ties:
            if not tie.get('winner') and len(tie.get('teams', [])) == 2:
                future.append({'stage': rules.stage_label(tie['stage']), 'teams': [ctx.team(t) for t in tie['teams']]})
    if future: result['upcoming_drawn_ties'] = future
    if any(stage.startswith('swiss_') for stage in stages):
        records = state.get('records') or {f['data']['team_id']: [f['data']['wins'], f['data']['losses']]
                                         for f in ctx.facts.values() if f['kind'] == 'swiss_record' and f['competition_id'] == cid}
        result['swiss_round_completed'] = max(int(s.rsplit('_', 1)[1]) for s in stages if s.startswith('swiss_'))
        result['whole_swiss_phase_finished'] = len(records) == 16 and all(w == 3 or l == 3 for w, l in records.values())
        result['qualification_summary'] = {label: [ctx.team(t) for t, (w, l) in records.items() if condition(w, l)]
            for label, condition in (('qualified', lambda w, l: w == 3), ('eliminated', lambda w, l: l == 3), ('still_competing', lambda w, l: w < 3 and l < 3))}
        active_this_week = {tid for f in games for tid in f['team_ids']}
        result['qualified_before_this_week'] = [ctx.team(t) for t, (w, l) in records.items() if w == 3 and t not in active_this_week]
        result['newly_qualified_this_week'] = [ctx.team(t) for t, (w, l) in records.items() if w == 3 and t in active_this_week]
        result['knockout_seeding_final'] = result['whole_swiss_phase_finished']
        if result['whole_swiss_phase_finished']:
            result['knockout_seeds'] = [{'team': ctx.team(t), 'seed': seed} for t, seed in sorted(state.get('knockout_seeds', {}).items(), key=lambda x: x[1])]
    if any(s.startswith(('group_', 'cross_group_')) for s in stages):
        result['whole_group_phase_finished'] = bool(state.get('stage_done', {}).get('groups'))
        if result['whole_group_phase_finished']:
            if cid == 'aureole':
                result['group_qualifiers'] = {group: [ctx.team(r['team_id']) for r in rows[:2]] for group, rows in state.get('group_standings', {}).items()}
            elif cid == 'ascendant':
                result['direct_quarterfinalists'] = [ctx.team(t) for t in state.get('direct', [])]
                result['play_in_pairs'] = [[ctx.team(t) for t in tie['teams']] for tie in state.get('playin', [])]
                if state.get('nirvana_sixth'): result['eliminated'] = [ctx.team(state['nirvana_sixth'])]
    if cid == 'ascendant' and 'play_in' in stages:
        ties = state.get('playin', [])
        result['whole_play_in_phase_finished'] = bool(ties) and all(t.get('winner') for t in ties)
        if result['whole_play_in_phase_finished']:
            result['direct_quarterfinalists'] = [ctx.team(t) for t in state.get('direct', [])]
            result['play_in_winners'] = [ctx.team(t['winner']) for t in ties]
            result['quarterfinalists'] = result['direct_quarterfinalists'] + result['play_in_winners']
            result['play_in_results'] = [ctx.match(t['match_id']) for t in ties if t['match_id'] in ctx.weekly]
    if any(result.get(key) for key in ('whole_swiss_phase_finished', 'whole_group_phase_finished', 'whole_play_in_phase_finished')):
        quarters = state.get('quarterfinals', [])
        if quarters:
            result['quarterfinal_draw'] = {'pairings': [[ctx.team(t) for t in tie['teams']] for tie in quarters],
                                         'format': '主客场两回合' if quarters[0].get('legs') else '单场淘汰'}
    return result


def select_candidates(candidates, *, local_extra_story=False):
    candidates=list(candidates)
    if local_extra_story:
        groups={}
        for candidate in candidates:
            games=candidate['content'].get('matches',[])
            if (candidate['mandatory'] and candidate['priority']==90 and candidate['kind']=='match_story'
                    and games and all(g['competition']==games[0]['competition'] for g in games)):
                groups.setdefault(games[0]['competition'],[]).append(candidate)
        for competition,items in groups.items():
            if len(items)<2:continue
            merged={'key':'roundup:'+competition,'priority':90,'kind':'qualification_roundup',
                    'angle':'同一赛事的资格结果','mandatory':True,
                    'content':{'matches':[g for item in items for g in item['content']['matches']]},
                    'fact_ids':sorted({f for item in items for f in item['fact_ids']})}
            for item in items:candidates.remove(item)
            candidates.append(merged)
    candidates.sort(key=lambda c: (-c['priority'], c['key']))
    mandatory=[c for c in candidates if c['mandatory']]
    optional=[c for c in candidates if not c['mandatory']]
    selected=mandatory+optional[:max(0,4-len(mandatory))]
    if local_extra_story and len(mandatory)>=4 and optional and optional[0]['priority']>=110:
        selected.append(optional[0])
    return sorted(selected,key=lambda c:(-c['priority'],c['key']))


def build_plan(packet, sources, *, local_extra_story=False, include_edition_awards=True):
    validate_packet(packet, sources)
    ctx = Context(packet, sources)
    candidates = []

    def add(priority, kind, angle, content, refs, *, key, mandatory=False):
        candidates.append({'key': key, 'priority': priority, 'kind': kind, 'angle': angle,
                           'mandatory': mandatory, 'content': content, 'fact_ids': sorted(set(refs))})

    # Standings at the end of a league are themselves the main story, even when
    # no team strictly beats another team's mathematical maximum points.
    closed_leagues = set()
    for cid, rows in ctx.tables.items():
        if not any(f['competition_id'] == cid for f in ctx.weekly.values()):
            continue
        if ctx.closed(cid):
            closed_leagues.add(cid)
            relevant = []
            for mid, f in ctx.weekly.items():
                if f['competition_id'] != cid: continue
                importance = 0
                for tid in f['team_ids']:
                    row = ctx.row(cid, tid); before = row.get('week_comparison', {}).get('before_rank')
                    importance = max(importance, 90 if row['rank'] == 1 else 0)
                    cutoff = 6 if cid == 'second' else 17
                    if before is not None and (before <= cutoff) != (row['rank'] <= cutoff): importance = max(importance, 80)
                relevant.append((importance, mid))
            selected = [mid for _, mid in sorted(relevant, key=lambda p: (-p[0], p[1]))[:2]]
            refs = [f['fact_id'] for f in rows]
            refs += [f['fact_id'] for f in ctx.facts.values() if any(mid in f['match_ids'] for mid in selected)]
            games = [ctx.match(mid) for mid in selected]
            for game in games:
                game.pop('league_background', None); game.pop('league_week_end', None)
            content = {'league': ctx.league_context(cid), 'decisive_matches': games}
            official, award_ref = ctx.edition_awards(cid) if include_edition_awards else (None, None)
            if official is not None:
                content['edition_awards'] = official
                refs.append(award_ref)
            honours = ctx.honours(rows[0]['data']['team_id'], cid)
            if honours: content['champion_honours'] = honours
            add(210, 'league_closure', '联赛收官：冠军与最终资格已经确定', content, refs, key=cid, mandatory=True)

    grouped = {}
    for mid, f in ctx.weekly.items():
        if f['competition_id'] in closed_leagues: continue
        tie = next((t for t in ctx.facts.values() if t['kind'] == 'tie_result' and mid in t['match_ids']), None)
        key = tie['fact_id'] if tie else mid
        grouped.setdefault(key, []).append(mid)
    for key, mids in grouped.items():
        cid = ctx.games[mids[-1]]['competition_id']
        facts = [f for f in ctx.facts.values() if any(mid in f['match_ids'] for mid in mids)]
        content = {'matches': [ctx.match(mid) for mid in mids]}
        phases = phase_context(ctx, cid)
        if cid not in ('premier', 'second'): content['phase'] = phases
        priority, angle, mandatory = 15, '选择值得关注的赛果与人物', False
        statuses = [f for f in facts if f['kind'] == 'competition_status']
        champions = [f for f in statuses if f['data']['status'] == 'champion']
        defender = next((f for f in ctx.facts.values() if f['kind'] == 'historical_title' and f['competition_id'] == cid
                         and any(f['data']['team_id'] in ctx.games[mid]['team_ids'] for mid in mids)), None)
        if champions and cid == 'playoff':
            priority, angle, mandatory = 215, '附加赛决胜：最后一个升级名额确定', True
            content['promoted_via_playoff'] = ctx.team(champions[0]['data']['team_id'])
        elif champions:
            priority, angle, mandatory = 230, '冠军产生：本届决胜、人物与历史意义', True
            h = ctx.honours(champions[0]['data']['team_id'], cid)
            if h: content['champion_honours'] = h
            official, award_ref = ctx.edition_awards(cid) if include_edition_awards else (None, None)
            if official is not None:
                content['edition_awards'] = official
                facts.append(ctx.facts[award_ref])
        elif defender and any(f['data']['team_id'] == defender['data']['team_id'] and f['data']['status'] == 'eliminated' for f in statuses):
            priority, angle, mandatory = 205, '卫冕冠军本届出局', True
        elif any(f['data']['status'] == 'qualified' for f in statuses):
            priority, angle, mandatory = 90, '关键晋级与下一阶段路径', True
        elif any(f['data']['status'] == 'eliminated' for f in statuses):
            priority, angle = 75, '本届出局与比赛结果'
        elif any(f['data']['status'] == 'entered_losers_bracket' for f in statuses):
            priority, angle = 65, '转入败者组，仍保留夺冠机会'
        for match in content['matches']:
            if match.get('expectation_comparison',{}).get('is_upset') and priority < 125:
                priority, angle = 125, '综合赛前战绩与身价证据支持的冷门'
            goals = match.get('goals', [])
            flow = match.get('score_flow', {})
            late = bool(goals and goals[-1]['minute'] >= 85 and goals[-1]['field_effect'] in
                        ('首开纪录', '取得领先', '再次取得领先', '反超比分', '扳平比分'))
            if (flow.get('winner_trailing_spells', 0) or late) and priority < 110:
                priority, angle = 110, '有完整进球顺序支持的逆转或末段关键进球'
            if (match.get('season_first_losses') and any(r['team'] in match['season_first_losses'] and r['played'] >= 5
                    for r in match['league_background']) and priority < 150):
                priority, angle = 150, '联赛赛季首败与原有走势的终结'
            for p in match.get('players', []):
                if p.get('goals', 0) >= 3 and priority < 130:
                    priority, angle = 130, '亮眼个人发挥与比赛结果'
            for event in match['streak_events']:
                value = 115 if event['ended'] and event['before_match'] >= 6 else 70 if event['ended'] else min(85, event['after_match'] * 2 + 30)
                if value > priority:
                    kind_label = {'wins':'连胜','losses':'连败','unbeaten':'不败','winless':'不胜'}[event['kind']]
                    count = event['before_match'] if event['ended'] else event['after_match']
                    angle = event['team'] + ('结束' if event['ended'] else '延续') + f"{event['scope']}{count}场{kind_label}"
                    priority = value
            background = match['league_background']
            winner = ctx.games[mids[-1]]['data']['on_field_winner_team_id']
            winner_name = ctx.team(winner) if winner else None
            if len(background) == 2 and winner_name:
                win = next((r for r in background if r['team'] == winner_name), None)
                loss = next((r for r in background if r['team'] != winner_name), None)
                upset = win and loss and ((win['league'] == loss['league'] and min(win['played'], loss['played']) >= 5 and win['rank'] - loss['rank'] >= 7)
                                          or (ctx.league_of(winner) == 'second' and loss['league'] == ctx.competition('premier')))
                if upset:
                    match['upset_basis'] = background
                    if priority < 125: priority, angle = 125, '较低排名或级别球队赢球，结合赛前依据评论'
            if match.get('former_clubs') and priority < 95:
                priority, angle = 95, '面对老东家的贡献'
        if cid in ('premier', 'second'):
            lead = ctx.tables[cid][0]['data']
            if any(lead['team_id'] in ctx.games[mid]['team_ids'] for mid in mids):
                content['league_race'] = ctx.league_context(cid)
                race = lead.get('lead_context', {})
                narrowed = (not race.get('leader_changed') and race.get('before_gap') is not None
                            and race.get('after_gap') is not None and race['before_gap'] - race['after_gap'] >= 2)
                value = 140 if race.get('leader_changed') else 135 if narrowed else 45
                if value > priority:
                    priority = value
                    angle = '榜首易主' if race.get('leader_changed') else '榜首未变，领先优势缩小' if narrowed else '领跑球队本周的走势'
        refs = [f['fact_id'] for f in facts]
        refs += [f['fact_id'] for f in ctx.tables.get(cid, [])] if 'league_race' in content else []
        if defender: refs.append(defender['fact_id'])
        add(priority, 'match_story', angle, content, refs, key=key, mandatory=mandatory)

    for cid in {f['competition_id'] for f in ctx.weekly.values()} - {'premier', 'second'}:
        phase = phase_context(ctx, cid)
        if phase.get('whole_swiss_phase_finished') or phase.get('whole_group_phase_finished') or phase.get('whole_play_in_phase_finished'):
            # Summary covers the phase-wide result; selected matches remain
            # separate only when they add a genuinely important story.
            refs = [f['fact_id'] for f in ctx.facts.values() if f['competition_id'] == cid]
            angle = '瑞士阶段收官：八强名单与最终种子' if phase.get('whole_swiss_phase_finished') else '阶段收官：晋级、突围与淘汰的路径'
            add(180, 'phase_closure', angle, phase, refs, key='phase:' + cid, mandatory=True)

    # Early champions are league-wide events, independent of the chosen match.
    for fact in ctx.facts.values():
        if fact['kind'] != 'title_clinched' or fact['competition_id'] in closed_leagues: continue
        cid = fact['competition_id']
        content = {'league': ctx.league_context(cid), 'champion': ctx.team(fact['data']['team_id']), 'clinched_early_this_week': True}
        refs = [fact['fact_id'], *[f['fact_id'] for f in ctx.tables[cid]]]
        add(225, 'early_champion', '提前夺冠：本周已排除追赶者', content, refs, key='title:' + cid, mandatory=True)
    # One team's two cup matches are one weekly arc, not two present-tense
    # claims with different streak counts. Only merge into a title story; do
    # not connect the whole fixture graph through incidental shared teams.
    for main in list(candidates):
        if main not in candidates or not main['mandatory'] or main['kind'] != 'match_story' or 'matches' not in main['content']: continue
        main_champions = {f['data']['team_id'] for f in ctx.facts.values() if f['fact_id'] in main['fact_ids']
                          and f['kind'] == 'competition_status' and f['data']['status'] == 'champion'}
        if not main_champions: continue
        names = {ctx.team(t) for t in main_champions}
        for other in list(candidates):
            if other is main or other['kind'] != 'match_story' or other not in candidates: continue
            games = other['content'].get('matches', [])
            if not games or any(g['competition'] in ('Premier Division', 'Second Division') for g in games): continue
            if not any(names & {g['home'], g['away']} for g in games): continue
            main['content']['matches'].extend(games)
            main['content'].setdefault('other_competition_phases', []).append(other['content'].get('phase', {}))
            main['fact_ids'] = sorted(set(main['fact_ids'] + other['fact_ids']))
            candidates.remove(other)
        games_by_mid = {mid: (ctx.competition(f['competition_id']), ctx.team(f['data']['home_team_id']), ctx.team(f['data']['away_team_id'])) for mid, f in ctx.weekly.items()}
        index = {identity: i for i, identity in enumerate(games_by_mid.values())}
        main['content']['matches'].sort(key=lambda g: index[(g['competition'], g['home'], g['away'])])
        if len(main['content']['matches']) > 1:
            main['angle'] = '一支球队的双线一周：冠军与另一赛事的结果'
            main['content']['sequence_basis'] = '本周统计处理顺序；没有具体比赛日或相隔天数'
    selected=select_candidates(candidates,local_extra_story=local_extra_story)
    if not selected:
        raise PressError('本周没有可报道的故事。')
    stories = []
    for n, c in enumerate(selected, 1):
        stories.append({'id': 'S' + str(n), 'kind': c['kind'], 'angle': c['angle'], 'must_cover': c['mandatory'],
                        'suggested_words_zh': 250 if n == 1 else 180, 'content': c['content'], 'fact_ids': c['fact_ids']})
    return {'version': VERSION, 'packet_sha256': digest(packet), 'source_sha256': digest(sources), 'name_aliases': name_aliases(packet),
            'issue': {'season': packet['meta']['season'], 'completed_week': packet['meta']['completed_week'],
                      'target_words_zh': [600, 1000], 'matches_this_week': len(ctx.weekly), 'calendar_precision': '只知周次，不提供具体比赛日或相隔天数'},
            'stories': stories}


def split_cross_competition_stories(editorial, packet):
    """A team's weekly arc can contain different cups' milestones. Write those
    separately so 'this cup' can only refer to one competition in each return.
    Keep the selection and all immutable evidence; no extra assembly request.
    """
    facts = {f['fact_id']: f for f in packet['facts']}
    result = []
    for story in editorial['stories']:
        content = story['content']
        games = content.get('matches', [])
        competitions = list(dict.fromkeys(g['competition'] for g in games))
        if len(competitions) < 2:
            result.append(story); continue
        primary = content['phase']['competition']
        competitions.sort(key=lambda name: name != primary)
        phases = {p['competition']: p for p in [content['phase'], *content.get('other_competition_phases', [])]}
        for n, name in enumerate(competitions, 1):
            child = deepcopy(story)
            child['id'] += '-' + str(n)
            child['angle'] = '冠军与该赛事的队史意义' if name == primary else '同周另一项杯赛的结果与人物'
            child['content'] = {'matches': [g for g in games if g['competition'] == name], 'phase': phases[name]}
            if name == primary and content.get('champion_honours'):
                child['content']['champion_honours'] = content['champion_honours']
            cid = next(cid for cid, entity in packet['entities']['competitions'].items() if entity['name_en'] == name)
            mids = {mid for fid in story['fact_ids'] if facts[fid]['competition_id'] == cid for mid in facts[fid]['match_ids']}
            child['fact_ids'] = [fid for fid in story['fact_ids'] if facts[fid]['competition_id'] == cid or mids.intersection(facts[fid]['match_ids'])]
            result.append(child)
    editorial['stories'] = result


def generation_plan(packet, sources, *, grouped=False, storywise=None):
    if storywise is None:
        storywise = not grouped
    editorial = build_plan(packet, sources)
    if storywise:
        split_cross_competition_stories(editorial, packet)
    system = (Path(__file__).resolve().parents[2] / 'docs/press/prompts/weekly-issue.md').read_text(encoding='utf-8').split('\n---\n', 1)[1].strip()
    stories = editorial['stories']
    groups = [[story] for story in stories] if storywise else [stories] if not grouped or len(stories) < 2 else [stories[:(len(stories) + 1)//2], stories[(len(stories) + 1)//2:]]
    units = []
    for i, group in enumerate(groups, 1):
        payload = {'issue': deepcopy(editorial['issue']),
            'stories': [{k: author_content(deepcopy(v)) for k, v in s.items() if k != 'fact_ids'} for s in group]}
        if storywise:
            payload['issue']['target_words_zh'] = [180, 240] if i == 1 else [80, 160]
            content = payload['stories'][0]['content']; matches = content.get('matches', [])
            if (len(matches) == 1 and not any(matches[0].get(k) for k in
                    ('key_goals', 'qualification_outcomes', 'club_records', 'streaks', 'two_leg_tie'))
                    and not content.get('league_race')):
                payload['issue']['target_words_zh'] = [40, 80]
        if len(groups) > 1:
            if not storywise: payload['issue']['target_words_zh'] = [300, 500]
            payload['issue']['part'] = i; payload['issue']['parts'] = len(groups)
        payload['issue']['writing_scope'] = '一则报道：标题、一句导语、一至两个自然段，篇幅随故事变化，程序合成整期' if storywise else '多则故事组成的一篇报道'
        units.append({'competition_id': 'story-' + group[0]['id'] if storywise else 'issue' if len(groups) == 1 else 'stories-' + str(i), 'story_ids': [s['id'] for s in group],
                      'prompt_versions': [PROMPT], 'request': {'model': 'deepseek-flash',
                          'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high', 'messages': [
                          {'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}]}})
    return {'strategy': VERSION, 'unit_mode': 'per-story' if storywise else 'grouped' if grouped else 'whole',
            'assembly': {'summary': 'lead-only'}, 'editorial_plan': editorial, 'sections': units}


def prepare_story_article(raw, packet, plan, unit):
    by_id = {s['id']: s for s in plan['editorial_plan']['stories']}
    allowed = set(unit['story_ids']); sections = []
    # Current output is three strings. This avoids paying for a nested citation
    # grammar that does not prove prose truth. Keep earlier complete returns
    # readable under their frozen contracts.
    if isinstance(raw.get('body'), str) and raw['body'].strip():
        converted, heading, paragraphs = [], '', []
        for block in re.split(r'\n\s*\n', raw['body'].strip()):
            lines = block.splitlines()
            if re.match(r'^#{1,3}\s+', lines[0]):
                if paragraphs: converted.append({'heading': heading, 'story_ids': unit['story_ids'], 'paragraphs': paragraphs})
                heading = re.sub(r'^#{1,3}\s+', '', lines[0]).strip(); paragraphs = []
                if len(lines) > 1: paragraphs.append('\n'.join(lines[1:]).strip())
            else:
                paragraphs.append(block.strip())
        if paragraphs: converted.append({'heading': heading, 'story_ids': unit['story_ids'], 'paragraphs': paragraphs})
        raw = {**raw, 'sections': converted}
    if not isinstance(raw.get('sections'), list): raise PressError('周报没有可读章节。')
    for section in raw['sections']:
        ids = section.get('story_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(s, str) or s not in allowed for s in ids):
            raise PressError('章节引用了未提供的故事。')
        paragraphs = section.get('paragraphs')
        if not isinstance(paragraphs, list) or not paragraphs or any(not isinstance(p, str) or not p.strip() for p in paragraphs):
            raise PressError('周报章节没有可读正文。')
        refs = sorted({f for s in ids for f in by_id[s]['fact_ids']})
        heading = section.get('heading')
        if not isinstance(heading, str) or not heading.strip():
            heading = raw.get('title') or by_id[ids[0]]['angle']
        sections.append({'heading': heading, 'heading_fact_ids': refs, 'blocks': [
            {'kind': 'body', 'label': None, 'text': p, 'fact_ids': refs} for p in paragraphs]})
    if not sections: raise PressError('周报没有可读正文。')
    refs = sorted({f for s in sections for f in s['heading_fact_ids']})
    def text_or(value, fallback): return value.strip() if isinstance(value, str) and value.strip() else fallback
    result = {'schema_version': 'press.article/0.1-draft', 'report_id': packet['meta']['report_id'],
              'title': text_or(raw.get('title'), by_id[unit['story_ids'][0]]['angle']), 'title_fact_ids': refs,
              'summary': text_or(raw.get('deck'), sections[0]['heading']), 'summary_fact_ids': refs,
              'used_topic_ids': [], 'sections': sections}
    # Resolve only unique, literal team prefixes from this frozen packet. No
    # fuzzy corrections or current configuration are needed for old material.
    aliases = {**team_name_aliases(packet), **plan['editorial_plan'].get('name_aliases', {})}
    def names(text):
        if not aliases: return text
        def literal(key):
            return (r'(?<![A-Za-z])' if key[0].isascii() and key[0].isalpha() else '') + re.escape(key) + (r'(?![A-Za-z])' if key[-1].isascii() and key[-1].isalpha() else '')
        normalized = re.sub('|'.join(literal(k) for k in sorted(aliases, key=len, reverse=True)), lambda m: aliases[m.group()], text)
        for name in set(aliases.values()):
            normalized = re.sub(re.escape(name) + r'\s*' + re.escape(name), lambda m: name, normalized)
            if '（' in name:
                # Alternate bilingual order may expand both known aliases:
                # English (Chinese) -> bilingual (bilingual). Collapse the
                # display duplicate only, without editing article assertions.
                normalized = re.sub(re.escape(name) + r'\s*[（(]\s*' + re.escape(name) + r'\s*[）)]', lambda m: name, normalized)
        return normalized
    result['title'] = names(result['title']); result['summary'] = names(result['summary'])
    for section in result['sections']:
        section['heading'] = names(section['heading'])
        for block in section['blocks']: block['text'] = names(block['text'])
    validate_article_basics(result, packet)
    return result


def assemble_story_articles(packet, fragments, *, plan=None):
    first = deepcopy(fragments[0][1])
    if len(fragments) > 1:
        first['sections'] = [deepcopy(s) for _, article in fragments for s in article['sections']]
        if not plan or plan.get('assembly', {}).get('summary') != 'lead-only':
            # Older frozen tasks keep their original assembly contract.
            first['summary'] = ' '.join(article['summary'] for _, article in fragments)
            first['summary_fact_ids'] = sorted({fid for _, article in fragments for fid in article['summary_fact_ids']})
    validate_article_basics(first, packet)
    return first
