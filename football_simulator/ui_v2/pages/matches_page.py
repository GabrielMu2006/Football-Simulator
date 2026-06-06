from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QComboBox,
    QGridLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2.widgets import CardFrame, build_metric_card, color_position_items, set_table_row, setup_table


class MatchCenterPage(QWidget):
    def __init__(
        self,
        open_team_callback: Callable[[str], None],
        open_player_callback: Callable[[str | None, str | None], None],
    ) -> None:
        super().__init__()
        self.snapshot: SaveSnapshot | None = None
        self.open_team_callback = open_team_callback
        self.open_player_callback = open_player_callback
        self._current_week_data: dict | None = None
        self._all_match_records: list[dict] = []
        self._match_records: list[dict] = []
        self._current_result: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_grid = QGridLayout()
        summary_grid.setSpacing(16)
        self.week_card = build_metric_card("查看周次", "-", "选择一周后查看该周所有比赛。")
        self.match_count_card = build_metric_card("比赛场数", "-", "该周共结算的比赛数量。")
        self.competition_card = build_metric_card("赛事类型", "-", "该周涉及的赛事种类。")
        self.focus_card = build_metric_card("当前焦点", "-", "选中比赛后显示当前对阵。")
        summary_grid.addWidget(self.week_card, 0, 0)
        summary_grid.addWidget(self.match_count_card, 0, 1)
        summary_grid.addWidget(self.competition_card, 0, 2)
        summary_grid.addWidget(self.focus_card, 0, 3)
        layout.addLayout(summary_grid)

        controls = CardFrame("周次选择", "支持回看任意已模拟周次，并查看单场详细事件。")
        self.week_picker = QComboBox()
        self.week_picker.currentIndexChanged.connect(self._on_week_changed)
        self.competition_filter = QComboBox()
        self.competition_filter.currentIndexChanged.connect(self._apply_match_filter)
        controls.body_layout.addWidget(self.week_picker)
        controls.body_layout.addWidget(self.competition_filter)
        layout.addWidget(controls)

        content_grid = QGridLayout()
        content_grid.setSpacing(16)

        self.matches_panel = CardFrame("当周比赛列表", "先选一场比赛，再在右侧查看详细事件和球员数据。")
        self.matches_table = QTableWidget()
        setup_table(self.matches_table, ["编号", "赛事", "轮次", "主队", "比分", "客队"])
        self.matches_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.matches_table.horizontalHeader().setStretchLastSection(True)
        self.matches_table.itemSelectionChanged.connect(self._on_match_selected)
        self.matches_panel.body_layout.addWidget(self.matches_table)
        content_grid.addWidget(self.matches_panel, 0, 0)

        self.detail_panel = CardFrame("比赛详情", "这里会展示关键事件和这场比赛里产生的数据。")
        self.detail_title = QLabel("还没有选中比赛。")
        self.detail_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f8fbff;")
        detail_actions = QHBoxLayout()
        detail_actions.setContentsMargins(0, 0, 0, 0)
        self.open_home_team_button = QPushButton("查看主队")
        self.open_away_team_button = QPushButton("查看客队")
        self.open_home_team_button.clicked.connect(lambda: self._open_current_team("home"))
        self.open_away_team_button.clicked.connect(lambda: self._open_current_team("away"))
        detail_actions.addWidget(self.open_home_team_button)
        detail_actions.addWidget(self.open_away_team_button)
        detail_actions.addStretch(1)
        self.events_text = QTextEdit()
        self.events_text.setReadOnly(True)
        self.player_stats_table = QTableWidget()
        setup_table(self.player_stats_table, ["球员", "位置", "进", "助", "机", "防", "扑", "零"])
        self.player_stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.player_stats_table.horizontalHeader().setStretchLastSection(True)
        self.player_stats_table.itemDoubleClicked.connect(self._open_selected_player)
        self.detail_panel.body_layout.addWidget(self.detail_title)
        self.detail_panel.body_layout.addLayout(detail_actions)
        self.detail_panel.body_layout.addWidget(self.events_text)
        self.detail_panel.body_layout.addWidget(self.player_stats_table)
        content_grid.addWidget(self.detail_panel, 0, 1)

        layout.addLayout(content_grid, 1)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        previous_week_label = self.week_picker.currentData(Qt.UserRole)
        self.snapshot = snapshot
        self.week_picker.blockSignals(True)
        self.week_picker.clear()
        self.week_picker.blockSignals(False)
        self.matches_table.setRowCount(0)
        self.player_stats_table.setRowCount(0)
        self.events_text.setPlainText("")
        self.detail_title.setText("还没有选中比赛。")
        self.open_home_team_button.setEnabled(False)
        self.open_away_team_button.setEnabled(False)
        self._match_records = []
        self._all_match_records = []
        self._current_week_data = None
        self._current_result = None
        self.competition_filter.blockSignals(True)
        self.competition_filter.clear()
        self.competition_filter.addItem("全部赛事")
        self.competition_filter.blockSignals(False)

        if snapshot is None or not snapshot.simulated_weeks:
            self._set_summary("-", "-", "-", "-")
            self.events_text.setPlainText("当前还没有可查看的已模拟比赛。")
            return

        for week_data in snapshot.simulated_weeks:
            label = f"第 {week_data['week_number']} 周 | {week_data['label']}"
            self.week_picker.addItem(label, week_data["week_number"])

        target_index = self._latest_match_week_index()
        if previous_week_label is not None:
            for index in range(self.week_picker.count()):
                if self.week_picker.itemData(index, Qt.UserRole) == previous_week_label:
                    target_index = index
                    break
        self.week_picker.setCurrentIndex(target_index)
        self._load_selected_week()

    def focus_latest_week(self) -> None:
        target_index = self._latest_match_week_index()
        if target_index >= 0:
            self.week_picker.setCurrentIndex(target_index)
            self._load_selected_week()

    def _on_week_changed(self) -> None:
        self._load_selected_week()

    def _load_selected_week(self) -> None:
        if self.snapshot is None or self.week_picker.currentIndex() < 0:
            return
        week_number = self.week_picker.currentData(Qt.UserRole)
        week_data = next(
            (item for item in self.snapshot.simulated_weeks if item.get("week_number") == week_number),
            None,
        )
        self._current_week_data = week_data
        self.matches_table.setRowCount(0)
        self.player_stats_table.setRowCount(0)
        self.events_text.setPlainText("")
        self.detail_title.setText("还没有选中比赛。")
        self.open_home_team_button.setEnabled(False)
        self.open_away_team_button.setEnabled(False)
        self._match_records = []
        self._all_match_records = []
        self._current_result = None

        if week_data is None:
            self._set_summary("-", "-", "-", "-")
            return

        competitions = set()
        match_index = 1
        for key in ("premier_matchdays", "second_matchdays", "cup_matchdays", "playoff_matchdays"):
            for matchday in week_data.get(key, []):
                competition = matchday.get("competition", "赛事")
                competitions.add(competition)
                round_number = matchday.get("round_number", "-")
                for result in matchday.get("results", []):
                    record = {
                        "competition": competition,
                        "round_number": round_number,
                        "result": result,
                    }
                    self._all_match_records.append(record)
                    match_index += 1

        self.competition_filter.blockSignals(True)
        self.competition_filter.clear()
        self.competition_filter.addItem("全部赛事")
        for competition in sorted(competitions):
            self.competition_filter.addItem(competition)
        self.competition_filter.blockSignals(False)
        self._apply_match_filter()

    def _apply_match_filter(self) -> None:
        self.matches_table.setRowCount(0)
        self.player_stats_table.setRowCount(0)
        self.events_text.setPlainText("")
        self.detail_title.setText("还没有选中比赛。")
        self.open_home_team_button.setEnabled(False)
        self.open_away_team_button.setEnabled(False)
        selected_competition = self.competition_filter.currentText()
        if selected_competition and selected_competition != "全部赛事":
            self._match_records = [
                record for record in self._all_match_records if record["competition"] == selected_competition
            ]
        else:
            self._match_records = list(self._all_match_records)

        competitions = {record["competition"] for record in self._match_records}
        for match_index, record in enumerate(self._match_records, start=1):
            result = record["result"]
            set_table_row(
                self.matches_table,
                self.matches_table.rowCount(),
                [
                    str(match_index),
                    record["competition"],
                    str(record["round_number"]),
                    result["home_team"],
                    f"{result['home_goals']}-{result['away_goals']}",
                    result["away_team"],
                ],
            )

        self._set_summary(
            f"第 {self._current_week_data['week_number']} 周" if self._current_week_data else "-",
            str(len(self._match_records)),
            str(len(competitions)),
            self._match_records[0]["competition"] if self._match_records else "-",
        )

        if self._match_records:
            self.matches_table.selectRow(0)
            self._on_match_selected()
        else:
            self.events_text.setPlainText("当前筛选下没有已记录的比赛。")

    def _on_match_selected(self) -> None:
        selected = self.matches_table.selectionModel().selectedRows()
        if not selected or self.snapshot is None:
            return
        row_index = selected[0].row()
        if row_index >= len(self._match_records):
            return
        record = self._match_records[row_index]
        result = record["result"]
        self._current_result = result
        title = (
            f"{record['competition']} | 第 {record['round_number']} 轮/回合 | "
            f"{result['home_team']} {result['home_goals']}-{result['away_goals']} {result['away_team']}"
        )
        self.detail_title.setText(title)
        self.focus_card.value_label.setText(f"{result['home_team']} vs {result['away_team']}")  # type: ignore[attr-defined]
        self.open_home_team_button.setEnabled(True)
        self.open_away_team_button.setEnabled(True)
        events = result.get("key_events", [])
        self.events_text.setPlainText("\n".join(events) if events else "这场比赛没有记录到关键事件。")

        self.player_stats_table.setRowCount(0)
        label_map = {row.player.player_id: row.player for row in self.snapshot.player_stats}
        player_rows = []
        for player_id, delta in result.get("player_stats", {}).items():
            total = sum(
                int(delta.get(field, 0))
                for field in (
                    "goals",
                    "assists",
                    "chances_created",
                    "successful_defenses",
                    "successful_saves",
                    "clean_sheets",
                )
            )
            if total <= 0:
                continue
            player = label_map.get(player_id)
            player_rows.append(
                (
                    total,
                    player_id,
                    player.label if player else player_id,
                    player.position if player else "-",
                    delta,
                )
            )
        player_rows.sort(key=lambda item: (-item[0], item[1]))
        for _, player_id, label, position, delta in player_rows:
            row_idx = self.player_stats_table.rowCount()
            set_table_row(
                self.player_stats_table,
                row_idx,
                [
                    label,
                    position,
                    str(delta.get("goals", 0)),
                    str(delta.get("assists", 0)),
                    str(delta.get("chances_created", 0)),
                    str(delta.get("successful_defenses", 0)),
                    str(delta.get("successful_saves", 0)),
                    str(delta.get("clean_sheets", 0)),
                ],
            )
            self.player_stats_table.item(row_idx, 0).setData(Qt.UserRole, label)
            self.player_stats_table.item(row_idx, 0).setData(Qt.UserRole + 1, player_id)
        color_position_items(self.player_stats_table, 1)

    def _set_summary(self, week: str, match_count: str, competition_count: str, focus: str) -> None:
        self.week_card.value_label.setText(week)  # type: ignore[attr-defined]
        self.match_count_card.value_label.setText(match_count)  # type: ignore[attr-defined]
        self.competition_card.value_label.setText(competition_count)  # type: ignore[attr-defined]
        self.focus_card.value_label.setText(focus)  # type: ignore[attr-defined]

    def _latest_match_week_index(self) -> int:
        for index in range(self.week_picker.count() - 1, -1, -1):
            week_number = self.week_picker.itemData(index, Qt.UserRole)
            if self.snapshot is not None:
                week_data = next(
                    (item for item in self.snapshot.simulated_weeks if item.get("week_number") == week_number),
                    None,
                )
                if week_data and _week_has_matches(week_data):
                    return index
        return max(0, self.week_picker.count() - 1)

    def _open_current_team(self, side: str) -> None:
        if not self._current_result:
            return
        team_name = self._current_result["home_team"] if side == "home" else self._current_result["away_team"]
        self.open_team_callback(team_name)

    def _open_selected_player(self, *_args) -> None:
        selected = self.player_stats_table.selectionModel().selectedRows()
        if not selected:
            return
        item = self.player_stats_table.item(selected[0].row(), 0)
        if item is None:
            return
        self.open_player_callback(
            item.data(Qt.UserRole + 1),
            item.data(Qt.UserRole),
        )


def _week_has_matches(week_data: dict) -> bool:
    for key in ("premier_matchdays", "second_matchdays", "cup_matchdays", "playoff_matchdays"):
        for matchday in week_data.get(key, []):
            if matchday.get("results"):
                return True
    return False
