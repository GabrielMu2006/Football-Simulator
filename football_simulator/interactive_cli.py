from typing import Any, Callable, Dict, List, Optional

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from football_simulator.runtime import (
    create_save_directory,
    delete_save_directory,
    list_save_names,
    load_current_save_name,
    normalize_save_name,
    save_exists,
    save_root,
    store_current_save_name,
)
from football_simulator.state import (
    apply_ability_review_decisions,
    apply_draft_prospects,
    apply_transfer_review_decisions,
    find_player_rows,
    find_team_rows,
    get_player_honor_leaders,
    get_player_history_totals,
    get_player_single_season_records,
    get_team_honor_leaders,
    get_team_history_totals,
    get_team_single_season_records,
    initialize_save_state,
    load_last_draft_log,
    load_save_snapshot,
    simulate_next_week,
)


console = Console()

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

MENU_OPTIONS = [
    ("1", "初始化赛季"),
    ("2", "模拟下一周"),
    ("3", "查看状态"),
    ("4", "查看积分榜"),
    ("5", "查看球员排行榜"),
    ("6", "查看球队排行榜"),
    ("7", "查看球队状态"),
    ("8", "按场查看最近一周详细赛况"),
    ("9", "存档管理"),
    ("10", "查看荣誉榜"),
    ("0", "退出"),
]


def main() -> None:
    _prepare_terminal()
    save_name = load_current_save_name()
    actions: Dict[str, Callable[[str], Any]] = {
        "1": _action_initialize,
        "2": _action_simulate_week,
        "3": _action_status,
        "4": _action_standings,
        "5": _action_player_leaders,
        "6": _action_team_leaders,
        "7": _action_team_status,
        "8": _action_recent_week_details,
        "10": _action_honor_leaders,
    }

    while True:
        _handle_pending_ability_review_if_needed(save_name)
        _handle_pending_transfer_review_if_needed(save_name)
        _handle_pending_draft_if_needed(save_name)
        console.clear()
        console.print(_render_menu(save_name))
        choice = console.input("[bold cyan]请选择操作：[/]").strip()

        if choice == "0":
            console.clear()
            console.print(Panel.fit("已退出。", title="足球模拟器", border_style="cyan"))
            return

        if choice == "9":
            save_name = _action_manage_saves(save_name)
            continue

        console.clear()
        console.print(_render_header(save_name))
        action = actions.get(choice)
        if action is None:
            console.print(_render_error("无效选项。"))
            _pause()
            continue

        try:
            result = action(save_name)
            if result is not None:
                console.print(result)
        except (FileNotFoundError, ValueError) as exc:
            console.print(_render_error(str(exc)))
        _pause()


def _render_menu(save_name: str) -> Group:
    menu_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    menu_table.add_column("选项", width=8, justify="center", style="bold cyan", no_wrap=True)
    menu_table.add_column("功能", style="bold white", no_wrap=True)
    for key, label in MENU_OPTIONS:
        menu_table.add_row(key, label)

    return Group(
        _render_header(save_name),
        _render_save_overview(save_name),
        Panel(menu_table, title="主菜单", border_style="bright_blue"),
    )


def _render_header(save_name: str) -> Panel:
    title = Text("足球模拟器", style="bold white")
    subtitle = Text(f"当前存档：{save_name}", style="cyan")
    return Panel.fit(
        Group(title, subtitle),
        border_style="cyan",
        padding=(0, 2),
    )


def _render_save_overview(save_name: str) -> Panel:
    if not save_exists(save_name):
        return Panel(
            f"该存档目录还不存在。\n你可以先到“存档管理”里创建，或直接初始化当前存档。\n位置：{save_root() / save_name}",
            title="存档概览",
            border_style="yellow",
        )

    try:
        snapshot = load_save_snapshot(save_name)
    except (FileNotFoundError, ValueError):
        return Panel(
            "该存档还没有赛季数据。\n先选择“初始化赛季”开始。",
            title="存档概览",
            border_style="yellow",
        )

    next_week_text = "本赛季已全部完成"
    if snapshot.current_week < len(snapshot.weeks):
        next_week = snapshot.weeks[snapshot.current_week]
        if next_week.kind == "promotion_playoff":
            next_week_text = f"第 {next_week.week_number} 周 | {next_week.label}"
        else:
            rounds_text = "、".join(str(number) for number in next_week.round_numbers) or "无"
            next_week_text = f"第 {next_week.week_number} 周 | {next_week.label} | 双联赛轮次：{rounds_text}"

    info_table = Table(box=None, expand=True, show_header=False, pad_edge=False)
    info_table.add_column(style="bold white", width=16)
    info_table.add_column(style="cyan")
    info_table.add_row("赛季", f"第 {snapshot.season_number} 赛季")
    info_table.add_row("当前周次", f"{snapshot.current_week}/{len(snapshot.weeks)}")
    info_table.add_row("赛季状态", "已结束" if snapshot.season_complete else "进行中")
    info_table.add_row("待审能力变动", str(len(snapshot.pending_ability_review)))
    info_table.add_row("待审转会", str(len(snapshot.pending_transfer_review)))
    info_table.add_row("待处理选秀", "是" if snapshot.pending_draft.get("status") == "awaiting_input" else "否")
    info_table.add_row("历史赛季", str(len(snapshot.history)))
    info_table.add_row("下一个周次", next_week_text)
    return Panel(info_table, title="存档概览", border_style="blue")


def _action_initialize(save_name: str) -> Group:
    if not save_exists(save_name):
        create_save_directory(save_name)
    snapshot = initialize_save_state(save_name)
    store_current_save_name(save_name)

    team_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    team_table.add_column("联赛", style="bold cyan", width=10, no_wrap=True)
    team_table.add_column("球队", style="bold white")
    team_table.add_column("评分", justify="right", style="cyan", width=8)
    team_table.add_column("真实球员", justify="right", style="cyan", width=10)
    for team in snapshot.teams:
        team_table.add_row(team.division, team.name, f"{team.rating:.1f}", str(team.real_player_count))

    roster_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    roster_table.add_column("联赛", style="cyan", width=10, no_wrap=True)
    roster_table.add_column("球队", style="white", no_wrap=True)
    roster_table.add_column("位置", style="cyan", width=6, justify="center", no_wrap=True)
    roster_table.add_column("球员", style="bold white", no_wrap=True)
    roster_table.add_column("能力", justify="right", style="cyan", width=6, no_wrap=True)
    roster_table.add_column("类型", style="magenta", width=10, no_wrap=True)
    for team in snapshot.teams:
        for player in team.roster:
            roster_table.add_row(
                team.division,
                team.name,
                player.position,
                player.label,
                str(player.ability),
                "真实球员" if player.is_real else "默认球员",
            )

    return Group(
        Panel.fit(
            f"已为存档 '{snapshot.save_name}' 初始化第 {snapshot.season_number} 赛季。",
            title="初始化完成",
            border_style="green",
        ),
        Panel(team_table, title="球队概览", border_style="blue"),
        Panel(roster_table, title="初始球员分配", border_style="blue"),
    )


