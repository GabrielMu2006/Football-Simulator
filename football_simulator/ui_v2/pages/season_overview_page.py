from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2.services import SimulatorUIService
from football_simulator.ui_v2.widgets import CardFrame, ZONE_BACKGROUNDS, build_metric_card, set_table_row, setup_table, shade_row


class SeasonOverviewPage(QWidget):
    def __init__(
        self,
        service: SimulatorUIService,
        save_name_getter: Callable[[], str],
        state_callback: Callable[[SaveSnapshot | None], None],
        open_matches_callback: Callable[[], None],
    ) -> None:
        super().__init__()
        self.service = service
        self.save_name_getter = save_name_getter
        self.state_callback = state_callback
        self.open_matches_callback = open_matches_callback
        self.snapshot: SaveSnapshot | None = None
        self._decision_boxes: list[QComboBox] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        summary_grid = QGridLayout()
        summary_grid.setSpacing(16)
        self.timeline_card = build_metric_card("赛历阶段", "-", "这里会显示当前阶段与下一步关键节点。")
        self.cups_card = build_metric_card("杯赛状态", "-", "这里会汇总当前激活的杯赛数量。")
        self.review_card = build_metric_card("能力审核", "-", "赛季末这里会变成可处理状态。")
        self.next_card = build_metric_card("下一关键周", "-", "根据周历给出下一个关键时间点。")
        summary_grid.addWidget(self.timeline_card, 0, 0)
        summary_grid.addWidget(self.cups_card, 0, 1)
        summary_grid.addWidget(self.review_card, 0, 2)
        summary_grid.addWidget(self.next_card, 0, 3)
        layout.addLayout(summary_grid)

        self.tabs = QTabWidget()

        overview_page = QWidget()
        overview_layout = QGridLayout(overview_page)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(16)

        self.timeline_panel = CardFrame("赛历时间轴", "完整 52 周赛历。当前周、冬窗、夏窗、杯赛周会有不同底色。")
        timeline_actions = QHBoxLayout()
        timeline_actions.setContentsMargins(0, 0, 0, 0)
        timeline_actions.addStretch(1)
        self.open_recent_matches_button = QPushButton("查看最近一周详细赛况")
        self.open_recent_matches_button.clicked.connect(self.open_matches_callback)
        timeline_actions.addWidget(self.open_recent_matches_button)
        self.timeline_panel.body_layout.addLayout(timeline_actions)
        self.timeline_table = QTableWidget()
        setup_table(self.timeline_table, ["周次", "类型", "联赛轮次", "杯赛/附加赛"])
        self.timeline_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.timeline_table.horizontalHeader().setStretchLastSection(True)
        self.timeline_panel.body_layout.addWidget(self.timeline_table)
        overview_layout.addWidget(self.timeline_panel, 0, 0, 1, 2)

        self.promotion_panel = CardFrame("升降级形势", "这里会显示一级联赛降级区与次级联赛升级区。")
        self.promotion_table = QTableWidget()
        setup_table(self.promotion_table, ["区域", "球队", "积分", "净胜球", "备注"])
        self.promotion_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.promotion_table.horizontalHeader().setStretchLastSection(True)
        self.promotion_panel.body_layout.addWidget(self.promotion_table)
        overview_layout.addWidget(self.promotion_panel, 1, 0)

        self.cup_summary_panel = CardFrame("杯赛摘要", "当前赛季三项杯赛的启用状态和冠军情况。")
        self.cup_summary_table = QTableWidget()
        setup_table(self.cup_summary_table, ["赛事", "状态", "冠军/备注"])
        self.cup_summary_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.cup_summary_table.horizontalHeader().setStretchLastSection(True)
        self.cup_summary_panel.body_layout.addWidget(self.cup_summary_table)
        overview_layout.addWidget(self.cup_summary_panel, 1, 1)

        self.review_panel = CardFrame("能力变动审核", "如果当前赛季已经完成并且有待审核能力变动，可以直接在这里处理。")
        self.review_hint = QLabel("当前没有待审核的能力变动。")
        self.review_hint.setWordWrap(True)
        self.review_table = QTableWidget()
        setup_table(self.review_table, ["球员", "位置", "旧能力", "新能力", "变化", "决定"])
        self.review_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.review_table.horizontalHeader().setStretchLastSection(True)
        self.apply_button = QPushButton("提交能力审核")
        self.apply_button.clicked.connect(self._apply_reviews)
        self.review_panel.body_layout.addWidget(self.review_hint)
        self.review_panel.body_layout.addWidget(self.review_table)
        self.review_panel.body_layout.addWidget(self.apply_button, 0, Qt.AlignRight)
        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.addWidget(self.review_panel)

        self.tabs.addTab(overview_page, "赛季概览")
        self.tabs.addTab(review_page, "能力审核")
        layout.addWidget(self.tabs)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self.snapshot = snapshot
        if snapshot is None or not snapshot.weeks:
            self.timeline_card.value_label.setText("-")  # type: ignore[attr-defined]
            self.cups_card.value_label.setText("-")  # type: ignore[attr-defined]
            self.review_card.value_label.setText("-")  # type: ignore[attr-defined]
            self.next_card.value_label.setText("-")  # type: ignore[attr-defined]
            self.review_hint.setText("请先初始化一个赛季。")
            self.review_table.setRowCount(0)
            self.apply_button.setEnabled(False)
            self.open_recent_matches_button.setEnabled(False)
            return

        current_label = snapshot.weeks[snapshot.current_week].label if snapshot.current_week < len(snapshot.weeks) else "赛季已结束"
        cup_count = sum(
            1
            for value in snapshot.cup_state.values()
            if isinstance(value, dict) and value.get("active")
        )
        pending_count = len(snapshot.pending_ability_review)
        next_key_week = next(
            (week for week in snapshot.weeks[snapshot.current_week:] if week.kind in {"winter_break", "summer_break", "promotion_playoff"} or week.cup_events),
            None,
        )
        self.timeline_card.value_label.setText(current_label)  # type: ignore[attr-defined]
        self.cups_card.value_label.setText(str(cup_count))  # type: ignore[attr-defined]
        self.review_card.value_label.setText(str(pending_count))  # type: ignore[attr-defined]
        self.next_card.value_label.setText(
            "-" if next_key_week is None else f"W{next_key_week.week_number}"
        )  # type: ignore[attr-defined]
        self.open_recent_matches_button.setEnabled(bool(snapshot.simulated_weeks))
        self._populate_overview(snapshot)

        self.review_table.setRowCount(0)
        self._decision_boxes = []
        if not snapshot.pending_ability_review:
            self.review_hint.setText("当前没有待审核的能力变动。")
            self.apply_button.setEnabled(False)
            return

        self.review_hint.setText("请为每一条能力变动选择“通过”或“拒绝”，然后提交。")
        for item in snapshot.pending_ability_review:
            row_index = self.review_table.rowCount()
            set_table_row(
                self.review_table,
                row_index,
                [
                    item["name"],
                    item["position"],
                    str(item["old_ability"]),
                    str(item["new_ability"]),
                    f"{int(item['delta']):+d}",
                    "",
                ],
            )
            decision_box = QComboBox()
            decision_box.addItems(["通过", "拒绝"])
            self.review_table.setCellWidget(row_index, 5, decision_box)
            self._decision_boxes.append(decision_box)
        self.apply_button.setEnabled(True)

    def _apply_reviews(self) -> None:
        if self.snapshot is None or not self.snapshot.pending_ability_review:
            return
        decisions = {
            item["name"]: self._decision_boxes[index].currentText() == "通过"
            for index, item in enumerate(self.snapshot.pending_ability_review)
        }
        try:
            state = self.service.apply_ability_review(self.save_name_getter(), decisions)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", str(exc))
            return
        self.state_callback(state.snapshot)
        QMessageBox.information(self, "Football Simulator UI v2", "已完成能力变动审核。")

    def _populate_overview(self, snapshot: SaveSnapshot) -> None:
        self.timeline_table.setRowCount(0)
        for week in snapshot.weeks:
            league_rounds = []
            if week.premier_round_numbers:
                league_rounds.append("一级 " + "、".join(str(number) for number in week.premier_round_numbers))
            if week.second_round_numbers:
                league_rounds.append("次级 " + "、".join(str(number) for number in week.second_round_numbers))
            cup_text = "、".join(week.cup_events) if week.cup_events else (week.promotion_playoff_stage or "-")
            row_index = self.timeline_table.rowCount()
            set_table_row(
                self.timeline_table,
                row_index,
                [
                    f"W{week.week_number}" + ("（当前）" if week.week_number == snapshot.current_week else ""),
                    week.label,
                    " | ".join(league_rounds) if league_rounds else "-",
                    cup_text,
                ],
            )
            if week.week_number == snapshot.current_week:
                shade_row(self.timeline_table, row_index, ZONE_BACKGROUNDS["current"])
            elif week.kind == "winter_break":
                shade_row(self.timeline_table, row_index, "#26381f")
            elif week.kind == "summer_break":
                shade_row(self.timeline_table, row_index, "#342a1d")
            elif week.cup_events:
                shade_row(self.timeline_table, row_index, "#2c2543")

        self.promotion_table.setRowCount(0)
        premier_bottom = list(reversed(snapshot.premier_table[-3:]))
        for row in premier_bottom:
            set_table_row(
                self.promotion_table,
                self.promotion_table.rowCount(),
                ["一级降级区", row.team.name, str(row.points), str(row.goals_for - row.goals_against), "倒数 3 名降级"],
            )
        for row in snapshot.second_table[:2]:
            set_table_row(
                self.promotion_table,
                self.promotion_table.rowCount(),
                ["次级直升区", row.team.name, str(row.points), str(row.goals_for - row.goals_against), "前 2 名直升"],
            )
        for row in snapshot.second_table[2:6]:
            set_table_row(
                self.promotion_table,
                self.promotion_table.rowCount(),
                ["次级附加赛区", row.team.name, str(row.points), str(row.goals_for - row.goals_against), "第 3-6 名打附加赛"],
            )

        self.cup_summary_table.setRowCount(0)
        cup_labels = [("优胜者杯", "winners_cup"), ("挑战杯", "challenge_cup"), ("超级杯", "super_cup")]
        for label, key in cup_labels:
            cup_data = snapshot.cup_state.get(key, {})
            status = "启用" if cup_data.get("active") else "未启用"
            champion = cup_data.get("champion") or "-"
            set_table_row(
                self.cup_summary_table,
                self.cup_summary_table.rowCount(),
                [label, status, champion],
            )
