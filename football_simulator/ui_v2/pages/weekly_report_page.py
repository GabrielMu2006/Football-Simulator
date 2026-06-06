from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2.widgets import CardFrame, build_metric_card, color_position_items, set_table_row, setup_table


class WeeklyReportPage(QWidget):
    def __init__(
        self,
        open_matches_callback: Callable[[], None],
        open_pending_callback: Callable[[], None],
    ) -> None:
        super().__init__()
        self.snapshot: SaveSnapshot | None = None
        self.open_matches_callback = open_matches_callback
        self.open_pending_callback = open_pending_callback

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(6, 6, 6, 6)
        self.content_layout.setSpacing(16)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        summary_grid = QGridLayout()
        summary_grid.setSpacing(16)
        self.week_card = build_metric_card("本周", "-", "最近一次已模拟周次。")
        self.matches_card = build_metric_card("比赛", "-", "本周结算比赛数量。")
        self.competitions_card = build_metric_card("赛事", "-", "本周涉及赛事类型。")
        self.pending_card = build_metric_card("待办", "-", "模拟后需要处理的事项。")
        summary_grid.addWidget(self.week_card, 0, 0)
        summary_grid.addWidget(self.matches_card, 0, 1)
        summary_grid.addWidget(self.competitions_card, 0, 2)
        summary_grid.addWidget(self.pending_card, 0, 3)
        self.content_layout.addLayout(summary_grid)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.open_matches_button = QPushButton("查看单场详细赛况")
        self.open_matches_button.clicked.connect(self.open_matches_callback)
        self.open_pending_button = QPushButton("处理待办事项")
        self.open_pending_button.clicked.connect(self.open_pending_callback)
        actions.addWidget(self.open_matches_button)
        actions.addWidget(self.open_pending_button)
        actions.addStretch(1)
        self.content_layout.addLayout(actions)

        self.headlines_panel = CardFrame("本周焦点")
        self.headlines_label = QLabel("还没有可显示的战报。")
        self.headlines_label.setWordWrap(True)
        self.headlines_panel.body_layout.addWidget(self.headlines_label)
        self.content_layout.addWidget(self.headlines_panel)

        self.best_panel = CardFrame("本周最佳表现", "只基于本周已经生成的比赛数据计算，不影响评分、身价或存档。")
        self.best_table = QTableWidget()
        setup_table(self.best_table, ["类别", "名称", "说明"])
        self.best_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.best_table.horizontalHeader().setStretchLastSection(True)
        self.best_panel.body_layout.addWidget(self.best_table)
        self.content_layout.addWidget(self.best_panel)

        self.premier_panel, self.premier_table = self._build_results_panel("一级联赛")
        self.second_panel, self.second_table = self._build_results_panel("次级联赛")
        self.cups_panel, self.cups_table = self._build_results_panel("杯赛")
        self.playoff_panel, self.playoff_table = self._build_results_panel("升降级附加赛")
        self.content_layout.addWidget(self.premier_panel)
        self.content_layout.addWidget(self.second_panel)
        self.content_layout.addWidget(self.cups_panel)
        self.content_layout.addWidget(self.playoff_panel)

        self.pending_panel = CardFrame("本周待办", "模拟后产生的审核、交易、选秀会集中在这里。")
        self.pending_table = QTableWidget()
        setup_table(self.pending_table, ["类型", "数量", "说明"])
        self.pending_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pending_table.horizontalHeader().setStretchLastSection(True)
        self.pending_panel.body_layout.addWidget(self.pending_table)
        self.content_layout.addWidget(self.pending_panel)
        self.content_layout.addStretch(1)

    def _build_results_panel(self, title: str) -> tuple[CardFrame, QTableWidget]:
        panel = CardFrame(title)
        table = QTableWidget()
        setup_table(table, ["赛事", "轮次", "主队", "比分", "客队", "事件"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(180)
        panel.body_layout.addWidget(table)
        return panel, table

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self.snapshot = snapshot
        for table in (
            self.best_table,
            self.premier_table,
            self.second_table,
            self.cups_table,
            self.playoff_table,
            self.pending_table,
        ):
            table.setRowCount(0)

        if snapshot is None or not snapshot.simulated_weeks:
            self._set_cards("-", "-", "-", "-")
            self.headlines_label.setText("本赛季还没有已模拟周次。")
            self.open_matches_button.setEnabled(False)
            self.open_pending_button.setEnabled(False)
            return

        week_data = snapshot.simulated_weeks[-1]
        sections = {
            "premier_matchdays": self.premier_table,
            "second_matchdays": self.second_table,
            "cup_matchdays": self.cups_table,
            "playoff_matchdays": self.playoff_table,
        }

        match_count = 0
        competitions = set()
        headlines: list[str] = []
        biggest_win: tuple[int, str] | None = None
        highest_scoring: tuple[int, str] | None = None
        top_team: tuple[int, int, str, str] | None = None
        top_player: tuple[int, str, str, str] | None = None
        upset_line: str | None = None
        rank_lookup = self._rank_lookup(snapshot)
        player_lookup = {row.player.player_id: row.player for row in snapshot.player_stats}

        for key, table in sections.items():
            for matchday in week_data.get(key, []):
                competition = matchday.get("competition", "赛事")
                competitions.add(competition)
                round_number = matchday.get("round_number", "-")
                for result in matchday.get("results", []):
                    match_count += 1
                    home_goals = int(result["home_goals"])
                    away_goals = int(result["away_goals"])
                    total_goals = home_goals + away_goals
                    margin = abs(home_goals - away_goals)
                    score = f"{home_goals}-{away_goals}"
                    line = f"{result['home_team']} {score} {result['away_team']}"
                    event_count = len(result.get("key_events", []))
                    set_table_row(
                        table,
                        table.rowCount(),
                        [
                            competition,
                            str(round_number),
                            result["home_team"],
                            score,
                            result["away_team"],
                            str(event_count),
                        ],
                    )
                    if biggest_win is None or margin > biggest_win[0]:
                        biggest_win = (margin, line)
                    if highest_scoring is None or total_goals > highest_scoring[0]:
                        highest_scoring = (total_goals, line)
                    winning_team = None
                    if home_goals > away_goals:
                        winning_team = result["home_team"]
                        losing_team = result["away_team"]
                    elif away_goals > home_goals:
                        winning_team = result["away_team"]
                        losing_team = result["home_team"]
                    else:
                        losing_team = None
                    if winning_team:
                        team_score = margin * 10 + total_goals
                        if top_team is None or (team_score, total_goals) > (top_team[0], top_team[1]):
                            top_team = (
                                team_score,
                                total_goals,
                                winning_team,
                                f"{line}，净胜 {margin} 球",
                            )
                        winner_rank = rank_lookup.get(winning_team)
                        loser_rank = rank_lookup.get(losing_team) if losing_team else None
                        if (
                            winner_rank is not None
                            and loser_rank is not None
                            and winner_rank > loser_rank + 4
                            and upset_line is None
                        ):
                            upset_line = f"{winning_team} 击败排名更高的 {losing_team}。"

                    for player_id, delta in result.get("player_stats", {}).items():
                        player = player_lookup.get(player_id)
                        player_score = (
                            int(delta.get("goals", 0)) * 8
                            + int(delta.get("assists", 0)) * 5
                            + int(delta.get("chances_created", 0)) * 2
                            + int(delta.get("successful_defenses", 0)) * 2
                            + int(delta.get("successful_saves", 0))
                            + int(delta.get("clean_sheets", 0)) * 4
                        )
                        if player_score <= 0:
                            continue
                        label = player.label if player else player_id
                        position = player.position if player else "-"
                        note = (
                            f"{position} | 进 {delta.get('goals', 0)} 助 {delta.get('assists', 0)} "
                            f"创 {delta.get('chances_created', 0)} 防 {delta.get('successful_defenses', 0)} "
                            f"扑 {delta.get('successful_saves', 0)} 零 {delta.get('clean_sheets', 0)}"
                        )
                        if top_player is None or player_score > top_player[0]:
                            top_player = (player_score, label, position, note)

        pending_rows = self._pending_rows(snapshot)
        for row in pending_rows:
            set_table_row(self.pending_table, self.pending_table.rowCount(), row)

        self._set_cards(
            f"第 {week_data['week_number']} 周",
            str(match_count),
            str(len(competitions)),
            str(sum(int(row[1]) for row in pending_rows)),
        )
        self.open_matches_button.setEnabled(match_count > 0)
        self.open_pending_button.setEnabled(bool(pending_rows))

        headlines.append(f"{week_data['label']}")
        if highest_scoring is not None:
            headlines.append(f"进球最多：{highest_scoring[1]}，共 {highest_scoring[0]} 球。")
        if biggest_win is not None and biggest_win[0] > 0:
            headlines.append(f"最大分差：{biggest_win[1]}，净胜 {biggest_win[0]} 球。")
        if upset_line:
            headlines.append(f"冷门提醒：{upset_line}")
        if pending_rows:
            headlines.append("本周产生了新的待处理事项。")
        self.headlines_label.setText("\n".join(headlines))

        if top_team is not None:
            set_table_row(self.best_table, self.best_table.rowCount(), ["最佳球队", top_team[2], top_team[3]])
        if top_player is not None:
            set_table_row(self.best_table, self.best_table.rowCount(), ["最佳球员", top_player[1], top_player[3]])
            color_position_items(self.best_table, 2)
        if self.best_table.rowCount() == 0:
            set_table_row(self.best_table, 0, ["暂无", "-", "这一周没有足够的比赛数据。"])

    def _pending_rows(self, snapshot: SaveSnapshot) -> list[list[str]]:
        rows: list[list[str]] = []
        if snapshot.pending_ability_review:
            rows.append(["能力变动", str(len(snapshot.pending_ability_review)), "赛季末球员能力调整等待审核"])
        if snapshot.pending_transfer_review:
            rows.append(["转会审核", str(len(snapshot.pending_transfer_review)), "窗口期交易提案等待处理"])
        if snapshot.pending_draft.get("status") == "awaiting_input":
            rows.append(["选秀", "1", "赛季结束，等待录入新秀名单"])
        return rows

    def _set_cards(self, week: str, matches: str, competitions: str, pending: str) -> None:
        self.week_card.value_label.setText(week)  # type: ignore[attr-defined]
        self.matches_card.value_label.setText(matches)  # type: ignore[attr-defined]
        self.competitions_card.value_label.setText(competitions)  # type: ignore[attr-defined]
        self.pending_card.value_label.setText(pending)  # type: ignore[attr-defined]

    def _rank_lookup(self, snapshot: SaveSnapshot) -> dict[str, int]:
        lookup: dict[str, int] = {}
        for rows in (snapshot.premier_table, snapshot.second_table):
            for index, row in enumerate(rows, start=1):
                lookup[row.team.name] = index
        return lookup
