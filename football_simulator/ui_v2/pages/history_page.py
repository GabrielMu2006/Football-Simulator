from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHeaderView, QTableWidget, QTabWidget, QVBoxLayout, QWidget

from football_simulator.state import (
    SaveSnapshot,
    get_player_honor_leaders,
    get_season_awards,
    get_team_honor_leaders,
)
from football_simulator.ui_v2.widgets import build_metric_card, set_table_row, setup_table


class HistoryPage(QWidget):
    def __init__(
        self,
        open_team_callback: Callable[[str], None],
        open_player_callback: Callable[[str | None, str | None], None],
    ) -> None:
        super().__init__()
        self.open_team_callback = open_team_callback
        self.open_player_callback = open_player_callback
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.seasons_card = build_metric_card("历史赛季", "-", "已经写入历史的赛季数量")
        self.team_leader_card = build_metric_card("球队榜首", "-", "历史荣誉积分最高球队")
        self.player_leader_card = build_metric_card("球员榜首", "-", "历史荣誉积分最高球员")
        self.champions_card = build_metric_card("冠军记录", "-", "已展示的冠军赛季")
        cards.addWidget(self.seasons_card, 0, 0)
        cards.addWidget(self.team_leader_card, 0, 1)
        cards.addWidget(self.player_leader_card, 0, 2)
        cards.addWidget(self.champions_card, 0, 3)
        layout.addLayout(cards)

        self.tabs = QTabWidget()
        self.team_honor_table = self._build_table(["排名", "球队", "荣誉积分", "总冠军", "联赛", "优胜者杯", "挑战杯", "超级杯"])
        self.player_honor_table = self._build_table(["排名", "球员", "位置", "荣誉积分", "总冠军", "Top20", "最佳排名", "射手王", "助攻王", "MVP"])
        self.champions_table = self._build_table(["赛季", "一级联赛", "次级联赛", "优胜者杯", "挑战杯", "超级杯"])
        self.top20_table = self._build_table(["赛季", "排名", "球员", "位置", "球队", "评分", "Top20分", "进", "助"])
        self.competition_awards_table = self._build_table(["赛季", "赛事", "奖项", "球员", "位置", "球队", "关键数据", "评分"])
        self.team_honor_table.itemDoubleClicked.connect(self._open_selected_team_honor)
        self.player_honor_table.itemDoubleClicked.connect(self._open_selected_player_honor)
        self.champions_table.itemDoubleClicked.connect(self._open_selected_champion)
        self.top20_table.itemDoubleClicked.connect(self._open_selected_award_player)
        self.competition_awards_table.itemDoubleClicked.connect(self._open_selected_award_player)

        self.tabs.addTab(self.team_honor_table, "球队荣誉榜")
        self.tabs.addTab(self.player_honor_table, "球员荣誉榜")
        self.tabs.addTab(self.champions_table, "历届冠军")
        self.tabs.addTab(self.top20_table, "年度Top20")
        self.tabs.addTab(self.competition_awards_table, "赛事个人奖")
        layout.addWidget(self.tabs)

    def _build_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget()
        setup_table(table, headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self._populate_team_honors(snapshot)
        self._populate_player_honors(snapshot)
        self._populate_champions(snapshot)
        self._populate_awards(snapshot)
        self._populate_cards(snapshot)

    def _populate_cards(self, snapshot: SaveSnapshot | None) -> None:
        if snapshot is None:
            self.seasons_card.value_label.setText("-")  # type: ignore[attr-defined]
            self.team_leader_card.value_label.setText("-")  # type: ignore[attr-defined]
            self.player_leader_card.value_label.setText("-")  # type: ignore[attr-defined]
            self.champions_card.value_label.setText("-")  # type: ignore[attr-defined]
            return
        team_leaders = get_team_honor_leaders(snapshot)
        player_leaders = get_player_honor_leaders(snapshot)
        self.seasons_card.value_label.setText(str(len(snapshot.history)))  # type: ignore[attr-defined]
        self.team_leader_card.value_label.setText(team_leaders[0]["team_name"] if team_leaders else "-")  # type: ignore[attr-defined]
        self.player_leader_card.value_label.setText(player_leaders[0]["label"] if player_leaders else "-")  # type: ignore[attr-defined]
        self.champions_card.value_label.setText(str(self.champions_table.rowCount()))  # type: ignore[attr-defined]

    def _populate_team_honors(self, snapshot: SaveSnapshot | None) -> None:
        self.team_honor_table.setRowCount(0)
        if snapshot is None:
            return
        for index, row in enumerate(get_team_honor_leaders(snapshot), start=1):
            row_index = self.team_honor_table.rowCount()
            set_table_row(
                self.team_honor_table,
                row_index,
                [
                    str(index),
                    row["team_name"],
                    str(row.get("honor_points", 0)),
                    str(row.get("total_titles", 0)),
                    str(row.get("premier_titles", 0)),
                    str(row.get("winners_cup_titles", 0)),
                    str(row.get("challenge_cup_titles", 0)),
                    str(row.get("super_cup_titles", 0)),
                ],
            )
            self.team_honor_table.item(row_index, 1).setData(Qt.UserRole, row["team_name"])

    def _populate_player_honors(self, snapshot: SaveSnapshot | None) -> None:
        self.player_honor_table.setRowCount(0)
        if snapshot is None:
            return
        for index, row in enumerate(get_player_honor_leaders(snapshot), start=1):
            row_index = self.player_honor_table.rowCount()
            set_table_row(
                self.player_honor_table,
                row_index,
                [
                    str(index),
                    row["label"],
                    row["position"],
                    str(row.get("honor_points", 0)),
                    str(row.get("total_titles", 0)),
                    str(row.get("top20_finishes", 0)),
                    "-" if row.get("top20_best_rank") is None else str(row.get("top20_best_rank")),
                    str(row.get("top_scorer_awards", 0)),
                    str(row.get("assist_leader_awards", 0)),
                    str(row.get("mvp_awards", 0)),
                ],
            )
            self.player_honor_table.item(row_index, 1).setData(Qt.UserRole, row.get("player_id"))
            self.player_honor_table.item(row_index, 1).setData(Qt.UserRole + 1, row["label"])

    def _populate_champions(self, snapshot: SaveSnapshot | None) -> None:
        self.champions_table.setRowCount(0)
        if snapshot is None:
            return
        seasons = list(snapshot.history)
        archived_season_numbers = {int(season["season_number"]) for season in seasons}
        if snapshot.season_number not in archived_season_numbers and snapshot.season_complete:
            seasons.append(
                {
                    "season_number": snapshot.season_number,
                    "team_stats": [],
                    "cup_champions": snapshot.cup_champions,
                    "premier_order": [row.team.name for row in snapshot.premier_table],
                    "second_order": [row.team.name for row in snapshot.second_table],
                }
            )
        seasons.sort(key=lambda item: int(item["season_number"]))
        for season in seasons:
            premier_order = season.get("premier_order", [])
            second_order = season.get("second_order", [])
            cup_champions = season.get("cup_champions", {})
            row_index = self.champions_table.rowCount()
            set_table_row(
                self.champions_table,
                row_index,
                [
                    f"S{season['season_number']}",
                    premier_order[0] if premier_order else "-",
                    second_order[0] if second_order else "-",
                    cup_champions.get("优胜者杯", "-") or "-",
                    cup_champions.get("挑战杯", "-") or "-",
                    cup_champions.get("超级杯", "-") or "-",
                ],
            )
            for column in range(1, self.champions_table.columnCount()):
                item = self.champions_table.item(row_index, column)
                if item is not None and item.text() != "-":
                    item.setData(Qt.UserRole, item.text())

    def _populate_awards(self, snapshot: SaveSnapshot | None) -> None:
        self.top20_table.setRowCount(0)
        self.competition_awards_table.setRowCount(0)
        if snapshot is None:
            return
        for season in sorted(get_season_awards(snapshot), key=lambda item: item["season_number"], reverse=True):
            season_number = season["season_number"]
            for item in season.get("top20", []):
                row_index = self.top20_table.rowCount()
                set_table_row(
                    self.top20_table,
                    row_index,
                    [
                        f"S{season_number}",
                        str(item["rank"]),
                        item["label"],
                        item["position"],
                        item["team_name"],
                        f"{float(item['rating']):.2f}",
                        f"{float(item['score']):.2f}",
                        str(item.get("goals", 0)),
                        str(item.get("assists", 0)),
                    ],
                )
                self.top20_table.item(row_index, 2).setData(Qt.UserRole, item.get("player_id"))
                self.top20_table.item(row_index, 2).setData(Qt.UserRole + 1, item["label"])

            for competition, awards in season.get("competitions", {}).items():
                for award_key, award_label in (
                    ("top_scorer", "射手王"),
                    ("assist_leader", "助攻王"),
                    ("mvp", "MVP"),
                ):
                    item = awards.get(award_key)
                    if not item:
                        continue
                    row_index = self.competition_awards_table.rowCount()
                    set_table_row(
                        self.competition_awards_table,
                        row_index,
                        [
                            f"S{season_number}",
                            competition,
                            award_label,
                            item["label"],
                            item["position"],
                            item["team_name"],
                            _award_stat_summary(item, award_key),
                            f"{float(item['rating']):.2f}",
                        ],
                    )
                    self.competition_awards_table.item(row_index, 3).setData(Qt.UserRole, item.get("player_id"))
                    self.competition_awards_table.item(row_index, 3).setData(Qt.UserRole + 1, item["label"])

    def _open_selected_team_honor(self, *_args) -> None:
        selected = self.team_honor_table.selectionModel().selectedRows()
        if not selected:
            return
        item = self.team_honor_table.item(selected[0].row(), 1)
        if item is not None and item.data(Qt.UserRole):
            self.open_team_callback(item.data(Qt.UserRole))

    def _open_selected_player_honor(self, *_args) -> None:
        selected = self.player_honor_table.selectionModel().selectedRows()
        if not selected:
            return
        item = self.player_honor_table.item(selected[0].row(), 1)
        if item is None:
            return
        player_id = item.data(Qt.UserRole)
        label = item.data(Qt.UserRole + 1)
        self.open_player_callback(player_id if player_id and not str(player_id).startswith("real::") else None, label)

    def _open_selected_champion(self, item) -> None:
        if item is not None and item.column() > 0 and item.data(Qt.UserRole):
            self.open_team_callback(item.data(Qt.UserRole))

    def _open_selected_award_player(self, item) -> None:
        if item is None:
            return
        row = item.row()
        table = item.tableWidget()
        if table is self.top20_table:
            player_item = table.item(row, 2)
        else:
            player_item = table.item(row, 3)
        if player_item is None:
            return
        player_id = player_item.data(Qt.UserRole)
        label = player_item.data(Qt.UserRole + 1)
        self.open_player_callback(player_id if player_id and not str(player_id).startswith("real::") else None, label)


def _award_stat_summary(item: dict, award_key: str) -> str:
    if item.get("position") == "GK":
        return f"扑 {item.get('successful_saves', 0)} | 零 {item.get('clean_sheets', 0)}"
    if award_key == "top_scorer":
        return f"进 {item.get('goals', 0)} | 助 {item.get('assists', 0)}"
    if award_key == "assist_leader":
        return f"助 {item.get('assists', 0)} | 进 {item.get('goals', 0)}"
    return f"进 {item.get('goals', 0)} | 助 {item.get('assists', 0)} | 创 {item.get('chances_created', 0)}"
