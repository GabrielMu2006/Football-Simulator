"""GFE-100: deterministic focus and short facts derived from stored results.

Nothing here reads stored headline strings: stored events are used only as
*triggers* (type/week/competition/team_id). All wording is rebuilt from stable
IDs and results so a refresh cannot change the story and no unconfirmed claim is
made (no "champion"/"qualified" unless the stored stage fact says so).
"""

from __future__ import annotations

from typing import List, Optional

CUP_ORDER = ("aureole", "confluence", "starpath", "ember", "ascendant", "zenith")


def team_name(world: dict, team_id: Optional[str]) -> str:
    team = (world.get("teams") or {}).get(team_id) or {}
    return team.get("club_en") or "未知球队"


def _results_by_week(world: dict, week: int) -> List[dict]:
    rows = [
        fixture
        for fixture in world["season"].get("fixtures", [])
        if fixture.get("status") == "completed" and int(fixture.get("week") or 0) == week
    ]
    rows.sort(key=lambda fixture: (0 if fixture.get("competition") == "premier" else 1, str(fixture.get("match_id"))))
    return rows


def _league_outcome(fixture: dict, team_id: str) -> str:
    home = fixture["home_team_id"] == team_id
    ours = int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0)
    theirs = int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0)
    if not home:
        ours, theirs = theirs, ours
    return "win" if ours > theirs else "loss" if ours < theirs else "draw"


def league_fixtures(
    world: dict,
    team_id: Optional[str] = None,
    *,
    upto_week: int,
    competition: str = "premier",
) -> List[dict]:
    """Completed league matches in schedule order, never past ``upto_week``.

    Empty weeks, winter windows and cup rounds simply do not appear here, so a
    consecutive-match run is not broken by the calendar. Unplayed matches are
    excluded rather than counted as draws. Same-week fixtures keep their saved
    order (round_number, then match_id).
    """
    rows = [
        fixture
        for fixture in world["season"].get("fixtures", [])
        if fixture.get("competition") == competition
        and fixture.get("status") == "completed"
        and 0 < int(fixture.get("week") or 0) <= upto_week
        and (team_id is None or team_id in (fixture.get("home_team_id"), fixture.get("away_team_id")))
    ]
    rows.sort(key=lambda fixture: (int(fixture.get("week") or 0), int(fixture.get("round_number") or 0), str(fixture.get("match_id"))))
    return rows


def league_streak(world: dict, team_id: str, *, upto_week: int, competition: str = "premier") -> int:
    """Consecutive league wins up to ``upto_week``, counted by match."""
    streak = 0
    for match in reversed(league_fixtures(world, team_id, upto_week=upto_week, competition=competition)):
        if _league_outcome(match, team_id) != "win":
            break
        streak += 1
    return streak


def league_unbeaten(world: dict, team_id: str, *, upto_week: int, competition: str = "premier") -> int:
    """Consecutive league matches without defeat up to ``upto_week``, by match."""
    run = 0
    for match in reversed(league_fixtures(world, team_id, upto_week=upto_week, competition=competition)):
        if _league_outcome(match, team_id) == "loss":
            break
        run += 1
    return run


def _comeback(world: dict, fixture: dict) -> Optional[str]:
    """Team that won after trailing, only from the real goal sequence."""
    events = sorted(
        [event for event in (fixture.get("events") or []) if event.get("type") == "goal"],
        key=lambda event: (int(event.get("minute") or 0), 0 if (event.get("period") or "90") == "90" else 1),
    )
    if len(events) < 2:
        return None
    winner = fixture.get("winner_team_id")
    if not winner:
        return None
    leader_seen = None
    for event in events:
        score = event.get("score") or [0, 0]
        home_id = fixture["home_team_id"]
        away_id = fixture["away_team_id"]
        leading = home_id if score[0] > score[1] else away_id if score[1] > score[0] else None
        if leading and leading != winner:
            leader_seen = leading
            break
    return winner if leader_seen else None


def _championship_this_week(world: dict, week: int) -> List[dict]:
    rows = []
    for entry in (world["season"].get("championships") or {}).values():
        if int(entry.get("decided_week") or 0) != week:
            continue
        rows.append(entry)
    rows.sort(key=lambda entry: (CUP_ORDER.index(entry["competition_id"]) if entry.get("competition_id") in CUP_ORDER else 99, str(entry.get("champion_team_id"))))
    return rows


