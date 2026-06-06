from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabWidget, QTableWidget, QVBoxLayout, QWidget, QHeaderView

from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2.widgets import ZONE_BACKGROUNDS, setup_table, set_table_row, shade_row


class StandingsPage(QWidget):
    HEADERS = ["名次", "区域", "球队", "赛", "胜", "平", "负", "进", "失", "净", "积", "近况"]

    def __init__(self, open_team_callback: Callable[[str], None]) -> None:
        super().__init__()
        self.open_team_callback = open_team_callback
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.premier_table = QTableWidget()
        self.second_table = QTableWidget()
        for table in (self.premier_table, self.second_table):
            setup_table(table, self.HEADERS)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
            table.itemDoubleClicked.connect(self._open_selected_team)
        self.tabs.addTab(self.premier_table, "一级联赛")
        self.tabs.addTab(self.second_table, "次级联赛")
        layout.addWidget(self.tabs)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self._populate_table(self.premier_table, snapshot.premier_table if snapshot else [], "premier", snapshot)
        self._populate_table(self.second_table, snapshot.second_table if snapshot else [], "second", snapshot)

    def _populate_table(self, table: QTableWidget, rows: list, division_key: str, snapshot: SaveSnapshot | None) -> None:
        table.setRowCount(0)
        form_lookup = _recent_form(snapshot)
        for index, row in enumerate(rows, start=1):
            row_index = table.rowCount()
            zone, zone_color = _zone_for_rank(index, len(rows), division_key)
            set_table_row(
                table,
                row_index,
                [
                    str(index),
                    zone,
                    row.team.name,
                    str(row.played),
                    str(row.wins),
                    str(row.draws),
                    str(row.losses),
                    str(row.goals_for),
                    str(row.goals_against),
                    str(row.goals_for - row.goals_against),
                    str(row.points),
                    form_lookup.get(row.team.name, "-"),
                ],
            )
            table.item(row_index, 2).setData(Qt.UserRole, row.team.name)
            if zone_color:
                shade_row(table, row_index, zone_color)

    def _open_selected_team(self, *_args) -> None:
        table = self.tabs.currentWidget()
        if not isinstance(table, QTableWidget):
            return
        selected = table.selectionModel().selectedRows()
        if not selected:
            return
        team_item = table.item(selected[0].row(), 2)
        if team_item is None:
            return
        team_name = team_item.data(Qt.UserRole)
        if team_name:
            self.open_team_callback(team_name)


def _zone_for_rank(rank: int, total: int, division_key: str) -> tuple[str, str | None]:
    if division_key == "premier":
        if rank == 1:
            return "争冠", ZONE_BACKGROUNDS["champion"]
        if rank > total - 3:
            return "降级区", ZONE_BACKGROUNDS["relegation"]
        return "-", None
    if rank <= 2:
        return "直升区", ZONE_BACKGROUNDS["promotion"]
    if 3 <= rank <= 6:
        return "附加赛区", ZONE_BACKGROUNDS["playoff"]
    return "-", None


def _recent_form(snapshot: SaveSnapshot | None) -> dict[str, str]:
    if snapshot is None:
        return {}
    forms: dict[str, list[str]] = {}
    for week in snapshot.simulated_weeks[-5:]:
        for key in ("premier_matchdays", "second_matchdays"):
            for matchday in week.get(key, []):
                for result in matchday.get("results", []):
                    home = result["home_team"]
                    away = result["away_team"]
                    home_goals = int(result["home_goals"])
                    away_goals = int(result["away_goals"])
                    if home_goals > away_goals:
                        home_mark, away_mark = "胜", "负"
                    elif home_goals < away_goals:
                        home_mark, away_mark = "负", "胜"
                    else:
                        home_mark = away_mark = "平"
                    forms.setdefault(home, []).append(home_mark)
                    forms.setdefault(away, []).append(away_mark)
    return {team: " ".join(values[-5:]) for team, values in forms.items()}
