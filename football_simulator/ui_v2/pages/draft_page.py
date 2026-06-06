from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2.services import SimulatorUIService
from football_simulator.ui_v2.widgets import CardFrame, build_metric_card, set_table_row, setup_table


class DraftPage(QWidget):
    def __init__(
        self,
        service: SimulatorUIService,
        save_name_getter: Callable[[], str],
        state_callback: Callable[[SaveSnapshot | None], None],
    ) -> None:
        super().__init__()
        self.service = service
        self.save_name_getter = save_name_getter
        self.state_callback = state_callback
        self.snapshot: SaveSnapshot | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        intro = CardFrame(
            "选秀中心",
            "赛季结束并完成所有审核之后，系统会从本存档配置的选秀池按顺序抽取本届名单；如果配置候选不足，可以手动补录，也可以不补录。能力由系统随机生成，初始身价固定为 30.00M。",
        )
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self.name_input = QLineEdit()
        self.position_box = QComboBox()
        self.position_box.addItems(["GK", "DF", "MF", "FW"])
        self.add_button = QPushButton("加入新秀名单")
        self.add_button.clicked.connect(self._add_prospect)
        form.addRow("姓名", self.name_input)
        form.addRow("位置", self.position_box)
        form.addRow("", self.add_button)
        intro.body_layout.addWidget(form_widget)
        layout.addWidget(intro)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.status_card = build_metric_card("选秀状态", "-", "赛季结束后开放")
        self.pool_card = build_metric_card("候选人数", "0", "本次录入的新秀数量")
        self.last_pick_card = build_metric_card("最近结果", "-", "最近一次选秀摘要")
        cards.addWidget(self.status_card, 0, 0)
        cards.addWidget(self.pool_card, 0, 1)
        cards.addWidget(self.last_pick_card, 0, 2)
        layout.addLayout(cards)

        self.order_table = QTableWidget()
        setup_table(self.order_table, ["顺位", "球队", "上赛季排名依据"])
        self.order_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.order_table)

        self.pending_table = QTableWidget()
        setup_table(self.pending_table, ["姓名", "位置"])
        self.pending_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pending_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.pending_table)

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        self.clear_button = QPushButton("清空名单")
        self.submit_button = QPushButton("确认开始选秀")
        self.clear_button.clicked.connect(self._clear)
        self.submit_button.clicked.connect(self._submit)
        actions_layout.addWidget(self.clear_button)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.submit_button)
        layout.addWidget(actions)

        self.result_table = QTableWidget()
        setup_table(self.result_table, ["顺位", "球队", "新秀", "位置", "能力", "初始身价"])
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.result_table, 1)

        self.status_label = QLabel("当前没有待处理的选秀。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self.snapshot = snapshot
        self._refresh_state()

    def _refresh_state(self) -> None:
        pending = self.snapshot is not None and self.snapshot.pending_draft.get("status") == "awaiting_input"
        self.name_input.setEnabled(bool(pending))
        self.position_box.setEnabled(bool(pending))
        self.add_button.setEnabled(bool(pending))
        self.clear_button.setEnabled(bool(pending))
        self.submit_button.setEnabled(bool(pending))
        if pending:
            target_count = int(self.snapshot.pending_draft.get("candidate_count", 0))
            self.status_label.setText(
                f"当前赛季已结束，正在等待选秀。本届计划选秀人数为 {target_count} 人。"
                "系统会优先使用本存档配置中的顺序候选；如果候选不足，你可以在这里手动补录，也可以直接确认。"
            )
            self.status_card.value_label.setText("等待录入")  # type: ignore[attr-defined]
        else:
            self.status_label.setText("当前没有待处理的选秀。")
            self.status_card.value_label.setText("未开放")  # type: ignore[attr-defined]
        if pending:
            self.pool_card.value_label.setText(str(self.snapshot.pending_draft.get("candidate_count", 0)))  # type: ignore[attr-defined]
            self.pool_card.note_label.setText(f"手动补录 {self.pending_table.rowCount()} 人")  # type: ignore[attr-defined]
        else:
            self.pool_card.value_label.setText(str(self.pending_table.rowCount()))  # type: ignore[attr-defined]
            self.pool_card.note_label.setText("本次录入的新秀数量")  # type: ignore[attr-defined]
        self._populate_order_table()
        self._load_last_result()

    def _add_prospect(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Football Simulator UI v2", "新秀姓名不能为空。")
            return
        position = self.position_box.currentText()
        set_table_row(self.pending_table, self.pending_table.rowCount(), [name, position])
        self.name_input.clear()
        self.pool_card.note_label.setText(f"手动补录 {self.pending_table.rowCount()} 人")  # type: ignore[attr-defined]

    def _clear(self) -> None:
        self.pending_table.setRowCount(0)
        self.pool_card.note_label.setText("手动补录 0 人")  # type: ignore[attr-defined]

    def _submit(self) -> None:
        if self.snapshot is None or self.snapshot.pending_draft.get("status") != "awaiting_input":
            return
        prospects = []
        for row in range(self.pending_table.rowCount()):
            prospects.append(
                {
                    "name": self.pending_table.item(row, 0).text(),
                    "position": self.pending_table.item(row, 1).text(),
                }
            )
        try:
            state = self.service.apply_draft(self.save_name_getter(), prospects)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", str(exc))
            return
        self.pending_table.setRowCount(0)
        self.state_callback(state.snapshot)
        QMessageBox.information(self, "Football Simulator UI v2", "已完成本赛季选秀。")

    def _populate_order_table(self) -> None:
        self.order_table.setRowCount(0)
        if self.snapshot is None:
            return
        for index, row in enumerate(reversed(self.snapshot.premier_table), start=1):
            set_table_row(
                self.order_table,
                self.order_table.rowCount(),
                [str(index), row.team.name, f"一级联赛第 {len(self.snapshot.premier_table) - index + 1} 名"],
            )

    def _load_last_result(self) -> None:
        self.result_table.setRowCount(0)
        try:
            draft_log = self.service.load_last_draft(self.save_name_getter())
        except Exception:
            self.last_pick_card.value_label.setText("-")  # type: ignore[attr-defined]
            return
        results = draft_log.get("results", [])
        self.last_pick_card.value_label.setText(f"{len(results)} 人")  # type: ignore[attr-defined]
        for index, row in enumerate(results, start=1):
            set_table_row(
                self.result_table,
                self.result_table.rowCount(),
                [
                    str(index),
                    row["team_name"],
                    row["name"],
                    row["position"],
                    str(row["ability"]),
                    f"{float(row['market_value']):.2f}M",
                ],
            )
