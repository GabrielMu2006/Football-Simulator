"""EmptyState：无数据/空结果的居中占位组件。"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from football_simulator.ui_v2.components import TEXT_COLOR_BRIGHT, TEXT_COLOR_MUTED

_TITLE_STYLESHEET = f"color: {TEXT_COLOR_BRIGHT}; background: transparent; font-size: 18px; font-weight: 800;"
_DESCRIPTION_STYLESHEET = f"color: {TEXT_COLOR_MUTED}; background: transparent; font-size: 14px;"
_HINT_STYLESHEET = f"color: {TEXT_COLOR_MUTED}; background: transparent; font-size: 12px; font-style: italic;"
_ICON_STYLESHEET = "color: rgba(125, 211, 252, 56); background: transparent; font-size: 56px; font-weight: 800;"


class EmptyState(QWidget):
    """居中排版的空状态提示，``sizePolicy`` 为 Expanding，不带任何滚动区。"""

    def __init__(
        self,
        title: str,
        description: Optional[str] = None,
        hint: Optional[str] = None,
        parent: Optional[QWidget] = None,
        action_text: str | None = None,
        action_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._action_callback = action_callback

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)
        layout.addStretch(1)

        # 装饰性占位符号：低透明度青色，不表达具体业务语义。
        self._icon_label = QLabel("◉")
        self._icon_label.setObjectName("emptyStateIcon")
        self._icon_label.setStyleSheet(_ICON_STYLESHEET)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._icon_label.setAccessibleName("空状态图标")
        layout.addWidget(self._icon_label)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("emptyStateTitle")
        self._title_label.setStyleSheet(_TITLE_STYLESHEET)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._title_label.setAccessibleName(title)
        layout.addWidget(self._title_label)

        if description:
            self._description_label = QLabel(description)
            self._description_label.setObjectName("emptyStateDescription")
            self._description_label.setStyleSheet(_DESCRIPTION_STYLESHEET)
            self._description_label.setWordWrap(True)
            self._description_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self._description_label.setAccessibleName(description)
            layout.addWidget(self._description_label)
        else:
            self._description_label = None

        if hint:
            self._hint_label = QLabel(hint)
            self._hint_label.setObjectName("emptyStateHint")
            self._hint_label.setStyleSheet(_HINT_STYLESHEET)
            self._hint_label.setWordWrap(True)
            self._hint_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self._hint_label.setAccessibleName(hint)
            layout.addWidget(self._hint_label)
        else:
            self._hint_label = None

        if action_text:
            self._action_button = QPushButton(action_text, self)
            self._action_button.setObjectName("emptyStateActionButton")
            self._action_button.clicked.connect(self._on_action_clicked)
            layout.addWidget(self._action_button, 0, Qt.AlignmentFlag.AlignHCenter)
        else:
            self._action_button = None

        layout.addStretch(1)

    def _on_action_clicked(self) -> None:
        if self._action_callback is not None:
            self._action_callback()
