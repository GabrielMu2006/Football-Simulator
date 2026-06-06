import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import List

from football_simulator.runtime import save_root
from football_simulator.state import (
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


class FootballSimulatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("足球模拟器")
        self.root.geometry("1180x780")
        self.root.minsize(980, 680)

        self.save_name = tk.StringVar(value="default")
        self.entity_var = tk.StringVar(value="players")
        self.scope_var = tk.StringVar(value="current")
        self.metric_var = tk.StringVar(value="goals")
        self.limit_var = tk.StringVar(value="10")
        self.query_var = tk.StringVar()
        self.query_mode_var = tk.StringVar(value="player")

        self._build_layout()
        self._refresh_metric_options()
        self._append_output(
            "足球模拟器 macOS 版\n"
            f"存档目录：{save_root()}\n\n"
            "先点击“初始化赛季”，然后用“模拟下一周”按周推进。"
        )

    def _build_layout(self) -> None:
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)

        control_frame = ttk.Frame(frame)
        control_frame.pack(fill="x")

        ttk.Label(control_frame, text="存档").grid(row=0, column=0, sticky="w")
        ttk.Entry(control_frame, textvariable=self.save_name, width=20).grid(
            row=1, column=0, padx=(0, 12), sticky="ew"
        )
        ttk.Button(control_frame, text="初始化赛季", command=self.initialize_save).grid(
            row=1, column=1, padx=(0, 8), sticky="ew"
        )
        ttk.Button(control_frame, text="模拟下一周", command=self.simulate_week).grid(
            row=1, column=2, padx=(0, 8), sticky="ew"
        )
        ttk.Button(control_frame, text="查看状态", command=self.show_status).grid(
            row=1, column=3, padx=(0, 8), sticky="ew"
        )
        ttk.Button(control_frame, text="查看积分榜", command=self.show_standings).grid(
            row=1, column=4, sticky="ew"
        )

        leader_frame = ttk.LabelFrame(frame, text="排行榜", padding=10)
        leader_frame.pack(fill="x", pady=(12, 0))

        ttk.Label(leader_frame, text="类型").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            leader_frame,
            textvariable=self.entity_var,
            state="readonly",
            values=("players", "teams"),
            width=12,
        ).grid(row=1, column=0, padx=(0, 10), sticky="w")
        self.entity_var.trace_add("write", lambda *_: self._refresh_metric_options())

        ttk.Label(leader_frame, text="范围").grid(row=0, column=1, sticky="w")
        ttk.Combobox(
            leader_frame,
            textvariable=self.scope_var,
            state="readonly",
            values=("current", "history", "record"),
            width=12,
        ).grid(row=1, column=1, padx=(0, 10), sticky="w")

        ttk.Label(leader_frame, text="指标").grid(row=0, column=2, sticky="w")
        self.metric_box = ttk.Combobox(
            leader_frame,
            textvariable=self.metric_var,
            state="readonly",
            width=22,
        )
        self.metric_box.grid(row=1, column=2, padx=(0, 10), sticky="w")

        ttk.Label(leader_frame, text="显示条数").grid(row=0, column=3, sticky="w")
        ttk.Entry(leader_frame, textvariable=self.limit_var, width=8).grid(
            row=1, column=3, padx=(0, 10), sticky="w"
        )

        ttk.Button(leader_frame, text="查看排行榜", command=self.show_leaders).grid(
            row=1, column=4, sticky="w"
        )

        detail_frame = ttk.LabelFrame(frame, text="详情", padding=10)
        detail_frame.pack(fill="x", pady=(12, 0))

        ttk.Combobox(
            detail_frame,
            textvariable=self.query_mode_var,
            state="readonly",
            values=("player", "team"),
            width=12,
        ).grid(row=0, column=0, padx=(0, 10), sticky="w")
        ttk.Entry(detail_frame, textvariable=self.query_var, width=36).grid(
            row=0, column=1, padx=(0, 10), sticky="ew"
        )
        ttk.Button(detail_frame, text="查看详情", command=self.show_details).grid(
            row=0, column=2, sticky="w"
        )
        detail_frame.columnconfigure(1, weight=1)

        self.output = ScrolledText(
            frame,
            wrap="word",
            font=("Menlo", 12),
            padx=12,
            pady=12,
        )
        self.output.pack(fill="both", expand=True, pady=(12, 0))
        self.output.configure(state="disabled")

    def initialize_save(self) -> None:
        self._run_action(self._do_initialize)

    def simulate_week(self) -> None:
        self._run_action(self._do_simulate_week)

    def show_status(self) -> None:
        self._run_action(self._do_show_status)

    def show_standings(self) -> None:
        self._run_action(self._do_show_standings)

    def show_leaders(self) -> None:
        self._run_action(self._do_show_leaders)

    def show_details(self) -> None:
        self._run_action(self._do_show_details)

    def _run_action(self, action) -> None:
        try:
            text = action()
        except (FileNotFoundError, ValueError) as exc:
            messagebox.showerror("足球模拟器", str(exc))
            return
        self._set_output(text)

    def _do_initialize(self) -> str:
        snapshot = initialize_save_state(self.save_name.get().strip())
        lines = [
            f"已为存档 '{snapshot.save_name}' 初始化第 {snapshot.season_number} 赛季。",
            f"存档路径：{save_root() / snapshot.save_name}",
            "",
            "初始球员分配",
            "-" * 86,
        ]
        for team in snapshot.teams:
            lines.append(
                f"{team.name} | 评分 {team.rating:.1f} | 真实球员 {team.real_player_count}"
            )
            for player in team.roster:
                player_type = "真实球员" if player.is_real else "默认球员"
                lines.append(
                    f"  {player.position:<2} {player.label:<22} "
                    f"能力 {player.ability:>2} [{player_type}]"
                )
            lines.append("")
        return "\n".join(lines)

    def _do_simulate_week(self) -> str:
        result = simulate_next_week(self.save_name.get().strip())
        lines = [
            f"第 {result.snapshot.season_number} 赛季 | 第 {result.week.week_number}/{len(result.snapshot.weeks)} 周",
            f"周类型：{result.week.label}",
            "-" * 86,
        ]
        if not result.matchdays:
            if result.week.kind in {"long_break", "short_break"}:
                lines.append("本周没有比赛，联赛处于休赛期。")
            else:
                lines.append("本周没有比赛。")
        else:
            for matchday in result.matchdays:
                lines.append(f"第 {matchday.round_number} 轮")
                for match in matchday.results:
                    lines.append(
                        f"  {match.home_team.name} {match.home_goals}-{match.away_goals} {match.away_team.name}"
                    )
                    for event in match.key_events[:2]:
                        lines.append(f"    {event}")
                lines.append("")

        if result.season_completed_now:
            champion = result.snapshot.table[0]
            lines.append(
                f"赛季结束。冠军：{champion.team.name}，积分 {champion.points}。"
            )
        elif result.snapshot.current_week < len(result.snapshot.weeks):
            next_week = result.snapshot.weeks[result.snapshot.current_week]
            lines.append(f"下周：第 {next_week.week_number} 周（{next_week.label}）")
        return "\n".join(lines)

    def _do_show_status(self) -> str:
        snapshot = load_save_snapshot(self.save_name.get().strip())
        lines = [
            f"当前赛季：第 {snapshot.season_number} 赛季",
            f"当前周次：{snapshot.current_week}/{len(snapshot.weeks)}",
            f"赛季是否结束：{'是' if snapshot.season_complete else '否'}",
            f"已模拟联赛轮次：{sum(len(week_data.get('matchdays', [])) for week_data in snapshot.simulated_weeks)}/38",
            f"已归档历史赛季：{len(snapshot.history)}",
        ]
        if snapshot.current_week < len(snapshot.weeks):
            next_week = snapshot.weeks[snapshot.current_week]
            lines.extend(
                [
                    "",
                    f"下周：第 {next_week.week_number} 周",
                    f"周类型：{next_week.label}",
                    f"轮次：{', '.join(str(number) for number in next_week.round_numbers) or '无'}",
                ]
            )
        return "\n".join(lines)

    def _do_show_standings(self) -> str:
        snapshot = load_save_snapshot(self.save_name.get().strip())
        lines = [
            "积分榜",
            "-" * 86,
            f"{'名次':<4}{'球队':<24}{'赛':>4}{'胜':>4}{'平':>4}{'负':>4}{'进':>5}{'失':>5}{'净':>5}{'积分':>6}",
        ]
        for position, row in enumerate(snapshot.table, start=1):
            lines.append(
                f"{position:<4}{row.team.name:<24}{row.played:>4}{row.wins:>4}{row.draws:>4}{row.losses:>4}"
                f"{row.goals_for:>5}{row.goals_against:>5}{row.goals_for - row.goals_against:>5}{row.points:>6}"
            )
        return "\n".join(lines)

    def _do_show_leaders(self) -> str:
        snapshot = load_save_snapshot(self.save_name.get().strip())
        entity = self.entity_var.get()
        scope = self.scope_var.get()
        metric = self.metric_var.get()
        limit = max(1, int(self.limit_var.get() or "10"))

        if entity == "players":
            if metric not in PLAYER_METRICS:
                raise ValueError(f"不支持的球员指标：{metric}")
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
            lines = [
                f"球员排行榜 | {SCOPE_LABELS[scope]} | {PLAYER_METRICS[metric]}",
                "-" * 98,
                f"{'球员':<24}{'球队':<24}{'赛季':<8}{'数值':>8}",
            ]
            for row in rows:
                lines.append(
                    f"{row['label']:<24}{row.get('team_name', '-'): <24}"
                    f"{str(row.get('season_number', row.get('seasons', '-'))):<8}{row[metric]:>8}"
                )
            return "\n".join(lines)

        if metric not in TEAM_METRICS:
            raise ValueError(f"不支持的球队指标：{metric}")
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
        lines = [
            f"球队排行榜 | {SCOPE_LABELS[scope]} | {TEAM_METRICS[metric]}",
            "-" * 98,
            f"{'球队':<24}{'赛季':<8}{'数值':>8}",
        ]
        for row in rows:
            lines.append(
                f"{row['team_name']:<24}{str(row.get('season_number', row.get('seasons', '-'))):<8}{row[metric]:>8}"
            )
        return "\n".join(lines)

    def _do_show_details(self) -> str:
        snapshot = load_save_snapshot(self.save_name.get().strip())
        query = self.query_var.get().strip()
        if not query:
            raise ValueError("请输入球员名或球队名。")
        if self.query_mode_var.get() == "player":
            return _format_player_detail(snapshot, query)
        return _format_team_detail(snapshot, query)

    def _refresh_metric_options(self) -> None:
        entity = self.entity_var.get()
        options = tuple(PLAYER_METRICS) if entity == "players" else tuple(TEAM_METRICS)
        self.metric_box.configure(values=options)
        if self.metric_var.get() not in options:
            self.metric_var.set(options[0])

    def _set_output(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.configure(state="disabled")
        self.output.see("1.0")

    def _append_output(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.configure(state="disabled")
        self.output.see("end")


def _format_player_detail(snapshot, query: str) -> str:
    current_rows = find_player_rows(snapshot, query)
    if not current_rows:
        raise ValueError(f"当前赛季没有匹配到球员 '{query}'。")

    history_totals = {row["player_id"]: row for row in get_player_history_totals(snapshot)}
    best_single_season = {}
    for row in get_player_single_season_records(snapshot):
        current_best = best_single_season.get(row["player_id"])
        score = (
            row["goals"],
            row["assists"],
            row["chances_created"],
            row["successful_defenses"],
            row["successful_saves"],
        )
        if current_best is None or score > (
            current_best["goals"],
            current_best["assists"],
            current_best["chances_created"],
            current_best["successful_defenses"],
            current_best["successful_saves"],
        ):
            best_single_season[row["player_id"]] = row

    lines = ["球员详情", "-" * 98]
    for row in current_rows:
        history = history_totals[row["player_id"]]
        record = best_single_season[row["player_id"]]
        lines.extend(
            [
                f"{row['label']} | 球队 {row['team_name']} | 位置 {row['position']} | 赛季 {row['season_number']}",
                (
                    f"  本赛季：进球 {row['goals']} | 助攻 {row['assists']} | "
                    f"创造机会 {row['chances_created']} | 成功防守 {row['successful_defenses']} | "
                    f"成功扑救 {row['successful_saves']}"
                ),
                (
                    f"  历史总计：进球 {history['goals']} | 助攻 {history['assists']} | "
                    f"创造机会 {history['chances_created']} | 成功防守 {history['successful_defenses']} | "
                    f"成功扑救 {history['successful_saves']} | 赛季数 {history['seasons']}"
                ),
                (
                    f"  最佳赛季：第 {record['season_number']} 赛季 | 进球 {record['goals']} | "
                    f"助攻 {record['assists']} | 创造机会 {record['chances_created']} | "
                    f"成功防守 {record['successful_defenses']} | 成功扑救 {record['successful_saves']}"
                ),
                "",
            ]
        )
    return "\n".join(lines)


def _format_team_detail(snapshot, query: str) -> str:
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

    lines = ["球队详情", "-" * 110]
    for row in current_rows:
        history = history_totals[row["team_name"]]
        record = best_single_season[row["team_name"]]
        lines.extend(
            [
                f"{row['team_name']} | 第 {row['season_number']} 赛季",
                (
                    f"  本赛季战绩：赛 {row['played']} | 胜 {row['wins']} | 平 {row['draws']} | "
                    f"负 {row['losses']} | 进 {row['goals_for']} | 失 {row['goals_against']} | "
                    f"净胜球 {row['goals_for'] - row['goals_against']} | 积分 {row['points']}"
                ),
                (
                    f"  本赛季球队数据：进球 {row['goals']} | 助攻 {row['assists']} | "
                    f"创造机会 {row['chances_created']} | 成功防守 {row['successful_defenses']} | "
                    f"成功扑救 {row['successful_saves']}"
                ),
                (
                    f"  历史总计：赛季数 {history['seasons']} | 胜 {history['wins']} | "
                    f"进 {history['goals_for']} | 失 {history['goals_against']} | "
                    f"积分 {history['points']} | 进球 {history['goals']}"
                ),
                (
                    f"  最佳赛季：第 {record['season_number']} 赛季 | 积分 {record['points']} | "
                    f"进 {record['goals_for']} | 失 {record['goals_against']} | "
                    f"净胜球 {record['goal_diff']} | 进球 {record['goals']}"
                ),
                "",
            ]
        )
    return "\n".join(lines)


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


def main() -> None:
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass
    FootballSimulatorApp(root)
    root.mainloop()
