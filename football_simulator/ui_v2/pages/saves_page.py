from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2.services import SimulatorUIService
from football_simulator.ui_v2.widgets import CardFrame


class SavesPage(QWidget):
    def __init__(
        self,
        service: SimulatorUIService,
        save_name_getter: Callable[[], str],
        save_state_callback: Callable[[str, SaveSnapshot | None], None],
    ) -> None:
        super().__init__()
        self.service = service
        self.save_name_getter = save_name_getter
        self.save_state_callback = save_state_callback
        self.snapshot: SaveSnapshot | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        header = CardFrame("存档管理", f"存档目录：{self.service.save_directory()}")
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)
        self.new_save_input = QLineEdit()
        self.new_save_input.setPlaceholderText("输入新存档名")
        self.create_button = QPushButton("新建存档")
        self.select_button = QPushButton("切换到所选存档")
        self.delete_button = QPushButton("删除所选存档")
        self.create_button.clicked.connect(self._create_save)
        self.select_button.clicked.connect(self._select_save)
        self.delete_button.clicked.connect(self._delete_save)
        controls_layout.addWidget(self.new_save_input, 1)
        controls_layout.addWidget(self.create_button)
        controls_layout.addWidget(self.select_button)
        controls_layout.addWidget(self.delete_button)
        header.body_layout.addWidget(controls)
        layout.addWidget(header)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)
        self.save_list = QListWidget()
        self.save_list.currentItemChanged.connect(self._refresh_summary)
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        body_layout.addWidget(self.save_list, 1)
        body_layout.addWidget(self.summary, 2)
        layout.addWidget(body, 1)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self.snapshot = snapshot
        self._refresh_save_list()

    def _refresh_save_list(self) -> None:
        current = self.save_name_getter()
        self.save_list.clear()
        saves = self.service.available_saves()
        for save_name in saves:
            item = QListWidgetItem(self._save_card_text(save_name, save_name == current))
            item.setSizeHint(QSize(260, 74))
            if save_name == current:
                item.setSelected(True)
            item.setData(Qt.UserRole, save_name)
            self.save_list.addItem(item)
        if self.save_list.count():
            target_row = 0
            for index in range(self.save_list.count()):
                if self.save_list.item(index).data(Qt.UserRole) == current:
                    target_row = index
                    break
            self.save_list.setCurrentRow(target_row)
        else:
            self.summary.setPlainText("当前还没有可用存档。")

    def _save_card_text(self, save_name: str, is_current: bool) -> str:
        prefix = "当前存档 | " if is_current else ""
        snapshot = self.service.preview_snapshot(save_name)
        if snapshot is None:
            return f"{prefix}{save_name}\n未初始化\n可初始化新赛季"
        pending = (
            len(snapshot.pending_ability_review)
            + len(snapshot.pending_transfer_review)
            + (1 if snapshot.pending_draft.get("status") == "awaiting_input" else 0)
        )
        return (
            f"{prefix}{save_name}\n"
            f"第 {snapshot.season_number} 赛季 | W{snapshot.current_week}/{len(snapshot.weeks)}\n"
            f"历史 {len(snapshot.history)} 季 | 待办 {pending}"
        )

    def _refresh_summary(self) -> None:
        item = self.save_list.currentItem()
        if item is None:
            return
        save_name = item.data(Qt.UserRole)
        snapshot = self.service.preview_snapshot(save_name)
        if snapshot is None:
            self.summary.setPlainText(
                "\n".join(
                    [
                        f"存档：{save_name}",
                        "状态：还没有初始化赛季",
                        "可用操作：初始化赛季后开始游玩",
                    ]
                )
            )
            return
        next_phase = snapshot.weeks[snapshot.current_week].label if snapshot.current_week < len(snapshot.weeks) else "赛季已结束"
        self.summary.setPlainText(
            "\n".join(
                [
                    f"存档：{save_name}",
                    f"赛季：第 {snapshot.season_number} 赛季",
                    f"当前周次：{snapshot.current_week}/{len(snapshot.weeks)}",
                    f"当前阶段：{next_phase}",
                    f"历史赛季：{len(snapshot.history)}",
                    f"待审能力变动：{len(snapshot.pending_ability_review)}",
                    f"待审转会：{len(snapshot.pending_transfer_review)}",
                    f"待处理选秀：{'是' if snapshot.pending_draft.get('status') == 'awaiting_input' else '否'}",
                ]
            )
        )

    def _create_save(self) -> None:
        save_name = self.new_save_input.text().strip()
        if not save_name:
            QMessageBox.warning(self, "Football Simulator UI v2", "请输入新存档名。")
            return
        try:
            state = self.service.create_save(save_name)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", str(exc))
            return
        self.new_save_input.clear()
        self._refresh_save_list()
        self.save_state_callback(state.save_name, state.snapshot)
        QMessageBox.information(self, "Football Simulator UI v2", f"已创建存档 {state.save_name}。")

    def _select_save(self) -> None:
        item = self.save_list.currentItem()
        if item is None:
            return
        save_name = item.data(Qt.UserRole)
        state = self.service.load_state(save_name)
        self.save_state_callback(save_name, state.snapshot)
        QMessageBox.information(self, "Football Simulator UI v2", f"已切换到存档 {save_name}。")

    def _delete_save(self) -> None:
        item = self.save_list.currentItem()
        if item is None:
            return
        save_name = item.data(Qt.UserRole)
        if save_name == self.save_name_getter():
            QMessageBox.warning(self, "Football Simulator UI v2", "不能删除当前正在使用的存档。")
            return
        answer = QMessageBox.question(
            self,
            "Football Simulator UI v2",
            f"确定要删除存档 {save_name} 吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_save(save_name)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", str(exc))
            return
        self._refresh_save_list()
        QMessageBox.information(self, "Football Simulator UI v2", f"已删除存档 {save_name}。")
