"""GFE-020: player-facing match events built from structured facts.

The engine stores both structured fields (type/period/team_id/player_id/assist_id/
score) and a pre-rendered ``text`` string. The stored string leaks internal slot
labels ("默认 FW 1") for generated players, so the timeline is rendered here from
the structured fields instead. Stored text and player identities are untouched.
"""

from __future__ import annotations

from typing import Optional

ROLE_LABELS = {"GK": "门将", "DF": "后卫", "MF": "中场", "FW": "前锋"}


def real_name(world: dict, player_id: Optional[str]) -> Optional[str]:
    """Name only for real players; generated players never expose slot labels."""
    if not player_id:
        return None
    data = (world.get("players") or {}).get(player_id) or {}
    if not data.get("is_real"):
        return None
    name = (data.get("name") or "").strip()
    return name or None


def role_label(world: dict, player_id: Optional[str]) -> str:
    if not player_id:
        return ""
    data = (world.get("players") or {}).get(player_id) or {}
    return ROLE_LABELS.get(data.get("position"), "球员")


def _minute_label(minute: int, period: str) -> str:
    return f"{minute}′（加时）" if period == "et" else f"{minute}′"


def _team_name(world: dict, team_id: Optional[str]) -> str:
    team = (world.get("teams") or {}).get(team_id) or {}
    return team.get("club_en") or "未知球队"


def goal_tally(events: list) -> dict:
    """Goals per team from structured goal events (for consistency checks)."""
    tally = {}
    for event in events:
        if event.get("type") != "goal":
            continue
        team_id = event.get("team_id")
        if team_id:
            tally[team_id] = tally.get(team_id, 0) + 1
    return tally


def display_events(detail: dict, world: dict, *, scope: str = "real", include_minor: bool = False) -> list:
    """Ordered player-facing events.

    ``scope`` = "real" keeps generated players out of the text (team-level
    wording); ``scope`` = "all" may name the neutral role ("前锋") but never the
    internal slot number. Goals are always present regardless of scope so the
    score line never loses a change.
    """
    raw = [event for event in (detail.get("events") or []) if event.get("type") != "note"]
    raw.sort(key=lambda event: (int(event.get("minute") or 0), 0 if (event.get("period") or "90") == "90" else 1))
    rows = []
    for index, event in enumerate(raw):
        kind = event.get("type")
        minute = int(event.get("minute") or 0)
        period = event.get("period") or "90"
        team_id = event.get("team_id")
        team = _team_name(world, team_id) if team_id else ""
        player_id = event.get("player_id")
        label = _minute_label(minute, period)
        score = event.get("score")
        score_text = f"{score[0]}–{score[1]}" if isinstance(score, (list, tuple)) and len(score) == 2 else None
        if kind == "goal":
            scorer = real_name(world, player_id)
            assist = real_name(world, event.get("assist_id"))
            if scorer:
                text = f"{label} {team} 进球：{scorer}"
            elif scope == "all" and player_id:
                text = f"{label} {team} 进球：{role_label(world, player_id)}"
            else:
                text = f"{label} {team} 进球" if team else f"{label} 进球"
            if assist:
                text += f"（助攻 {assist}）"
            rows.append({
                "key": f"goal-{index}-{match_key(detail)}",
                "kind": "goal",
                "major": True,
                "minute": minute,
                "period": period,
                "label": label,
                "team_id": team_id,
                "team": team,
                "player_id": player_id,
                "text": text,
                "score": score_text,
            })
            continue
        if kind == "save":
            keeper = real_name(world, player_id)
            if keeper:
                text = f"{label} {keeper} 扑出射门"
            elif team:
                text = f"{label} {team} 门将扑出射门"
            else:
                text = f"{label} 扑出射门"
        elif kind == "defense":
            defender = real_name(world, player_id)
            if defender:
                text = f"{label} {defender} 化解进攻"
            elif team:
                text = f"{label} {team} 化解进攻"
            else:
                text = f"{label} 化解进攻"
        else:
            continue
        if not include_minor:
            continue
        rows.append({
            "key": f"{kind}-{index}-{match_key(detail)}",
            "kind": kind,
            "major": False,
            "minute": minute,
            "period": period,
            "label": label,
            "team_id": team_id,
            "team": team,
            "player_id": player_id,
            "text": text,
            "score": score_text,
        })
    return rows


def match_key(detail: dict) -> str:
    fixture = detail.get("fixture") or {}
    return str(fixture.get("match_id") or "match")


def shootout_rows(detail: dict, world: dict) -> list:
    """Penalty shootout as its own block; it never counts as goals or assists."""
    rows = []
    for kick in detail.get("shootout") or []:
        team = _team_name(world, kick.get("team_id"))
        name = real_name(world, kick.get("player_id")) or role_label(world, kick.get("player_id")) or "球员"
        rows.append({
            "round": kick.get("round"),
            "team": team,
            "player": name,
            "scored": bool(kick.get("scored")),
            "text": f"第 {kick.get('round')} 轮 · {team} · {name} {'罚进' if kick.get('scored') else '罚失'}",
        })
    return rows


def goal_timeline(world: dict, fixtures: list, *, limit: int = 8) -> list:
    """Goals from finished fixtures, rendered like the timeline (no slot labels)."""
    rows = []
    for fixture in fixtures:
        detail = {"fixture": fixture, "events": fixture.get("events") or []}
        for event in display_events(detail, world):
            if event["kind"] != "goal":
                continue
            home = _team_name(world, fixture.get("home_team_id"))
            away = _team_name(world, fixture.get("away_team_id"))
            rows.append({**event, "match_id": fixture.get("match_id"), "matchup": f"{home} vs {away}", "week": fixture.get("week")})
        if len(rows) >= limit:
            break
    return rows[:limit]
