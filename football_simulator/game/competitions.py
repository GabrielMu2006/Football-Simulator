"""六项杯赛的纯领域实现（抽签、瑞士轮、双败、突围、种子与荣誉）。

所有抽签只在新赛季初始化或上一阶段完成时的写命令里执行一次，并把结果
（参赛者、种子、签表、对阵、场地、原因）写入世界状态。读取页面只读这些
持久化事实。

关键合同：
- 赛事使用稳定 ID（aureole/confluence/starpath/ember/ascendant/zenith）；
- 90 分钟打平且必须决胜 → 加时 → 点球；两回合只在次回合总比分平局时加时；
- 普通单场淘汰非决赛由上季联赛排名较高者主场；决赛中立场无主场加成；
- 中立场可以在参赛队自己的球场，但场地模式仍是 neutral。
"""

from __future__ import annotations

import random
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from football_simulator.game import rules
from football_simulator.game.calendar import (
    add_match,
    completed_matches,
    get_fixture,
    standings_from_matches,
)

GROUP_LETTERS = ("A", "B", "C", "D")


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def previous_ranks(world: dict) -> Dict[str, int]:
    """上季总顺序 1..40（一级 1–20，再接次级 1–20）。缺失时按当前分区。"""
    previous = world["season"].get("previous") or {}
    order = list(previous.get("premier_order", [])) + list(previous.get("second_order", []))
    rank = {team_id: index + 1 for index, team_id in enumerate(order)}
    if len(rank) < 40:
        fallback = [
            *[team_id for team_id, team in world["teams"].items() if team["division"] == "一级联赛"],
            *[team_id for team_id, team in world["teams"].items() if team["division"] == "次级联赛"],
        ]
        for index, team_id in enumerate(fallback):
            rank.setdefault(team_id, index + 1)
    return rank


def previous_order(world: dict) -> List[str]:
    previous = world["season"].get("previous") or {}
    return list(previous.get("premier_order", [])) + list(previous.get("second_order", []))


def previous_honor_totals(world: dict) -> Dict[str, int]:
    previous = world["season"].get("previous") or {}
    return dict(previous.get("honor_totals", {}))


def _next_id(world: dict, key: str) -> int:
    value = int(world["season"].get(key, 0)) + 1
    world["season"][key] = value
    return value


def _tie_id(world: dict, competition: str, stage: str) -> str:
    number = _next_id(world, "tie_seq")
    return f"tie-{competition}-{stage}-{number:03d}"


def _rank(rank: Dict[str, int], team_id: str) -> int:
    return int(rank.get(team_id, 999))


def _better(rank: Dict[str, int], first: str, second: str) -> Tuple[str, str]:
    if _rank(rank, first) <= _rank(rank, second):
        return first, second
    return second, first


def create_two_leg_tie(
    world: dict,
    competition: str,
    stage: str,
    *,
    teams: Tuple[str, str],
    weeks: Tuple[int, int],
    rank: Dict[str, int],
) -> dict:
    higher, lower = _better(rank, teams[0], teams[1])
    tie_id = _tie_id(world, competition, stage)
    first_leg = add_match(
        world,
        competition=competition,
        stage=f"{stage}_leg_1",
        week=weeks[0],
        home_team_id=lower,
        away_team_id=higher,
        leg=1,
        tie_id=tie_id,
        need_winner=False,
    )
    second_leg = add_match(
        world,
        competition=competition,
        stage=f"{stage}_leg_2",
        week=weeks[1],
        home_team_id=higher,
        away_team_id=lower,
        leg=2,
        tie_id=tie_id,
        need_winner=True,
        aggregate_from_tie=True,
    )
    return {
        "tie_id": tie_id,
        "stage": stage,
        "teams": [teams[0], teams[1]],
        "higher": higher,
        "lower": lower,
        "legs": [first_leg, second_leg],
        "winner": None,
        "aggregate": None,
    }


def create_single_tie(
    world: dict,
    competition: str,
    stage: str,
    *,
    teams: Tuple[str, str],
    week: int,
    rank: Dict[str, int],
    neutral_stadium: Optional[str] = None,
    neutral_reason: Optional[str] = None,
    group: Optional[str] = None,
) -> dict:
    if neutral_stadium:
        home, away = teams
        venue_mode = "neutral"
        stadium_id = neutral_stadium
    else:
        home, away = _better(rank, teams[0], teams[1])
        venue_mode = "home"
        stadium_id = None
    tie_id = _tie_id(world, competition, stage)
    match_id = add_match(
        world,
        competition=competition,
        stage=stage,
        week=week,
        home_team_id=home,
        away_team_id=away,
        need_winner=True,
        venue_mode=venue_mode,
        stadium_id=stadium_id,
        neutral_reason=neutral_reason,
        group=group,
    )
    return {
        "tie_id": tie_id,
        "stage": stage,
        "teams": [home, away],
        "home": home,
        "away": away,
        "match_id": match_id,
        "winner": None,
    }


def resolve_single_tie(world: dict, tie: dict) -> Optional[str]:
    if tie.get("winner"):
        return tie["winner"]
    match = get_fixture(world, tie["match_id"])
    if match["status"] != "completed":
        return None
    tie["winner"] = match["winner_team_id"]
    return tie["winner"]


def resolve_two_leg_tie(world: dict, tie: dict) -> Optional[str]:
    if tie.get("winner"):
        return tie["winner"]
    legs = [get_fixture(world, match_id) for match_id in tie["legs"]]
    if any(leg["status"] != "completed" for leg in legs):
        return None
    totals = {tie["teams"][0]: 0, tie["teams"][1]: 0}
    for leg in legs:
        totals[leg["home_team_id"]] += leg["home_goals"] + leg.get("home_goals_et", 0)
        totals[leg["away_team_id"]] += leg["away_goals"] + leg.get("away_goals_et", 0)
    tie["aggregate"] = [totals[tie["teams"][0]], totals[tie["teams"][1]]]
    if totals[tie["teams"][0]] != totals[tie["teams"][1]]:
        tie["winner"] = (
            tie["teams"][0] if totals[tie["teams"][0]] > totals[tie["teams"][1]] else tie["teams"][1]
        )
    else:
        tie["winner"] = legs[1]["winner_team_id"]
    return tie["winner"]


