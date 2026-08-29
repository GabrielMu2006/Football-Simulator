"""PageHeader：页面标题 + 面包屑 + 右侧动作区。"""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import TEXT_COLOR_BRIGHT, TEXT_COLOR_MUTED
from football_simulator.ui_v2.components.entity_link import EntityLink

_SEPARATOR = "›"

_CURRENT_CRUMB_STYLESHEET = f"color: {TEXT_COLOR_BRIGHT}; background: transparent; font-weight: 600;"
_SEPARATOR_STYLESHEET = f"color: {TEXT_COLOR_MUTED}; background: transparent;"


class PageHeader(QWidget):
    """页面头部：左侧标题与面包屑，右侧 actions 横排区域。

    面包屑来自 ``navigation.breadcrumbs``：带 ``route`` 的项渲染为 ``EntityLink``
    （青色、可点击、键盘可达），``route=None`` 的当前页渲染为不可点的高亮文本。
    """

    def __init__(
        self,
        title: str,
        breadcrumbs: Optional[Sequence[navigation.Breadcrumb]] = None,
        navigator: Optional[object] = None,
        actions: Optional[Sequence[QWidget]] = None,
        avatar: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        self._navigator = navigator

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        if avatar is not None:
            root.addWidget(avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)
        self._title_label = QLabel(title)
        # 复用全局样式 QLabel#titleLabel（22px / 900 / 亮色），与现有页面视觉语言一致。
        self._title_label.setObjectName("titleLabel")
        left.addWidget(self._title_label)

        self._crumb_row = QWidget(self)
        self._crumb_layout = QHBoxLayout(self._crumb_row)
        self._crumb_layout.setContentsMargins(0, 0, 0, 0)
        self._crumb_layout.setSpacing(6)
        left.addWidget(self._crumb_row)
        root.addLayout(left, 1)

        self._actions_layout = QHBoxLayout()
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(8)
        root.addLayout(self._actions_layout)

        self.set_breadcrumbs(breadcrumbs or [])
        for widget in actions or ():
            self.add_action(widget)

    # -- 面包屑 ---------------------------------------------------------------

    def set_breadcrumbs(self, breadcrumbs: Sequence[navigation.Breadcrumb]) -> None:
        """重建面包屑行；空列表时隐藏面包屑行。"""

        while self._crumb_layout.count():
            item = self._crumb_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for position, crumb in enumerate(breadcrumbs):
            if position:
                separator = QLabel(_SEPARATOR)
                separator.setStyleSheet(_SEPARATOR_STYLESHEET)
                separator.setAccessibleName(f"面包屑分隔符 {_SEPARATOR}")
                self._crumb_layout.addWidget(separator)
            if crumb.route is not None and self._navigator is not None:
                self._crumb_layout.addWidget(
                    EntityLink(crumb.label, crumb.route, self._navigator)  # type: ignore[arg-type]
                )
            else:
                # 当前页（或未提供 navigator）不可点击。
                current = QLabel(crumb.label)
                current.setObjectName("pageHeaderCrumbCurrent")
                current.setStyleSheet(_CURRENT_CRUMB_STYLESHEET)
                current.setAccessibleName(crumb.label)
                self._crumb_layout.addWidget(current)
        self._crumb_layout.addStretch(1)
        self._crumb_row.setVisible(bool(breadcrumbs))

    # -- 右侧动作区 -------------------------------------------------------------

    def add_action(self, widget: QWidget) -> None:
        """把动作控件加入右侧横排区域。"""

        self._actions_layout.addWidget(widget)