def _qualification_this_week(world: dict, week: int) -> List[dict]:
    season = world["season"]
    rows = []
    for event in world.get("story_events", []):
        if event.get("type") != "qualification" or int(event.get("week") or 0) != week:
            continue
        competition = event.get("competition")
        state = (season.get("cups") or {}).get(competition) or {}
        stage_done = state.get("stage_done") or {}
        rows.append({"competition": competition, "state": state, "stage_done": stage_done})
    rows.sort(key=lambda row: CUP_ORDER.index(row["competition"]) if row["competition"] in CUP_ORDER else 99)
    return rows


def _final_match_id(world: dict, competition: str) -> Optional[str]:
    fixtures = [fixture for fixture in world["season"].get("fixtures", []) if fixture.get("competition") == competition]
    completed = [fixture for fixture in fixtures if fixture.get("status") == "completed"]
    if not completed:
        return None
    completed.sort(key=lambda fixture: (-int(fixture.get("week") or 0), str(fixture.get("match_id"))))
    return completed[0].get("match_id")


def _latest_completed_week(world: dict) -> int:
    weeks = [
        int(fixture.get("week") or 0)
        for fixture in world["season"].get("fixtures", [])
        if fixture.get("status") == "completed"
    ]
    return max(weeks) if weeks else 0


def focus_week(world: dict, data: dict) -> int:
    """Week the focus talks about: last played week, even after the season ends."""
    week = int(data.get("last_week") or 0)
    if week < 1 or data.get("season_complete") or data.get("finalized"):
        latest = _latest_completed_week(world)
        if latest:
            return latest
    return week