def _play_week(world: dict, cup_id: str) -> None:
    # 进度函数只使用已落盘结果，不在这里抽签或模拟。
    pass


# ---------------------------------------------------------------------------
# 杯赛 1：君临杯（16 队四组双循环 → 两回合八强/半决赛/决赛）
# ---------------------------------------------------------------------------


def initialize_aureole(world: dict, participants: List[str], rank: Dict[str, int], rng: random.Random) -> dict:
    pots = [participants[0:4], participants[4:8], participants[8:12], participants[12:16]]
    groups = {letter: [] for letter in GROUP_LETTERS}
    for pot in pots:
        order = list(pot)
        rng.shuffle(order)
        for index, letter in enumerate(GROUP_LETTERS):
            groups[letter].append(order[index])

    weeks = [5, 8, 12, 15, 20, 23]
    group_fixtures = []
    for letter in GROUP_LETTERS:
        teams = groups[letter]
        first_rounds = _round_robin_four(teams)
        schedule = first_rounds + [[(away, home) for home, away in pairs] for pairs in first_rounds]
        for round_index, pairs in enumerate(schedule):
            for home, away in pairs:
                match_id = add_match(
                    world,
                    competition="aureole",
                    stage=f"group_{round_index + 1}",
                    week=weeks[round_index],
                    home_team_id=home,
                    away_team_id=away,
                    round_number=round_index + 1,
                    group=letter,
                    need_winner=False,
                )
                group_fixtures.append(match_id)
    return {
        "active": True,
        "participants": participants,
        "groups": groups,
        "group_fixtures": group_fixtures,
        "group_standings": {},
        "quarterfinals": [],
        "semifinals": [],
        "final": None,
        "placements": {},
        "champion": None,
        "runner_up": None,
        "stage_done": {},
    }


def _round_robin_four(teams: Sequence[str]) -> List[List[Tuple[str, str]]]:
    a, b, c, d = teams
    return [
        [(a, d), (b, c)],
        [(a, c), (d, b)],
        [(a, b), (c, d)],
    ]


def progress_aureole(world: dict, state: dict, week: int, rng: random.Random) -> None:
    rank = previous_ranks(world)
    if week == 23 and not state["stage_done"].get("groups"):
        matches = [
            match
            for match in completed_matches(world, "aureole")
            if match["stage"].startswith("group_")
        ]
        standings = {}
        for letter, teams in state["groups"].items():
            group_matches = [match for match in matches if match.get("group") == letter]
            standings[letter] = standings_from_matches(teams, group_matches, previous_rank=rank)
        state["group_standings"] = standings
        winners = [
            {"team_id": standings[letter][0]["team_id"], "group": letter}
            for letter in GROUP_LETTERS
        ]
        runners = [
            {"team_id": standings[letter][1]["team_id"], "group": letter}
            for letter in GROUP_LETTERS
        ]
        rng.shuffle(runners)
        assignment: List[dict] = []

        def assign(index: int) -> bool:
            if index == len(winners):
                return True
            for runner_index, runner in enumerate(runners):
                if runner in assignment or runner["group"] == winners[index]["group"]:
                    continue
                assignment.append(runner)
                if assign(index + 1):
                    return True
                assignment.pop()
            return False

        if not assign(0):
            # 4 组 4 个异组第二必然存在完美匹配；走到这里说明状态异常。
            raise RuntimeError("君临杯八强抽签无法回避同组对手。")
        ties = []
        for winner, runner in zip(winners, assignment):
            ties.append(
                create_two_leg_tie(
                    world,
                    "aureole",
                    "quarterfinal",
                    teams=(winner["team_id"], runner["team_id"]),
                    weeks=(31, 32),
                    rank=rank,
                )
            )
        state["quarterfinals"] = ties
        state["qf_groups"] = {
            ties[index]["tie_id"]: [winners[index]["group"], assignment[index]["group"]]
            for index in range(4)
        }
        state["stage_done"]["groups"] = True

    if week == 32 and state["quarterfinals"] and not state["stage_done"].get("quarterfinal"):
        for tie in state["quarterfinals"]:
            resolve_two_leg_tie(world, tie)
        if all(tie["winner"] for tie in state["quarterfinals"]):
            winners = [tie["winner"] for tie in state["quarterfinals"]]
            state["semifinals"] = [
                create_two_leg_tie(
                    world,
                    "aureole",
                    "semifinal",
                    teams=(winners[0], winners[1]),
                    weeks=(38, 39),
                    rank=rank,
                ),
                create_two_leg_tie(
                    world,
                    "aureole",
                    "semifinal",
                    teams=(winners[2], winners[3]),
                    weeks=(38, 39),
                    rank=rank,
                ),
            ]
            state["stage_done"]["quarterfinal"] = True

    if week == 39 and state["semifinals"] and not state["stage_done"].get("semifinal"):
        for tie in state["semifinals"]:
            resolve_two_leg_tie(world, tie)
        if all(tie["winner"] for tie in state["semifinals"]):
            state["final"] = create_two_leg_tie(
                world,
                "aureole",
                "final",
                teams=(state["semifinals"][0]["winner"], state["semifinals"][1]["winner"]),
                weeks=(46, 47),
                rank=rank,
            )
            state["stage_done"]["semifinal"] = True

    if week == 47 and state["final"] and not state["stage_done"].get("final"):
        resolve_two_leg_tie(world, state["final"])
        if state["final"]["winner"]:
            state["champion"] = state["final"]["winner"]
            state["runner_up"] = (
                state["final"]["teams"][1]
                if state["final"]["winner"] == state["final"]["teams"][0]
                else state["final"]["teams"][0]
            )
            placements = {state["champion"]: 1, state["runner_up"]: 2}
            for loser in _losers(state["semifinals"]):
                placements[loser] = 3
            for loser in _losers(state["quarterfinals"]):
                placements[loser] = 5
            for letter in GROUP_LETTERS:
                rows = state["group_standings"][letter]
                placements[rows[2]["team_id"]] = 9
                placements[rows[3]["team_id"]] = 13
            state["placements"] = placements
            state["stage_done"]["final"] = True


