"""联赛排名链（自 state.py 原样抽取，行为冻结）。

排名规则：积分 → 净胜球 → 进球 → 相互战绩 → （外部传入的）附加裁决，
无裁决时同分组按球队名称排序（确定性回退，供查询层使用）。
"""

from __future__ import annotations

from typing import Callable, Dict, List

from football_simulator.models import MatchResult, TableRow


def apply_table_result(table_map: Dict[str, TableRow], result: MatchResult) -> None:
    home_row = table_map[result.home_team.name]
    away_row = table_map[result.away_team.name]
    home_row.record_match(result.home_goals, result.away_goals)
    away_row.record_match(result.away_goals, result.home_goals)


def rank_table_rows(
    rows: List[TableRow],
    results: List[MatchResult],
    playoff_resolutions: list[dict],
) -> List[TableRow]:
    sorted_rows: List[TableRow] = []
    grouped = group_by_metric(rows, lambda row: (row.points, row.goals_for - row.goals_against, row.goals_for))
    resolution_map = {tuple(item["teams"]): item["order"] for item in playoff_resolutions}

    for group in grouped:
        if len(group) == 1:
            sorted_rows.extend(group)
            continue

        head_to_head_groups = group_by_metric(
            group,
            lambda row: head_to_head_tuple(row.team.name, group, results),
        )
        for tied_group in head_to_head_groups:
            if len(tied_group) == 1:
                sorted_rows.extend(tied_group)
                continue

            team_names = tuple(sorted(row.team.name for row in tied_group))
            order = resolution_map.get(team_names)
            if order is None:
                tied_group.sort(key=lambda row: row.team.name)
                sorted_rows.extend(tied_group)
            else:
                order_index = {team_name: index for index, team_name in enumerate(order)}
                sorted_rows.extend(sorted(tied_group, key=lambda row: order_index[row.team.name]))

    return sorted_rows


def group_by_metric(rows: List[TableRow], key_func: Callable) -> List[List[TableRow]]:
    ordered = sorted(rows, key=key_func, reverse=True)
    groups: List[List[TableRow]] = []
    for row in ordered:
        metric = key_func(row)
        if not groups or key_func(groups[-1][0]) != metric:
            groups.append([row])
        else:
            groups[-1].append(row)
    return groups


def head_to_head_tuple(team_name: str, group: List[TableRow], results: List[MatchResult]) -> tuple[int, int, int]:
    team_names = {row.team.name for row in group}
    points = 0
    goal_diff = 0
    goals_for = 0
    for result in results:
        if result.home_team.name not in team_names or result.away_team.name not in team_names:
            continue
        if result.home_team.name == team_name:
            goals_for += result.home_goals
            goal_diff += result.home_goals - result.away_goals
            if result.home_goals > result.away_goals:
                points += 3
            elif result.home_goals == result.away_goals:
                points += 1
        elif result.away_team.name == team_name:
            goals_for += result.away_goals
            goal_diff += result.away_goals - result.home_goals
            if result.away_goals > result.home_goals:
                points += 3
            elif result.away_goals == result.home_goals:
                points += 1
    return points, goal_diff, goals_for
