"""Reweight saved final placements without resimulating matches or annual ranks."""
from copy import deepcopy
from football_simulator.game import rules


def reweight(world: dict) -> dict:
    result = deepcopy(world)
    profile = rules.HONOR_PROFILE

    def points(comp, rank):
        if comp == rules.PREMIER:
            return rules.premier_honor_points(rank, profile)
        if comp in rules.CUP_IDS:
            return rules.honor_points_for(comp, rank, profile)
        return 0

    def update(container):
        rows = container.get("honor_rows") or []
        if not rows and (container.get("premier_table") or container.get("final_tables") or any(container.get("honor_totals", {}).values())):
            raise ValueError("缺少历史最终名次荣誉记录，未进行重算。")
        totals = {team_id: 0 for team_id in result["teams"]}
        seen = set()
        for row in rows:
            key = (row["team_id"], row["competition"])
            if key in seen:
                raise ValueError(f"重复荣誉记录：{key}")
            if row["team_id"] not in result["teams"] or row["competition"] not in (rules.PREMIER, rules.SECOND, *rules.CUP_IDS):
                raise ValueError(f"无效荣誉记录：{key}")
            if not 1 <= int(row["rank"]) <= (20 if row["competition"] in (rules.PREMIER, rules.SECOND) else int(rules.CUP_META[row["competition"]]["entrants"])):
                raise ValueError(f"无效最终名次：{key}")
            seen.add(key)
            row["points"] = points(row["competition"], int(row["rank"]))
            totals[row["team_id"]] = totals.get(row["team_id"], 0) + row["points"]
        if rows:
            container["honor_totals"] = totals
        container["honor_scoring_profile"] = profile

    for archive in result.get("archives") or []:
        update(archive)
    if result["season"].get("finalized"):
        update(result["season"])
    result["honor_scoring_profile"] = profile
    previous = result["season"].get("previous") or {}
    number = int(result["season_number"]) - 1
    archive = next((a for a in result.get("archives", []) if int(a["season_number"]) == number), None)
    if previous.get("honor_rows"):
        update(previous)
    elif archive and "honor_totals" in previous:
        previous["honor_totals"] = dict(archive.get("honor_totals") or {})
    return result
