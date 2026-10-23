"""GFE-120: read-only bracket read model built from stored ties and results.

The model never re-computes the competition format. Every node comes from a tie
that already exists in the saved state, and a connector is drawn only when the
winning team of one tie actually appears in the next tie. Competitions whose
rules redraw each round (Destiny Cup) therefore show completed rounds plus an
explicit "待抽签" placeholder instead of a speculative path.
"""

from __future__ import annotations

from typing import List, Optional

REDRAW = {"confluence"}

STAGE_SEQUENCE = {
    "confluence": ("round_32", "round_16", "quarterfinal", "semifinal", "final"),
}

STAGE_LABELS = {
    "round_32": "32强",
    "round_16": "16强",
    "quarterfinal": "八强",
    "semifinal": "半决赛",
    "final": "决赛",
}


def _node(world: dict, tie: dict) -> dict:
    teams = []
    team_ids = tie.get("team_ids") or []
    names = tie.get("teams") or []
    legs = tie.get("legs") or []
    single = tie.get("match")
    scores = {}
    if legs:
        for leg in legs:
            if leg.get("status") != "completed":
                continue
            for side in ("home", "away"):
                team_id = leg.get(f"{side}_team_id")
                if not team_id:
                    continue
                goals = int(leg.get(f"{side}_goals") or 0) + int(leg.get(f"{side}_goals_et") or 0)
                scores[team_id] = scores.get(team_id, 0) + goals
        score_text = "总比分 " + " – ".join(f"{scores.get(team_id, 0)}" for team_id in team_ids) if scores else ""
        match_ids = [leg.get("match_id") for leg in legs if leg.get("match_id")]
        completed = all(leg.get("status") == "completed" for leg in legs)
        status_text = "已完赛" if completed else (f"已完成 {sum(1 for leg in legs if leg.get('status') == 'completed')}/{len(legs)} 回合")
        last = legs[-1]
        if completed and last.get("home_pk") is not None:
            penalties = {last.get("home_team_id"): last["home_pk"], last.get("away_team_id"): last["away_pk"]}
            status_text = "点球 " + "–".join(str(penalties.get(team_id, "—")) for team_id in team_ids)
        elif completed and any(leg.get("decided_by") == "extra_time" for leg in legs):
            status_text = "加时"
    elif single:
        team_id = single.get("home_team_id")
        scores = {
            single.get("home_team_id"): int(single.get("total_home") or 0),
            single.get("away_team_id"): int(single.get("total_away") or 0),
        } if single.get("status") == "completed" else {}
        score_text = single.get("score") or "vs"
        match_ids = [single.get("match_id")] if single.get("match_id") else []
        status_text = single.get("status_text") or "未赛"
        if single.get("home_pk") is not None:
            status_text = f"点球 {single['home_pk']}–{single['away_pk']}"
    else:
        score_text = ""
        match_ids = []
        status_text = "待定"

    for index, team_id in enumerate(team_ids):
        teams.append(
            {
                "team_id": team_id,
                "name": names[index] if index < len(names) else ((world.get("teams") or {}).get(team_id) or {}).get("club_en", "待定"),
                "score": scores.get(team_id),
                "winner": bool(tie.get("winner_id") == team_id and tie.get("winner_id")),
            }
        )
    if not teams:
        teams = [{"team_id": None, "name": "待定", "score": None, "winner": False} for _ in range(2)]
    return {
        "tie_id": tie.get("tie_id"),
        "teams": teams,
        "team_ids": team_ids,
        "score_text": score_text,
        "status_text": status_text,
        "winner_id": tie.get("winner_id"),
        "winner": tie.get("winner"),
        "two_leg": bool(tie.get("two_leg")),
        "match_ids": match_ids,
        "legs": [
            {
                "match_id": leg.get("match_id"),
                "label": f"第 {leg.get('leg')} 回合" if leg.get("leg") else "回合",
                "score": leg.get("score"),
                "week": leg.get("week"),
                "venue": leg.get("venue"),
                "venue_mode": leg.get("venue_mode"),
                "completed": leg.get("status") == "completed",
                "home_team_id": leg.get("home_team_id"),
                "away_team_id": leg.get("away_team_id"),
                "scores": {
                    leg.get(f"{side}_team_id"): int(leg.get(f"{side}_goals") or 0) + int(leg.get(f"{side}_goals_et") or 0)
                    for side in ("home", "away")
                } if leg.get("status") == "completed" else {},
            }
            for leg in legs
        ],
        "venues": [
            (f"{'首' if index == 0 else '次'}回合 · " if legs else "")
            + ("中立场 · " if fixture.get("venue_mode") == "neutral" else "")
            + str(fixture.get("venue") or "").split("（")[0]
            for index, fixture in enumerate(legs or ([single] if single else [])) if fixture.get("venue")
        ],
        "pending": not match_ids and not team_ids,
    }


