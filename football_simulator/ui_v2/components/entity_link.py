"""EntityLink：统一实体链接（实施方案 §7.2 全局链接合同）。

视觉与交互合同：

- 青色链接色、hover 变色加下划线、指针变化（PointingHandCursor）；
- 键盘可达：``FocusPolicy=StrongFocus``，获得焦点后显示可访问焦点框，
  单击即导航，聚焦后 Enter/Return 导航，不依赖双击。
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import QLabel, QWidget

from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import LINK_COLOR, LINK_COLOR_FOCUS, LINK_COLOR_HOVER

_Navigator = Callable[[navigation.Route], None]

# :focus 用 1px 描边做可访问焦点框；常态用同宽透明边框，避免获得/失去焦点时布局抖动。
# text-decoration 供支持的样式后备；下划线同时由 hover/focus 事件切换 QFont 保证生效。
_LINK_STYLESHEET = f"""
QLabel#entityLink {{
    color: {LINK_COLOR};
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 1px 2px;
}}
QLabel#entityLink:hover {{
    color: {LINK_COLOR_HOVER};
    text-decoration: underline;
}}
QLabel#entityLink:focus {{
    color: {LINK_COLOR_HOVER};
    border: 1px solid {LINK_COLOR_FOCUS};
    text-decoration: underline;
}}
"""


class EntityLink(QLabel):
    """单个实体链接：单击导航、聚焦后 Enter/Return 导航。"""

    def __init__(
        self,
        text: str,
        route: Optional[navigation.Route],
        navigator: Optional[_Navigator],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self._route = route
        self._navigator = navigator
        self.setObjectName("entityLink")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(_LINK_STYLESHEET)
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.setAccessibleDescription("链接，按 Enter 打开")

    # -- 数据 -----------------------------------------------------------------

    @property
    def route(self) -> Optional[navigation.Route]:
        return self._route

    def set_route(self, route: Optional[navigation.Route]) -> None:
        """更新链接目标路由。"""

        if route is not None and not isinstance(route, navigation.Route):
            raise TypeError(f"set_route 需要 Route 实例，得到 {type(route).__name__}")
        self._route = route

    @property
    def navigator(self) -> Optional[_Navigator]:
        return self._navigator

    def set_navigator(self, navigator: Optional[_Navigator]) -> None:
        self._navigator = navigator

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        super().setText(text)
        self.setToolTip(text)
        self.setAccessibleName(text)

    # -- 激活 -----------------------------------------------------------------

    def _activate(self) -> None:
        if self._route is not None and self._navigator is not None:
            self._navigator(self._route)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self._activate()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- hover / 焦点视觉反馈 ---------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._set_underline(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.unsetCursor()
        self._set_underline(False)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._set_underline(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._set_underline(False)
        super().focusOutEvent(event)

    def _set_underline(self, underlined: bool) -> None:
        font: QFont = self.font()
        if font.underline() != underlined:
            font.setUnderline(underlined)
            self.setFont(font)