# ---------------------------------------------------------------------------
# 杯赛 2：命途杯（32 队单场淘汰，每轮重抽）
# ---------------------------------------------------------------------------


def initialize_confluence(world: dict, participants: List[str], rank: Dict[str, int], rng: random.Random) -> dict:
    shuffled = list(participants)
    rng.shuffle(shuffled)
    ties = []
    for index in range(0, len(shuffled), 2):
        ties.append(
            create_single_tie(
                world,
                "confluence",
                "round_32",
                teams=(shuffled[index], shuffled[index + 1]),
                week=6,
                rank=rank,
            )
        )
    return {
        "active": True,
        "participants": participants,
        "rounds": [{"stage": "round_32", "week": 6, "ties": ties}],
        "champion": None,
        "runner_up": None,
        "placements": {},
        "stage_done": {},
    }


def progress_confluence(world: dict, state: dict, week: int, rng: random.Random) -> None:
    rank = previous_ranks(world)
    schedule = [
        ("round_32", 6, "round_16", 11),
        ("round_16", 11, "quarterfinal", 17),
        ("quarterfinal", 17, "semifinal", 28),
        ("semifinal", 28, "final", 30),
    ]
    for current_stage, current_week, next_stage, next_week in schedule:
        if week != current_week:
            continue
        current = next((item for item in state["rounds"] if item["stage"] == current_stage), None)
        if current is None:
            continue
        for tie in current["ties"]:
            resolve_single_tie(world, tie)
        if any(not tie["winner"] for tie in current["ties"]):
            continue
        winners = [tie["winner"] for tie in current["ties"]]
        if next_stage == "final":
            if state.get("final_tie"):
                continue
            stadium = world["season"]["neutral_venues"]["confluence"]
            final_tie = create_single_tie(
                world,
                "confluence",
                "final",
                teams=(winners[0], winners[1]),
                week=30,
                rank=rank,
                neutral_stadium=stadium,
                neutral_reason="决赛：赛季初始化抽定的中立场地，双方无主场加成",
            )
            state["final_tie"] = final_tie
            state["rounds"].append({"stage": "final", "week": 30, "ties": [final_tie]})
            continue
        rng.shuffle(winners)
        ties = []
        for index in range(0, len(winners), 2):
            ties.append(
                create_single_tie(
                    world,
                    "confluence",
                    next_stage,
                    teams=(winners[index], winners[index + 1]),
                    week=next_week,
                    rank=rank,
                )
            )
        state["rounds"].append({"stage": next_stage, "week": next_week, "ties": ties})

    final_round = next((item for item in state["rounds"] if item["stage"] == "final"), None)
    if final_round and week == 30 and not state["stage_done"].get("final"):
        tie = final_round["ties"][0]
        resolve_single_tie(world, tie)
        if tie["winner"]:
            state["champion"] = tie["winner"]
            state["runner_up"] = tie["teams"][1] if tie["winner"] == tie["teams"][0] else tie["teams"][0]
            state["placements"] = _placements_from_rounds(
                state["rounds"],
                rank_order=[
                    ("round_32", 17),
                    ("round_16", 9),
                    ("quarterfinal", 5),
                    ("semifinal", 3),
                    ("final", 1),
                ],
                champion=state["champion"],
                runner_up=state["runner_up"],
            )
            state["stage_done"]["final"] = True


def _placements_from_rounds(rounds: List[dict], rank_order: List[Tuple[str, int]], champion: str, runner_up: str) -> Dict[str, int]:
    placements = {champion: 1, runner_up: 2}
    for stage, rank_start in rank_order:
        if stage in ("final",):
            continue
        round_data = next((item for item in rounds if item["stage"] == stage), None)
        if not round_data:
            continue
        for tie in round_data["ties"]:
            loser = tie["teams"][1] if tie.get("winner") == tie["teams"][0] else tie["teams"][0]
            placements.setdefault(loser, rank_start)
    return placements


# ---------------------------------------------------------------------------
# 杯赛 3：极境杯（CS 式 BU 瑞士轮 + 两回合八强/半决赛 + 中立决赛）
# ---------------------------------------------------------------------------


def initialize_starpath(world: dict, participants: List[str], rank: Dict[str, int], rng: random.Random) -> dict:
    seeds = {team_id: index + 1 for index, team_id in enumerate(participants)}
    pairs = [(participants[index], participants[index + 8]) for index in range(8)]
    state = {
        "active": True,
        "participants": participants,
        "seeds": seeds,
        "records": {team_id: [0, 0] for team_id in participants},
        "opponents": {team_id: [] for team_id in participants},
        "bu": {team_id: 0 for team_id in participants},
        "rounds": [],
        "qualified": [],
        "eliminated": [],
        "knockout_seeds": {},
        "quarterfinals": [],
        "semifinals": [],
        "final": None,
        "placements": {},
        "champion": None,
        "runner_up": None,
        "stage_done": {},
    }
    _add_swiss_round(world, state, round_number=1, week=9, pairs=pairs, rank=rank)
    return state


def _add_swiss_round(world: dict, state: dict, *, round_number: int, week: int, pairs: List[Tuple[str, str]], rank: Dict[str, int]) -> None:
    matches = []
    home_by_match = []
    for first, second in pairs:
        home, away = _better(rank, first, second)
        match_id = add_match(
            world,
            competition="starpath",
            stage=f"swiss_{round_number}",
            week=week,
            home_team_id=home,
            away_team_id=away,
            round_number=round_number,
            need_winner=True,
        )
        matches.append(match_id)
        home_by_match.append((home, away))
    state["rounds"].append(
        {
            "round": round_number,
            "week": week,
            "pairings": [[home, away] for home, away in home_by_match],
            "matches": matches,
            "rematch_reason": None,
        }
    )