def _action_simulate_week(save_name: str) -> Group:
    result = simulate_next_week(save_name)
    store_current_save_name(save_name)
    summary = Panel.fit(
        Group(
            Text(
                f"第 {result.snapshot.season_number} 赛季 | 第 {result.week.week_number}/{len(result.snapshot.weeks)} 周",
                style="bold white",
            ),
            Text(f"周类型：{result.week.label}", style="cyan"),
        ),
        title="本周模拟完成",
        border_style="green",
    )

    blocks: List[Any] = [summary]
    if not result.premier_matchdays and not result.second_matchdays and not result.cup_matchdays and not result.playoff_matchdays:
        notice = "本周没有比赛，联赛处于休赛期。" if result.week.kind in {
            "winter_break",
            "summer_break",
        } else "本周没有比赛。"
        blocks.append(Panel(notice, title="赛果概览", border_style="yellow"))
    else:
        for matchday in result.premier_matchdays:
            blocks.append(_render_matchday_scores(matchday.round_number, matchday.results, matchday.competition))
        for matchday in result.second_matchdays:
            blocks.append(_render_matchday_scores(matchday.round_number, matchday.results, matchday.competition))
        for matchday in result.cup_matchdays:
            blocks.append(_render_matchday_scores(matchday.round_number, matchday.results, matchday.competition))
        for matchday in result.playoff_matchdays:
            blocks.append(_render_matchday_scores(matchday.round_number, matchday.results, matchday.competition))

    footer_lines = []
    if result.season_completed_now:
        champion = result.snapshot.table[0]
        footer_lines.append(f"赛季结束。冠军：{champion.team.name}，积分 {champion.points}。")
    elif result.snapshot.current_week < len(result.snapshot.weeks):
        next_week = result.snapshot.weeks[result.snapshot.current_week]
        footer_lines.append(f"下周：第 {next_week.week_number} 周（{next_week.label}）")
    footer_lines.append("如需查看详细事件，请回到菜单后选择“按场查看最近一周详细赛况”，再挑选具体比赛。")
    blocks.append(Panel("\n".join(footer_lines), title="后续信息", border_style="cyan"))
    if result.snapshot.pending_ability_review:
        review_group = _process_pending_ability_review(save_name, result.snapshot.pending_ability_review)
        blocks.append(review_group)
    if result.snapshot.pending_transfer_review:
        transfer_group = _process_pending_transfer_review(save_name, result.snapshot.pending_transfer_review)
        blocks.append(transfer_group)
    refreshed_snapshot = load_save_snapshot(save_name)
    if refreshed_snapshot.pending_draft.get("status") == "awaiting_input":
        draft_group = _process_pending_draft(save_name)
        blocks.append(draft_group)
    return Group(*blocks)


def _render_matchday_scores(round_number: int, results: List[Any], competition: str = "一级联赛") -> Panel:
    score_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    score_table.add_column("主队", style="bold white", no_wrap=True)
    score_table.add_column("比分", justify="center", style="cyan", width=9, no_wrap=True)
    score_table.add_column("客队", style="bold white", no_wrap=True)
    for match in results:
        score_table.add_row(
            match.home_team.name,
            f"{match.home_goals}-{match.away_goals}",
            match.away_team.name,
        )
    title = f"{competition} | 第 {round_number} 轮赛果" if competition != "升级附加赛" else f"{competition} | 第 {round_number} 回合"
    return Panel(score_table, title=title, border_style="blue")


def _action_recent_week_details(save_name: str) -> Group:
    snapshot = load_save_snapshot(save_name)
    if not snapshot.simulated_weeks:
        raise ValueError("当前还没有已模拟的周次。")

    week_data = snapshot.simulated_weeks[-1]
    matchdays = (
        [("一级联赛", item) for item in week_data.get("premier_matchdays", [])]
        + [("次级联赛", item) for item in week_data.get("second_matchdays", [])]
        + [(item.get("competition", "杯赛"), item) for item in week_data.get("cup_matchdays", [])]
        + [("升级附加赛", item) for item in week_data.get("playoff_matchdays", [])]
    )
    if not matchdays:
        return Group(
            Panel.fit(
                Group(
                    Text(f"第 {week_data['week_number']} 周详细赛况", style="bold white"),
                    Text(f"周类型：{week_data['label']}", style="cyan"),
                ),
                title="最近一周",
                border_style="green",
            ),
            Panel("本周没有比赛。", title="详细赛况", border_style="yellow"),
        )

    selection_rows: List[dict] = []
    selection_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    selection_table.add_column("编号", justify="center", width=8, style="bold cyan", no_wrap=True)
    selection_table.add_column("赛事", justify="center", width=10, style="cyan", no_wrap=True)
    selection_table.add_column("轮次", justify="center", width=8, style="cyan", no_wrap=True)
    selection_table.add_column("主队", style="bold white", no_wrap=True)
    selection_table.add_column("比分", justify="center", width=9, style="cyan", no_wrap=True)
    selection_table.add_column("客队", style="bold white", no_wrap=True)

    match_number = 1
    for competition, matchday in matchdays:
        for result in matchday.get("results", []):
            selection_rows.append(
                {
                    "match_number": match_number,
                    "competition": competition,
                    "round_number": matchday["round_number"],
                    "result": result,
                }
            )
            selection_table.add_row(
                str(match_number),
                competition,
                str(matchday["round_number"]),
                result["home_team"],
                f"{result['home_goals']}-{result['away_goals']}",
                result["away_team"],
            )
            match_number += 1

    console.print(
        Group(
            Panel.fit(
                Group(
                    Text(f"第 {week_data['week_number']} 周详细赛况", style="bold white"),
                    Text(f"周类型：{week_data['label']}", style="cyan"),
                ),
                title="最近一周",
                border_style="green",
            ),
            Panel(selection_table, title="请选择要查看的比赛", border_style="blue"),
        )
    )

    choice = console.input("[bold cyan]请输入比赛编号：[/]").strip()
    if not choice.isdigit():
        raise ValueError("比赛编号必须是数字。")

    selected_index = int(choice)
    selected_row = next(
        (row for row in selection_rows if row["match_number"] == selected_index),
        None,
    )
    if selected_row is None:
        raise ValueError("未找到对应的比赛编号。")

    console.clear()
    console.print(_render_header(save_name))
    return _render_selected_match_detail(week_data, selected_row)


def _render_selected_match_detail(week_data: dict, selected_row: dict) -> Group:
    result = selected_row["result"]
    info_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    info_table.add_column("项目", style="bold white", width=16, no_wrap=True)
    info_table.add_column("内容", style="cyan", no_wrap=True)
    info_table.add_row("周次", f"第 {week_data['week_number']} 周")
    info_table.add_row("赛事", selected_row["competition"])
    info_table.add_row("轮次", f"第 {selected_row['round_number']} 轮")
    info_table.add_row("对阵", f"{result['home_team']} vs {result['away_team']}")
    info_table.add_row("比分", f"{result['home_goals']}-{result['away_goals']}")

    event_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    event_table.add_column("事件", style="white")
    events = result.get("key_events", []) or ["本场没有关键事件。"]
    for event in events:
        event_table.add_row(event)

    return Group(
        Panel(info_table, title="比赛概览", border_style="blue"),
        Panel(event_table, title="关键事件", border_style="blue"),
    )