def _links(columns: List[dict]) -> List[dict]:
    """Connect rounds only where the previous winner really appears next."""
    links = []
    for left_index in range(len(columns) - 1):
        left = columns[left_index]["nodes"]
        right = columns[left_index + 1]["nodes"]
        for source in left:
            winner = source.get("winner_id")
            if not winner:
                continue
            for target in right:
                if winner in (target.get("team_ids") or []):
                    links.append({"from": source["tie_id"], "to": target["tie_id"], "team_id": winner})
    return links


def _stage_of_ties(world: dict, competition: str) -> dict:
    """tie_id -> stored stage key, without parsing Chinese labels or team names."""
    state = ((world.get("season") or {}).get("cups") or {}).get(competition) or {}
    mapping = {}
    for key, value in state.items():
        items = value if isinstance(value, list) else [value]
        for tie in items:
            if isinstance(tie, dict) and tie.get("tie_id"):
                mapping.setdefault(tie["tie_id"], "final" if competition in REDRAW and key == "final_tie" else key)
            elif isinstance(tie, dict) and tie.get("stage"):
                for nested in tie.get("ties") or []:
                    if isinstance(nested, dict) and nested.get("tie_id"):
                        mapping[nested["tie_id"]] = tie["stage"]
    return mapping


def cup_bracket(world: dict, competition: str, detail: dict) -> Optional[dict]:
    if detail.get("kind") != "cup":
        return None
    knockout = detail.get("knockout") or []
    bracket_rounds = detail.get("bracket_rounds") or []
    if competition == "ember":
        return _double_elimination(world, detail, bracket_rounds)
    if not knockout:
        return None
    stage_map = _stage_of_ties(world, competition)
    columns = []
    for index, round_data in enumerate(knockout):
        ties = round_data.get("ties") or []
        nodes = [_node(world, tie) for tie in ties]
        if not nodes:
            continue
        stage = stage_map.get((ties[0] or {}).get("tie_id")) if ties else None
        columns.append({"key": stage or f"round-{index}", "label": round_data.get("label") or STAGE_LABELS.get("", ""), "nodes": nodes})
    if not columns:
        return None
    pending = None
    if competition in REDRAW:
        pending = _redraw_pending(world, competition, detail, columns)
    return {
        "mode": "columns",
        "competition": competition,
        "columns": columns,
        "links": _links(columns),
        "pending": pending,
        "completed": bool(detail.get("champion_id")),
        "champion": detail.get("champion"),
        "champion_id": detail.get("champion_id"),
    }


def _redraw_pending(world: dict, competition: str, detail: dict, columns: List[dict]) -> Optional[str]:
    """Name the next redraw step without inventing opponents."""
    stages = STAGE_SEQUENCE.get(competition, ())
    stage_of_label = {STAGE_LABELS[key]: key for key in stages}
    reached = {stage_of_label.get(column["label"]) for column in columns}
    reached.discard(None)
    last_state = detail.get("current_stage") or ""
    for stage in stages:
        if stage in reached:
            continue
        if _stage_started(world, competition, stage):
            continue
        return f"{STAGE_LABELS.get(stage, stage)}待抽签"
    if last_state and not detail.get("champion_id"):
        return f"{last_state}进行中"
    return None


def _stage_started(world: dict, competition: str, stage: str) -> bool:
    for fixture in world["season"].get("fixtures", []):
        if fixture.get("competition") != competition:
            continue
        fixture_stage = str(fixture.get("stage") or "").rsplit("_leg_", 1)[0]
        if fixture_stage == stage:
            return True
    return False


EMBER_ROUNDS = (
    ("wb_quarterfinal", "胜者组首轮", "wb"),
    ("wb_semifinal", "胜者组次轮", "wb"),
    ("wb_final", "胜者组决赛", "wb"),
    ("lb_round_1", "败者组首轮", "lb"),
    ("lb_round_2", "败者组次轮", "lb"),
    ("lb_round_3", "败者组第三轮", "lb"),
    ("lb_final", "败者组决赛", "lb"),
    ("grand_final", "总决赛", "gf"),
)

EMBER_GROUPS = (("wb", "胜者组"), ("lb", "败者组"), ("gf", "总决赛"))