def progress_starpath(world: dict, state: dict, week: int, rng: random.Random) -> None:
    rank = previous_ranks(world)
    rounds_by_week = {9: 1, 14: 2, 18: 3, 21: 4, 24: 5}
    if week in rounds_by_week:
        round_number = rounds_by_week[week]
        round_data = next((item for item in state["rounds"] if item["round"] == round_number), None)
        if round_data and not round_data.get("completed"):
            _update_swiss_records(world, state, round_data)
            round_data["completed"] = True
            if round_number < 5:
                next_week = {1: 14, 2: 18, 3: 21, 4: 24}[round_number]
                _create_next_swiss_round(world, state, round_number + 1, next_week, rank)
            else:
                _create_starpath_knockout(world, state, rank)
    if week == 34 and state["quarterfinals"] and not state["stage_done"].get("quarterfinal"):
        for tie in state["quarterfinals"]:
            resolve_two_leg_tie(world, tie)
        if all(tie["winner"] for tie in state["quarterfinals"]):
            winners = [tie["winner"] for tie in state["quarterfinals"]]
            state["semifinals"] = [
                create_two_leg_tie(
                    world, "starpath", "semifinal",
                    teams=(winners[0], winners[1]), weeks=(41, 42), rank=rank,
                ),
                create_two_leg_tie(
                    world, "starpath", "semifinal",
                    teams=(winners[2], winners[3]), weeks=(41, 42), rank=rank,
                ),
            ]
            state["stage_done"]["quarterfinal"] = True
    if week == 42 and state["semifinals"] and not state["stage_done"].get("semifinal"):
        for tie in state["semifinals"]:
            resolve_two_leg_tie(world, tie)
        if all(tie["winner"] for tie in state["semifinals"]):
            stadium = world["season"]["neutral_venues"]["starpath"]
            state["final"] = create_single_tie(
                world,
                "starpath",
                "final",
                teams=(state["semifinals"][0]["winner"], state["semifinals"][1]["winner"]),
                week=49,
                rank=rank,
                neutral_stadium=stadium,
                neutral_reason="决赛：赛季初始化抽定的中立场地，双方无主场加成",
            )
            state["stage_done"]["semifinal"] = True
    if week == 49 and state["final"] and not state["stage_done"].get("final"):
        resolve_single_tie(world, state["final"])
        if state["final"]["winner"]:
            state["champion"] = state["final"]["winner"]
            state["runner_up"] = (
                state["final"]["teams"][1]
                if state["champion"] == state["final"]["teams"][0]
                else state["final"]["teams"][0]
            )
            placements = {state["champion"]: 1, state["runner_up"]: 2}
            for tie in state["semifinals"]:
                placements[_loser(tie)] = 3
            for tie in state["quarterfinals"]:
                placements[_loser(tie)] = 5
            for team_id in state["participants"]:
                if team_id in placements:
                    continue
                wins, losses = state["records"][team_id]
                if (wins, losses) == (2, 3):
                    placements[team_id] = 9
                elif (wins, losses) == (1, 3):
                    placements[team_id] = 12
                else:
                    placements[team_id] = 15
            state["placements"] = placements
            state["stage_done"]["final"] = True


def _update_swiss_records(world: dict, state: dict, round_data: dict) -> None:
    for match_id in round_data["matches"]:
        match = get_fixture(world, match_id)
        if match["status"] != "completed":
            raise RuntimeError("瑞士轮存在未完成比赛，不能推进下一轮。")
        winner = match["winner_team_id"]
        loser = match["away_team_id"] if winner == match["home_team_id"] else match["home_team_id"]
        state["records"][winner][0] += 1
        state["records"][loser][1] += 1
        state["opponents"][winner].append(loser)
        state["opponents"][loser].append(winner)
    for team_id in state["participants"]:
        total = 0
        for opponent in set(state["opponents"][team_id]):
            wins, losses = state["records"][opponent]
            total += wins - losses
        state["bu"][team_id] = total
    state["qualified"] = sorted(
        [team_id for team_id, record in state["records"].items() if record[0] >= 3],
    )
    state["eliminated"] = sorted(
        [team_id for team_id, record in state["records"].items() if record[1] >= 3],
    )


def _create_next_swiss_round(world: dict, state: dict, round_number: int, week: int, rank: Dict[str, int]) -> None:
    groups: Dict[Tuple[int, int], List[str]] = {}
    for team_id, (wins, losses) in state["records"].items():
        if wins >= 3 or losses >= 3:
            continue
        groups.setdefault((wins, losses), []).append(team_id)
    pairs: List[Tuple[str, str]] = []
    rematch_note = None
    for record in sorted(groups, key=lambda item: (-item[0], item[1])):
        teams = sorted(
            groups[record],
            key=lambda team_id: (-state["bu"].get(team_id, 0), state["seeds"].get(team_id, 99)),
        )
        if len(teams) == 6:
            group_pairs, note = _pair_six(teams, state, round_number)
            rematch_note = rematch_note or note
        else:
            group_pairs, note = _pair_high_low(teams, state, round_number)
            rematch_note = rematch_note or note
        pairs.extend(group_pairs)
    _add_swiss_round(world, state, round_number=round_number, week=week, pairs=pairs, rank=rank)
    state["rounds"][-1]["rematch_reason"] = rematch_note


def _prior_opponents(state: dict, team_id: str) -> set:
    return set(state["opponents"].get(team_id, []))


def _pair_six(teams: List[str], state: dict, round_number: int) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    table = rules.swiss_priority_table()
    best = None
    for option in table:
        pairs = [(teams[first - 1], teams[second - 1]) for first, second in option]
        rematches = sum(1 for first, second in pairs if second in _prior_opponents(state, first))
        if rematches == 0:
            return pairs, None
        if best is None or rematches < best[0]:
            best = (rematches, pairs)
    pairs = best[1]
    return pairs, f"第 {round_number} 轮同战绩组无完全回避重赛方案，按 CS 最少重赛原则接受 {best[0]} 场重赛"


