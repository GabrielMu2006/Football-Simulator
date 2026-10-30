"""Freeze a completed competition in the same transaction as its final match.

Match events and player facts remain in the existing immutable SQL projections.
The edition stores the draw, fixture metadata, scores, awards and registrations
needed to reproduce the competition after the world moves to another season.
"""
from copy import deepcopy
from football_simulator.game import rules

VERSION = 1


def key(season, competition):
    return f"{int(season)}:{competition}"


def compact_fixture(fixture):
    return {k: deepcopy(v) for k, v in fixture.items() if k not in ("events", "player_facts", "shootout", "events_json", "shootout_json")}


def finished(world, competition):
    fixtures = [f for f in world["season"]["fixtures"] if f["competition"] == competition]
    if not fixtures or any(f["status"] != "completed" for f in fixtures):
        return False
    if competition in rules.CUP_IDS:
        return len(fixtures) == rules.CUP_META[competition]["match_count"] and bool(world["season"].get("cups", {}).get(competition, {}).get("champion"))
    if competition in (rules.PREMIER, rules.SECOND):
        return len(fixtures) == 380
    return len(fixtures) == 6 and bool(world["season"].get("promotion_playoff", {}).get("winner"))


def snapshot(world, competition, *, awards=None, source="decision"):
    from football_simulator.game import world as game_world
    season = world["season"]
    fixtures = [f for f in season["fixtures"] if f["competition"] == competition]
    previous = season.get("previous") or {}
    championship = next((row for row in season.get("championships", {}).values() if row["competition_id"] == competition), None)
    return {
        "season_number": int(world["season_number"]), "competition_id": competition,
        "completed_week": max(f["week"] for f in fixtures), "source": source,
        "fixtures": [compact_fixture(f) for f in fixtures],
        "state": deepcopy(season.get("promotion_playoff", {}) if competition == rules.PLAYOFF else season.get("cups", {}).get(competition, {})),
        "league": deepcopy(season["league"]),
        "tables": deepcopy(season.get("final_tables") or game_world.league_tables(world)),
        "previous": {name: deepcopy(previous.get(name, {} if name == "honor_totals" else [])) for name in ("premier_order", "second_order", "honor_totals")},
        "awards": deepcopy(awards or {}), "championship": deepcopy(championship),
        "player_teams": {pid: player.get("team_id") for pid, player in world["players"].items()},
        "neutral_venues": deepcopy(season.get("neutral_venues") or {}),
    }


def capture_finished(world):
    if not world.get("edition_archive_version"):
        # New-rule saves still need an event-end roster even when the optional
        # full historical-edition archive is disabled.
        if world.get("awards_rule_version") == "0.3.1":
            snapshots = world["season"].setdefault("award_team_snapshots", {})
            for competition in rules.ALL_COMPETITION_IDS:
                if competition not in snapshots and finished(world, competition):
                    snapshots[competition] = {pid: player.get("team_id") for pid, player in world["players"].items()}
        return []
    from football_simulator.game import world as game_world
    archives = world.setdefault("edition_archives", {})
    captured = []
    for competition in rules.ALL_COMPETITION_IDS:
        identifier = key(world["season_number"], competition)
        if identifier in archives or not finished(world, competition):
            continue
        existing = world["season"].get("awards", {}).get("competitions", {}).get(competition)
        awards = deepcopy(existing) if existing is not None else (game_world.compute_competition_awards(world, competition_ids=[competition]).get(competition, {}) if competition != rules.PLAYOFF else {})
        archives[identifier] = snapshot(world, competition, awards=awards)
        if awards:
            world["season"].setdefault("awards", {}).setdefault("competitions", {})[competition] = deepcopy(awards)
        captured.append(identifier)
    return captured
