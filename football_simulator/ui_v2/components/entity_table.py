"""EntityTable：页面唯一纵向滚动面的数据表（实施方案 §8.2 滚动硬规则）。

- 内部为 ``QAbstractTableModel`` + ``QTableView`` + ``QSortFilterProxyModel``，
  表头点击排序；表头固定、整行选择、无纵向表头、逐像素纵向滚动。
- ``EntityTable`` 自身就是页面的唯一纵向滚动面（``QTableView`` 自带滚动），
  外层不得再套 ``QScrollArea``；组件不自带其它小型纵向滚动区。
- 行激活（``doubleClicked`` 与键盘 ``activated``/Enter）触发导航：
  若 ``route_for_row`` 返回 ``Route`` 且设置了 ``navigator`` 则导航，并通过
  ``on_row_activated`` 信号携带该行 DTO；返回 ``None`` 则不导航。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import QHeaderView, QSizePolicy, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget

from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    ALT_ROW_COLOR,
    BG_COLOR_INPUT,
    BORDER_COLOR_SOFT,
    GRID_COLOR,
    HEADER_BG_COLOR,
    ROW_HOVER_COLOR,
    SELECTION_COLOR,
    TEXT_COLOR,
)

_Navigator = Callable[[navigation.Route], None]
_RouteForRow = Callable[[Any], Optional[navigation.Route]]

# None 值统一显示为长破折号，与现有页面的"-"占位风格一致且更醒目。
_PLACEHOLDER = "—"
_SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1

_TABLE_STYLESHEET = f"""
QTableView#entityTableView {{
    background: {BG_COLOR_INPUT};
    color: {TEXT_COLOR};
    alternate-background-color: {ALT_ROW_COLOR};
    gridline-color: {GRID_COLOR};
    selection-background-color: {SELECTION_COLOR};
    selection-color: #ffffff;
    border: 1px solid {BORDER_COLOR_SOFT};
    border-radius: 9px;
}}
QTableView#entityTableView::item {{
    padding: 6px 8px;
    border: none;
}}
QTableView#entityTableView::item:hover {{
    background: {ROW_HOVER_COLOR};
}}
QTableView#entityTableView QHeaderView::section {{
    background: {HEADER_BG_COLOR};
    color: #dfe9f7;
    border: none;
    border-right: 1px solid {GRID_COLOR};
    padding: 8px;
    font-weight: 800;
}}
"""


@dataclass(frozen=True)
class ColumnSpec:
    """列定义：``key`` 为行对象（DTO）的属性名，``width`` 为 None 时自适应内容。"""

    key: str
    title: str
    width: Optional[int] = None
    alignment: Qt.AlignmentFlag = Qt.AlignLeft
    sort_role: bool = True
    stretch: bool = False


class _EntityTableModel(QAbstractTableModel):
    """把任意 DTO 列表映射为表格模型；None 显示占位符。"""

    def __init__(self, columns: Sequence[ColumnSpec], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._columns: List[ColumnSpec] = list(columns)
        self._rows: List[Any] = []

    def set_rows(self, rows: Sequence[Any]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row_index: int) -> Optional[Any]:
        if 0 <= row_index < len(self._rows):
            return self._rows[row_index]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt API
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt API
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid():
            return None
        column = self._columns[index.column()]
        value = getattr(self._rows[index.row()], column.key, None)
        if role == int(Qt.ItemDataRole.DisplayRole):
            return _PLACEHOLDER if value is None else str(value)
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return None if value is None else str(value)
        if role == _SORT_ROLE:
            if not column.sort_role:
                return None
            if isinstance(value, bool):
                return str(value)
            if isinstance(value, (int, float)):
                return value
            return _PLACEHOLDER if value is None else str(value)
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            return column.alignment | Qt.AlignmentFlag.AlignVCenter
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:  # noqa: N802 - Qt API
        if orientation == Qt.Orientation.Horizontal and role == int(Qt.ItemDataRole.DisplayRole):
            if 0 <= section < len(self._columns):
                return self._columns[section].title
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802 - Qt API
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemNeverHasChildren
        )


class _EntitySortProxyModel(QSortFilterProxyModel):
    """按 ``_SORT_ROLE`` 原始值排序（数值列按数值比较），None 一律排在最后。"""

    def __init__(self, columns: Sequence[ColumnSpec], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._columns: List[ColumnSpec] = list(columns)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802 - Qt API
        if 0 <= left.column() < len(self._columns) and not self._columns[left.column()].sort_role:
            return False
        left_value = left.data(_SORT_ROLE)
        right_value = right.data(_SORT_ROLE)
        if left_value is None:
            return False
        if right_value is None:
            return True
        if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
            return left_value < right_value
        return str(left_value) < str(right_value)


class _EllipsizeDelegate(QStyledItemDelegate):
    """长文本省略号 + 完整内容 tooltip（UI#11 长文本溢出）。"""

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - Qt API
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideRight


class _EntityTableView(QTableView):
    """内部表格视图。

    在 Qt 6.10 上，``QAbstractItemView`` 不再在 Return/Enter 时自动发出
    ``activated``（各平台行为也不一致）；为了落实 §7.2 键盘合同（聚焦后
    Enter/Return 打开），这里显式处理：按 Return/Enter 时对当前行发出
    ``activated``，其余按键交回基类（方向键滚动等不受影响）。
    """

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.activated.emit(self.currentIndex())
            event.accept()
            return
        super().keyPressEvent(event)


class EntityTable(QWidget):
    """通用实体表：页面唯一纵向滚动面，行激活导航。"""

    # 行被激活（双击 / 键盘 Enter）且存在导航目标时发出，携带该行 DTO。
    on_row_activated = Signal(object)

    def __init__(
        self,
        columns: Sequence[ColumnSpec],
        navigator: Optional[_Navigator] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("entityTable")
        self._columns: List[ColumnSpec] = list(columns)
        self._navigator = navigator
        self._route_for_row: Optional[_RouteForRow] = None

        self._model = _EntityTableModel(self._columns, self)
        self._proxy = _EntitySortProxyModel(self._columns, self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(_SORT_ROLE)

        self._view = _EntityTableView(self)
        self._view.setObjectName("entityTableView")
        self._view.setItemDelegate(_EllipsizeDelegate(self._view))
        self._view.setModel(self._proxy)
        self._view.setSortingEnabled(True)
        # setSortingEnabled 会按表头默认指示器（第 0 列）立即排序，这里恢复为
        # 源顺序：表头无指示器，首次点击按升序排序。
        self._view.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        header = self._view.horizontalHeader()
        header.setSectionsClickable(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(True)
        for index, column in enumerate(self._columns):
            if column.stretch:
                # 宽屏适配：标记列随窗口弹性拉伸，避免右侧大面积空档。
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
            elif column.width is not None:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
                self._view.setColumnWidth(index, column.width)
            else:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)

        self._view.verticalHeader().setVisible(False)
        # 统一行高：让队徽（22–40px 图片）有足够行内空间完整显示。
        self._view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._view.verticalHeader().setDefaultSectionSize(50)
        self._view.verticalHeader().setMinimumSectionSize(44)
        self._view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._view.setWordWrap(False)
        self._view.setShowGrid(False)
        self._view.setAlternatingRowColors(True)
        self._view.setCornerButtonEnabled(False)
        self._view.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self._view.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self._view.setMinimumHeight(0)
        self._view.setStyleSheet(_TABLE_STYLESHEET)
        self._view.doubleClicked.connect(self._handle_activated_index)
        self._view.activated.connect(self._handle_activated_index)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        # 占满分配给它的空间：与外层页面布局配合，让表格成为唯一纵向滚动面。
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # -- 公开接口 -------------------------------------------------------------

    @property
    def view(self) -> QTableView:
        """内部 ``QTableView``（页面的唯一纵向滚动面）。"""

        return self._view

    @property
    def model(self) -> _EntityTableModel:
        return self._model

    @property
    def navigator(self) -> Optional[_Navigator]:
        return self._navigator

    def set_navigator(self, navigator: Optional[_Navigator]) -> None:
        self._navigator = navigator

    def set_rows(self, rows: Sequence[Any], route_for_row: Optional[_RouteForRow] = None) -> None:
        """替换全部行；``route_for_row(row) -> Optional[Route]`` 决定行激活目标。"""

        self._route_for_row = route_for_row
        self._model.set_rows(rows)
        # 保持当前排序：源模型重置后按表头指示器重排（-1 表示未排序，跳过）。
        section = self._view.horizontalHeader().sortIndicatorSection()
        if section >= 0:
            self._view.sortByColumn(section, self._view.horizontalHeader().sortIndicatorOrder())

    # -- 行激活 ---------------------------------------------------------------

    def _handle_activated_index(self, index: QModelIndex) -> None:
        if index is None or not index.isValid():
            return
        source_index = self._proxy.mapToSource(index)
        row = self._model.row_at(source_index.row())
        if row is None:
            return
        route: Optional[navigation.Route] = None
        if self._route_for_row is not None:
            route = self._route_for_row(row)
        if route is None:
            return
        if self._navigator is not None:
            self._navigator(route)
        self.on_row_activated.emit(row)