def focus_candidate(world: dict, data: dict, *, watch_team_ids=()) -> Optional[dict]:
    """One match-focused story with a factual one-liner, or None.

    Priority: confirmed title/qualification -> leader dropped points -> league
    streak ended -> comeback win -> watched team -> first result.
    """
    week = focus_week(world, data)
    results = _results_by_week(world, week) if week >= 1 else []
    by_id = {fixture["match_id"]: fixture for fixture in results}
    table = league_table_upto(world, week) if week >= 1 else []
    if not table:
        table = (world["season"].get("final_tables") or {}).get("premier") or []

    for entry in _championship_this_week(world, week):
        match_id = entry.get("decided_match_id") or _final_match_id(world, entry["competition_id"])
        team = team_name(world, entry.get("champion_team_id"))
        label = _competition_name(entry["competition_id"])
        return {
            "kind": "champion",
            "priority": 0,
            "match_id": match_id if match_id in by_id else None,
            "team_id": entry.get("champion_team_id"),
            "headline": f"{team} 夺得{label}冠军",
            "consequence": _season_label(entry) + (" · 决赛已结束" if match_id else ""),
            "story": f"{team} 夺得{label}冠军",
            "route": ("competition", {"competition_id": entry["competition_id"]}),
        }

    for item in _qualification_this_week(world, week):
        competition = item["competition"]
        match_id = _final_match_id(world, competition)
        label = _competition_name(competition)
        return {
            "kind": "qualification",
            "priority": 1,
            "match_id": match_id if match_id in by_id else None,
            "team_id": None,
            "headline": f"{label}阶段结束，八强席位确定",
            "consequence": "签表已更新" if match_id else "",
            "story": f"{label}阶段结束，八强席位确定",
            "route": ("competition", {"competition_id": competition}),
        }

    league_results = [fixture for fixture in results if fixture.get("competition") == "premier"]
    if week >= 1 and league_results:
        before = league_table_upto(world, week - 1)
        after = league_table_upto(world, week)
        if before and after and int(before[0].get("played") or 0) > 0:
            leader_id = before[0]["team_id"]
            for fixture in league_results:
                if leader_id not in (fixture["home_team_id"], fixture["away_team_id"]):
                    continue
                outcome = _league_outcome(fixture, leader_id)
                if outcome == "win":
                    continue
                new_leader = after[0]["team_id"]
                rival = fixture["away_team_id"] if fixture["home_team_id"] == leader_id else fixture["home_team_id"]
                verb = "逼平" if outcome == "draw" else "击败"
                if new_leader == leader_id:
                    consequence = f"{team_name(world, new_leader)} 继续领跑"
                else:
                    previous_leader_row = next(
                        (row for row in after if row.get("team_id") == leader_id), None
                    )
                    same_points = bool(
                        previous_leader_row
                        and int(after[0].get("points") or 0) == int(previous_leader_row.get("points") or 0)
                    )
                    rise = "同分登顶" if same_points else "反超登顶"
                    consequence = f"{team_name(world, leader_id)} 让出榜首，{team_name(world, new_leader)} {rise}"
                return {
                    "kind": "leader_dropped",
                    "priority": 2,
                    "match_id": fixture["match_id"],
                    "team_id": rival,
                    "headline": f"{team_name(world, rival)} {verb}榜首 {team_name(world, leader_id)}",
                    "consequence": consequence,
                    "story": f"{team_name(world, rival)} {verb}榜首 {team_name(world, leader_id)} · {consequence}",
                    "route": ("match", {"match_id": fixture["match_id"]}),
                }

    best_streak = None
    for fixture in results:
        if fixture.get("competition") != "premier":
            continue
        for team_id in (fixture["home_team_id"], fixture["away_team_id"]):
            if _league_outcome(fixture, team_id) != "loss":
                continue
            streak = league_streak(world, team_id, upto_week=week - 1)
            if streak >= 5 and (best_streak is None or streak > best_streak[0]):
                best_streak = (streak, team_id, fixture)
    if best_streak:
        streak, team_id, fixture = best_streak
        winner = fixture["winner_team_id"]
        loser = team_id
        return {
            "kind": "streak_end",
            "priority": 3,
            "match_id": fixture["match_id"],
            "team_id": winner,
            "headline": f"{team_name(world, winner)} {_winner_score(fixture, winner)} {team_name(world, loser)}",
            "consequence": f"终结 {team_name(world, loser)} 联赛 {streak} 连胜",
            "story": f"{team_name(world, winner)} 终结 {team_name(world, loser)} 联赛 {streak} 连胜",
            "route": ("match", {"match_id": fixture["match_id"]}),
        }

    for fixture in results:
        winner = _comeback(world, fixture)
        if winner:
            loser = fixture["away_team_id"] if winner == fixture["home_team_id"] else fixture["home_team_id"]
            return {
                "kind": "comeback",
                "priority": 4,
                "match_id": fixture["match_id"],
                "team_id": winner,
                "headline": f"{team_name(world, winner)} {_winner_score(fixture, winner)} {team_name(world, loser)}",
                "consequence": f"{team_name(world, winner)} 在落后情况下完成逆转",
                "story": f"{team_name(world, winner)} 在落后情况下完成逆转",
                "route": ("match", {"match_id": fixture["match_id"]}),
            }

    watched = set(watch_team_ids or ())
    if watched:
        for fixture in results:
            if watched & {fixture["home_team_id"], fixture["away_team_id"]}:
                return {
                    "kind": "watch",
                    "priority": 5,
                    "match_id": fixture["match_id"],
                    "team_id": None,
                    "headline": f"{team_name(world, fixture['home_team_id'])} {_score(fixture)} {team_name(world, fixture['away_team_id'])}",
                    "consequence": "关注球队本周赛果",
                    "story": f"{team_name(world, fixture['home_team_id'])} {_score(fixture)} {team_name(world, fixture['away_team_id'])}",
                    "route": ("match", {"match_id": fixture["match_id"]}),
                }

    if results:
        fixture = results[0]
        return {
            "kind": "result",
            "priority": 6,
            "match_id": fixture["match_id"],
            "team_id": None,
            "headline": f"{team_name(world, fixture['home_team_id'])} {_score(fixture)} {team_name(world, fixture['away_team_id'])}",
            "consequence": _competition_name(fixture.get("competition")) + f" · 第 {fixture.get('week')} 周",
            "story": f"{team_name(world, fixture['home_team_id'])} {_score(fixture)} {team_name(world, fixture['away_team_id'])} · {_competition_name(fixture.get('competition'))}",
            "route": ("match", {"match_id": fixture["match_id"]}),
        }
    return None


