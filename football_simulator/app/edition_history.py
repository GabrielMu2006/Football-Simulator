"""Read-only edition views and a backed-up upgrade for older current-format saves."""
from collections import defaultdict
from copy import deepcopy

from football_simulator.game import editions, rules
from football_simulator.game.calendar import standings_from_matches
from football_simulator.persistence_v2 import store


def _fixtures(save_name, season, competition):
    rows = store.fetch_matches(save_name, season=season, competition=competition, status="completed")
    for row in rows:
        stage = row["stage"]
        row.update(round=int(stage.split("_")[-1]) if stage.startswith("swiss_") else int(rules.week_slot(row["week"]).get("league_round") or 0),
                   leg=2 if stage.endswith("_leg_2") else 1, group=None, tie_id=None)
    return rows


def _ties(fixtures):
    groups = defaultdict(list)
    for match in sorted(fixtures, key=lambda f: f["match_id"]):
        stage = match["stage"].split("_leg_")[0]
        if stage.startswith(("swiss_", "group_", "cross_group_")) or stage == "league":
            continue
        pair = tuple(sorted((match["home_team_id"], match["away_team_id"])))
        groups[(stage, pair)].append(match)
    result = defaultdict(list)
    for (stage, _pair), matches in groups.items():
        matches.sort(key=lambda f: (f.get("leg", 1), f["match_id"]))
        teams = [matches[0]["home_team_id"], matches[0]["away_team_id"]]
        tie_id = f"history:{matches[0]['match_id']}"
        scores = {team: 0 for team in teams}
        for fixture in matches:
            fixture["tie_id"] = tie_id
            for side in ("home", "away"):
                scores[fixture[f"{side}_team_id"]] += int(fixture.get(f"{side}_goals") or 0) + int(fixture.get(f"{side}_goals_et") or 0)
        winner = max(scores, key=scores.get) if len(set(scores.values())) > 1 else matches[-1].get("winner_team_id")
        tie = {"tie_id": tie_id, "stage": stage, "teams": teams, "winner": winner}
        if len(matches) == 2:
            tie.update(legs=[m["match_id"] for m in matches], aggregate=[scores[t] for t in teams])
        else:
            tie["match_id"] = matches[0]["match_id"]
        result[stage].append(tie)
    return result


def _reconstruct_state(world, season, competition, fixtures, archive):
    """Recover played ties from saved matches; never redraw or invent seeds."""
    previous = next((a for a in world.get("archives", []) if int(a["season_number"]) == season - 1), {})
    order = list(previous.get("premier_order") or []) + list(previous.get("second_order") or [])
    ranks = {tid: i for i, tid in enumerate(order, 1)}
    participants = sorted({m[side] for m in fixtures for side in ("home_team_id", "away_team_id")}, key=lambda tid: (ranks.get(tid, 999), tid))
    placements = (archive.get("cup_placements") or {}).get(competition, {})
    champion = (archive.get("cup_champions") or {}).get(competition)
    state = {"participants": participants, "champion": champion, "runner_up": next((tid for tid, rank in placements.items() if rank == 2), None),
             "placements": deepcopy(placements), "stage_done": {"groups": True, "swiss": True, "final": True}}
    ties = _ties(fixtures)
    if competition == rules.PLAYOFF:
        return {"semifinals": ties.get("promotion_semifinal", []), "final": next(iter(ties.get("promotion_final", [])), None),
                "winner": archive.get("transition", {}).get("playoff_winner")}
    if competition == "confluence":
        state["rounds"] = [{"stage": stage, "ties": ties[stage]} for stage in ("round_32", "round_16", "quarterfinal", "semifinal", "final") if ties.get(stage)]
    elif competition == "ember":
        for stage in ("wb_quarterfinal", "wb_semifinal", "wb_final", "lb_round_1", "lb_round_2", "lb_round_3", "lb_final", "grand_final"):
            state[stage] = next(iter(ties.get(stage, [])), None) if stage in ("wb_final", "lb_final", "grand_final") else ties.get(stage, [])
    else:
        state.update(quarterfinals=ties.get("quarterfinal", []), semifinals=ties.get("semifinal", []), final=next(iter(ties.get("final", [])), None))
    if competition == "aureole":
        # The four connected components are the four original groups. Their
        # first persisted fixture order is the original A/B/C/D creation order.
        components = []
        for fixture in sorted(fixtures, key=lambda f: f["match_id"]):
            if not fixture["stage"].startswith("group_"):
                continue
            pair = {fixture["home_team_id"], fixture["away_team_id"]}
            overlaps = [component for component in components if component & pair]
            for component in overlaps:
                pair |= component
                components.remove(component)
            components.append(pair)
        components.sort(key=lambda group: min(f["match_id"] for f in fixtures if f["stage"].startswith("group_") and f["home_team_id"] in group))
        state["groups"] = {letter: sorted(group) for letter, group in zip("ABCD", components)}
        state["group_standings"] = {letter: standings_from_matches(teams, [f for f in fixtures if f["stage"].startswith("group_") and f["home_team_id"] in teams], previous_rank=ranks) for letter, teams in state["groups"].items()}
        for fixture in fixtures:
            if fixture["stage"].startswith("group_"):
                fixture["group"] = next((letter for letter, teams in state["groups"].items() if fixture["home_team_id"] in teams), None)
    if competition == "ascendant":
        state["groups"] = {"D": participants[:6], "N": participants[6:]}
        matches = [f for f in fixtures if f["stage"].startswith("cross_group_")]
        state["standings"] = {group: standings_from_matches(teams, matches, previous_rank=ranks, partial=True) for group, teams in state["groups"].items()}
        state["playin"] = ties.get("play_in", [])
        state["direct"] = [r["team_id"] for r in state["standings"]["D"][:4] + state["standings"]["N"][:1]]
    if competition == "starpath":
        records = {tid: [0, 0] for tid in participants}
        opponents = defaultdict(set)
        rounds = []
        for number in range(1, 6):
            matches = sorted([f for f in fixtures if f["stage"] == f"swiss_{number}"], key=lambda f: f["match_id"])
            if not matches:
                continue
            rounds.append({"round": number, "week": matches[0]["week"], "matches": [f["match_id"] for f in matches], "pairings": [[f["home_team_id"], f["away_team_id"]] for f in matches]})
            for fixture in matches:
                home, away, winner = fixture["home_team_id"], fixture["away_team_id"], fixture["winner_team_id"]
                for tid, other in ((home, away), (away, home)):
                    records[tid][0 if tid == winner else 1] += 1
                    opponents[tid].add(other)
        bu = {tid: sum(records[other][0] - records[other][1] for other in opponents[tid]) for tid in participants}
        qualified = [tid for tid in participants if records[tid][0] == 3]
        qualified.sort(key=lambda tid: (records[tid][1], -bu[tid], ranks.get(tid, 999), tid))
        state.update(rounds=rounds, records=records, bu=bu, qualified=qualified,
                     eliminated=[tid for tid in participants if records[tid][1] == 3], knockout_seeds={tid: i for i, tid in enumerate(qualified, 1)})
    return state