def _action_status(save_name: str) -> Panel:
    snapshot = load_save_snapshot(save_name)
    store_current_save_name(save_name)
    status_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    status_table.add_column("项目", style="bold white", width=16, no_wrap=True)
    status_table.add_column("内容", style="cyan", no_wrap=True)
    status_table.add_row("当前赛季", f"第 {snapshot.season_number} 赛季")
    status_table.add_row("当前周次", f"{snapshot.current_week}/{len(snapshot.weeks)}")
    status_table.add_row("赛季是否结束", "是" if snapshot.season_complete else "否")
    status_table.add_row(
        "已模拟一级轮次",
        f"{sum(len(week_data.get('premier_matchdays', [])) for week_data in snapshot.simulated_weeks)}/38",
    )
    status_table.add_row(
        "已模拟次级轮次",
        f"{sum(len(week_data.get('second_matchdays', [])) for week_data in snapshot.simulated_weeks)}/38",
    )
    status_table.add_row("待审能力变动", str(len(snapshot.pending_ability_review)))
    status_table.add_row("待审转会", str(len(snapshot.pending_transfer_review)))
    status_table.add_row("已归档历史赛季", str(len(snapshot.history)))
    if snapshot.current_week < len(snapshot.weeks):
        next_week = snapshot.weeks[snapshot.current_week]
        status_table.add_row("下周", f"第 {next_week.week_number} 周")
        status_table.add_row("周类型", next_week.label)
        status_table.add_row(
            "轮次",
            "、".join(str(number) for number in next_week.round_numbers) or "无",
        )
    return Panel(status_table, title="赛季状态", border_style="blue")


def _action_standings(save_name: str) -> Panel:
    snapshot = load_save_snapshot(save_name)
    store_current_save_name(save_name)
    return Panel(
        Group(
            _build_standings_table(snapshot.premier_table),
            Text(""),
            _build_standings_table(snapshot.second_table),
        ),
        title="双联赛积分榜",
        border_style="blue",
    )


def _build_standings_table(rows: List[Any]) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("名次", justify="right", width=6, style="bold cyan", no_wrap=True)
    table.add_column("球队", style="bold white", min_width=24, no_wrap=True)
    table.add_column("赛", justify="right", width=5, style="cyan", no_wrap=True)
    table.add_column("胜", justify="right", width=5, style="cyan", no_wrap=True)
    table.add_column("平", justify="right", width=5, style="cyan", no_wrap=True)
    table.add_column("负", justify="right", width=5, style="cyan", no_wrap=True)
    table.add_column("进", justify="right", width=5, style="cyan", no_wrap=True)
    table.add_column("失", justify="right", width=5, style="cyan", no_wrap=True)
    table.add_column("净", justify="right", width=5, style="cyan", no_wrap=True)
    table.add_column("积分", justify="right", width=6, style="bold green", no_wrap=True)
    for position, row in enumerate(rows, start=1):
        table.add_row(
            str(position),
            row.team.name,
            str(row.played),
            str(row.wins),
            str(row.draws),
            str(row.losses),
            str(row.goals_for),
            str(row.goals_against),
            str(row.goals_for - row.goals_against),
            str(row.points),
        )
    table.title = rows[0].team.division if rows else "积分榜"
    return table


def _action_player_leaders(save_name: str) -> Panel:
    snapshot = load_save_snapshot(save_name)
    store_current_save_name(save_name)
    scope = _prompt_scope()
    metric = _prompt_metric("player", PLAYER_METRICS)
    rows = _player_rows(snapshot, scope)
    rows = _sort_metric_rows(rows, metric, lower_is_better=False)[:10]

    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("球员", style="bold white", no_wrap=True)
    table.add_column("球队", style="white", no_wrap=True)
    table.add_column("赛季", justify="center", width=10, style="cyan", no_wrap=True)
    table.add_column("数值", justify="right", width=8, style="bold green", no_wrap=True)
    for row in rows:
        table.add_row(
            row["label"],
            row.get("team_name", "-"),
            str(row.get("season_number", row.get("seasons", "-"))),
            str(row[metric]),
        )
    return Panel(table, title=f"球员排行榜 | {SCOPE_LABELS[scope]} | {PLAYER_METRICS[metric]}", border_style="blue")


def _action_team_leaders(save_name: str) -> Panel:
    snapshot = load_save_snapshot(save_name)
    store_current_save_name(save_name)
    scope = _prompt_scope()
    metric = _prompt_metric("team", TEAM_METRICS)
    rows = _team_rows(snapshot, scope)
    rows = _sort_metric_rows(rows, metric, lower_is_better=(metric == "goals_against"))[:10]

    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("球队", style="bold white", no_wrap=True)
    table.add_column("赛季", justify="center", width=10, style="cyan", no_wrap=True)
    table.add_column("数值", justify="right", width=8, style="bold green", no_wrap=True)
    for row in rows:
        table.add_row(
            row["team_name"],
            str(row.get("season_number", row.get("seasons", "-"))),
            str(row[metric]),
        )
    return Panel(table, title=f"球队排行榜 | {SCOPE_LABELS[scope]} | {TEAM_METRICS[metric]}", border_style="blue")


def _action_honor_leaders(save_name: str) -> Panel:
    snapshot = load_save_snapshot(save_name)
    store_current_save_name(save_name)

    option_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    option_table.add_column("编号", justify="center", width=8, style="bold cyan", no_wrap=True)
    option_table.add_column("榜单", style="white", no_wrap=True)
    option_table.add_row("1", "历史球队荣誉积分榜")
    option_table.add_row("2", "历史球员荣誉积分榜")
    console.print(Panel(option_table, title="选择荣誉榜", border_style="blue"))
    choice = console.input("[bold cyan]请选择荣誉榜：[/]").strip()

    if choice == "1":
        rows = get_team_honor_leaders(snapshot)[:20]
        table = Table(box=box.SIMPLE_HEAVY, expand=True)
        table.add_column("名次", justify="center", width=6, style="bold cyan", no_wrap=True)
        table.add_column("球队", style="bold white", no_wrap=True)
        table.add_column("荣誉积分", justify="right", width=10, style="bold green", no_wrap=True)
        table.add_column("总冠军", justify="right", width=8, style="cyan", no_wrap=True)
        table.add_column("联赛", justify="right", width=6, style="cyan", no_wrap=True)
        table.add_column("次级", justify="right", width=6, style="cyan", no_wrap=True)
        table.add_column("优胜者杯", justify="right", width=10, style="cyan", no_wrap=True)
        table.add_column("挑战杯", justify="right", width=8, style="cyan", no_wrap=True)
        table.add_column("超级杯", justify="right", width=8, style="cyan", no_wrap=True)
        for index, row in enumerate(rows, start=1):
            table.add_row(
                str(index),
                row["team_name"],
                str(row.get("honor_points", 0)),
                str(row.get("total_titles", 0)),
                str(row.get("premier_titles", 0)),
                str(row.get("second_titles", 0)),
                str(row.get("winners_cup_titles", 0)),
                str(row.get("challenge_cup_titles", 0)),
                str(row.get("super_cup_titles", 0)),
            )
        return Panel(table, title="历史球队荣誉积分榜", border_style="blue")

    if choice == "2":
        rows = get_player_honor_leaders(snapshot)[:20]
        table = Table(box=box.SIMPLE_HEAVY, expand=True)
        table.add_column("名次", justify="center", width=6, style="bold cyan", no_wrap=True)
        table.add_column("球员", style="bold white", no_wrap=True)
        table.add_column("球队", style="white", no_wrap=True)
        table.add_column("荣誉积分", justify="right", width=10, style="bold green", no_wrap=True)
        table.add_column("总冠军", justify="right", width=8, style="cyan", no_wrap=True)
        table.add_column("联赛", justify="right", width=6, style="cyan", no_wrap=True)
        table.add_column("次级", justify="right", width=6, style="cyan", no_wrap=True)
        table.add_column("优胜者杯", justify="right", width=10, style="cyan", no_wrap=True)
        table.add_column("挑战杯", justify="right", width=8, style="cyan", no_wrap=True)
        table.add_column("超级杯", justify="right", width=8, style="cyan", no_wrap=True)
        for index, row in enumerate(rows, start=1):
            table.add_row(
                str(index),
                row["label"],
                row.get("team_name", "-"),
                str(row.get("honor_points", 0)),
                str(row.get("total_titles", 0)),
                str(row.get("premier_titles", 0)),
                str(row.get("second_titles", 0)),
                str(row.get("winners_cup_titles", 0)),
                str(row.get("challenge_cup_titles", 0)),
                str(row.get("super_cup_titles", 0)),
            )
        return Panel(table, title="历史球员荣誉积分榜", border_style="blue")

    raise ValueError("荣誉榜编号必须是 1 或 2。")