# (source stage, source index, outcome, target stage, target index, target slot)
# Mirrors game/competitions.py:progress_ember exactly; read-only, no format change.
EMBER_EDGES = (
    (("wb_quarterfinal", 0, "winner", "wb_semifinal", 0, 0),
     ("wb_quarterfinal", 1, "winner", "wb_semifinal", 0, 1),
     ("wb_quarterfinal", 2, "winner", "wb_semifinal", 1, 0),
     ("wb_quarterfinal", 3, "winner", "wb_semifinal", 1, 1)),
    (("wb_quarterfinal", 0, "loser", "lb_round_1", 0, 0),
     ("wb_quarterfinal", 1, "loser", "lb_round_1", 0, 1),
     ("wb_quarterfinal", 2, "loser", "lb_round_1", 1, 0),
     ("wb_quarterfinal", 3, "loser", "lb_round_1", 1, 1)),
    (("wb_semifinal", 0, "winner", "wb_final", 0, 0),
     ("wb_semifinal", 1, "winner", "wb_final", 0, 1)),
    (("wb_semifinal", 1, "loser", "lb_round_2", 0, 1),
     ("wb_semifinal", 0, "loser", "lb_round_2", 1, 1)),
    (("lb_round_1", 0, "winner", "lb_round_2", 0, 0),
     ("lb_round_1", 1, "winner", "lb_round_2", 1, 0)),
    (("lb_round_2", 0, "winner", "lb_round_3", 0, 0),
     ("lb_round_2", 1, "winner", "lb_round_3", 0, 1)),
    (("lb_round_3", 0, "winner", "lb_final", 0, 0),),
    (("wb_final", 0, "loser", "lb_final", 0, 1),),
    (("wb_final", 0, "winner", "grand_final", 0, 0),),
    (("lb_final", 0, "winner", "grand_final", 0, 1),),
)

EMBER_NEXT = {
    "wb_quarterfinal": "胜者进入胜者组次轮，败者进入败者组首轮",
    "wb_semifinal": "胜者进入胜者组决赛，败者落入败者组次轮",
    "wb_final": "胜者进入总决赛，败者进入败者组决赛",
    "lb_round_1": "胜者进入败者组次轮",
    "lb_round_2": "胜者进入败者组第三轮",
    "lb_round_3": "胜者进入败者组决赛",
    "lb_final": "胜者进入总决赛",
    "grand_final": "单场决出冠军，不设重置赛",
}


def _ember_state(world: dict) -> dict:
    return ((world.get("season") or {}).get("cups") or {}).get("ember") or {}


def _stage_ties(state: dict, stage: str) -> List[dict]:
    value = state.get(stage)
    if value is None:
        return []
    if isinstance(value, list):
        return [tie for tie in value if tie]
    return [value]


def _tie_loser(tie: dict) -> Optional[str]:
    teams = tie.get("teams") or []
    return next((team for team in teams if team != tie.get("winner")), None)


def _ember_edges(state: dict) -> List[dict]:
    """Every winner/loser placement that the saved bracket already implies."""
    edges = []
    for group in EMBER_EDGES:
        for source_stage, source_index, outcome, target_stage, target_index, target_slot in group:
            sources = _stage_ties(state, source_stage)
            targets = _stage_ties(state, target_stage)
            if source_index >= len(sources) or target_index >= len(targets):
                continue
            source = sources[source_index]
            target = targets[target_index]
            team_id = source.get("winner") if outcome == "winner" else _tie_loser(source)
            if not team_id:
                continue
            edges.append({
                "source_tie_id": source.get("tie_id"),
                "source_stage": source_stage,
                "outcome": outcome,
                "target_tie_id": target.get("tie_id"),
                "target_stage": target_stage,
                "target_slot": target_slot,
                "team_id": team_id,
            })
    return edges


def _ember_path_text(
    direction: str,
    stage_label: str,
    outcome: Optional[str],
    slot: int,
    index: Optional[int] = None,
) -> str:
    side = "胜者" if outcome == "winner" else "败者"
    where = "来源" if direction == "in" else "下一站"
    place = stage_label if index is None else f"{stage_label} 第 {index + 1} 场"
    if direction == "in" and outcome:
        return f"{where}：{place} {side}"
    if direction == "out" and outcome:
        return f"{where}：{place} · {side}"
    return f"{where}：{place}"