def legacy_entry(world, save_name, season, competition, archive):
    fixtures = _fixtures(save_name, season, competition)
    if not fixtures:
        return None
    state = _reconstruct_state(world, season, competition, fixtures, archive)
    previous = next((a for a in world.get("archives", []) if int(a["season_number"]) == season - 1), {})
    table_key = "premier_table" if competition == rules.PREMIER else "second_table"
    league = {key: [r["team_id"] for r in archive.get(key + "_table", [])] for key in ("premier", "second")}
    return {"season_number": season, "competition_id": competition, "completed_week": max(f["week"] for f in fixtures),
            "source": "saved_results", "fixtures": [editions.compact_fixture(f) for f in fixtures], "state": state,
            "league": league, "tables": {key: archive.get(key + "_table", []) for key in ("premier", "second")},
            "previous": {k: previous.get(k, []) for k in ("premier_order", "second_order")},
            "awards": deepcopy(archive.get("awards", {}).get("competitions", {}).get(competition, {})),
            "player_teams": {}, "neutral_venues": {competition: next((f["stadium_id"] for f in fixtures if f["venue_mode"] == "neutral"), None)}}


def upgrade(world, save_name):
    result = deepcopy(world)
    result["edition_archive_version"] = editions.VERSION
    archives = result.setdefault("edition_archives", {})
    for archive in result.get("archives", []):
        season = int(archive["season_number"])
        if season == int(result["season_number"]):
            continue  # Current-season state is exact, including draw metadata.
        for comp in rules.ALL_COMPETITION_IDS:
            identifier = editions.key(season, comp)
            if identifier not in archives:
                entry = legacy_entry(result, save_name, season, comp, archive)
                if entry:
                    archives[identifier] = entry
    editions.capture_finished(result)
    return result


def display_world(world, save_name, competition, season):
    """Return an isolated edition context; never change or simulate the live save."""
    season = int(season)
    entry = world.get("edition_archives", {}).get(editions.key(season, competition))
    if entry is None and season == int(world["season_number"]):
        return world
    if entry is None:
        archive = next((a for a in world.get("archives", []) if int(a["season_number"]) == season), None)
        if archive is None:
            return None
        entry = legacy_entry(world, save_name, season, competition, archive)
        if entry is None:
            return None
    view = dict(world)
    view["season_number"] = season
    view["players"] = {pid: {**player, "team_id": entry.get("player_teams", {}).get(pid, player.get("team_id"))} for pid, player in world["players"].items()}
    fixtures = deepcopy(entry["fixtures"])
    by_id = {f["match_id"]: f for f in fixtures}
    conn = store.open_readonly(save_name)
    try:
        for fact in conn.execute("SELECT p.* FROM player_match_stats p JOIN matches m ON m.match_id=p.match_id WHERE p.season_number=? AND m.competition=?", (season, competition)):
            row = dict(fact)
            if row["match_id"] in by_id:
                by_id[row["match_id"]].setdefault("player_facts", {})[row["player_id"]] = row
    finally:
        conn.close()
    # Legacy rows have no frozen registration map; recorded match attribution
    # takes precedence over the player's current club, including winter moves.
    if not entry.get("player_teams"):
        for fixture in sorted(fixtures, key=lambda f: (f["week"], f["match_id"])):
            for pid, fact in fixture.get("player_facts", {}).items():
                if pid in view["players"]:
                    view["players"][pid]["team_id"] = fact["team_id"]
    championship = entry.get("championship")
    view["season"] = {"number": season, "week": entry["completed_week"], "fixtures": fixtures,
                      "league": deepcopy(entry["league"]), "final_tables": deepcopy(entry["tables"]), "previous": deepcopy(entry.get("previous") or {}),
                      "cups": {competition: deepcopy(entry["state"])} if competition in rules.CUP_IDS else {},
                      "promotion_playoff": deepcopy(entry["state"]) if competition == rules.PLAYOFF else {},
                      "championships": {championship["title_id"]: championship} if championship else {},
                      "neutral_venues": deepcopy(entry.get("neutral_venues") or {}),
                      "awards": {"competitions": {competition: deepcopy(entry.get("awards") or {})}},
                      "pending": {}, "finalized": True, "season_complete": True, "honor_totals": {}, "events": []}
    view["_fixture_index"] = by_id
    view["_edition_source"] = entry["source"]
    return view
