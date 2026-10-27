"""Build a weekly writing view and its frozen, locally auditable evidence.

All reads use the writer's connection and transaction. No RNG, HTTP, UI or
display-query truncation is involved. Unknown history is explicitly limited.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json

from football_simulator.game import rules
from football_simulator.game.calendar import standings_from_matches
from football_simulator.game.competitions import previous_ranks
from football_simulator.press import SCHEMA_VERSION, EXPORTER_VERSION, POLICY_VERSION
from football_simulator.press.standings import compare_tables, equivalent_streaks, season_records
from football_simulator.version import APP_VERSION


def point(match):
    return {"season": int(match["season_number"]), "week": int(match["week"])}


def order(match):
    return (int(match["season_number"]), int(match["week"]), {"premier": 0, "second": 1, "playoff": 2}.get(match["competition"], 3), match["match_id"])


def field_score(match):
    return tuple(int(match.get(k) or 0) + int(match.get(k + "_et") or 0) for k in ("home_goals", "away_goals"))


def field_result(match, team):
    home, away = field_score(match)
    gf, ga = (home, away) if match["home_team_id"] == team else (away, home)
    return "W" if gf > ga else "L" if gf < ga else "D"


def canonical_match(match, players):
    home, away = field_score(match)
    events = match.get("events") or json.loads(match.get("events_json") or "[]")
    return {
        "match_id": match["match_id"], "stage": match["stage"],
        "leg": int(match.get("leg") or (2 if match["stage"].endswith("leg_2") else 1)),
        "tie_id": match.get("tie_id"), "home_team_id": match["home_team_id"], "away_team_id": match["away_team_id"],
        "regulation": {"home": int(match["home_goals"]), "away": int(match["away_goals"])},
        "extra_time": {"home": int(match.get("home_goals_et") or 0), "away": int(match.get("away_goals_et") or 0)} if match.get("extra_time_played") else None,
        "shootout": {"home": int(match["home_pk"]), "away": int(match["away_pk"])} if match.get("home_pk") is not None and match.get("away_pk") is not None else None,
        "on_field_winner_team_id": match["home_team_id"] if home > away else match["away_team_id"] if away > home else None,
        "shootout_winner_team_id": (match["home_team_id"] if match["home_pk"] > match["away_pk"] else match["away_team_id"]) if match.get("home_pk") is not None and match.get("away_pk") is not None else None,
        "stadium_id": match.get("stadium_id"), "venue_mode": match.get("venue_mode", "home"),
        "goal_events": [{"minute": int(e["minute"]), "team_id": e["team_id"], "player_id": e.get("player_id") if players.get(e.get("player_id"), {}).get("is_real") else None}
                        for e in events if e.get("type") == "goal" and e.get("minute") is not None and e.get("team_id") in (match["home_team_id"], match["away_team_id"])],
    }


def walk_ties(value):
    if isinstance(value, dict):
        if isinstance(value.get("teams"), list) and len(value["teams"]) == 2 and (value.get("match_id") or value.get("legs")):
            yield value
        else:
            for child in value.values():
                yield from walk_ties(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_ties(child)


def tie_matches(tie):
    return list(tie.get("legs") or ([tie["match_id"]] if tie.get("match_id") else []))


def status_for_team(state, team, matches):
    """Qualification and field wins are intentionally independent concepts."""
    if state.get("champion") == team or state.get("winner") == team:
        return "champion", None
    if team in state.get("eliminated", {}):
        return "eliminated", None
    if team in state.get("placements", {}):
        return "eliminated", None
    if team == state.get("nirvana_sixth"):
        return "eliminated", None
    if state.get("stage_done", {}).get("groups"):
        for rows in state.get("group_standings", {}).values():
            if team in [r["team_id"] for r in rows[2:]]:
                return "eliminated", None
    decided = [t for t in walk_ties(state) if t.get("winner") and all(mid in matches for mid in tie_matches(t)) and team in t["teams"]]
    if decided:
        latest = max(decided, key=lambda t: max(order(matches[mid]) for mid in tie_matches(t)))
        if latest["winner"] != team:
            if "loss_stage" in state and latest["stage"].startswith("wb_"):
                return "entered_losers_bracket", None
            return "eliminated", None
    waiting = [t for t in walk_ties(state) if not t.get("winner") and team in t["teams"]]
    if waiting:
        nxt = min(waiting, key=lambda t: (t.get("stage", ""), t.get("tie_id", "")))
        if nxt.get("legs") and any(mid in matches for mid in nxt["legs"]):
            return "awaiting_tie_second_leg", nxt["stage"]
        # A newly scheduled knockout tie proves qualification, Swiss rounds do not.
        if not nxt.get("stage", "").startswith("swiss"):
            return "qualified", nxt.get("stage")
    if team in state.get("qualified", []):
        return "qualified", "quarterfinal"
    if team in state.get("direct", []):
        return "qualified", "quarterfinal"
    return "in_competition", None


def strict_title(tables, fixtures, leader):
    """Conservative proof: strictly ahead of EVERY rival's maximum points."""
    schedule = Counter(t for f in fixtures for t in (f["home_team_id"], f["away_team_id"]))
    if not tables or any(schedule[row["team_id"]] != 38 for row in tables):
        return False
    return all(leader["points"] > r["points"] + 3 * (38 - r["played"]) for r in tables if r["team_id"] != leader["team_id"])


