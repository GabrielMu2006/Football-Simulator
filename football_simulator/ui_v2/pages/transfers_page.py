from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.state import SaveSnapshot, get_trade_detail_rows
from football_simulator.ui_v2.services import SimulatorUIService
from football_simulator.ui_v2.widgets import CardFrame, color_position_items, make_badge, set_table_row, setup_table


class TransfersPage(QWidget):
    def __init__(
        self,
        service: SimulatorUIService,
        save_name_getter: Callable[[], str],
        state_callback: Callable[[SaveSnapshot | None], None],
        open_player_callback: Callable[[str | None, str | None], None],
    ) -> None:
        super().__init__()
        self.service = service
        self.save_name_getter = save_name_getter
        self.state_callback = state_callback
        self.open_player_callback = open_player_callback
        self.snapshot: SaveSnapshot | None = None
        self._check_boxes: list[QCheckBox] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        header = CardFrame("转会审核中心", "冬窗和夏窗的随机交易会在这里汇总。勾选代表通过，不勾选代表拒绝。")
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)
        self.approve_all_button = QPushButton("全部通过")
        self.reject_all_button = QPushButton("全部拒绝")
        self.submit_button = QPushButton("提交转会审核")
        self.approve_all_button.clicked.connect(lambda: self._set_all_checks(True))
        self.reject_all_button.clicked.connect(lambda: self._set_all_checks(False))
        self.submit_button.clicked.connect(self._submit)
        actions_layout.addWidget(self.approve_all_button)
        actions_layout.addWidget(self.reject_all_button)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.submit_button)
        header.body_layout.addWidget(actions)
        layout.addWidget(header)

        self.empty_label = QLabel("当前没有待审核的转会。")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(14)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll, 1)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self.snapshot = snapshot
        self._rebuild()

    def _rebuild(self) -> None:
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._check_boxes = []

        pending = [] if self.snapshot is None else get_trade_detail_rows(self.snapshot)
        has_pending = bool(pending)
        self.empty_label.setVisible(not has_pending)
        self.scroll.setVisible(has_pending)
        self.submit_button.setEnabled(has_pending)
        self.approve_all_button.setEnabled(has_pending)
        self.reject_all_button.setEnabled(has_pending)
        if not has_pending:
            self.empty_label.setText("当前没有待审核的转会。模拟进入冬窗或夏窗之后，这里会出现交易提案。")
            return

        for index, item in enumerate(pending, start=1):
            value_delta_a = _signed_money(item["team_a_value_delta"])
            value_delta_b = _signed_money(item["team_b_value_delta"])
            card = CardFrame(
                f"交易提案 {index}",
                f"{item['team_a']} 送出 {item['team_a_total_value']:.2f}M | "
                f"{item['team_b']} 送出 {item['team_b_total_value']:.2f}M | "
                f"差额 {item['value_gap']:.2f}M",
            )
            review_checkbox = QCheckBox("通过这笔交易")
            review_checkbox.setChecked(True)
            card.body_layout.addWidget(review_checkbox)
            self._check_boxes.append(review_checkbox)

            summary_row = QHBoxLayout()
            summary_row.setContentsMargins(0, 0, 0, 0)
            summary_row.addWidget(make_badge(f"差额 {item['value_gap']:.2f}M", "#f9c74f"))
            summary_row.addWidget(make_badge(f"{item['team_a']} 身价 {value_delta_a}", "#8ec5ff"))
            summary_row.addWidget(make_badge(f"{item['team_b']} 身价 {value_delta_b}", "#9ae6b4"))
            summary_row.addWidget(make_badge("位置合法" if item["team_a_positions_valid"] and item["team_b_positions_valid"] else "位置风险", "#a7f3d0" if item["team_a_positions_valid"] and item["team_b_positions_valid"] else "#fca5a5"))
            summary_row.addStretch(1)
            card.body_layout.addLayout(summary_row)

            trade_grid = QGridLayout()
            trade_grid.setSpacing(12)
            left = self._trade_side_widget(
                item["team_a"],
                "送出",
                item["team_a_players"],
                item["team_a_positions_before"],
                item["team_a_positions_after"],
                item["team_a_ability_delta"],
                item["team_a_value_delta"],
            )
            right = self._trade_side_widget(
                item["team_b"],
                "送出",
                item["team_b_players"],
                item["team_b_positions_before"],
                item["team_b_positions_after"],
                item["team_b_ability_delta"],
                item["team_b_value_delta"],
            )
            trade_grid.addWidget(left, 0, 0)
            trade_grid.addWidget(right, 0, 1)
            card.body_layout.addLayout(trade_grid)
            self.scroll_layout.addWidget(card)
        self.scroll_layout.addStretch(1)

    def _trade_side_widget(
        self,
        team_name: str,
        action: str,
        players: list[dict],
        positions_before: dict,
        positions_after: dict,
        ability_delta: int,
        value_delta: float,
    ) -> QWidget:
        wrapper = CardFrame(
            f"{team_name} {action}",
            f"能力变化 {_signed_number(ability_delta)} | 身价变化 {_signed_money(value_delta)} | 位置 {_format_position_flow(positions_before, positions_after)}",
        )
        table = QTableWidget()
        setup_table(table, ["球员", "位置", "能力", "身价"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.itemDoubleClicked.connect(self._open_player_from_table_item)
        for player in players:
            row_index = table.rowCount()
            set_table_row(
                table,
                row_index,
                [
                    player["name"],
                    player["position"],
                    str(player["ability"]),
                    f"{player['market_value']:.2f}M",
                ],
            )
            table.item(row_index, 0).setData(Qt.UserRole, player.get("player_id"))
            table.item(row_index, 0).setData(Qt.UserRole + 1, player.get("name"))
        color_position_items(table, 1)
        wrapper.body_layout.addWidget(table)
        return wrapper

    def _open_player_from_table_item(self, item) -> None:
        if item is None:
            return
        player_item = item.tableWidget().item(item.row(), 0)
        if player_item is None:
            return
        self.open_player_callback(player_item.data(Qt.UserRole), player_item.data(Qt.UserRole + 1))

    def _set_all_checks(self, checked: bool) -> None:
        for box in self._check_boxes:
            box.setChecked(checked)

    def _submit(self) -> None:
        if self.snapshot is None or not self.snapshot.pending_transfer_review:
            return
        decisions = {
            item["trade_id"]: self._check_boxes[index].isChecked()
            for index, item in enumerate(self.snapshot.pending_transfer_review)
        }
        try:
            state = self.service.apply_transfer_review(self.save_name_getter(), decisions)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", str(exc))
            return
        self.state_callback(state.snapshot)
        QMessageBox.information(self, "Football Simulator UI v2", "已完成转会审核。")


def _signed_money(value: float) -> str:
    return f"{value:+.2f}M"


def _signed_number(value: int) -> str:
    return f"{value:+d}"


def _format_position_flow(before: dict, after: dict) -> str:
    parts = []
    for position in ("GK", "DF", "MF", "FW"):
        parts.append(f"{position} {before.get(position, 0)}->{after.get(position, 0)}")
    return " | ".join(parts)
