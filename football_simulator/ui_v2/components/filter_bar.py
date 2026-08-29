"""FilterBar：搜索框 + 下拉筛选条，带 300ms 防抖搜索信号。"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Sequence

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from football_simulator.ui_v2.components import TEXT_COLOR_MUTED

SEARCH_DEBOUNCE_MS = 300

_LABEL_STYLESHEET = f"color: {TEXT_COLOR_MUTED}; background: transparent;"


class FilterBar(QWidget):
    """筛选条。

    - ``add_search`` / ``add_combo`` 构建控件；combo 以传入的 ``object_name``
      作为 ``state()`` 的键。
    - 搜索输入经 300ms ``QTimer`` 防抖后发出 ``search_changed`` 信号
      （若构造时提供了 ``on_search_changed`` 回调则同时调用）。
    - ``state()`` / ``restore()`` 用于页面状态缓存往返；``restore`` 容忍缺失键。
    """

    # 搜索文本防抖后的信号。
    search_changed = Signal(str)
    # “清除筛选”按钮触发（清空后发出，供页面连接刷新）。
    reset_changed = Signal()

    def __init__(
        self,
        on_search_changed: Optional[Callable[[str], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("filterBar")
        self._on_search_changed = on_search_changed
        self._search: Optional[QLineEdit] = None
        self._combos: Dict[str, QComboBox] = {}
        self._checks: Dict[str, QCheckBox] = {}

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._emit_search_changed)

    # -- 构建 -----------------------------------------------------------------

    def add_search(self, placeholder: str) -> QLineEdit:
        """添加搜索框（带防抖），返回创建的 ``QLineEdit``。"""

        edit = QLineEdit(self)
        edit.setObjectName("filterBarSearch")
        edit.setPlaceholderText(placeholder)
        edit.setClearButtonEnabled(True)
        edit.textChanged.connect(self._schedule_search)
        self._layout.addWidget(edit, 1)
        self._search = edit
        return edit

    def add_combo(self, label: str, options: Sequence[str], object_name: str) -> QComboBox:
        """添加带文字标签的下拉筛选，返回创建的 ``QComboBox``。"""

        caption = QLabel(label)
        caption.setObjectName(f"filterBarLabel_{object_name}")
        caption.setStyleSheet(_LABEL_STYLESHEET)
        combo = QComboBox(self)
        combo.setObjectName(object_name)
        combo.addItems(list(options))
        self._layout.addWidget(caption)
        self._layout.addWidget(combo)
        self._combos[object_name] = combo
        return combo

    def add_check(self, label: str, object_name: str) -> QCheckBox:
        """添加复选框筛选（如“只显示真实球员”），返回创建的 ``QCheckBox``。

        复选框状态纳入 ``state()`` / ``restore()`` 往返。
        """

        check = QCheckBox(label, self)
        check.setObjectName(object_name)
        check.setToolTip(label)
        self._layout.addWidget(check)
        self._checks[object_name] = check
        return check

    def add_reset(self, label: str = "清除筛选") -> QPushButton:
        """添加“清除筛选”按钮：清空搜索、回退全部下拉、取消全部复选框。"""

        button = QPushButton(label, self)
        button.setObjectName("filterBarReset")
        button.setToolTip("清空搜索与全部筛选，恢复默认列表")
        button.clicked.connect(self._reset_all)
        self._layout.addWidget(button)
        return button

    # -- 状态往返 ---------------------------------------------------------------

    def state(self) -> Dict[str, str]:
        """当前筛选状态：``{"search": ..., "<combo object_name>": ...}``。"""

        result: Dict[str, str] = {}
        if self._search is not None:
            result["search"] = self._search.text()
        for name, combo in self._combos.items():
            result[name] = combo.currentText()
        for name, check in self._checks.items():
            result[name] = "1" if check.isChecked() else "0"
        return result

    def restore(self, state: Optional[Dict[str, str]]) -> None:
        """恢复筛选状态；缺失的键保持当前值，信号在恢复期间被抑制。"""

        data = state or {}
        self._debounce_timer.stop()
        if self._search is not None and data.get("search") is not None:
            self._search.blockSignals(True)
            self._search.setText(str(data["search"]))
            self._search.blockSignals(False)
        for name, combo in self._combos.items():
            value = data.get(name)
            if value is None:
                continue
            index = combo.findText(str(value))
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        for name, check in self._checks.items():
            value = data.get(name)
            if value is None:
                continue
            check.blockSignals(True)
            check.setChecked(str(value) == "1")
            check.blockSignals(False)

    # -- 重置 ---------------------------------------------------------------

    def _reset_all(self) -> None:
        """清空全部筛选并通知页面刷新（信号与构造回调双保险，页面只接其一）。"""
        self._debounce_timer.stop()
        if self._search is not None:
            self._search.blockSignals(True)
            self._search.clear()
            self._search.blockSignals(False)
        for combo in self._combos.values():
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        for check in self._checks.values():
            check.blockSignals(True)
            check.setChecked(False)
            check.blockSignals(False)
        self.reset_changed.emit()
        self.search_changed.emit("")
        if self._on_search_changed is not None:
            self._on_search_changed("")

    # -- 搜索防抖 ---------------------------------------------------------------

    def _schedule_search(self, _text: str) -> None:
        # 每次输入重启单发计时器，停顿 300ms 后才视为一次搜索。
        self._debounce_timer.start()

    def _emit_search_changed(self) -> None:
        if self._search is None:
            return
        text = self._search.text()
        self.search_changed.emit(text)
        if self._on_search_changed is not None:
            self._on_search_changed(text)