def _pair_high_low(teams: List[str], state: dict, round_number: int) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    teams = list(teams)
    if len(teams) % 2 != 0:
        raise RuntimeError("同战绩组人数必须为偶数。")
    if len(teams) <= 2:
        return ([(teams[0], teams[1])] if len(teams) == 2 else []), None

    def search(remaining: List[str]) -> Optional[List[Tuple[str, str]]]:
        if not remaining:
            return []
        first = remaining[0]
        candidates = remaining[1:]
        # 优先最高种子对最低可用种子；回溯避免整组死路。
        for partner in reversed(candidates):
            if partner in _prior_opponents(state, first):
                continue
            rest = [team for team in remaining[1:] if team != partner]
            result = search(rest)
            if result is not None:
                return [(first, partner), *result]
        return None

    result = search(teams)
    if result is not None:
        return result, None
    # 兜底：同战绩优先，最小化重赛。
    best: Optional[Tuple[int, List[Tuple[str, str]]]] = None
    ordered = list(teams)

    def fallback_search(remaining: List[str], acc: List[Tuple[str, str]], rematches: int) -> None:
        nonlocal best
        if not remaining:
            if best is None or rematches < best[0]:
                best = (rematches, list(acc))
            return
        if best is not None and rematches >= best[0]:
            return
        first = remaining[0]
        for partner in remaining[1:]:
            rest = [team for team in remaining[1:] if team != partner]
            fallback_search(
                rest,
                [*acc, (first, partner)],
                rematches + (1 if partner in _prior_opponents(state, first) else 0),
            )

    fallback_search(ordered, [], 0)
    pairs = best[1] if best else [(teams[index], teams[-index - 1]) for index in range(len(teams) // 2)]
    return pairs, f"第 {round_number} 轮无完全回避重赛方案，按最少重赛原则接受 {best[0] if best else '若干'} 场重赛"


def _create_starpath_knockout(world: dict, state: dict, rank: Dict[str, int]) -> None:
    qualified = list(state["qualified"])
    if len(qualified) != 8:
        raise RuntimeError(f"极境杯瑞士轮应产生 8 支出线队，实际 {len(qualified)}。")
    categories = {0: [], 1: [], 2: []}
    for team_id in qualified:
        wins, losses = state["records"][team_id]
        categories[losses].append(team_id)
    ordered: List[str] = []
    for losses in (0, 1, 2):
        bucket = sorted(
            categories[losses],
            key=lambda team_id: (-state["bu"][team_id], state["seeds"].get(team_id, 99)),
        )
        ordered.extend(bucket)
    state["knockout_seeds"] = {team_id: index + 1 for index, team_id in enumerate(ordered)}
    state["qualified"] = ordered
    pairs = [(ordered[0], ordered[7]), (ordered[3], ordered[4]), (ordered[1], ordered[6]), (ordered[2], ordered[5])]
    state["quarterfinals"] = [
        create_two_leg_tie(world, "starpath", "quarterfinal", teams=pair, weeks=(33, 34), rank=rank)
        for pair in pairs
    ]
    state["stage_done"]["swiss"] = True


# ---------------------------------------------------------------------------
# 杯赛 4：不灭杯（8 队双败，固定交叉落位，总决赛一场无重置）
# ---------------------------------------------------------------------------


def initialize_ember(world: dict, participants: List[str], rank: Dict[str, int], rng: random.Random) -> dict:
    slots = list(participants)
    rng.shuffle(slots)
    qf_pairs = [(slots[index], slots[index + 1]) for index in range(0, 8, 2)]
    state = {
        "active": True,
        "participants": participants,
        "slots": slots,
        "loss_stage": {team_id: None for team_id in participants},
        "wb_quarterfinal": [],
        "wb_semifinal": [],
        "lb_round_1": [],
        "lb_round_2": [],
        "wb_final": None,
        "lb_round_3": [],
        "lb_final": None,
        "grand_final": None,
        "eliminated": {},
        "placements": {},
        "champion": None,
        "runner_up": None,
        "third": None,
        "fourth": None,
        "stage_done": {},
    }
    state["wb_quarterfinal"] = [
        _ember_match(world, state, "wb_quarterfinal", pair, week=35, rank=rank)
        for pair in qf_pairs
    ]
    return state


def _ember_home(state: dict, rank: Dict[str, int], first: str, second: str) -> Tuple[str, str, str]:
    loss_first = state["loss_stage"].get(first)
    loss_second = state["loss_stage"].get(second)
    if loss_first is None and loss_second is not None:
        return first, second, "主场依据：胜者组无败绩"
    if loss_second is None and loss_first is not None:
        return second, first, "主场依据：胜者组无败绩"
    if loss_first is not None and loss_second is not None and loss_first != loss_second:
        if loss_first > loss_second:
            return first, second, f"主场依据：更晚落入败者组（第 {loss_first} 阶段 vs 第 {loss_second} 阶段）"
        return second, first, f"主场依据：更晚落入败者组（第 {loss_second} 阶段 vs 第 {loss_first} 阶段）"
    home, away = _better(rank, first, second)
    return home, away, "主场依据：晋级路径等价，比较上季联赛排名"


def _ember_match(world: dict, state: dict, stage: str, teams: Tuple[str, str], *, week: int, rank: Dict[str, int], grand_final: bool = False) -> dict:
    home, away, reason = _ember_home(state, rank, teams[0], teams[1])
    if grand_final and state["loss_stage"].get(home) is not None:
        home, away = away, home
        reason = "主场依据：胜者组冠军无败绩"
    tie_id = _tie_id(world, "ember", stage)
    match_id = add_match(
        world,
        competition="ember",
        stage=stage,
        week=week,
        home_team_id=home,
        away_team_id=away,
        need_winner=True,
    )
    get_fixture(world, match_id)["venue_reason"] = reason
    return {
        "tie_id": tie_id,
        "stage": stage,
        "teams": [teams[0], teams[1]],
        "home": home,
        "away": away,
        "match_id": match_id,
        "winner": None,
        "reason": reason,
    }


def progress_ember(world: dict, state: dict, week: int, rng: random.Random) -> None:
    rank = previous_ranks(world)
    if week == 35 and not state["stage_done"].get("wb_qf"):
        if not _resolve_ember_round(world, state, state["wb_quarterfinal"]):
            return
        winners = [tie["winner"] for tie in state["wb_quarterfinal"]]
        losers = [_loser(tie) for tie in state["wb_quarterfinal"]]
        for team_id in losers:
            state["loss_stage"][team_id] = 1
        state["wb_semifinal"] = [
            _ember_match(world, state, "wb_semifinal", (winners[0], winners[1]), week=37, rank=rank),
            _ember_match(world, state, "wb_semifinal", (winners[2], winners[3]), week=37, rank=rank),
        ]
        state["lb_round_1"] = [
            _ember_match(world, state, "lb_round_1", (losers[0], losers[1]), week=37, rank=rank),
            _ember_match(world, state, "lb_round_1", (losers[2], losers[3]), week=37, rank=rank),
        ]
        state["stage_done"]["wb_qf"] = True

    if week == 37 and not state["stage_done"].get("wb_sf"):
        if not _resolve_ember_round(world, state, state["wb_semifinal"]):
            return
        if not _resolve_ember_round(world, state, state["lb_round_1"]):
            return
        wb_winners = [tie["winner"] for tie in state["wb_semifinal"]]
        wb_losers = [_loser(tie) for tie in state["wb_semifinal"]]
        lb_winners = [tie["winner"] for tie in state["lb_round_1"]]
        for team_id in wb_losers:
            state["loss_stage"][team_id] = 2
        state["lb_round_2"] = [
            _ember_match(world, state, "lb_round_2", (lb_winners[0], wb_losers[1]), week=40, rank=rank),
            _ember_match(world, state, "lb_round_2", (lb_winners[1], wb_losers[0]), week=40, rank=rank),
        ]
        state["wb_final"] = _ember_match(world, state, "wb_final", (wb_winners[0], wb_winners[1]), week=44, rank=rank)
        state["stage_done"]["wb_sf"] = True

    if week == 40 and not state["stage_done"].get("lb_r2"):
        if not _resolve_ember_round(world, state, state["lb_round_2"]):
            return
        lb2_winners = [tie["winner"] for tie in state["lb_round_2"]]
        state["lb_round_3"] = [
            _ember_match(world, state, "lb_round_3", (lb2_winners[0], lb2_winners[1]), week=44, rank=rank)
        ]
        state["stage_done"]["lb_r2"] = True

    if week == 44 and not state["stage_done"].get("wb_final"):
        if not _resolve_ember_round(world, state, [state["wb_final"]]) or not _resolve_ember_round(world, state, state["lb_round_3"]):
            return
        wb_champion = state["wb_final"]["winner"]
        wb_loser = _loser(state["wb_final"])
        state["loss_stage"][wb_loser] = 3
        state["fourth"] = _loser(state["lb_round_3"][0])
        state["lb_final"] = _ember_match(
            world, state, "lb_final", (state["lb_round_3"][0]["winner"], wb_loser), week=45, rank=rank
        )
        state["stage_done"]["wb_final"] = True

    if week == 45 and state["lb_final"] and not state["stage_done"].get("lb_final"):
        if not _resolve_ember_round(world, state, [state["lb_final"]]):
            return
        lb_champion = state["lb_final"]["winner"]
        state["third"] = _loser(state["lb_final"])
        state["grand_final"] = _ember_match(
            world, state, "grand_final", (state["wb_final"]["winner"], lb_champion), week=48, rank=rank, grand_final=True
        )
        state["stage_done"]["lb_final"] = True

    if week == 48 and state["grand_final"] and not state["stage_done"].get("grand_final"):
        if not _resolve_ember_round(world, state, [state["grand_final"]]):
            return
        state["champion"] = state["grand_final"]["winner"]
        state["runner_up"] = _loser(state["grand_final"])
        placements = {
            state["champion"]: 1,
            state["runner_up"]: 2,
            state["third"]: 3,
            state["fourth"]: 4,
        }
        for tie in state["lb_round_2"]:
            placements[_loser(tie)] = 5
        for tie in state["lb_round_1"]:
            placements[_loser(tie)] = 7
        state["placements"] = placements
        state["stage_done"]["grand_final"] = True


def _resolve_ember_round(world: dict, state: dict, ties: List[dict]) -> bool:
    for tie in ties:
        resolve_single_tie(world, tie)
    return all(tie["winner"] for tie in ties)


# ---------------------------------------------------------------------------
# 杯赛 5：凌越杯（12 队两组组外循环 → 突围 → 八强单败）
# ---------------------------------------------------------------------------


def initialize_ascendant(world: dict, participants: List[str], rank: Dict[str, int], rng: random.Random) -> dict:
    d_group = list(participants[:6])
    n_group = list(participants[6:12])
    schedule = _cross_group_schedule(d_group, n_group, rng)
    weeks = [4, 7, 10, 13, 16, 19]
    group_fixtures = []
    for round_index, pairs in enumerate(schedule):
        for home, away, group in pairs:
            match_id = add_match(
                world,
                competition="ascendant",
                stage=f"cross_group_{round_index + 1}",
                week=weeks[round_index],
                home_team_id=home,
                away_team_id=away,
                round_number=round_index + 1,
                group=group,
                need_winner=False,
            )
            group_fixtures.append(match_id)
    return {
        "active": True,
        "participants": participants,
        "groups": {"D": d_group, "N": n_group},
        "schedule": schedule,
        "group_fixtures": group_fixtures,
        "standings": {},
        "direct": [],
        "playin": [],
        "quarterfinals": [],
        "semifinals": [],
        "final": None,
        "placements": {},
        "champion": None,
        "runner_up": None,
        "stage_done": {},
    }


def _cross_group_schedule(d_group: List[str], n_group: List[str], rng: random.Random) -> List[List[Tuple[str, str, str]]]:
    n_order = list(n_group)
    rng.shuffle(n_order)
    rounds: List[List[Tuple[str, str, str]]] = []
    for round_index in range(6):
        pairs: List[Tuple[str, str, str]] = []
        for index, team_d in enumerate(d_group):
            team_n = n_order[(index + round_index) % 6]
            if (round_index - 2 * index) % 6 < 3:
                pairs.append((team_d, team_n, "D"))
            else:
                pairs.append((team_n, team_d, "N"))
        rounds.append(pairs)
    rng.shuffle(rounds)
    if rng.random() < 0.5:
        rounds = [[(away, home, group) for home, away, group in pairs] for pairs in rounds]
    for team_id in [*d_group, *n_group]:
        home_count = sum(1 for pairs in rounds for home, away, _ in pairs if home == team_id)
        if home_count != 3:
            raise RuntimeError(f"凌越杯组外赛程主场数不是 3：{team_id}={home_count}")
    return rounds


def progress_ascendant(world: dict, state: dict, week: int, rng: random.Random) -> None:
    rank = previous_ranks(world)
    if week == 19 and not state["stage_done"].get("groups"):
        matches = [
            match for match in completed_matches(world, "ascendant") if match["stage"].startswith("cross_group_")
        ]
        standings = {}
        # 组外循环：每场比赛同时计入两组；同组球队之间从未交手，因此不能
        # 用“同组相互战绩”作为同分裁决。
        for group_key, teams in state["groups"].items():
            standings[group_key] = standings_from_matches(teams, matches, previous_rank=rank, partial=True)
        state["standings"] = standings
        d_rows, n_rows = standings["D"], standings["N"]
        state["direct"] = [row["team_id"] for row in d_rows[:4]] + [n_rows[0]["team_id"]]
        pot_a = [d_rows[4]["team_id"], d_rows[5]["team_id"], n_rows[1]["team_id"]]
        pot_b = [n_rows[2]["team_id"], n_rows[3]["team_id"], n_rows[4]["team_id"]]
        rng.shuffle(pot_a)
        rng.shuffle(pot_b)
        state["playin"] = [
            create_single_tie(
                world,
                "ascendant",
                "play_in",
                teams=(pot_a[index], pot_b[index]),
                week=22,
                rank=rank,
            )
            for index in range(3)
        ]
        state["nirvana_sixth"] = n_rows[5]["team_id"]
        state["stage_done"]["groups"] = True

    if week == 22 and state["playin"] and not state["stage_done"].get("playin"):
        if not all(resolve_single_tie(world, tie) for tie in state["playin"]):
            return
        winners = [tie["winner"] for tie in state["playin"]]
        qualified = list(state["direct"]) + winners
        slots: List[Optional[str]] = [None] * 8
        slots[0] = state["direct"][0]
        slots[7] = state["direct"][1]
        rest = [team_id for team_id in qualified if team_id not in (slots[0], slots[7])]
        rng.shuffle(rest)
        free = [index for index in range(8) if slots[index] is None]
        for index, team_id in zip(free, rest):
            slots[index] = team_id
        pairs = [(slots[index], slots[index + 1]) for index in range(0, 8, 2)]
        state["quarterfinals"] = [
            create_single_tie(world, "ascendant", "quarterfinal", teams=pair, week=29, rank=rank)
            for pair in pairs
        ]
        state["bracket_slots"] = slots
        state["stage_done"]["playin"] = True

    if week == 29 and state["quarterfinals"] and not state["stage_done"].get("quarterfinal"):
        if not all(resolve_single_tie(world, tie) for tie in state["quarterfinals"]):
            return
        winners = [tie["winner"] for tie in state["quarterfinals"]]
        state["semifinals"] = [
            create_single_tie(world, "ascendant", "semifinal", teams=(winners[0], winners[1]), week=36, rank=rank),
            create_single_tie(world, "ascendant", "semifinal", teams=(winners[2], winners[3]), week=36, rank=rank),
        ]
        state["stage_done"]["quarterfinal"] = True

    if week == 36 and state["semifinals"] and not state["stage_done"].get("semifinal"):
        if not all(resolve_single_tie(world, tie) for tie in state["semifinals"]):
            return
        stadium = world["season"]["neutral_venues"]["ascendant"]
        state["final"] = create_single_tie(
            world,
            "ascendant",
            "final",
            teams=(state["semifinals"][0]["winner"], state["semifinals"][1]["winner"]),
            week=43,
            rank=rank,
            neutral_stadium=stadium,
            neutral_reason="决赛：赛季初始化抽定的中立场地，双方无主场加成",
        )
        state["stage_done"]["semifinal"] = True

    if week == 43 and state["final"] and not state["stage_done"].get("final"):
        resolve_single_tie(world, state["final"])
        if state["final"]["winner"]:
            state["champion"] = state["final"]["winner"]
            state["runner_up"] = _loser(state["final"])
            placements = {state["champion"]: 1, state["runner_up"]: 2}
            for tie in state["semifinals"]:
                placements[_loser(tie)] = 3
            for tie in state["quarterfinals"]:
                placements[_loser(tie)] = 5
            for tie in state["playin"]:
                placements[_loser(tie)] = 9
            placements[state["nirvana_sixth"]] = 12
            state["placements"] = placements
            state["stage_done"]["final"] = True


# ---------------------------------------------------------------------------
# 杯赛 6：永世杯（冠军资格去重补位 → 荣誉分种子固定签表）
# ---------------------------------------------------------------------------


def zenith_qualification(world: dict) -> Tuple[List[str], Dict[str, List[str]]]:
    previous = world["season"].get("previous") or {}
    premier_order = list(previous.get("premier_order", []))
    second_order = list(previous.get("second_order", []))
    champions = dict(previous.get("cup_champions", {}))
    sources: Dict[str, List[str]] = {}

    def add(team_id: Optional[str], source: str) -> None:
        if not team_id:
            return
        sources.setdefault(team_id, [])
        if source not in sources[team_id]:
            sources[team_id].append(source)

    for cup_id in rules.REGULAR_CUP_IDS:
        champion = champions.get(cup_id)
        if champion:
            add(champion, f"{rules.display_name(cup_id, 'zh')}冠军")
    if premier_order:
        add(premier_order[0], "一级联赛冠军")
    if second_order:
        add(second_order[0], "次级联赛冠军")
    if champions.get("zenith"):
        add(champions["zenith"], f"上届{rules.display_name('zenith', 'zh')}冠军")

    selected = list(sources.keys())
    for team_id in premier_order:
        if len(selected) >= 8:
            break
        if team_id not in selected:
            selected.append(team_id)
            sources[team_id] = ["一级联赛排名递补"]
    if len(selected) < 8:
        for team_id in second_order:
            if len(selected) >= 8:
                break
            if team_id not in selected:
                selected.append(team_id)
                sources[team_id] = ["上季总顺序递补"]
    return selected, sources


def initialize_zenith(world: dict, rank: Dict[str, int], rng: random.Random) -> dict:
    participants, sources = zenith_qualification(world)
    honors = previous_honor_totals(world)
    ordered = sorted(
        participants,
        key=lambda team_id: (-int(honors.get(team_id, 0)), _rank(rank, team_id)),
    )
    seeds = {team_id: index + 1 for index, team_id in enumerate(ordered)}
    by_seed = {seed: team_id for team_id, seed in seeds.items()}
    pairs = [(by_seed[1], by_seed[8]), (by_seed[4], by_seed[5]), (by_seed[2], by_seed[7]), (by_seed[3], by_seed[6])]
    state = {
        "active": True,
        "participants": participants,
        "sources": sources,
        "seeds": seeds,
        "seed_order": ordered,
        "quarterfinals": [],
        "semifinals": [],
        "final": None,
        "placements": {},
        "champion": None,
        "runner_up": None,
        "stage_done": {},
    }
    qf_week = rules.stage_weeks("zenith")["quarterfinal"][0]
    state["quarterfinals"] = [
        create_single_tie(world, "zenith", "quarterfinal", teams=pair, week=qf_week, rank=rank)
        for pair in pairs
    ]
    return state


def progress_zenith(world: dict, state: dict, week: int, rng: random.Random) -> None:
    rank = previous_ranks(world)
    weeks = rules.stage_weeks("zenith")
    qf_week = weeks["quarterfinal"][0]
    sf_week = weeks["semifinal"][0]
    final_week = weeks["final"][0]
    if week == qf_week and not state["stage_done"].get("quarterfinal"):
        if not all(resolve_single_tie(world, tie) for tie in state["quarterfinals"]):
            return
        winners = [tie["winner"] for tie in state["quarterfinals"]]
        state["semifinals"] = [
            create_single_tie(world, "zenith", "semifinal", teams=(winners[0], winners[1]), week=sf_week, rank=rank),
            create_single_tie(world, "zenith", "semifinal", teams=(winners[2], winners[3]), week=sf_week, rank=rank),
        ]
        state["stage_done"]["quarterfinal"] = True
    if week == sf_week and state["semifinals"] and not state["stage_done"].get("semifinal"):
        if not all(resolve_single_tie(world, tie) for tie in state["semifinals"]):
            return
        stadium = world["season"]["neutral_venues"]["zenith"]
        state["final"] = create_single_tie(
            world,
            "zenith",
            "final",
            teams=(state["semifinals"][0]["winner"], state["semifinals"][1]["winner"]),
            week=final_week,
            rank=rank,
            neutral_stadium=stadium,
            neutral_reason="决赛：赛季初始化抽定的中立场地，双方无主场加成",
        )
        state["stage_done"]["semifinal"] = True
    if week == final_week and state["final"] and not state["stage_done"].get("final"):
        resolve_single_tie(world, state["final"])
        if state["final"]["winner"]:
            state["champion"] = state["final"]["winner"]
            state["runner_up"] = _loser(state["final"])
            placements = {state["champion"]: 1, state["runner_up"]: 2}
            for tie in state["semifinals"]:
                placements[_loser(tie)] = 3
            for tie in state["quarterfinals"]:
                placements[_loser(tie)] = 5
            state["placements"] = placements
            state["stage_done"]["final"] = True


# ---------------------------------------------------------------------------
# 初始化与逐周分派
# ---------------------------------------------------------------------------


def initialize_cups(world: dict, season_number: int, rng: random.Random) -> None:
    rank = previous_ranks(world)
    order = previous_order(world)
    cups: Dict[str, dict] = {}
    if season_number >= 2 and len(order) >= 32:
        cups["aureole"] = initialize_aureole(world, order[:16], rank, rng)
        cups["confluence"] = initialize_confluence(world, order[:32], rank, rng)
        cups["starpath"] = initialize_starpath(world, order[:16], rank, rng)
        cups["ember"] = initialize_ember(world, order[:8], rank, rng)
        cups["ascendant"] = initialize_ascendant(world, order[:12], rank, rng)
    if season_number >= 3:
        cups["zenith"] = initialize_zenith(world, rank, rng)

    neutral_cups = [
        cup_id for cup_id in ("confluence", "starpath", "ascendant", "zenith") if cup_id in cups
    ]
    stadium_ids = [item["stadium_id"] for item in rules.load_stadiums()]
    chosen = rng.sample(stadium_ids, len(neutral_cups))
    world["season"]["neutral_venues"] = dict(zip(neutral_cups, chosen))
    world["season"]["cups"] = cups


def progress_cups(world: dict, week: int, rng: random.Random) -> None:
    handlers = {
        "aureole": progress_aureole,
        "confluence": progress_confluence,
        "starpath": progress_starpath,
        "ember": progress_ember,
        "ascendant": progress_ascendant,
        "zenith": progress_zenith,
    }
    cups = world["season"].get("cups", {})
    for cup_id, state in cups.items():
        if not state.get("active"):
            continue
        handler = handlers[cup_id]
        handler(world, state, week, rng)


def cup_final_ready(world: dict, cup_id: str) -> bool:
    state = world["season"].get("cups", {}).get(cup_id)
    return bool(state and state.get("champion"))


def _loser(tie: dict) -> str:
    first, second = tie["teams"]
    return second if tie.get("winner") == first else first


def _losers(ties: Iterable[dict]) -> List[str]:
    return [_loser(tie) for tie in ties]