def _action_player_detail(save_name: str) -> Group:
    snapshot = load_save_snapshot(save_name)
    store_current_save_name(save_name)
    query = console.input("[bold cyan]请输入球员名：[/]").strip()
    if not query:
        raise ValueError("球员名不能为空。")
    return _format_player_detail(snapshot, query)


def _action_team_status(save_name: str) -> Optional[Group]:
    snapshot = load_save_snapshot(save_name)
    store_current_save_name(save_name)
    selected_team_name = _prompt_team_selection(snapshot)
    console.clear()
    console.print(_render_header(save_name))
    team_view, player_options = _build_team_status_view(snapshot, selected_team_name)
    console.print(team_view)

    if not player_options:
        return None

    choice = console.input("[bold cyan]输入球员编号查看完整状态，直接回车返回：[/]").strip()
    if not choice:
        return None

    selected_player = player_options.get(choice)
    if selected_player is None:
        raise ValueError("未找到对应的球员编号。")

    console.clear()
    console.print(_render_header(save_name))
    return Group(
        team_view,
        _render_selected_player_status(snapshot, selected_team_name, selected_player),
    )


def _prompt_scope() -> str:
    scope_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    scope_table.add_column("编号", justify="center", width=8, style="bold cyan", no_wrap=True)
    scope_table.add_column("范围", style="white", no_wrap=True)
    options = [("1", "current"), ("2", "history"), ("3", "record")]
    for number, key in options:
        scope_table.add_row(number, SCOPE_LABELS[key])
    console.print(Panel(scope_table, title="选择榜单范围", border_style="blue"))
    scope = console.input("[bold cyan]请选择范围：[/]").strip().lower()
    mapped_scope = dict(options).get(scope, scope)
    if mapped_scope not in SCOPE_LABELS:
        raise ValueError("范围必须是 1、2、3 或对应的内部标识。")
    return mapped_scope


def _action_manage_saves(current_save_name: str) -> str:
    while True:
        console.clear()
        console.print(_render_header(current_save_name))
        console.print(_render_save_management(current_save_name))
        choice = console.input("[bold cyan]请选择操作：[/]").strip()

        try:
            if choice == "0":
                return current_save_name

            if choice == "1":
                new_name = console.input("[bold cyan]请输入新存档名：[/]").strip()
                normalized_name = normalize_save_name(new_name)
                create_save_directory(normalized_name)
                store_current_save_name(normalized_name)
                _show_message(f"已新建并切换到存档：{normalized_name}")
                current_save_name = normalized_name
                continue

            if choice == "2":
                selected_name = _prompt_save_selection("请选择要切换到的存档")
                store_current_save_name(selected_name)
                _show_message(f"已切换到存档：{selected_name}")
                current_save_name = selected_name
                continue

            if choice == "3":
                selected_name = _prompt_save_selection("请选择要删除的存档")
                if selected_name == current_save_name:
                    _show_message("不能删除当前正在使用的存档。请先切换到其他存档。")
                    continue
                delete_save_directory(selected_name)
                _show_message(f"已删除存档：{selected_name}")
                continue

            _show_message("无效选项。")
        except (FileNotFoundError, ValueError) as exc:
            _show_message(str(exc))


def _render_save_management(current_save_name: str) -> Group:
    saves = list_save_names()

    save_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    save_table.add_column("编号", justify="center", width=8, style="bold cyan", no_wrap=True)
    save_table.add_column("存档名", style="bold white", no_wrap=True)
    save_table.add_column("状态", style="cyan", no_wrap=True)
    save_table.add_column("路径", style="white")

    if not saves:
        save_table.add_row("-", "暂无存档", "未创建", str(save_root()))
    else:
        for index, save_name in enumerate(saves, start=1):
            save_table.add_row(
                str(index),
                save_name,
                _describe_save_status(save_name, current_save_name),
                str(save_root() / save_name),
            )

    action_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    action_table.add_column("编号", justify="center", width=8, style="bold cyan", no_wrap=True)
    action_table.add_column("操作", style="white", no_wrap=True)
    action_table.add_row("1", "新建存档")
    action_table.add_row("2", "选择存档")
    action_table.add_row("3", "删除存档")
    action_table.add_row("0", "返回主菜单")

    return Group(
        Panel(save_table, title="存档列表", border_style="blue"),
        Panel(action_table, title="存档操作", border_style="blue"),
    )


def _describe_save_status(save_name: str, current_save_name: str) -> str:
    marker = "当前使用中" if save_name == current_save_name else "可选"
    try:
        snapshot = load_save_snapshot(save_name)
    except (FileNotFoundError, ValueError):
        return f"{marker} | 未初始化"

    season_state = "已结束" if snapshot.season_complete else "进行中"
    return f"{marker} | 第 {snapshot.season_number} 赛季 | 第 {snapshot.current_week}/{len(snapshot.weeks)} 周 | {season_state}"


def _prompt_save_selection(title: str) -> str:
    saves = list_save_names()
    if not saves:
        raise ValueError("当前没有可用存档。")

    save_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    save_table.add_column("编号", justify="center", width=8, style="bold cyan", no_wrap=True)
    save_table.add_column("存档名", style="bold white", no_wrap=True)
    for index, save_name in enumerate(saves, start=1):
        save_table.add_row(str(index), save_name)

    console.clear()
    console.print(Panel(save_table, title=title, border_style="blue"))
    choice = console.input("[bold cyan]请输入存档编号：[/]").strip()
    if not choice.isdigit():
        raise ValueError("存档编号必须是数字。")

    selected_index = int(choice)
    if selected_index < 1 or selected_index > len(saves):
        raise ValueError("未找到对应的存档编号。")
    return saves[selected_index - 1]


def _prompt_metric(entity: str, metric_map: Dict[str, str]) -> str:
    entity_label = "球员" if entity == "player" else "球队"
    metric_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    metric_table.add_column("编号", justify="center", width=8, style="bold cyan", no_wrap=True)
    metric_table.add_column(f"{entity_label}指标", style="white", no_wrap=True)
    entries = list(metric_map.items())
    for index, (_, label) in enumerate(entries, start=1):
        metric_table.add_row(str(index), label)
    console.print(Panel(metric_table, title=f"选择{entity_label}指标", border_style="blue"))
    metric = console.input("[bold cyan]请选择指标：[/]").strip()
    if metric.isdigit():
        index = int(metric) - 1
        if 0 <= index < len(entries):
            return entries[index][0]
    if metric in metric_map:
        return metric
    raise ValueError(f"不支持的指标：{metric}")


def _player_rows(snapshot, scope: str) -> List[dict]:
    if scope == "current":
        return [
            {
                "label": row.player.label,
                "team_name": row.team_name,
                "season_number": row.season_number,
                **{key: getattr(row, key) for key in PLAYER_METRICS},
            }
            for row in snapshot.player_stats
        ]
    if scope == "history":
        return get_player_history_totals(snapshot)
    return get_player_single_season_records(snapshot)


