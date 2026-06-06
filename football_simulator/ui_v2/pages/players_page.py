from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QGridLayout,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)
from PySide6.QtCore import Qt

from football_simulator.state import (
    SaveSnapshot,
    get_player_history_totals,
    get_player_single_season_records,
    get_player_trend_points,
)
from football_simulator.ui_v2.widgets import TrendSparkline, build_metric_card, color_position_items, money_text, setup_table, set_table_row


class PlayersPage(QWidget):
    def __init__(self, open_team_callback: Callable[[str], None]) -> None:
        super().__init__()
        self.snapshot: SaveSnapshot | None = None
        self.open_team_callback = open_team_callback
        self.current_team_name: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(0, 0, 0, 0)

        self.league_filter = QComboBox()
        self.position_filter = QComboBox()
        self.type_filter = QComboBox()
        self.league_filter.addItems(["全部联赛", "一级联赛", "次级联赛"])
        self.position_filter.addItems(["全部位置", "GK", "DF", "MF", "FW"])
        self.type_filter.addItems(["全部球员", "真实球员", "默认球员"])
        for widget in (self.league_filter, self.position_filter, self.type_filter):
            widget.currentIndexChanged.connect(self._populate_player_list)
            filters_layout.addWidget(widget)
        layout.addWidget(filters)

        splitter = QSplitter(Qt.Horizontal)
        self.player_list = QListWidget()
        self.player_list.currentItemChanged.connect(self._refresh_detail)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.history_text = QTextEdit()
        self.history_text.setReadOnly(True)
        self.seasons_table = QTableWidget()
        setup_table(self.seasons_table, ["赛季", "球队", "联赛", "进", "助", "创", "防", "扑", "零", "评分", "身价", "奖项"])
        self.seasons_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.seasons_table.horizontalHeader().setStretchLastSection(True)
        self.trend_tab = QWidget()
        trend_layout = QVBoxLayout(self.trend_tab)
        trend_layout.setContentsMargins(0, 0, 0, 0)
        trend_layout.setSpacing(12)
        charts_row = QHBoxLayout()
        charts_row.setContentsMargins(0, 0, 0, 0)
        self.rating_trend = TrendSparkline("评分趋势", "#7dd3fc")
        self.value_trend = TrendSparkline("身价趋势", "#facc15")
        charts_row.addWidget(self.rating_trend)
        charts_row.addWidget(self.value_trend)
        trend_layout.addLayout(charts_row)
        self.trend_table = QTableWidget()
        setup_table(self.trend_table, ["赛季", "阶段", "周", "球队", "评分", "身价"])
        self.trend_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.trend_table.horizontalHeader().setStretchLastSection(True)
        trend_layout.addWidget(self.trend_table)

        right_tabs = QWidget()
        right_layout = QVBoxLayout(right_tabs)
        right_layout.setSpacing(14)
        self.current_label = QLabel("请选择球员。")
        self.current_label.setWordWrap(True)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        self.open_team_button = QPushButton("查看所属球队")
        self.open_team_button.clicked.connect(self._open_current_team)
        self.open_team_button.setEnabled(False)
        header_row.addWidget(self.current_label, 1)
        header_row.addWidget(self.open_team_button)
        right_layout.addLayout(header_row)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.position_card = build_metric_card("位置", "-", "球员场上职责")
        self.ability_card = build_metric_card("能力", "-", "当前能力值")
        self.rating_card = build_metric_card("本季评分", "-", "冬窗/赛季末结算")
        self.value_card = build_metric_card("身价", "-", "仅真实球员拥有身价")
        cards.addWidget(self.position_card, 0, 0)
        cards.addWidget(self.ability_card, 0, 1)
        cards.addWidget(self.rating_card, 0, 2)
        cards.addWidget(self.value_card, 0, 3)
        right_layout.addLayout(cards)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(self.summary_text, "当前赛季")
        self.detail_tabs.addTab(self.history_text, "历史与荣誉")
        self.detail_tabs.addTab(self.seasons_table, "各赛季记录")
        self.detail_tabs.addTab(self.trend_tab, "评分/身价趋势")
        right_layout.addWidget(self.detail_tabs, 1)

        splitter.addWidget(self.player_list)
        splitter.addWidget(right_tabs)
        splitter.setSizes([320, 940])
        layout.addWidget(splitter)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self.snapshot = snapshot
        self._populate_player_list()

    def focus_player(self, player_id: str | None = None, label: str | None = None) -> None:
        if self.snapshot is None:
            return
        self.league_filter.setCurrentText("全部联赛")
        self.position_filter.setCurrentText("全部位置")
        self.type_filter.setCurrentText("全部球员")
        self._populate_player_list()
        for index in range(self.player_list.count()):
            item = self.player_list.item(index)
            current_id = item.data(Qt.UserRole)
            if player_id is not None and current_id == player_id:
                self.player_list.setCurrentRow(index)
                return
            if label is not None and item.text().startswith(f"{label} |"):
                self.player_list.setCurrentRow(index)
                return

    def _populate_player_list(self) -> None:
        self.player_list.clear()
        if self.snapshot is None or not self.snapshot.player_stats:
            return
        rows = self.snapshot.player_stats
        league_label = self.league_filter.currentText()
        if league_label != "全部联赛":
            team_map = {team.name: team.division for team in self.snapshot.teams}
            rows = [row for row in rows if team_map.get(row.team_name) == league_label]
        position_label = self.position_filter.currentText()
        if position_label != "全部位置":
            rows = [row for row in rows if row.player.position == position_label]
        type_label = self.type_filter.currentText()
        if type_label == "真实球员":
            rows = [row for row in rows if row.player.is_real]
        elif type_label == "默认球员":
            rows = [row for row in rows if not row.player.is_real]

        rows = sorted(rows, key=lambda row: (row.player.is_real, row.player.ability, row.goals, row.assists), reverse=True)
        for row in rows:
            item = QListWidgetItem(f"{row.player.label} | {row.team_name}")
            item.setData(Qt.UserRole, row.player.player_id)
            self.player_list.addItem(item)
        if self.player_list.count():
            self.player_list.setCurrentRow(0)

    def _refresh_detail(self) -> None:
        if self.snapshot is None or not self.snapshot.player_stats:
            return
        item = self.player_list.currentItem()
        if item is None:
            return
        player_id = item.data(Qt.UserRole)
        row = next(player for player in self.snapshot.player_stats if player.player.player_id == player_id)
        self.current_team_name = row.team_name
        self.open_team_button.setEnabled(True)
        self.current_label.setText(f"{row.player.label} | {row.team_name} | {row.player.position}")
        self.position_card.value_label.setText(row.player.position)  # type: ignore[attr-defined]
        self.ability_card.value_label.setText(str(row.player.ability))  # type: ignore[attr-defined]
        self.rating_card.value_label.setText("待结算" if row.season_rating is None else f"{row.season_rating:.2f}")  # type: ignore[attr-defined]
        self.value_card.value_label.setText(money_text(row.market_value) if row.player.is_real else "-")  # type: ignore[attr-defined]
        self.summary_text.setPlainText(
            "\n".join(
                [
                    f"能力：{row.player.ability}",
                    f"类型：{'真实球员' if row.player.is_real else '默认球员'}",
                    f"出场：{row.appearances}",
                    f"评分：{'待结算' if row.season_rating is None else f'{row.season_rating:.2f}'}",
                    f"身价：{money_text(row.market_value) if row.player.is_real else '-'}",
                    _current_stat_line(row),
                ]
            )
        )

        history_key = f"real::{row.player.label}" if row.player.is_real else row.player.player_id
        history_row = next((entry for entry in get_player_history_totals(self.snapshot) if entry["player_id"] == history_key), None)
        if history_row is None:
            self.history_text.setPlainText("还没有历史总览。")
        else:
            self.history_text.setPlainText(
                "\n".join(
                    [
                        f"历史总赛季：{history_row['seasons']}",
                        _history_stat_line(history_row, row.player.position),
                        f"总冠军：{history_row['total_titles']}",
                        f"荣誉积分：{history_row['honor_points']}",
                        _history_award_line(history_row),
                        _award_labels_text(history_row.get("award_labels", [])),
                    ]
                )
            )

        season_rows = [entry for entry in get_player_single_season_records(self.snapshot) if entry["player_id"] == row.player.player_id or (row.player.is_real and entry["label"] == row.player.label)]
        self.seasons_table.setRowCount(0)
        for season in sorted(season_rows, key=lambda item: item["season_number"], reverse=True):
            set_table_row(
                self.seasons_table,
                self.seasons_table.rowCount(),
                [
                    f"S{season['season_number']}",
                    season["team_name"],
                    season.get("division", "-"),
                    str(season.get("goals", 0)),
                    str(season.get("assists", 0)),
                    str(season.get("chances_created", 0)),
                    str(season.get("successful_defenses", 0)),
                    str(season.get("successful_saves", 0)),
                    str(season.get("clean_sheets", 0)),
                    "-" if season.get("season_rating") is None else f"{float(season['season_rating']):.2f}",
                    "-" if season.get("market_value") is None else f"{float(season['market_value']):.2f}M",
                    _season_award_summary(season),
                ],
            )
        color_position_items(self.seasons_table, 2)
        self._populate_trends(row)

    def _populate_trends(self, row) -> None:
        self.trend_table.setRowCount(0)
        if self.snapshot is None or not row.player.is_real:
            self.rating_trend.set_points([], [])
            self.value_trend.set_points([], [])
            set_table_row(self.trend_table, 0, ["-", "-", "-", "-", "默认球员无趋势", "-"])
            return
        trend_points = get_player_trend_points(self.snapshot, row.player.player_id, row.player.label)
        labels = [f"S{point['season_number']}{point['stage']}" for point in trend_points]
        rating_values = [float(point["season_rating"]) for point in trend_points if point.get("season_rating") is not None]
        market_values = [float(point["market_value"]) for point in trend_points if point.get("market_value") is not None]
        self.rating_trend.set_points(rating_values, labels)
        self.value_trend.set_points(market_values, labels)
        if not trend_points:
            set_table_row(self.trend_table, 0, ["-", "-", "-", "-", "暂无趋势", "-"])
            return
        for point in sorted(trend_points, key=lambda item: (item["season_number"], item["week_number"]), reverse=True):
            set_table_row(
                self.trend_table,
                self.trend_table.rowCount(),
                [
                    f"S{point['season_number']}",
                    point["stage"],
                    str(point.get("week_number", "-")),
                    point.get("team_name", "-"),
                    f"{float(point['season_rating']):.2f}",
                    f"{float(point['market_value']):.2f}M",
                ],
            )

    def _open_current_team(self) -> None:
        if self.current_team_name:
            self.open_team_callback(self.current_team_name)


