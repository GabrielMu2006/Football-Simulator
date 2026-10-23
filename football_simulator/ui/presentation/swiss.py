"""CUI-070: read-only Swiss-round graph model from saved fixtures.

Pre-round records are recomputed from *earlier completed* Swiss fixtures, never
back-filled from the final records. Swiss rounds end at three wins/losses; the
later KO rounds (quarterfinal/semifinal/final) never count as Swiss results.
Unknown future pairings are not invented: only saved pairings become nodes.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

SWISS_STAGES = ("swiss_1", "swiss_2", "swiss_3", "swiss_4", "swiss_5")


def _team_name(world: dict, team_id: Optional[str]) -> str:
    team = (world.get("teams") or {}).get(team_id) or {}
    return team.get("club_en") or "待定"


def _pairings(world: dict, stage: str) -> List[dict]:
    rows = [fixture for fixture in world["season"].get("fixtures", []) if fixture.get("competition") == "starpath" and fixture.get("stage") == stage]
    rows.sort(key=lambda fixture: str(fixture.get("match_id")))
    return rows


def _record(fixture: dict, team_id: str) -> str:
    home = fixture.get("home_team_id") == team_id
    ours = int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0)
    theirs = int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0)
    if not home:
        ours, theirs = theirs, ours
    return "win" if ours > theirs else "loss" if ours < theirs else "draw"


def pre_round_records(world: dict, round_index: int) -> Dict[str, Tuple[int, int]]:
    """(wins, losses) from completed Swiss fixtures in rounds strictly before."""
    records: Dict[str, Tuple[int, int]] = {}
    for index, stage in enumerate(SWISS_STAGES[: round_index - 1], start=1):
        for fixture in _pairings(world, stage):
            if fixture.get("status") != "completed" or not fixture.get("winner_team_id"):
                continue
            winner = fixture.get("winner_team_id")
            for team_id in (fixture["home_team_id"], fixture["away_team_id"]):
                wins, losses = records.get(team_id, (0, 0))
                if team_id == winner:
                    wins += 1
                elif winner:
                    losses += 1
                elif _record(fixture, team_id) == "win":
                    wins += 1
                else:
                    losses += 1
                records[team_id] = (wins, losses)
    return records


def swiss_graph(world: dict) -> Optional[dict]:
    state = ((world["season"].get("cups") or {}).get("starpath") or {})
    rounds = []
    for round_index, stage in enumerate(SWISS_STAGES, start=1):
        pairings = _pairings(world, stage)
        if not pairings:
            continue
        before = pre_round_records(world, round_index)
        buckets: Dict[Tuple[int, int], List[dict]] = {}
        for fixture in pairings:
            home_id, away_id = fixture["home_team_id"], fixture["away_team_id"]
            record = before.get(home_id) or before.get(away_id) or (round_index - 1, 0)
            bucket_key = (int(record[0]), int(record[1]))
            completed = fixture.get("status") == "completed"
            from football_simulator.app.queries import stadium_name
            venue = stadium_name(fixture.get("stadium_id"), with_club=False)
            node = {
                "venues": [("中立场 · " if fixture.get("venue_mode") == "neutral" else "") + venue],
                "match_id": fixture.get("match_id"),
                "week": int(fixture.get("week") or 0),
                "status": fixture.get("status"),
                "completed": completed,
                "home_id": home_id,
                "away_id": away_id,
                "home": _team_name(world, home_id),
                "away": _team_name(world, away_id),
                "home_goals": int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0),
                "away_goals": int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0),
                "home_pk": fixture.get("home_pk"),
                "away_pk": fixture.get("away_pk"),
                "winner_id": fixture.get("winner_team_id") if completed else None,
                "score_text": (
                    f"{int(fixture.get('home_goals') or 0) + int(fixture.get('home_goals_et') or 0)}–{int(fixture.get('away_goals') or 0) + int(fixture.get('away_goals_et') or 0)}"
                    if completed else "vs"
                ),
            }
            if completed and fixture.get("home_pk") is not None and fixture.get("away_pk") is not None:
                node["pk_text"] = f"点球 {fixture['home_pk']}–{fixture['away_pk']}"
            buckets.setdefault(bucket_key, []).append(node)
        rounds.append({
            "round": round_index,
            "stage": stage,
            "week": int(pairings[0].get("week") or 0),
            "buckets": [
                {"record": (wins, losses), "label": f"{wins}–{losses}", "nodes": nodes}
                for (wins, losses), nodes in sorted(buckets.items(), key=lambda item: (-item[0][0], item[0][1]))
            ],
            "pairings": sum(len(nodes) for nodes in buckets.values()),
        })
    if not rounds:
        return None
    records = pre_round_records(world, len(SWISS_STAGES) + 1)
    qualified = sorted(
        ((team_id, record) for team_id, record in records.items() if record[0] >= 3),
        key=lambda item: (-item[1][0], item[1][1], item[0]),
    )
    eliminated = sorted(
        ((team_id, record) for team_id, record in records.items() if record[1] >= 3),
        key=lambda item: (item[1][0], -item[1][1], item[0]),
    )
    return {
        "mode": "swiss",
        "rounds": rounds,
        "qualified": [{"team_id": team_id, "name": _team_name(world, team_id), "record": f"{record[0]}–{record[1]}"} for team_id, record in qualified],
        "eliminated": [{"team_id": team_id, "name": _team_name(world, team_id), "record": f"{record[0]}–{record[1]}"} for team_id, record in eliminated],
        "current_round": rounds[-1]["round"],
        "completed": bool(state.get("stage_done", {}).get("swiss")) or bool(state.get("champion_id")),
    }