def short_facts(world: dict, data: dict, *, limit: int = 3, exclude_match: Optional[str] = None) -> List[dict]:
    """Up to ``limit`` distinct one-line facts with real destinations."""
    week = focus_week(world, data)
    facts = []
    seen = set()

    def add(kind: str, text: str, route, key=None):
        if len(facts) >= limit or not text:
            return
        fingerprint = key or text
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        facts.append({"kind": kind, "text": text, "route": route})

    results = _results_by_week(world, week) if week >= 1 else []
    table = (world["season"].get("final_tables") or {}).get("premier") or []
    if not table:
        from football_simulator.game import world as game_world

        table = game_world.league_tables(world)["premier"]

    for entry in _championship_this_week(world, week):
        add(
            "champion",
            f"{team_name(world, entry.get('champion_team_id'))} 赢得{_competition_name(entry['competition_id'])}冠军",
            ("competition", {"competition_id": entry["competition_id"]}),
            key=f"champ:{entry['competition_id']}",
        )

    if len(table) >= 2 and int(table[0].get("played") or 0) > 0:
        gap = int(table[0]["points"]) - int(table[1]["points"])
        if gap == 0:
            add("table", f"{team_name(world, table[0]['team_id'])} 与 {team_name(world, table[1]['team_id'])} 同分领跑", ("competition", {"competition_id": "premier"}), key="table:0")
        else:
            add("table", f"{team_name(world, table[0]['team_id'])} 领先 {team_name(world, table[1]['team_id'])} {gap} 分", ("competition", {"competition_id": "premier"}), key="table:gap")

    if week >= 2:
        for fixture in results:
            if fixture.get("competition") != "premier" or fixture["match_id"] == exclude_match:
                continue
            for team_id in (fixture["home_team_id"], fixture["away_team_id"]):
                streak = league_streak(world, team_id, upto_week=week)
                if streak >= 5:
                    add("streak", f"{team_name(world, team_id)} 联赛 {streak} 连胜", ("match", {"match_id": fixture["match_id"]}), key=f"streak:{team_id}")
                    break
            if len(facts) >= limit:
                break

    for fixture in results:
        if len(facts) >= limit:
            break
        if fixture["match_id"] == exclude_match:
            continue
        winner = _comeback(world, fixture)
        if winner:
            loser = fixture["away_team_id"] if winner == fixture["home_team_id"] else fixture["home_team_id"]
            add(
                "comeback",
                f"{team_name(world, winner)} 逆转 {team_name(world, loser)}（{_winner_score(fixture, winner)}）",
                ("match", {"match_id": fixture["match_id"]}),
                key=f"comeback:{fixture['match_id']}",
            )

    table_team = None
    if results:
        scored = sorted(
            results,
            key=lambda fixture: -(
                int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0)
                + int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0)
            ),
        )
        top = scored[0]
        total = (
            int(top.get("home_goals") or 0) + int(top.get("home_goals_et") or 0)
            + int(top.get("away_goals") or 0) + int(top.get("away_goals_et") or 0)
        )
        if total >= 4 and top["match_id"] != exclude_match:
            add(
                "goals",
                f"{team_name(world, top['home_team_id'])} {_score(top)} {team_name(world, top['away_team_id'])}，本周进球最多",
                ("match", {"match_id": top["match_id"]}),
                key=f"goals:{top['match_id']}",
            )
    return facts


def _score(fixture: dict) -> str:
    home = int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0)
    away = int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0)
    return f"{home}–{away}"


def _winner_score(fixture: dict, winner_id: str) -> str:
    """Score in the same team order as the headline; pens stay separate."""
    home = int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0)
    away = int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0)
    if home == away:
        home_pk, away_pk = fixture.get("home_pk"), fixture.get("away_pk")
        if home_pk is not None and away_pk is not None:
            penalties = f"{home_pk}–{away_pk}" if winner_id == fixture.get("home_team_id") else f"{away_pk}–{home_pk}"
            return f"{home}–{away}（点球 {penalties}）"
        return f"{home}–{away}"
    return f"{home}–{away}" if winner_id == fixture.get("home_team_id") else f"{away}–{home}"


def league_table_upto(world: dict, week: int, competition: str = "premier") -> List[dict]:
    """League table from that season's completed matches up to ``week`` (read-only).

    Never derives an earlier week from the current/final table: points, goal
    difference and head-to-head are all recomputed from the stored matches with
    the same team list, previous-season ranking and tie rules.
    """
    if week <= 0:
        return []
    from football_simulator.game import calendar
    from football_simulator.game import world as game_world

    key = "premier" if competition == "premier" else "second"
    team_ids = list(((world["season"].get("league") or {}).get(key) or []))
    if not team_ids:
        return []
    matches = league_fixtures(world, None, upto_week=week, competition=competition)
    try:
        rank = game_world.competitions.previous_ranks(world)
    except (KeyError, TypeError):
        rank = {}
    return calendar.standings_from_matches(team_ids, matches, previous_rank=rank)


def _competition_name(competition: Optional[str]) -> str:
    from football_simulator.game import rules

    if not competition:
        return "比赛"
    if competition in rules.CUP_IDS:
        return rules.competition_label(competition)
    return rules.display_name(competition)


def _season_label(entry: dict) -> str:
    season = entry.get("season_number")
    return f"第 {season} 赛季" if season else ""
