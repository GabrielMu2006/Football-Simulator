from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from football_simulator.state import (
    SaveSnapshot,
    get_team_history_totals,
    get_team_single_season_records,
)
from football_simulator.ui_v2.services import SimulatorUIService
from football_simulator.ui_v2.widgets import (
    build_metric_card,
    color_position_items,
    money_text,
    player_stat_line,
    setup_table,
    set_table_row,
)


class TeamsPage(QWidget):
    def __init__(
        self,
        service: SimulatorUIService,
        open_player_callback: Callable[[str | None, str | None], None],
    ) -> None:
        super().__init__()
        self.service = service
        self.snapshot: SaveSnapshot | None = None
        self.team_chinese_names: dict[str, str] = {}
        self.open_player_callback = open_player_callback

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)

        self.league_filter = QComboBox()
        self.league_filter.addItems(["全部球队", "一级联赛", "次级联赛"])
        self.league_filter.currentIndexChanged.connect(self._populate_team_list)
        self.team_list = QListWidget()
        self.team_list.currentItemChanged.connect(self._refresh_details)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(self.league_filter)
        left_layout.addWidget(self.team_list)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(14)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.division_card = build_metric_card("所属联赛", "-", "当前球队所在级别")
        self.record_card = build_metric_card("本季战绩", "-", "胜 / 平 / 负")
        self.market_card = build_metric_card("总身价", "-", "仅统计已结算真实球员身价")
        self.real_count_card = build_metric_card("真实球员", "-", "当前阵容真实球员数量")
        cards.addWidget(self.division_card, 0, 0)
        cards.addWidget(self.record_card, 0, 1)
        cards.addWidget(self.market_card, 0, 2)
        cards.addWidget(self.real_count_card, 0, 3)
        right_layout.addLayout(cards)

        self.tabs = QTabWidget()
        self.current_text = QTextEdit()
        self.current_text.setReadOnly(True)
        self.history_text = QTextEdit()
        self.history_text.setReadOnly(True)
        self.seasons_table = QTableWidget()
        setup_table(self.seasons_table, ["赛季", "联赛", "联赛成绩", "优胜者杯", "挑战杯", "超级杯", "荣誉积分"])
        self.seasons_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.seasons_table.horizontalHeader().setStretchLastSection(True)
        self.recent_table = QTableWidget()
        setup_table(self.recent_table, ["周次", "赛事", "主客", "对手", "比分", "结果"])
        self.recent_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.recent_table.horizontalHeader().setStretchLastSection(True)
        self.players_table = QTableWidget()
        setup_table(self.players_table, ["球员", "位置", "能力", "类型", "本赛季数据", "评分", "身价"])
        self.players_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.players_table.horizontalHeader().setStretchLastSection(True)
        self.players_table.itemDoubleClicked.connect(self._open_selected_player)

        self.tabs.addTab(self.current_text, "球队总览")
        self.tabs.addTab(self.players_table, "阵容与球员")
        self.tabs.addTab(self.recent_table, "近期比赛")
        self.tabs.addTab(self.seasons_table, "历季记录")
        self.tabs.addTab(self.history_text, "历史与荣誉")
        right_layout.addWidget(self.tabs, 1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([280, 980])
        layout.addWidget(splitter)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self.snapshot = snapshot
        self.team_chinese_names = self.service.team_chinese_names(snapshot.save_name) if snapshot is not None else {}
        self._populate_team_list()

    def focus_team(self, team_name: str) -> None:
        if self.snapshot is None:
            return
        target_team = next((team for team in self.snapshot.teams if team.name == team_name), None)
        if target_team is None:
            return
        desired_filter = target_team.division if self.league_filter.findText(target_team.division) != -1 else "全部球队"
        if self.league_filter.currentText() != desired_filter:
            self.league_filter.setCurrentText(desired_filter)
        else:
            self._populate_team_list()
        for index in range(self.team_list.count()):
            item = self.team_list.item(index)
            if item.data(Qt.UserRole) == team_name:
                self.team_list.setCurrentRow(index)
                return

    def _populate_team_list(self) -> None:
        self.team_list.clear()
        if self.snapshot is None or not self.snapshot.teams:
            return
        teams = self.snapshot.teams
        filter_label = self.league_filter.currentText()
        if filter_label != "全部球队":
            teams = [team for team in teams if team.division == filter_label]
        for team in teams:
            item = QListWidgetItem(self._team_display_name(team.name))
            item.setData(Qt.UserRole, team.name)
            self.team_list.addItem(item)
        if self.team_list.count():
            self.team_list.setCurrentRow(0)

    def _refresh_details(self) -> None:
        if self.snapshot is None or not self.snapshot.teams:
            return
        item = self.team_list.currentItem()
        if item is None:
            return
        team_name = item.data(Qt.UserRole)
        team = next(team for team in self.snapshot.teams if team.name == team_name)
        team_row = next(row for row in self.snapshot.team_stats if row.team_name == team_name)
        real_count = sum(1 for player in team.roster if player.is_real)
        chinese_name = self.team_chinese_names.get(team_name, "-")

        self.division_card.value_label.setText(team.division)  # type: ignore[attr-defined]
        self.record_card.value_label.setText(f"{team_row.wins}/{team_row.draws}/{team_row.losses}")  # type: ignore[attr-defined]
        self.market_card.value_label.setText(money_text(team_row.total_market_value))  # type: ignore[attr-defined]
        self.real_count_card.value_label.setText(f"{real_count}/11")  # type: ignore[attr-defined]

        self.current_text.setPlainText(
            "\n".join(
                [
                    f"球队：{team_name}",
                    f"中文名：{chinese_name}",
                    f"联赛：{team.division}",
                    f"战绩：{team_row.played} 赛 {team_row.wins} 胜 {team_row.draws} 平 {team_row.losses} 负",
                    f"进失球：{team_row.goals_for}/{team_row.goals_against}（净胜球 {team_row.goal_diff}）",
                    f"积分：{team_row.points}",
                    f"真实球员：{real_count}/11",
                    f"平均能力：{team.rating:.1f}",
                    f"总身价：{money_text(team_row.total_market_value)}",
                ]
            )
        )

        history_totals = next((row for row in get_team_history_totals(self.snapshot) if row["team_name"] == team_name), None)
        if history_totals is None:
            self.history_text.setPlainText("还没有历史总览。")
        else:
            self.history_text.setPlainText(
                "\n".join(
                    [
                        f"总赛季数：{history_totals['seasons']}",
                        f"历史总战绩：{history_totals['wins']} 胜 {history_totals['draws']} 平 {history_totals['losses']} 负",
                        f"历史总进失球：{history_totals['goals_for']}/{history_totals['goals_against']}",
                        f"总冠军：{history_totals['total_titles']}",
                        f"荣誉积分：{history_totals['honor_points']}",
                    ]
                )
            )

        season_rows = [row for row in get_team_single_season_records(self.snapshot) if row["team_name"] == team_name]
        self.seasons_table.setRowCount(0)
        for row in sorted(season_rows, key=lambda item: item["season_number"], reverse=True):
            set_table_row(
                self.seasons_table,
                self.seasons_table.rowCount(),
                [
                    f"S{row['season_number']}",
                    row.get("division", "-"),
                    row.get("league_result", "-"),
                    row.get("winners_cup_result", "未参赛"),
                    row.get("challenge_cup_result", "未参赛"),
                    row.get("super_cup_result", "未参赛"),
                    str(row.get("honor_points", 0)),
                ],
            )

        self._populate_recent_matches(team_name)
        player_rows = [row for row in self.snapshot.player_stats if row.team_name == team_name]
        self.players_table.setRowCount(0)
        for row in sorted(player_rows, key=lambda item: (item.player.is_real, item.player.ability), reverse=True):
            row_index = self.players_table.rowCount()
            set_table_row(
                self.players_table,
                row_index,
                [
                    row.player.label,
                    row.player.position,
                    str(row.player.ability),
                    "真实球员" if row.player.is_real else "默认球员",
                    player_stat_line(row),
                    "待结算" if row.season_rating is None else f"{row.season_rating:.2f}",
                    money_text(row.market_value) if row.player.is_real else "-",
                ],
            )
            self.players_table.item(row_index, 0).setData(Qt.UserRole, row.player.player_id)
            self.players_table.item(row_index, 0).setData(Qt.UserRole + 1, row.player.label)
        color_position_items(self.players_table, 1)

    def _populate_recent_matches(self, team_name: str) -> None:
        self.recent_table.setRowCount(0)
        if self.snapshot is None:
            return
        for match in _team_recent_matches(self.snapshot, team_name):
            set_table_row(
                self.recent_table,
                self.recent_table.rowCount(),
                [
                    f"W{match['week_number']}",
                    match["competition"],
                    match["side"],
                    self._team_display_name(match["opponent"]),
                    match["score"],
                    match["result"],
                ],
            )

    def _team_display_name(self, team_name: str) -> str:
        chinese_name = self.team_chinese_names.get(team_name)
        return f"{chinese_name} / {team_name}" if chinese_name else team_name

    def _open_selected_player(self, *_args) -> None:
        selected = self.players_table.selectionModel().selectedRows()
        if not selected:
            return
        row_index = selected[0].row()
        player_item = self.players_table.item(row_index, 0)
        if player_item is None:
            return
        self.open_player_callback(
            player_item.data(Qt.UserRole),
            player_item.data(Qt.UserRole + 1),
        )


def _team_recent_matches(snapshot: SaveSnapshot, team_name: str) -> list[dict]:
    matches: list[dict] = []
    for week in snapshot.simulated_weeks:
        for key in ("premier_matchdays", "second_matchdays", "cup_matchdays", "playoff_matchdays"):
            for matchday in week.get(key, []):
                competition = matchday.get("competition", "赛事")
                for result in matchday.get("results", []):
                    if result["home_team"] != team_name and result["away_team"] != team_name:
                        continue
                    is_home = result["home_team"] == team_name
                    goals_for = int(result["home_goals"] if is_home else result["away_goals"])
                    goals_against = int(result["away_goals"] if is_home else result["home_goals"])
                    outcome = "胜" if goals_for > goals_against else "平" if goals_for == goals_against else "负"
                    matches.append(
                        {
                            "week_number": week.get("week_number", "-"),
                            "competition": competition,
                            "side": "主场" if is_home else "客场",
                            "opponent": result["away_team"] if is_home else result["home_team"],
                            "score": f"{goals_for}-{goals_against}",
                            "result": outcome,
                        }
                    )
    return matches[-12:][::-1]
