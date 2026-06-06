import argparse
import sys
from typing import Iterable, List

from football_simulator.state import (
    WeekSimulationResult,
    find_player_rows,
    find_team_rows,
    get_player_history_totals,
    get_player_single_season_records,
    get_team_history_totals,
    get_team_single_season_records,
    initialize_save_state,
    load_save_snapshot,
    simulate_next_week,
)


PLAYER_METRICS = {
    "goals": "进球",
    "assists": "助攻",
    "chances_created": "创造机会",
    "successful_defenses": "成功防守",
    "successful_saves": "成功扑救",
    "clean_sheets": "零封",
}

TEAM_METRICS = {
    "points": "积分",
    "wins": "胜场",
    "goals_for": "总进球",
    "goals_against": "总失球",
    "goal_diff": "净胜球",
    "goals": "进球",
    "assists": "助攻",
    "chances_created": "创造机会",
    "successful_defenses": "成功防守",
    "successful_saves": "成功扑救",
    "clean_sheets": "零封",
}

SCOPE_LABELS = {
    "current": "当前赛季",
    "history": "历史总榜",
    "record": "单赛季纪录",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="管理本地足球模拟器存档。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="为存档初始化新赛季。")
    init_parser.add_argument("--save", default="default", help="存档文件夹名称。")

    simulate_parser = subparsers.add_parser(
        "simulate-week", help="模拟当前赛季的下一周。"
    )
    simulate_parser.add_argument("--save", default="default", help="存档文件夹名称。")

    status_parser = subparsers.add_parser("status", help="显示当前赛季状态。")
    status_parser.add_argument("--save", default="default", help="存档文件夹名称。")

    standings_parser = subparsers.add_parser("standings", help="显示当前积分榜。")
    standings_parser.add_argument("--save", default="default", help="存档文件夹名称。")

    leaders_parser = subparsers.add_parser("leaders", help="显示球员或球队排行榜。")
    leaders_parser.add_argument("--save", default="default", help="存档文件夹名称。")
    leaders_parser.add_argument(
        "--entity",
        choices=("players", "teams"),
        required=True,
        help="显示哪一种排行榜。",
    )
    leaders_parser.add_argument(
        "--scope",
        choices=("current", "history", "record"),
        default="current",
        help="当前赛季、历史总榜或单赛季纪录。",
    )
    leaders_parser.add_argument("--metric", required=True, help="用于排序的指标键。")
    leaders_parser.add_argument("--limit", type=int, default=10, help="显示行数。")

    player_parser = subparsers.add_parser("player", help="显示当前赛季球员详情。")
    player_parser.add_argument("--save", default="default", help="存档文件夹名称。")
    player_parser.add_argument("--name", required=True, help="要查询的球员名或标签。")

    team_parser = subparsers.add_parser("team", help="显示当前赛季球队详情。")
    team_parser.add_argument("--save", default="default", help="存档文件夹名称。")
    team_parser.add_argument("--name", required=True, help="要查询的球队名。")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        if args.command == "init":
            snapshot = initialize_save_state(args.save)
            _print_header(snapshot.save_name)
            print(
                f"已初始化第 {snapshot.season_number} 赛季，并完成真随机球员分配。"
            )
            _print_rosters(snapshot.teams)
            return

        if args.command == "simulate-week":
            result = simulate_next_week(args.save)
            _print_header(result.snapshot.save_name)
            _print_week_result(result)
            return

        snapshot = load_save_snapshot(args.save)
        _print_header(snapshot.save_name)

        if args.command == "status":
            _print_status(snapshot)
        elif args.command == "standings":
            _print_standings(snapshot.table)
        elif args.command == "leaders":
            _print_leaders(snapshot, args.entity, args.scope, args.metric, args.limit)
        elif args.command == "player":
            _print_player_detail(snapshot, args.name)
        elif args.command == "team":
            _print_team_detail(snapshot, args.name)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        sys.exit(1)


def _print_header(save_name: str) -> None:
    print("=== 足球模拟器 ===")
    print(f"存档：{save_name}")
    print()


def _print_rosters(teams) -> None:
    print("初始球员分配")
    print("-" * 86)
    for team in teams:
        print(f"{team.name} | 评分 {team.rating:.1f} | 真实球员 {team.real_player_count}")
        for player in team.roster:
            player_type = "真实球员" if player.is_real else "默认球员"
            print(
                f"  {player.position:<2} {player.label:<22}"
                f"能力 {player.ability:>2} [{player_type}]"
            )
        print()


def _print_week_result(result: WeekSimulationResult) -> None:
    week = result.week
    print(
        f"第 {result.snapshot.season_number} 赛季 | 第 {week.week_number}/{len(result.snapshot.weeks)} 周"
    )
    print(f"周类型：{week.label}")
    print("-" * 86)
    if not result.matchdays:
        if week.kind in {"long_break", "short_break"}:
            print("本周没有比赛，联赛处于休赛期。")
        else:
            print("本周没有比赛。")
    else:
        for matchday in result.matchdays:
            print(f"第 {matchday.round_number} 轮")
            for match in matchday.results:
                print(
                    f"  {match.home_team.name} {match.home_goals}-{match.away_goals} "
                    f"{match.away_team.name}"
                )
                for event in match.key_events[:2]:
                    print(f"    {event}")
            print()

    if result.season_completed_now:
        champion = result.snapshot.table[0]
        print(
            f"赛季结束。冠军：{champion.team.name}，积分 {champion.points}。"
        )
        print("本赛季已归档到历史总榜和单赛季纪录。")
    else:
        next_week_index = result.snapshot.current_week
        if next_week_index < len(result.snapshot.weeks):
            next_week = result.snapshot.weeks[next_week_index]
            print(f"下周：第 {next_week.week_number} 周（{next_week.label}）")


def _print_status(snapshot) -> None:
    print(f"当前赛季：第 {snapshot.season_number} 赛季")
    print(f"当前周次：{snapshot.current_week}/{len(snapshot.weeks)}")
    print(f"赛季是否结束：{'是' if snapshot.season_complete else '否'}")
    simulated_rounds = sum(
        len(week_data.get("matchdays", [])) for week_data in snapshot.simulated_weeks
    )
    print(f"已模拟联赛轮次：{simulated_rounds}/38")
    print(f"已归档历史赛季：{len(snapshot.history)}")
    print()
    if snapshot.current_week < len(snapshot.weeks):
        next_week = snapshot.weeks[snapshot.current_week]
        print(
            f"下周：第 {next_week.week_number} 周 | {next_week.label} | "
            f"轮次：{', '.join(str(round_number) for round_number in next_week.round_numbers) or '无'}"
        )
    else:
        print("本赛季已经没有剩余周次。")


def _print_standings(table) -> None:
    print("积分榜")
    print("-" * 86)
    print(
        f"{'名次':<4}{'球队':<24}{'赛':>4}{'胜':>4}{'平':>4}{'负':>4}"
        f"{'进':>5}{'失':>5}{'净':>5}{'积分':>6}"
    )
    for position, row in enumerate(table, start=1):
        print(
            f"{position:<4}{row.team.name:<24}{row.played:>4}{row.wins:>4}"
            f"{row.draws:>4}{row.losses:>4}{row.goals_for:>5}{row.goals_against:>5}"
            f"{row.goals_for - row.goals_against:>5}{row.points:>6}"
        )


def _print_leaders(snapshot, entity: str, scope: str, metric: str, limit: int) -> None:
    if entity == "players":
        if metric not in PLAYER_METRICS:
            raise ValueError(f"Unsupported player metric '{metric}'.")
        if scope == "current":
            rows = [
                {
                    "label": row.player.label,
                    "team_name": row.team_name,
                    "season_number": row.season_number,
                    **{key: getattr(row, key) for key in PLAYER_METRICS},
                }
                for row in snapshot.player_stats
            ]
        elif scope == "history":
            rows = get_player_history_totals(snapshot)
        else:
            rows = get_player_single_season_records(snapshot)
        rows = _sort_metric_rows(rows, metric, lower_is_better=False)[:limit]
        print(f"球员排行榜 | {SCOPE_LABELS[scope]} | {PLAYER_METRICS[metric]}")
        print("-" * 98)
        print(f"{'球员':<24}{'球队':<24}{'赛季':<8}{'数值':>8}")
        for row in rows:
            print(
                f"{row['label']:<24}{row.get('team_name', '-'): <24}"
                f"{str(row.get('season_number', row.get('seasons', '-'))):<8}"
                f"{row[metric]:>8}"
            )
        return

    if metric not in TEAM_METRICS:
        raise ValueError(f"Unsupported team metric '{metric}'.")
    if scope == "current":
        rows = [
            {
                "team_name": row.team_name,
                "season_number": row.season_number,
                **{key: getattr(row, key) for key in TEAM_METRICS if hasattr(row, key)},
                "goal_diff": row.goal_diff,
            }
            for row in snapshot.team_stats
        ]
    elif scope == "history":
        rows = get_team_history_totals(snapshot)
    else:
        rows = get_team_single_season_records(snapshot)
    rows = _sort_metric_rows(rows, metric, lower_is_better=(metric == "goals_against"))[:limit]
    print(f"球队排行榜 | {SCOPE_LABELS[scope]} | {TEAM_METRICS[metric]}")
    print("-" * 98)
    print(f"{'球队':<24}{'赛季':<8}{'数值':>8}")
    for row in rows:
        print(
            f"{row['team_name']:<24}"
            f"{str(row.get('season_number', row.get('seasons', '-'))):<8}"
            f"{row[metric]:>8}"
        )


def _print_player_detail(snapshot, query: str) -> None:
    current_rows = find_player_rows(snapshot, query)
    if not current_rows:
        raise ValueError(f"当前赛季没有匹配到球员 '{query}'。")

    history_totals = {
        row["player_id"]: row for row in get_player_history_totals(snapshot)
    }
    single_season_rows = get_player_single_season_records(snapshot)
    best_single_season = {}
    for row in single_season_rows:
        current_best = best_single_season.get(row["player_id"])
        candidate_score = (
            row["goals"],
            row["assists"],
            row["chances_created"],
            row["successful_defenses"],
            row["successful_saves"],
        )
        if current_best is None or candidate_score > (
            current_best["goals"],
            current_best["assists"],
            current_best["chances_created"],
            current_best["successful_defenses"],
            current_best["successful_saves"],
        ):
            best_single_season[row["player_id"]] = row

    print("球员详情")
    print("-" * 98)
    for row in current_rows:
        history = history_totals[row["player_id"]]
        record = best_single_season[row["player_id"]]
        print(
            f"{row['label']} | 球队 {row['team_name']} | 位置 {row['position']} | "
            f"赛季 {row['season_number']}"
        )
        print(
            f"  本赛季：进球 {row['goals']} | 助攻 {row['assists']} | "
            f"创造机会 {row['chances_created']} | 成功防守 {row['successful_defenses']} | "
            f"成功扑救 {row['successful_saves']}"
        )
        print(
            f"  历史总计：进球 {history['goals']} | 助攻 {history['assists']} | "
            f"创造机会 {history['chances_created']} | 成功防守 {history['successful_defenses']} | "
            f"成功扑救 {history['successful_saves']} | 赛季数 {history['seasons']}"
        )
        print(
            f"  最佳赛季：第 {record['season_number']} 赛季 | 进球 {record['goals']} | "
            f"助攻 {record['assists']} | 创造机会 {record['chances_created']} | "
            f"成功防守 {record['successful_defenses']} | 成功扑救 {record['successful_saves']}"
        )
        print()


def _print_team_detail(snapshot, query: str) -> None:
    current_rows = find_team_rows(snapshot, query)
    if not current_rows:
        raise ValueError(f"当前赛季没有匹配到球队 '{query}'。")

    history_totals = {row["team_name"]: row for row in get_team_history_totals(snapshot)}
    single_season_rows = get_team_single_season_records(snapshot)
    best_single_season = {}
    for row in single_season_rows:
        current_best = best_single_season.get(row["team_name"])
        candidate_score = (
            row["points"],
            row["goal_diff"],
            row["goals_for"],
            row["goals"],
        )
        if current_best is None or candidate_score > (
            current_best["points"],
            current_best["goal_diff"],
            current_best["goals_for"],
            current_best["goals"],
        ):
            best_single_season[row["team_name"]] = row
    print("球队详情")
    print("-" * 110)
    for row in current_rows:
        history = history_totals[row["team_name"]]
        record = best_single_season[row["team_name"]]
        print(f"{row['team_name']} | 第 {row['season_number']} 赛季")
        print(
            f"  本赛季战绩：赛 {row['played']} | 胜 {row['wins']} | 平 {row['draws']} | "
            f"负 {row['losses']} | 进 {row['goals_for']} | 失 {row['goals_against']} | "
            f"净胜球 {row['goals_for'] - row['goals_against']} | 积分 {row['points']}"
        )
        print(
            f"  本赛季球队数据：进球 {row['goals']} | 助攻 {row['assists']} | "
            f"创造机会 {row['chances_created']} | 成功防守 {row['successful_defenses']} | "
            f"成功扑救 {row['successful_saves']}"
        )
        print(
            f"  历史总计：赛季数 {history['seasons']} | 胜 {history['wins']} | "
            f"进 {history['goals_for']} | 失 {history['goals_against']} | "
            f"积分 {history['points']} | 进球 {history['goals']}"
        )
        print(
            f"  最佳赛季：第 {record['season_number']} 赛季 | 积分 {record['points']} | "
            f"进 {record['goals_for']} | 失 {record['goals_against']} | "
            f"净胜球 {record['goal_diff']} | 进球 {record['goals']}"
        )
        print()


def _sort_metric_rows(rows: List[dict], metric: str, lower_is_better: bool) -> List[dict]:
    return sorted(
        rows,
        key=lambda row: (
            row[metric],
            row.get("goals", 0),
            row.get("assists", 0),
            row.get("points", 0),
            row.get("label", row.get("team_name", "")),
        ),
        reverse=not lower_is_better,
    )


if __name__ == "__main__":
    main()
