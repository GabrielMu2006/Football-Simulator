"""全局搜索框（阶段 4 应用外壳组件，Agent D2）。

对应实施方案 §8.1：顶部上下文栏提供全局搜索，至少可按球员名/标签、球队名
定位实体，搜索结果直接进入实体页。

- 数据来自查询层（``queries.list_players`` / ``queries.list_teams``，经
  ``open_read_connection`` 只读连接）；所有查询包在 try/except 中，存档未
  初始化或查询失败一律按空结果处理。
- 输入 ≥1 字符时经 250ms 防抖后检索：球员前 6 条 + 球队前 4 条。
- 结果行显示"类型标记 + 名称 + 次要信息"（球员：位置/球队；球队：分区/积分），
  单击结果或输入框内按 Enter 直接 ``navigate`` 到对应实体路由；Esc 关闭下拉；
  清空输入即清空结果并收起下拉。
- 弹出列表是``Qt.Popup``临时下拉，不属于主内容，不计入 §8.2 的滚动硬规则。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from football_simulator.queries import list_players, list_teams, open_read_connection
from football_simulator.ui_v2.navigation import Route

_MUTED_COLOR = "#91a8c5"  # 与 theme.py subtitleLabel 同色

_SearchRow = Tuple[str, str, Route]


class GlobalSearchBox(QWidget):
    """QLineEdit + 弹出结果列表的全局搜索框。

    构造参数（由外壳注入）：

    - ``save_name_provider``: ``() -> str``，当前存档名；
    - ``season_provider``: ``() -> int``，当前赛季号；
    - ``navigate``: ``(Route) -> None``，选中结果后的导航回调（外壳的 Router）。
    """

    DEBOUNCE_MS = 250
    PLAYER_RESULT_LIMIT = 6
    TEAM_RESULT_LIMIT = 4
    MAX_POPUP_HEIGHT = 320
    MIN_POPUP_WIDTH = 300

    def __init__(
        self,
        save_name_provider: Callable[[], str],
        season_provider: Callable[[], int],
        navigate: Callable[[Route], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._save_name_provider = save_name_provider
        self._season_provider = season_provider
        self._navigate = navigate
        self._suppress_text_events = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText("搜索球员 / 球队…")
        self.line_edit.setClearButtonEnabled(True)
        self.line_edit.setToolTip("输入球员名或球队名，回车或点击结果直接打开实体页")
        self.line_edit.setAccessibleName("全局搜索")
        layout.addWidget(self.line_edit)

        # 临时下拉：Qt.Popup 自动在外部点击时关闭；NoFocus 保证输入框持续持有
        # 键盘焦点（方向键/回车由 eventFilter 转发给下拉）。
        self.popup = QListWidget()
        self.popup.setWindowFlags(Qt.WindowType.Popup)
        self.popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.popup.setUniformItemSizes(True)
        self.popup.itemClicked.connect(self._activate_item)
        self.popup.hide()

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(self.DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._run_search)

        self.line_edit.textChanged.connect(self._on_text_changed)
        self.line_edit.installEventFilter(self)

    # -- 输入事件 -------------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        if self._suppress_text_events:
            return
        if not text.strip():
            self._debounce_timer.stop()
            self.clear_results()
            return
        self._debounce_timer.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if obj is self.line_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                # 首次 Esc 关闭结果弹层；再次按 Esc 清空搜索文本。
                if self.popup.isVisible():
                    self.popup.hide()
                else:
                    self._suppress_text_events = True
                    self.line_edit.clear()
                    self._suppress_text_events = False
                    self.clear_results()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self.popup.isVisible() and self.popup.count():
                    step = 1 if key == Qt.Key.Key_Down else -1
                    row = min(self.popup.count() - 1, max(0, self.popup.currentRow() + step))
                    self.popup.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.popup.isVisible():
                item = self.popup.currentItem() or (self.popup.item(0) if self.popup.count() else None)
                if item is not None:
                    self._activate_item(item)
                    return True
        return super().eventFilter(obj, event)

    # -- 检索 -----------------------------------------------------------------

    def _run_search(self) -> None:
        text = self.line_edit.text().strip()
        if not text:
            self.clear_results()
            return
        rows = self._collect_results(text)
        self.popup.clear()
        if rows:
            for label, tooltip, route in rows:
                item = QListWidgetItem(label)
                item.setToolTip(tooltip)
                item.setData(Qt.ItemDataRole.UserRole, route)
                self.popup.addItem(item)
            self.popup.setCurrentRow(0)
        else:
            empty_item = QListWidgetItem("无匹配结果")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            empty_item.setForeground(QBrush(QColor(_MUTED_COLOR)))
            self.popup.addItem(empty_item)
        self._show_popup()

    def _collect_results(self, text: str) -> List[_SearchRow]:
        """检索当前存档；任何失败（未初始化存档等）都按空结果处理。"""
        save_name = ""
        season = 0
        try:
            save_name = str(self._save_name_provider() or "").strip()
            season = int(self._season_provider())
        except Exception:
            return []
        if not save_name:
            return []
        results: List[_SearchRow] = []
        try:
            with open_read_connection(save_name) as conn:
                for row in list_players(conn, season, search=text)[: self.PLAYER_RESULT_LIMIT]:
                    label = f"【球员】{row.display_name}　{row.position} · {row.team.display_name}"
                    results.append((label, label, Route("player", player=row.player_id, season=season)))
                for row in list_teams(conn, season, search=text)[: self.TEAM_RESULT_LIMIT]:
                    label = f"【球队】{row.team.display_name}　{row.season_division} · {row.points} 分"
                    results.append((label, label, Route("team", team=row.team.team_id, season=season)))
        except Exception:
            return []
        return results

    # -- 结果选择 -------------------------------------------------------------

    def _activate_item(self, item: QListWidgetItem) -> None:
        route = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(route, Route):
            return
        self.popup.hide()
        self.clear_input()
        if self._navigate is not None:
            self._navigate(route)

    # -- 弹出列表几何 ---------------------------------------------------------

    def _show_popup(self) -> None:
        popup = self.popup
        width = max(self.width(), self.MIN_POPUP_WIDTH)
        height = min(max(popup.sizeHint().height(), 40), self.MAX_POPUP_HEIGHT)
        popup.resize(width, height)
        popup.move(self.mapToGlobal(QPoint(0, self.height())))
        popup.show()

    # -- 公共清理 -------------------------------------------------------------

    def clear_input(self) -> None:
        """清空输入框并收起下拉（程序化清空不触发检索）。"""
        self._suppress_text_events = True
        try:
            self.line_edit.clear()
        finally:
            self._suppress_text_events = False
        self.clear_results()

    def clear_results(self) -> None:
        """清空结果列表并收起下拉。"""
        self._debounce_timer.stop()
        self.popup.clear()
        self.popup.hide()
