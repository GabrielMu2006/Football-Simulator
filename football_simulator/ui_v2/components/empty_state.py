"""EmptyState：无数据/空结果的居中占位组件。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from football_simulator.ui_v2.components import TEXT_COLOR_BRIGHT, TEXT_COLOR_MUTED

_TITLE_STYLESHEET = f"color: {TEXT_COLOR_BRIGHT}; background: transparent; font-size: 18px; font-weight: 800;"
_DESCRIPTION_STYLESHEET = f"color: {TEXT_COLOR_MUTED}; background: transparent; font-size: 14px;"
_HINT_STYLESHEET = f"color: {TEXT_COLOR_MUTED}; background: transparent; font-size: 12px; font-style: italic;"


class EmptyState(QWidget):
    """居中排版的空状态提示，``sizePolicy`` 为 Expanding，不带任何滚动区。"""

    def __init__(
        self,
        title: str,
        description: Optional[str] = None,
        hint: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)
        layout.addStretch(1)

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

        layout.addStretch(1)