def build_packet(conn, world, week, activation):
    season = int(world["season_number"])
    cutoff = {"season": season, "week": int(week)}
    raw = [dict(r) for r in conn.execute("SELECT * FROM matches WHERE status='completed' AND (season_number < ? OR (season_number=? AND week<=?))", (season, season, week))]
    metadata = {f["match_id"]: f for a in world.get("edition_archives", {}).values() for f in a.get("fixtures", [])}
    metadata.update({f["match_id"]: f for f in world["season"]["fixtures"]})
    for match in raw:
        meta = metadata.get(match["match_id"], {})
        for name in ("leg", "tie_id"):
            if name in meta:
                match[name] = meta[name]
    raw.sort(key=order)
    matches = {m["match_id"]: m for m in raw}
    weekly = [m for m in raw if point(m) == cutoff]
    histories = {int(a["season_number"]): a for a in world.get("archives", []) if int(a["season_number"]) < season}
    history_complete = set(histories) == set(range(1, season)) and all(
        sum(m["season_number"] == y for m in raw) == 766 + sum(rules.CUP_META[c]["match_count"] for c in rules.CUP_IDS if rules.cup_active(c, y))
        for y in histories)
    packet = {
        "meta": {"schema_version": SCHEMA_VERSION, "report_id": f"weekly:{world['world_id']}:{season}:{week}", "report_type": "weekly", "world_id": world["world_id"],
                 "season": season, "completed_week": week, "cutoff": cutoff, "input_validity": "ready" if weekly else "no_matches", "source_mode": "production_export",
                 "source_game_version": APP_VERSION, "rules_version": world["rules_version"], "exporter_version": EXPORTER_VERSION,
                 "history_first_season": min([season, *histories]), "fixture_coverage": "full", "missing_material": [], "sample_note": None,
                 "source_world_version": int(world["version"]), "save_instance_id": activation["instance_id"]},
        "entities": {k: {} for k in ("teams", "players", "competitions", "stadiums")},
        "week_summary": {"completed_matches": len(weekly), "goals_excluding_shootouts": sum(sum(field_score(m)) for m in weekly),
                         "competitions": [{"competition_id": c, "completed_matches": n} for c, n in sorted(Counter(m["competition"] for m in weekly).items())], "notes": []},
        "facts": [], "editorial": {"policy_version": POLICY_VERSION, "judgements": [], "topics": []},
    }
    packet["meta"]["missing_material"].append("逐场评分未冻结，使用实际进球、助攻、防守、扑救等表现。")
    if not history_complete:
        packet["meta"]["missing_material"].append("历史赛季或赛果不完整；队史首次和完整连续纪录不能无条件确认。")
    sources = {"schema_version": "press.sources/1", "identity": {"world_id": world["world_id"], "instance_id": activation["instance_id"], "source_world_version": world["version"], **cutoff}, "records": {}}
    records = sources["records"]
    stadiums = {r["stadium_id"]: r for r in rules.load_stadiums()}
    fact_index = {}

    def record(key, kind, value, when=None):
        records[key] = {"kind": kind, "as_of": deepcopy(when or cutoff), "value": deepcopy(value)}
        return key

    def match_source(m):
        return record("match:" + m["match_id"], "match", m, point(m))

    def emit(fid, kind, data, *, comp=None, teams=(), players=(), mids=(), keys=(), when=None):
        if fid in fact_index:
            return fid
        for tid in teams:
            packet["entities"]["teams"][tid] = {"name_en": world["teams"][tid]["club_en"]}
        for pid in players:
            p = world["players"][pid]
            packet["entities"]["players"][pid] = {"name": p["name"], "position": p["position"], "is_real": True}
        if comp:
            packet["entities"]["competitions"][comp] = {"name_en": rules.display_name(comp)}
        if kind == "streak":
            for cid in data["competition_ids"]:
                packet["entities"]["competitions"][cid] = {"name_en": rules.display_name(cid)}
        if kind == "match_result" and data["stadium_id"] in stadiums:
            s = stadiums[data["stadium_id"]]
            packet["entities"]["stadiums"][s["stadium_id"]] = {"name_en": s["name_en"], "name_zh": s["name_zh"]}
        proof_key = record("fact:" + fid, "derived", {"result": data, "evidence_keys": list(keys), "algorithm": EXPORTER_VERSION}, when)
        all_keys = list(dict.fromkeys([*keys, proof_key]))
        fact = {"fact_id": fid, "kind": kind, "time": deepcopy(when or cutoff), "competition_id": comp, "team_ids": list(teams), "player_ids": list(players), "match_ids": list(mids),
                "sources": [{"kind": records[k]["kind"], "key": k, "as_of": records[k]["as_of"]} for k in all_keys], "data": data}
        packet["facts"].append(fact); fact_index[fid] = fact
        return fid

    def add_match(m):
        data = canonical_match(m, world["players"])
        pids = list(dict.fromkeys(e["player_id"] for e in data["goal_events"] if e["player_id"]))
        return emit("match:" + m["match_id"], "match_result", data, comp=m["competition"], teams=[m["home_team_id"], m["away_team_id"]], players=pids, mids=[m["match_id"]], keys=[match_source(m)], when=point(m))

    def topic(kind, mids, required, context=(), priority="supporting", angle="", judgements=()):
        group = mids[0] if mids else kind
        packet["editorial"]["topics"].append({"topic_id": f"topic:{kind}:{group}", "event_type": kind, "story_group_id": group,
            "priority": priority, "required_fact_ids": list(dict.fromkeys(required)), "context_fact_ids": list(dict.fromkeys(context)), "judgement_ids": list(judgements), "suggested_angle": angle})

    for m in weekly:
        add_match(m)
    for comp in rules.LEAGUE_COMPETITIONS:
        team_ids = world["season"]["league"][comp]
        played = [m for m in raw if m["season_number"] == season and m["competition"] == comp]
        previous_rank = previous_ranks(world)
        rows = standings_from_matches(team_ids, played, previous_rank=previous_rank)
        before_rows = standings_from_matches(team_ids, [m for m in played if m["week"] < week], previous_rank=previous_rank)
        before_keys = [match_source(m) for m in played if m["week"] < week]
        comparisons, leader_context = compare_tables(before_rows, rows)
        season_evidence = season_records(team_ids, played, previous_rank)
        table_key = record("table:" + comp, "derived", {"team_ids": team_ids, "previous_rank": previous_rank,
            "match_keys": [match_source(m) for m in played], "rows": rows, "before_match_keys": before_keys, "before_rows": before_rows})
        for rank, row in enumerate(rows, 1):
            tid = row["team_id"]
            data = {"team_id": tid, "timing": "end_of_week", "rank": rank, "points": row["points"], "played": row["played"], "week_comparison": comparisons[tid], "season_record": season_evidence[tid]}
            team_refs = [tid]
            if rank == 1:
                data["lead_context"] = leader_context
                old_leader = leader_context["before_leader_team_id"]
                if old_leader and old_leader != tid:
                    team_refs.append(old_leader)
            fid = emit(f"table:{comp}:{tid}", "table_row", data, comp=comp, teams=team_refs, keys=[table_key])
            if rank == 1 and any(m["competition"] == comp for m in weekly):
                leader_games = [m for m in weekly if m["competition"] == comp and tid in (m["home_team_id"], m["away_team_id"])]
                if leader_games:
                    topic("league_lead:" + comp, [leader_games[-1]["match_id"]], [fid, "match:" + leader_games[-1]["match_id"]],
                          priority="primary" if leader_context["leader_changed"] else "supporting", angle="比较周初与周末：新领跑、延续领跑和分差变化严格区分；首轮不称升至或重返。")
        schedule = [f for f in world["season"]["fixtures"] if f["competition"] == comp]
        if (any(m['competition'] == comp for m in weekly) and min(r['played'] for r in rows) < 38
                and strict_title(rows, schedule, rows[0]) and not strict_title(before_rows, schedule, before_rows[0])):
            tid = rows[0]["team_id"]
            proof = record("title-proof:" + comp, "derived", {"rows": rows, "scheduled_team_counts": dict(Counter(t for f in schedule for t in (f["home_team_id"], f["away_team_id"])))})
            fid = emit("title:" + comp, "title_clinched", {"team_id": tid, "confirmed": True, "proof_method": "strict_points_bound", "proof_source_key": proof, "awards_ready": False}, comp=comp, teams=[tid], keys=[proof, table_key])
            topic("title_clinched:" + comp, [], [fid], priority="primary", angle="逐一排除所有对手的积分上界后确认提前夺冠。")

    # Package ALL real appearances this week, including modest performances.
    stats = [dict(r) for r in conn.execute("SELECT p.* FROM player_match_stats p JOIN matches m ON m.match_id=p.match_id WHERE p.season_number=? AND m.week=? AND m.status='completed' AND p.appeared=1", (season, week))]
    trades = {t.get("trade_id", str(i)): t for i, t in enumerate([*world.get("transfer_history", []), *world["season"].get("transfers", [])]) if (int(t.get("season", season)), int(t.get("week", 0))) <= (season, week) and t.get("status") in (None, "approved", "已批准")}
    for stat in stats:
        pid = stat["player_id"]
        if not world["players"].get(pid, {}).get("is_real"):
            continue
        m = matches[stat["match_id"]]; tid = stat["team_id"]
        data = {"match_id": m["match_id"], "player_id": pid, "team_id": tid, "appeared": True, **{k: int(stat[k]) for k in ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets")}}
        key = record(f"player:{m['match_id']}:{pid}", "player_match_stats", stat)
        fid = emit(f"performance:{m['match_id']}:{pid}", "player_match", data, comp=m["competition"], teams=[tid], players=[pid], mids=[m["match_id"]], keys=[key, match_source(m)])
        if stat["goals"] >= 2 or stat["assists"] >= 2 or stat["successful_saves"] >= 7 or stat["successful_defenses"] >= 6:
            topic("standout:" + pid, [m["match_id"]], [fid, "match:" + m["match_id"]], angle="使用实际贡献；门将和后卫不因进球少被忽略。")
        opponent = m["away_team_id"] if tid == m["home_team_id"] else m["home_team_id"]
        prior = []
        for t in trades.values():
            for from_team, to_team, field in ((t["team_a"], t["team_b"], "players_a"), (t["team_b"], t["team_a"], "players_b")):
                if pid in t[field] and from_team == opponent and (int(t["season"]), int(t["week"])) < (season, week):
                    prior.append((t, from_team, to_team))
        if prior:
            t, former, destination = max(prior, key=lambda row: (row[0]["season"], row[0]["week"]))
            player_moves = [r for r in trades.values() if pid in [*r["players_a"], *r["players_b"]] and (int(r["season"]), int(r["week"])) < (season, week)]
            latest_move = max(player_moves, key=lambda r: (r["season"], r["week"]))
            earlier = conn.execute("SELECT p.*,m.week FROM player_match_stats p JOIN matches m ON p.match_id=m.match_id WHERE p.player_id=? AND p.team_id=? AND p.appeared=1 AND (m.season_number<? OR (m.season_number=? AND m.week<?)) ORDER BY m.season_number,m.week,m.match_id LIMIT 1", (pid, former, season, season, week)).fetchone()
            tkey = record("trade:" + t["trade_id"], "transfer", t, {"season": int(t["season"]), "week": int(t["week"])})
            latest_key = record("trade:" + latest_move["trade_id"], "transfer", latest_move, {"season": int(latest_move["season"]), "week": int(latest_move["week"])})
            appearance_keys = [record(f"former-appearance:{pid}:{former}", "player_match_stats", dict(earlier), {"season": int(earlier["season_number"]), "week": int(earlier["week"])})] if earlier else []
            old = emit(f"former:{m['match_id']}:{pid}", "former_club", {"player_id": pid, "former_team_id": former, "current_team_id": tid, "transfer_effective": {"season": int(t["season"]), "week": int(t["week"])}, "appeared_for_former": True if earlier else False if history_complete else None,
                "departure_destination_team_id": destination, "is_immediate_previous_club": latest_move["trade_id"] == t["trade_id"]}, teams=list(dict.fromkeys([former, tid, destination])), players=[pid], mids=[m["match_id"]], keys=[tkey, latest_key, key, *appearance_keys])
            if stat["goals"] or stat["assists"]:
                topic("former_club:" + pid, [m["match_id"]], [old, fid, "match:" + m["match_id"]], angle="老东家身份与本场贡献来自转会记录及逐场归属。")

    # Per-match streaks: same-week double headers update in the actual engine order.
    for m in weekly:
        for tid in (m["home_team_id"], m["away_team_id"]):
            for scope in ("all_official", "league_all_levels"):
                if scope == "league_all_levels" and m["competition"] not in rules.LEAGUE_COMPETITIONS:
                    continue
                earlier = [r for r in raw if order(r) < order(m) and tid in (r["home_team_id"], r["away_team_id"]) and (scope == "all_official" or r["competition"] in rules.LEAGUE_COMPETITIONS)]
                if not earlier and not history_complete:
                    continue
                result = field_result(m, tid)
                for kind, good in (("wins", {"W"}), ("losses", {"L"}), ("unbeaten", {"W", "D"}), ("winless", {"L", "D"})):
                    trail = []
                    for prev in reversed(earlier):
                        if field_result(prev, tid) not in good:
                            break
                        trail.append(prev)
                    before = len(trail); after = before + 1 if result in good else 0
                    threshold = 3 if kind in ("wins", "losses") else 5
                    if max(before, after) < threshold:
                        continue
                    evidence = [*trail, m]
                    fid = emit(f"streak:{m['match_id']}:{tid}:{scope}:{kind}", "streak", {"team_id": tid, "scope": scope, "scope_competition_id": None, "kind": kind,
                        "before": before, "after": after, "ended": before > 0 and result not in good, "affected_match_id": m["match_id"], "crosses_seasons": any(r["season_number"] < season for r in trail),
                        "history_complete": history_complete or before < len(earlier), "competition_ids": sorted({r["competition"] for r in evidence}), "basis": "regulation_plus_extra_time_excluding_shootout"}, teams=[tid], mids=[m["match_id"]], keys=[match_source(r) for r in evidence])
                    topic(f"streak:{tid}:{scope}:{kind}", [m["match_id"]], [fid, "match:" + m["match_id"]], priority="supporting", angle="区分该范围内连续战绩及其终结；历史不完整时保留限定。")

    _cup_facts(world, weekly, matches, histories, history_complete, cutoff, packet, record, emit, add_match, topic, match_source)
    # Editorial judgements use pre-match standings, never the post-match rank.
    previous_rank = previous_ranks(world)
    for m in weekly:
        winner = canonical_match(m, world["players"])["on_field_winner_team_id"]
        if not winner:
            continue
        loser = m["away_team_id"] if winner == m["home_team_id"] else m["home_team_id"]
        for comp in rules.LEAGUE_COMPETITIONS:
            team_ids = world["season"]["league"][comp]
            if winner not in team_ids or loser not in team_ids:
                continue
            # League fixtures in a round share the round-start table. For cup
            # games, league play that week has already finished in the engine.
            before = [r for r in raw if r["season_number"] == season and r["competition"] == comp
                      and (r['week'] < m['week'] if m['competition'] in rules.LEAGUE_COMPETITIONS else r['week'] <= m['week'])]
            rows = standings_from_matches(team_ids, before, previous_rank=previous_rank)
            ranks = {r["team_id"]: i + 1 for i, r in enumerate(rows)}
            if min(r["played"] for r in rows) < 5 or ranks[winner] - ranks[loser] < 7:
                continue
            key = record("prematch:" + m["match_id"], "derived", {"rows": rows, "input_match_keys": [match_source(r) for r in before]})
            refs = ["match:" + m["match_id"]]
            for tid in (winner, loser):
                row = next(r for r in rows if r["team_id"] == tid)
                refs.append(emit(f"before:{m['match_id']}:{tid}", "table_row", {"team_id": tid, "timing": "before_match", "rank": ranks[tid], "points": row["points"], "played": row["played"]}, comp=comp, teams=[tid], mids=[m['match_id']], keys=[key]))
            jid = "upset:" + m["match_id"]
            packet["editorial"]["judgements"].append({"judgement_id": jid, "label": "upset", "basis_fact_ids": refs, "policy_version": POLICY_VERSION, "reason": "同级联赛已有至少5场样本，赛前排名差至少7位且较低者获场上胜利；建议门槛，尚待文风调校。"})
            topic("upset", [m["match_id"]], refs, priority="primary", angle="赛前排名构成爆冷依据，勿倒用赛后排名或现实名气。", judgements=[jid])
    if weekly and not packet["editorial"]["topics"]:
        for m in weekly[:2]:
            topic("ordinary_result", [m["match_id"]], ["match:" + m["match_id"]], priority="brief", angle="普通赛果平实交代，不虚构热点。")
    if not weekly:
        packet["week_summary"]["notes"].append("本周无正式比赛；离线返回说明，不生成赛事长文。")
    packet["editorial"]["streak_groups"] = equivalent_streaks(packet["facts"])
    # Small frozen background for history-aware writing, never current-world
    # lookups when an old issue is generated later.
    titles = []
    for year, archive in histories.items():
        for comp, tid in archive.get('cup_champions', {}).items():
            if tid:
                titles.append({'season': year, 'competition': comp, 'team_id': tid})
        for comp in rules.LEAGUE_COMPETITIONS:
            table = archive.get(comp + '_table', [])
            if table:
                titles.append({'season': year, 'competition': comp, 'team_id': table[0]['team_id']})
    for row in world['season'].get('championships', {}).values():
        if row['decided_week'] <= week:
            titles.append({'season': season, 'competition': row['competition_id'], 'team_id': row['champion_team_id']})
    for edition in world.get('edition_archives',{}).values():
        year=int(edition['season_number']);title=edition.get('championship') or {}
        if title.get('champion_team_id') and (year<season or (year==season and edition.get('completed_week') is not None and edition['completed_week']<=week)):
            titles.append({'season':year,'competition':edition['competition_id'],'team_id':title['champion_team_id']})
    record('honours-context', 'derived', {'history_complete': history_complete, 'titles': titles})
    from football_simulator.press.story_context import game_notes,title_background
    all_stats=[dict(r) for r in conn.execute('''SELECT p.* FROM player_match_stats p
        JOIN matches m ON m.match_id=p.match_id WHERE m.status='completed' AND p.appeared=1
        AND (m.season_number<? OR (m.season_number=? AND m.week<=?))''',(season,season,week))]
    notes=game_notes(world,raw,all_stats,season,week,target_week=week)
    record('story-context','derived',{'games':notes,'title_backgrounds':{
        c:title_background(world,season,c) for c in {m['competition'] for m in weekly}}})
    from football_simulator.press.expectations import load
    baseline=next((r for r in load(conn,season,week) if r['before_week']==week),None)
    if baseline:
        record('expectations:week','derived',baseline)
    return packet, sources


def _cup_facts(world, weekly, matches, histories, history_complete, cutoff, packet, record, emit, add_match, topic, match_source):
    states = dict(world["season"].get("cups", {}))
    if world["season"].get("promotion_playoff"):
        states[rules.PLAYOFF] = world["season"]["promotion_playoff"]
    for comp, state in states.items():
        current = [m for m in weekly if m["competition"] == comp]
        if not current:
            continue
        state_key = record("cup-state:" + comp, "derived", {"competition": comp, "state": state})
        for tie in walk_ties(state):
            legs = tie.get("legs", [])
            if not legs or not any(mid in {m["match_id"] for m in current} for mid in legs):
                continue
            played = [matches[mid] for mid in legs if mid in matches]
            for m in played:
                add_match(m)
            a, b = tie["teams"]; totals = Counter()
            for m in played:
                h, aw = field_score(m); totals[m["home_team_id"]] += h; totals[m["away_team_id"]] += aw
            emit("tie:" + tie["tie_id"], "tie_result", {"tie_id": tie["tie_id"], "team_a_id": a, "team_b_id": b, "team_a_goals": totals[a], "team_b_goals": totals[b],
                "leg_match_ids": [m["match_id"] for m in played], "complete": len(played) == len(legs) == 2, "advancing_team_id": tie.get("winner"), "away_goals_rule": False,
                "shootout_match_id": next((m["match_id"] for m in played if m.get("home_pk") is not None), None)}, comp=comp, teams=[a, b], mids=[m["match_id"] for m in played], keys=[state_key, *[match_source(m) for m in played]])
        previous = histories.get(int(world["season_number"]) - 1, {})
        champion = previous.get("cup_champions", {}).get(comp)
        for tid in sorted({t for m in current for t in (m["home_team_id"], m["away_team_id"])}):
            mids = [m["match_id"] for m in current if tid in (m["home_team_id"], m["away_team_id"])]
            status, nxt = status_for_team(state, tid, matches)
            fid = emit(f"status:{comp}:{tid}", "competition_status", {"team_id": tid, "status": status, "next_stage": nxt}, comp=comp, teams=[tid], mids=mids, keys=[state_key])
            if comp == "starpath" and tid in state.get("records", {}):
                wins, losses = state["records"][tid]
                emit(f"swiss-record:{tid}", "swiss_record", {"team_id": tid, "wins": int(wins), "losses": int(losses), "buchholz": int(state.get("bu", {}).get(tid, 0)), "basis": "swiss_ties_including_shootout_wins"}, comp=comp, teams=[tid], mids=mids, keys=[state_key])
            match_refs = ["match:" + mid for mid in mids]
            if status in ("champion", "eliminated", "qualified", "entered_losers_bracket", "awaiting_tie_second_leg"):
                topic("status:" + tid, mids, [fid, *match_refs], priority="primary" if status == "champion" else "supporting", angle="按本届实际资格与路径报道。")
            if tid == champion:
                key = record(f"historical-title:{comp}", "season_archive", {"season": int(world["season_number"]) - 1, "competition": comp, "champion": champion}, {"season": int(world["season_number"]) - 1, "week": 52})
                title = emit("defender:" + comp, "historical_title", {"team_id": tid, "season": int(world["season_number"]) - 1, "competition_id": comp, "result": "champion"}, comp=comp, teams=[tid], keys=[key], when={"season": int(world["season_number"]) - 1, "week": 52})
                if status == "eliminated":
                    topic("defender_exit", mids, [title, fid, *match_refs], priority="primary", angle="卫冕在本场结束；沿用实际赛事路径。")
            # Tournament best finishes are compared only against complete history.
            if comp == rules.PLAYOFF or not history_complete or status not in ("qualified", "champion"):
                continue
            value = 1 if status == "champion" else {"final": 2, "semifinal": 3, "quarterfinal": 5}.get(nxt)
            if value is None:
                continue
            if any(tid not in a.get("cup_placements", {}).get(comp, {}) and any(m["season_number"] == year and m["competition"] == comp and tid in (m["home_team_id"], m["away_team_id"]) for m in matches.values()) for year, a in histories.items()):
                packet["meta"]["missing_material"].append(f"{world['teams'][tid]['club_en']} 的 {rules.display_name(comp)} 历届最终成绩不完整，不确认队史首次。")
                continue
            previous_finishes = [int(a.get("cup_placements", {}).get(comp, {}).get(tid, 999)) for a in histories.values()]
            best = min(previous_finishes, default=999)
            if value > best:
                continue
            archive_key = record(f"history-finishes:{comp}:{tid}", "season_archive", {"finishes": previous_finishes, "history_complete": True})
            from football_simulator.app.profile_history import FINISH_LABELS
            best_label = None if best == 999 else FINISH_LABELS.get(rules.honor_key_for_rank(comp, best), str(best))
            record_id = emit(f"record:{comp}:{tid}", "record", {"entity_id": tid, "record_type": "best_finish", "comparison": "first" if best == 999 else "equalled" if best == value else "broken", "complete_history": True,
                "history_start_season": 1, "previous_best": best_label, "current_achievement": "冠军" if value == 1 else rules.stage_label(nxt)}, comp=comp, teams=[tid], mids=mids, keys=[archive_key, state_key])
            topic("record:" + tid, mids, [record_id, fid, *match_refs], angle="回顾最终夺冠成绩。" if status == 'champion' else "依据完整历史说明队史突破与后续路径。")