def _team_rows(snapshot, scope: str) -> List[dict]:
    if scope == "current":
        return [
            {
                "team_name": row.team_name,
                "division": row.division,
                "season_number": row.season_number,
                "played": row.played,
                "wins": row.wins,
                "draws": row.draws,
                "losses": row.losses,
                "goals_for": row.goals_for,
                "goals_against": row.goals_against,
                "points": row.points,
                **{key: getattr(row, key) for key in TEAM_METRICS if hasattr(row, key)},
                "goal_diff": row.goal_diff,
            }
            for row in snapshot.team_stats
        ]
    if scope == "history":
        return get_team_history_totals(snapshot)
    return get_team_single_season_records(snapshot)


def _format_player_detail(snapshot, query: str) -> Group:
    current_rows = find_player_rows(snapshot, query)
    if not current_rows:
        raise ValueError(f"当前赛季没有匹配到球员 '{query}'。")

    history_totals = {row["player_id"]: row for row in get_player_history_totals(snapshot)}
    best_single_season = {}
    for row in get_player_single_season_records(snapshot):
        history_key = _player_history_key_for_row(row)
        current_best = best_single_season.get(history_key)
        score = (
            row["goals"],
            row["assists"],
            row["chances_created"],
            row["successful_defenses"],
            row["successful_saves"],
            row.get("clean_sheets", 0),
        )
        if current_best is None or score > (
            current_best["goals"],
            current_best["assists"],
            current_best["chances_created"],
            current_best["successful_defenses"],
            current_best["successful_saves"],
            current_best.get("clean_sheets", 0),
        ):
            best_single_season[history_key] = row

    blocks: List[Any] = []
    for row in current_rows:
        history_key = _player_history_key_for_row(row)
        history = history_totals[history_key]
        record = best_single_season[history_key]
        detail_table = Table(box=box.SIMPLE_HEAVY, expand=True)
        detail_table.add_column("维度", style="bold white", width=16)
        detail_table.add_column("内容", style="cyan")
        detail_table.add_row("球队", row["team_name"])
        detail_table.add_row("位置", row["position"])
        detail_table.add_row("赛季", str(row["season_number"]))
        detail_table.add_row("本赛季", _format_player_stat_line(row))
        detail_table.add_row("本次结算评分", _format_rating_value(row.get("season_rating")))
        detail_table.add_row("本次结算身价", _format_market_value(row))
        detail_table.add_row(
            "本赛季杯赛",
            _format_cup_result_line(row),
        )
        detail_table.add_row("本赛季荣誉", f"荣誉积分 {row.get('honor_points', 0)} | {_format_honor_breakdown(row)}")
        detail_table.add_row(
            "历史总计",
            f"{_format_player_stat_line(history)} | 赛季数 {history['seasons']} | 荣誉积分 {history.get('honor_points', 0)} | {_format_honor_breakdown(history)}",
        )
        detail_table.add_row(
            "最佳赛季",
            f"第 {record['season_number']} 赛季 | {_format_player_stat_line(record)}",
        )
        blocks.append(Panel(detail_table, title=f"球员详情 | {row['label']}", border_style="blue"))
    return Group(*blocks)


def _format_team_detail(snapshot, query: str) -> Group:
    current_rows = find_team_rows(snapshot, query)
    if not current_rows:
        raise ValueError(f"当前赛季没有匹配到球队 '{query}'。")

    history_totals = {row["team_name"]: row for row in get_team_history_totals(snapshot)}
    best_single_season = {}
    for row in get_team_single_season_records(snapshot):
        current_best = best_single_season.get(row["team_name"])
        score = (row["points"], row["goal_diff"], row["goals_for"], row["goals"])
        if current_best is None or score > (
            current_best["points"],
            current_best["goal_diff"],
            current_best["goals_for"],
            current_best["goals"],
        ):
            best_single_season[row["team_name"]] = row

    blocks: List[Any] = []
    for row in current_rows:
        history = history_totals[row["team_name"]]
        record = best_single_season[row["team_name"]]
        detail_table = Table(box=box.SIMPLE_HEAVY, expand=True)
        detail_table.add_column("维度", style="bold white", width=16)
        detail_table.add_column("内容", style="cyan")
        detail_table.add_row("所属联赛", row.get("division", "未知"))
        detail_table.add_row("赛季", str(row["season_number"]))
        detail_table.add_row(
            "本赛季战绩",
            (
                f"赛 {row['played']} | 胜 {row['wins']} | 平 {row['draws']} | 负 {row['losses']} | "
                f"进 {row['goals_for']} | 失 {row['goals_against']} | 净胜球 {row['goals_for'] - row['goals_against']} | "
                f"积分 {row['points']}"
            ),
        )
        detail_table.add_row(
            "本赛季球队数据",
            (
                f"进球 {row['goals']} | 助攻 {row['assists']} | 创造机会 {row['chances_created']} | "
                f"成功防守 {row['successful_defenses']} | 成功扑救 {row['successful_saves']} | 零封 {row.get('clean_sheets', 0)}"
            ),
        )
        detail_table.add_row("本次结算总身价", _format_team_market_value(row))
        detail_table.add_row("本赛季杯赛", _format_cup_result_line(row))
        detail_table.add_row("本赛季荣誉", f"荣誉积分 {row.get('honor_points', 0)} | {_format_honor_breakdown(row)}")
        detail_table.add_row(
            "历史总计",
            (
                f"赛季数 {history['seasons']} | 胜 {history['wins']} | 进 {history['goals_for']} | "
                f"失 {history['goals_against']} | 积分 {history['points']} | 进球 {history['goals']} | 零封 {history.get('clean_sheets', 0)} | "
                f"荣誉积分 {history.get('honor_points', 0)} | {_format_honor_breakdown(history)}"
            ),
        )
        detail_table.add_row(
            "最佳赛季",
            (
                f"第 {record['season_number']} 赛季 | 积分 {record['points']} | 进 {record['goals_for']} | "
                f"失 {record['goals_against']} | 净胜球 {record['goal_diff']} | 进球 {record['goals']}"
            ),
        )
        blocks.append(Panel(detail_table, title=f"球队详情 | {row['team_name']}", border_style="blue"))
    return Group(*blocks)


def _prompt_team_selection(snapshot) -> str:
    selection_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    selection_table.add_column("编号", justify="center", width=8, style="bold cyan")
    selection_table.add_column("联赛", style="cyan", width=10, no_wrap=True)
    selection_table.add_column("球队", style="bold white")
    selection_table.add_column("名次", justify="right", width=6, style="cyan")
    selection_table.add_column("积分", justify="right", width=6, style="bold green")
    selection_table.add_column("战绩", style="white", width=18)

    options: Dict[str, str] = {}
    all_rows = [*snapshot.premier_table, *snapshot.second_table]
    for index, row in enumerate(all_rows, start=1):
        options[str(index)] = row.team.name
        selection_table.add_row(
            str(index),
            row.team.division,
            row.team.name,
            str(index),
            str(row.points),
            f"{row.wins}胜 {row.draws}平 {row.losses}负",
        )

    console.print(Panel(selection_table, title="选择球队", border_style="blue"))
    choice = console.input("[bold cyan]请输入球队编号：[/]").strip()
    team_name = options.get(choice)
    if team_name is None:
        raise ValueError("未找到对应的球队编号。")
    return team_name