def _double_elimination(world: dict, detail: dict, rounds: List[dict]) -> Optional[dict]:
    """Winner/loser/grand-final groups with real placement edges.

    Nodes come from stored ties only; edges come from the saved stage lists and
    the fixed progression above, so a path never relies on matching team names
    or on "both nodes contain the same team" (double elimination rematches).
    """
    state = _ember_state(world)
    stage_of = {}
    for stage, _label, _group in EMBER_ROUNDS:
        for tie in _stage_ties(state, stage):
            stage_of[tie.get("tie_id")] = stage
    label_of = {stage: label for stage, label, _group in EMBER_ROUNDS}
    group_of = {stage: group for stage, _label, group in EMBER_ROUNDS}

    ties = {}
    for round_data in rounds:
        for tie in round_data.get("ties") or []:
            stage = stage_of.get(tie.get("tie_id"))
            if not stage:
                continue
            ties.setdefault(stage, []).append(tie)

    nodes_by_stage = {}
    for stage, label, group in EMBER_ROUNDS:
        stage_ties = ties.get(stage) or []
        if not stage_ties:
            continue
        stage_ties.sort(key=lambda tie: str(tie.get("tie_id")))
        nodes = []
        for index, tie in enumerate(stage_ties):
            node = _node(world, tie)
            node.update({
                "stage": stage,
                "stage_label": label,
                "group": group,
                "index": index,
                "title": f"{label} 第 {index + 1} 场",
                "paths_in": [],
                "paths_out": [],
                "next_note": EMBER_NEXT.get(stage, ""),
            })
            nodes.append(node)
        nodes_by_stage[stage] = nodes

    edges = _ember_edges(state)
    links = []
    for edge in edges:
        source = next((node for node in nodes_by_stage.get(edge["source_stage"], []) if node["tie_id"] == edge["source_tie_id"]), None)
        target = next((node for node in nodes_by_stage.get(edge["target_stage"], []) if node["tie_id"] == edge["target_tie_id"]), None)
        if source is None or target is None:
            continue
        links.append({"from": source["tie_id"], "to": target["tie_id"], "team_id": edge["team_id"]})
        source["paths_out"].append({
            "direction": "out",
            "tie_id": target["tie_id"],
            "group": group_of[edge["target_stage"]],
            "stage": edge["target_stage"],
            "stage_label": label_of[edge["target_stage"]],
            "outcome": edge["outcome"],
            "slot": edge["target_slot"],
            "text": _ember_path_text(
                    "out", label_of[edge["target_stage"]], edge["outcome"], edge["target_slot"],
                    next((node["index"] for node in nodes_by_stage.get(edge["target_stage"], []) if node["tie_id"] == edge["target_tie_id"]), None),
                ),
        })
        target["paths_in"].append({
            "direction": "in",
            "tie_id": source["tie_id"],
            "group": group_of[edge["source_stage"]],
            "stage": edge["source_stage"],
            "stage_label": label_of[edge["source_stage"]],
            "outcome": edge["outcome"],
            "slot": edge["target_slot"],
            "text": _ember_path_text(
                    "in", label_of[edge["source_stage"]], edge["outcome"], edge["target_slot"],
                    next((node["index"] for node in nodes_by_stage.get(edge["source_stage"], []) if node["tie_id"] == edge["source_tie_id"]), None),
                ),
        })

    groups = []
    for key, label in EMBER_GROUPS:
        group_rounds = [
            {"key": stage, "label": label_of[stage], "nodes": nodes_by_stage[stage]}
            for stage, _label, group in EMBER_ROUNDS
            if group == key and nodes_by_stage.get(stage)
        ]
        groups.append({"key": key, "label": label, "rounds": group_rounds})

    created = [stage for stage, _label, _group in EMBER_ROUNDS if nodes_by_stage.get(stage)]
    if not created:
        return None
    if detail.get("champion_id"):
        current_group = "gf"
    else:
        current_group = group_of[created[-1]]

    return {
        "mode": "groups",
        "competition": "ember",
        "groups": groups,
        "links": links,
        "edges": edges,
        "current_group": current_group,
        "loss_paths": detail.get("loss_paths") or [],
        "completed": bool(detail.get("champion_id")),
        "champion": detail.get("champion"),
        "champion_id": detail.get("champion_id"),
        "no_reset": True,
    }


def playoff_bracket(world: dict, detail: dict) -> Optional[dict]:
    rounds = detail.get("rounds") or []
    if not rounds:
        return None
    stage_map = _stage_of_ties(world, "playoff")
    columns = []
    for index, round_data in enumerate(rounds):
        ties = round_data.get("ties") or []
        nodes = [_node(world, tie) for tie in ties]
        if nodes:
            stage = stage_map.get((ties[0] or {}).get("tie_id")) if ties else None
            columns.append({"key": stage or f"round-{index}", "label": round_data.get("label") or "", "nodes": nodes})
    if not columns:
        return None
    return {
        "mode": "columns",
        "competition": "playoff",
        "columns": columns,
        "links": _links(columns),
        "pending": None,
        "completed": bool(detail.get("winner")),
        "champion": detail.get("winner"),
        "champion_id": None,
    }


def bracket_for(world: dict, competition: str, detail: dict) -> Optional[dict]:
    if competition == "playoff":
        return playoff_bracket(world, detail)
    return cup_bracket(world, competition, detail)