def _current_stat_line(row) -> str:
    if row.player.position == "GK":
        return f"数据：出场 {row.appearances} | 成功扑救 {row.successful_saves} | 零封 {row.clean_sheets}"
    return (
        f"数据：出场 {row.appearances} | 进球 {row.goals} | 助攻 {row.assists} | "
        f"创造机会 {row.chances_created} | 成功防守 {row.successful_defenses}"
    )


def _history_stat_line(history_row: dict, position: str) -> str:
    if position == "GK":
        return f"历史总数据：扑 {history_row['successful_saves']} | 零 {history_row['clean_sheets']}"
    return (
        f"历史总数据：进 {history_row['goals']} | 助 {history_row['assists']} | "
        f"创 {history_row['chances_created']} | 防 {history_row['successful_defenses']}"
    )


def _history_award_line(history_row: dict) -> str:
    best_rank = history_row.get("top20_best_rank")
    return (
        f"个人奖项：Top20 {history_row.get('top20_finishes', 0)} 次"
        f"（最佳 {'-' if best_rank is None else f'第 {best_rank} 名'}） | "
        f"射手王 {history_row.get('top_scorer_awards', 0)} | "
        f"助攻王 {history_row.get('assist_leader_awards', 0)} | "
        f"MVP {history_row.get('mvp_awards', 0)}"
    )


def _award_labels_text(labels: list[str]) -> str:
    if not labels:
        return "荣誉明细：暂无个人奖项。"
    return "荣誉明细：\n" + "\n".join(f"- {label}" for label in labels)


def _season_award_summary(season: dict) -> str:
    labels = list(season.get("award_labels", []))
    if not labels:
        return "-"
    return "；".join(labels)