def _build_team_status_view(snapshot, team_name: str) -> tuple[Group, Dict[str, dict]]:
    current_rows = [row for row in find_team_rows(snapshot, team_name) if row["team_name"] == team_name]
    if not current_rows:
        raise ValueError(f"当前赛季没有匹配到球队 '{team_name}'。")

    row = current_rows[0]
    history_totals = {entry["team_name"]: entry for entry in get_team_history_totals(snapshot)}
    history = history_totals[team_name]

    best_single_season = {}
    for entry in get_team_single_season_records(snapshot):
        current_best = best_single_season.get(entry["team_name"])
        score = (entry["points"], entry["goal_diff"], entry["goals_for"], entry["goals"])
        if current_best is None or score > (
            current_best["points"],
            current_best["goal_diff"],
            current_best["goals_for"],
            current_best["goals"],
        ):
            best_single_season[entry["team_name"]] = entry
    record = best_single_season[team_name]

    detail_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    detail_table.add_column("维度", style="bold white", width=16, no_wrap=True)
    detail_table.add_column("内容", style="cyan", no_wrap=True)
    detail_table.add_row("所属联赛", row["division"])
    detail_table.add_row(
        "本赛季战绩",
        (
            f"赛 {row['played']} | 胜 {row['wins']} | 平 {row['draws']} | 负 {row['losses']} | "
            f"进 {row['goals_for']} | 失 {row['goals_against']} | 净胜球 {row['goal_diff']} | 积分 {row['points']}"
        ),
    )
    detail_table.add_row(
        "本赛季球队数据",
        (
            f"进球 {row['goals']} | 助攻 {row['assists']} | 创造机会 {row['chances_created']} | "
            f"成功防守 {row['successful_defenses']} | 成功扑救 {row['successful_saves']} | 零封 {row.get('clean_sheets', 0)}"
        ),
    )
    detail_table.add_row("本次结算总身价", _format_team_market_value(row))
    detail_table.add_row("本赛季杯赛成绩", _format_cup_result_line(row))
    detail_table.add_row("本赛季荣誉", f"荣誉积分 {row.get('honor_points', 0)} | {_format_honor_breakdown(row)}")
    detail_table.add_row(
        "历史总计",
        (
            f"赛季数 {history['seasons']} | 胜 {history['wins']} | 进 {history['goals_for']} | "
            f"失 {history['goals_against']} | 积分 {history['points']} | 进球 {history['goals']} | 零封 {history.get('clean_sheets', 0)} | "
            f"荣誉积分 {history.get('honor_points', 0)}"
        ),
    )
    detail_table.add_row(
        "最佳赛季",
        (
            f"第 {record['season_number']} 赛季 | 积分 {record['points']} | 进 {record['goals_for']} | "
            f"失 {record['goals_against']} | 净胜球 {record['goal_diff']} | 进球 {record['goals']}"
        ),
    )

    honor_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    honor_table.add_column("项目", style="bold white", no_wrap=True)
    honor_table.add_column("内容", style="cyan", no_wrap=True)
    honor_table.add_row("总荣誉积分", str(history.get("honor_points", 0)))
    honor_table.add_row(
        "冠军统计",
        _format_honor_breakdown(history),
    )

    seasons_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    seasons_table.add_column("赛季", justify="center", width=8, style="bold cyan", no_wrap=True)
    seasons_table.add_column("联赛", justify="center", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("联赛成绩", style="white", no_wrap=True)
    seasons_table.add_column("优胜者杯", justify="center", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("挑战杯", justify="center", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("超级杯", justify="center", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("总身价", justify="right", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("荣誉积分", justify="right", width=10, style="bold green", no_wrap=True)
    team_seasons = sorted(
        [entry for entry in get_team_single_season_records(snapshot) if entry["team_name"] == team_name],
        key=lambda entry: entry["season_number"],
    )
    for season in team_seasons:
        seasons_table.add_row(
            f"S{season['season_number']}",
            season.get("division", row["division"]),
            f"{season.get('league_result', '未定')} | 积{season['points']} | 净{season['goal_diff']}",
            season.get("winners_cup_result", "未参赛"),
            season.get("challenge_cup_result", "未参赛"),
            season.get("super_cup_result", "未参赛"),
            _format_team_market_value(season),
            str(season.get("honor_points", 0)),
        )

    team = next(team for team in snapshot.teams if team.name == team_name)
    current_player_stats = {entry.player.player_id: entry for entry in snapshot.player_stats if entry.team_name == team_name}

    roster_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    roster_table.add_column("编号", justify="center", width=6, style="bold cyan", no_wrap=True)
    roster_table.add_column("球员", style="bold white", no_wrap=True)
    roster_table.add_column("位置", justify="center", width=6, style="cyan", no_wrap=True)
    roster_table.add_column("能力", justify="right", width=6, style="cyan", no_wrap=True)
    roster_table.add_column("类型", width=10, style="magenta", no_wrap=True)
    roster_table.add_column("本赛季状态", style="white", no_wrap=True)
    roster_table.add_column("评分", justify="right", width=8, style="cyan", no_wrap=True)
    roster_table.add_column("身价", justify="right", width=10, style="bold green", no_wrap=True)

    player_options: Dict[str, dict] = {}
    for index, player in enumerate(team.roster, start=1):
        current = current_player_stats[player.player_id]
        player_options[str(index)] = {
            "player": player,
            "current": current,
        }
        roster_table.add_row(
            str(index),
            player.label,
            player.position,
            str(player.ability),
            "真实球员" if player.is_real else "默认球员",
            _format_player_stat_short(current.player.position, current),
            _format_rating_value(current.season_rating),
            _format_market_value(current),
        )

    return (
        Group(
            Panel(detail_table, title=f"球队状态 | {team_name}", border_style="blue"),
            Panel(honor_table, title="球队荣誉", border_style="blue"),
            Panel(seasons_table, title="球队历季情况", border_style="blue"),
            Panel(roster_table, title="队内球员当赛季状态", border_style="blue"),
        ),
        player_options,
    )


def _render_selected_player_status(snapshot, team_name: str, selected_player: dict) -> Panel:
    player = selected_player["player"]
    current = selected_player["current"]
    all_player_rows = sorted(
        [
            entry
            for entry in get_player_single_season_records(snapshot)
            if entry["label"] == player.label and (player.is_real or entry["team_name"] == team_name)
        ],
        key=lambda entry: entry["season_number"],
    )

    total_goals = sum(entry["goals"] for entry in all_player_rows)
    total_assists = sum(entry["assists"] for entry in all_player_rows)
    total_chances = sum(entry["chances_created"] for entry in all_player_rows)
    total_defenses = sum(entry["successful_defenses"] for entry in all_player_rows)
    total_saves = sum(entry["successful_saves"] for entry in all_player_rows)
    total_clean_sheets = sum(entry.get("clean_sheets", 0) for entry in all_player_rows)
    history_totals = {row["player_id"]: row for row in get_player_history_totals(snapshot)}
    current_snapshot_row = max(all_player_rows, key=lambda entry: entry["season_number"])
    history_key = _player_history_key_for_player(player)
    history_honors = history_totals.get(history_key, {})

    summary_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    summary_table.add_column("维度", style="bold white", width=16, no_wrap=True)
    summary_table.add_column("内容", style="cyan", no_wrap=True)
    summary_table.add_row("球队", team_name)
    summary_table.add_row("位置", player.position)
    summary_table.add_row("能力", str(player.ability))
    summary_table.add_row("类型", "真实球员" if player.is_real else "默认球员")
    summary_table.add_row(
        "本赛季状态",
        _format_player_stat_line(
            {
                "position": player.position,
                "goals": current.goals,
                "assists": current.assists,
                "chances_created": current.chances_created,
                "successful_defenses": current.successful_defenses,
                "successful_saves": current.successful_saves,
                "clean_sheets": current.clean_sheets,
            }
        ),
    )
    summary_table.add_row("本赛季杯赛", _format_cup_result_line(current_snapshot_row))
    summary_table.add_row("本次结算评分", _format_rating_value(current_snapshot_row.get("season_rating")))
    summary_table.add_row("本次结算身价", _format_market_value(current_snapshot_row))
    summary_table.add_row(
        "本赛季荣誉",
        f"荣誉积分 {current_snapshot_row.get('honor_points', 0)} | {_format_honor_breakdown(current_snapshot_row)}",
    )
    summary_table.add_row(
        "历史总计",
        (
            f"{_format_player_stat_line({'position': player.position, 'goals': total_goals, 'assists': total_assists, 'chances_created': total_chances, 'successful_defenses': total_defenses, 'successful_saves': total_saves, 'clean_sheets': total_clean_sheets})} | "
            f"赛季数 {len(all_player_rows)} | 荣誉积分 {history_honors.get('honor_points', 0)} | {_format_honor_breakdown(history_honors)}"
        ),
    )

    seasons_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    seasons_table.add_column("赛季", justify="center", width=8, style="bold cyan", no_wrap=True)
    seasons_table.add_column("球队", style="white", no_wrap=True)
    seasons_table.add_column("联赛", justify="center", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("联赛成绩", style="white", no_wrap=True)
    seasons_table.add_column("优胜者杯", justify="center", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("挑战杯", justify="center", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("超级杯", justify="center", width=10, style="cyan", no_wrap=True)
    seasons_table.add_column("当季状态", style="cyan", no_wrap=True)
    seasons_table.add_column("评分", justify="right", width=8, style="cyan", no_wrap=True)
    seasons_table.add_column("身价", justify="right", width=10, style="bold green", no_wrap=True)
    seasons_table.add_column("荣誉积分", justify="right", width=10, style="bold green", no_wrap=True)
    for row in all_player_rows:
        seasons_table.add_row(
            f"S{row['season_number']}",
            row["team_name"],
            row.get("division", "未知"),
            row.get("league_result", "未定"),
            row.get("winners_cup_result", "未参赛"),
            row.get("challenge_cup_result", "未参赛"),
            row.get("super_cup_result", "未参赛"),
            _format_player_stat_short(row["position"], row),
            _format_rating_value(row.get("season_rating")),
            _format_market_value(row),
            str(row.get("honor_points", 0)),
        )

    return Panel(
        Group(
            summary_table,
            Text(""),
            seasons_table,
        ),
        title=f"球员完整状态 | {player.label}",
        border_style="blue",
    )


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


def _render_error(message: str) -> Panel:
    return Panel(message, title="错误", border_style="red")


def _show_message(message: str) -> None:
    console.print(Panel.fit(message, title="提示", border_style="cyan"))
    _pause()


def _pause() -> None:
    console.input("\n[dim]按回车返回菜单[/]")


def _prepare_terminal() -> None:
    try:
        console.file.write("\x1b[8;42;168t")
        console.file.flush()
    except Exception:
        pass


def _handle_pending_ability_review_if_needed(save_name: str) -> None:
    if not save_exists(save_name):
        return
    try:
        snapshot = load_save_snapshot(save_name)
    except (FileNotFoundError, ValueError):
        return
    if not snapshot.pending_ability_review:
        return

    console.clear()
    console.print(_render_header(save_name))
    console.print(_process_pending_ability_review(save_name, snapshot.pending_ability_review))
    _pause()


def _handle_pending_transfer_review_if_needed(save_name: str) -> None:
    if not save_exists(save_name):
        return
    try:
        snapshot = load_save_snapshot(save_name)
    except (FileNotFoundError, ValueError):
        return
    if not snapshot.pending_transfer_review:
        return

    console.clear()
    console.print(_render_header(save_name))
    console.print(_process_pending_transfer_review(save_name, snapshot.pending_transfer_review))
    _pause()


def _handle_pending_draft_if_needed(save_name: str) -> None:
    if not save_exists(save_name):
        return
    try:
        snapshot = load_save_snapshot(save_name)
    except (FileNotFoundError, ValueError):
        return
    if snapshot.pending_draft.get("status") != "awaiting_input":
        return

    console.clear()
    console.print(_render_header(save_name))
    console.print(_process_pending_draft(save_name))
    _pause()


def _process_pending_ability_review(save_name: str, pending_review: List[dict]) -> Group:
    intro = Panel.fit(
        "赛季最后一场比赛已经结束，夏窗开始前请先审核真实球员能力变动。",
        title="能力变动审核",
        border_style="yellow",
    )
    review_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    review_table.add_column("编号", justify="center", width=6, style="bold cyan", no_wrap=True)
    review_table.add_column("球员", style="bold white", no_wrap=True)
    review_table.add_column("位置", justify="center", width=6, style="cyan", no_wrap=True)
    review_table.add_column("旧能力", justify="right", width=8, style="white", no_wrap=True)
    review_table.add_column("新能力", justify="right", width=8, style="bold green", no_wrap=True)
    review_table.add_column("变化", justify="right", width=8, style="magenta", no_wrap=True)
    for index, item in enumerate(pending_review, start=1):
        delta = int(item["delta"])
        delta_text = f"{delta:+d}"
        review_table.add_row(
            str(index),
            item["name"],
            item["position"],
            str(item["old_ability"]),
            str(item["new_ability"]),
            delta_text,
        )

    console.print(Group(intro, Panel(review_table, title="待审核名单", border_style="blue")))

    decisions: Dict[str, bool] = {}
    for index, item in enumerate(pending_review, start=1):
        while True:
            answer = console.input(
                f"[bold cyan]是否通过第 {index} 项变动：{item['name']} {item['old_ability']} -> {item['new_ability']}？(y/n)：[/]"
            ).strip().lower()
            if answer in {"y", "yes"}:
                decisions[item["name"]] = True
                break
            if answer in {"n", "no"}:
                decisions[item["name"]] = False
                break
            console.print(_render_error("请输入 y 或 n。"))

    apply_ability_review_decisions(save_name, decisions)
    accepted = [item for item in pending_review if decisions.get(item["name"])]
    rejected = [item for item in pending_review if not decisions.get(item["name"])]

    summary_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    summary_table.add_column("结果", style="bold white", no_wrap=True)
    summary_table.add_column("人数", justify="right", width=8, style="cyan", no_wrap=True)
    summary_table.add_row("通过", str(len(accepted)))
    summary_table.add_row("拒绝", str(len(rejected)))

    return Group(
        Panel.fit("已完成本次夏窗前真实球员能力变动审核。", title="审核完成", border_style="green"),
        Panel(summary_table, title="审核汇总", border_style="blue"),
    )


def _process_pending_transfer_review(save_name: str, pending_review: List[dict]) -> Group:
    intro = Panel.fit(
        "当前进入转会窗口，请先审核本周随机生成的交易。",
        title="转会审核",
        border_style="yellow",
    )

    panels: List[Any] = [intro]
    decisions: Dict[str, bool] = {}
    for index, item in enumerate(pending_review, start=1):
        review_table = Table(box=box.SIMPLE_HEAVY, expand=True)
        review_table.add_column(item["team_a"], style="bold white", no_wrap=True)
        review_table.add_column(item["team_b"], style="bold white", no_wrap=True)

        max_rows = max(len(item["team_a_players"]), len(item["team_b_players"]))
        left_rows = item["team_a_players"] + [None] * (max_rows - len(item["team_a_players"]))
        right_rows = item["team_b_players"] + [None] * (max_rows - len(item["team_b_players"]))
        for left, right in zip(left_rows, right_rows):
            left_text = (
                f"{left['name']} | {left['position']} | 能力 {left['ability']} | {left['market_value']:.2f}M"
                if left is not None
                else "-"
            )
            right_text = (
                f"{right['name']} | {right['position']} | 能力 {right['ability']} | {right['market_value']:.2f}M"
                if right is not None
                else "-"
            )
            review_table.add_row(left_text, right_text)

        summary = Text(
            f"编号 {index} | {item['team_a']} 送出 {item['team_a_total_value']:.2f}M | "
            f"{item['team_b']} 送出 {item['team_b_total_value']:.2f}M | 差额 {item['value_gap']:.2f}M",
            style="cyan",
        )
        console.print(Panel(Group(summary, review_table), title=f"交易提案 {index}", border_style="blue"))

        while True:
            answer = console.input(f"[bold cyan]是否通过交易提案 {index}？(y/n)：[/]").strip().lower()
            if answer in {"y", "yes"}:
                decisions[item["trade_id"]] = True
                break
            if answer in {"n", "no"}:
                decisions[item["trade_id"]] = False
                break
            console.print(_render_error("请输入 y 或 n。"))

    apply_transfer_review_decisions(save_name, decisions)
    approved = sum(1 for item in pending_review if decisions.get(item["trade_id"]))
    rejected = len(pending_review) - approved

    summary_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    summary_table.add_column("结果", style="bold white", no_wrap=True)
    summary_table.add_column("数量", justify="right", width=8, style="cyan", no_wrap=True)
    summary_table.add_row("通过", str(approved))
    summary_table.add_row("拒绝", str(rejected))
    return Group(
        Panel.fit("已完成本周转会审核。", title="审核完成", border_style="green"),
        Panel(summary_table, title="转会审核汇总", border_style="blue"),
    )


def _process_pending_draft(save_name: str) -> Group:
    intro = Panel.fit(
        "赛季已经全部结束。本届选秀会优先使用本存档配置中的顺序候选；如果候选不足，可以输入新秀补足，也可以直接回车跳过。每名新秀只需输入姓名和位置，能力会随机生成，初始身价固定为 30.00M。",
        title="赛季末选秀",
        border_style="yellow",
    )
    console.print(intro)

    prospects: List[dict] = []
    index = 1
    while True:
        name = console.input(f"[bold cyan]请输入第 {index} 名新秀姓名（直接回车结束）：[/]").strip()
        if not name:
            break
        while True:
            position = console.input("[bold cyan]请输入位置（GK/DF/MF/FW）：[/]").strip().upper()
            if position in {"GK", "DF", "MF", "FW"}:
                break
            console.print(_render_error("位置只能是 GK、DF、MF、FW。"))
        prospects.append({"name": name, "position": position})
        index += 1

    apply_draft_prospects(save_name, prospects)
    last_draft_state = load_last_draft_log(save_name)

    drafted_rows = last_draft_state.get("results", [])
    undrafted_rows = last_draft_state.get("undrafted", [])

    drafted_table = Table(box=box.SIMPLE_HEAVY, expand=True)
    drafted_table.add_column("顺位", justify="center", width=6, style="bold cyan", no_wrap=True)
    drafted_table.add_column("球队", style="bold white", no_wrap=True)
    drafted_table.add_column("新秀", style="bold white", no_wrap=True)
    drafted_table.add_column("位置", justify="center", width=6, style="cyan", no_wrap=True)
    drafted_table.add_column("能力", justify="right", width=6, style="green", no_wrap=True)
    drafted_table.add_column("初始身价", justify="right", width=10, style="magenta", no_wrap=True)
    for draft_index, row in enumerate(drafted_rows, start=1):
        drafted_table.add_row(
            str(draft_index),
            row["team_name"],
            row["name"],
            row["position"],
            str(row["ability"]),
            f"{float(row['market_value']):.2f}M",
        )

    blocks: List[Any] = [
        Panel.fit("已完成本赛季选秀，新秀会在下赛季作为正常真实球员参与比赛与交易。", title="选秀完成", border_style="green"),
        Panel(drafted_table, title="选秀结果", border_style="blue"),
    ]
    if undrafted_rows:
        undrafted_table = Table(box=box.SIMPLE_HEAVY, expand=True)
        undrafted_table.add_column("新秀", style="bold white", no_wrap=True)
        undrafted_table.add_column("位置", justify="center", width=6, style="cyan", no_wrap=True)
        undrafted_table.add_column("能力", justify="right", width=6, style="green", no_wrap=True)
        undrafted_table.add_column("初始身价", justify="right", width=10, style="magenta", no_wrap=True)
        for row in undrafted_rows:
            undrafted_table.add_row(
                row["name"],
                row["position"],
                str(row["ability"]),
                f"{float(row['market_value']):.2f}M",
            )
        blocks.append(Panel(undrafted_table, title="未被选中的新秀", border_style="yellow"))
    return Group(*blocks)


def _player_history_key_for_row(row: dict) -> str:
    if row.get("is_real"):
        return f"real::{row['label']}"
    return row["player_id"]


def _player_history_key_for_player(player: Any) -> str:
    if player.is_real:
        return f"real::{player.label}"
    return player.player_id


def _format_cup_result_line(row: dict) -> str:
    return (
        f"优胜者杯 {row.get('winners_cup_result', '未参赛')} | "
        f"挑战杯 {row.get('challenge_cup_result', '未参赛')} | "
        f"超级杯 {row.get('super_cup_result', '未参赛')}"
    )


def _format_honor_breakdown(row: dict) -> str:
    return (
        f"总冠军 {row.get('total_titles', 0)} | "
        f"联赛 {row.get('premier_titles', 0)} | "
        f"次级 {row.get('second_titles', 0)} | "
        f"优胜者杯 {row.get('winners_cup_titles', 0)} | "
        f"挑战杯 {row.get('challenge_cup_titles', 0)} | "
        f"超级杯 {row.get('super_cup_titles', 0)}"
    )


def _format_rating_value(rating: Any) -> str:
    if rating is None:
        return "待结算"
    return f"{float(rating):.2f}"


def _format_market_value(row: Any) -> str:
    is_real = row.player.is_real if hasattr(row, "player") else row.get("is_real", True)
    if not is_real:
        return "-"
    value = row.market_value if hasattr(row, "market_value") else row.get("market_value")
    if value is None:
        return "待结算"
    return f"{float(value):.2f}M"


def _format_team_market_value(row: dict) -> str:
    value = row.get("total_market_value")
    if value is None:
        return "待结算"
    return f"{float(value):.2f}M"


def _format_player_stat_line(row: dict) -> str:
    if row["position"] == "GK":
        return f"成功扑救 {row.get('successful_saves', 0)} | 零封 {row.get('clean_sheets', 0)}"
    return (
        f"进球 {row.get('goals', 0)} | 助攻 {row.get('assists', 0)} | "
        f"创造机会 {row.get('chances_created', 0)} | 成功防守 {row.get('successful_defenses', 0)}"
    )


def _format_player_stat_short(position: str, row: Any) -> str:
    def value(field_name: str) -> Any:
        if hasattr(row, field_name):
            return getattr(row, field_name)
        return row.get(field_name, 0)

    if position == "GK":
        return f"扑{value('successful_saves')} 零{value('clean_sheets')}"
    return (
        f"进{value('goals')} "
        f"助{value('assists')} "
        f"机{value('chances_created')} "
        f"防{value('successful_defenses')}"
    )
