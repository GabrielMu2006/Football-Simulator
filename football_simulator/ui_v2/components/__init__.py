"""UI v2 通用实体组件包（阶段 3）。

提供导航外壳使用的通用组件：EntityLink / EntityTable / PageHeader /
FilterBar / EmptyState。组件层只允许依赖 PySide6、``football_simulator.ui_v2.theme``
与 ``football_simulator.ui_v2.navigation``（禁止导入 state / persistence / queries）。

设计 token 来源说明
-------------------

``theme.py`` 目前只有 ``APP_STYLE`` 样式表字符串，没有独立导出的颜色常量；
按阶段 3 约定，本包把要用的颜色集中定义为模块级常量，取值逐条来自
``theme.py`` 的 ``APP_STYLE``（链接青色取自 ``widgets.py`` 中已有的青色），
来源见各常量行尾注释。组件不修改 ``theme.py`` / ``widgets.py``。
"""

from __future__ import annotations

# —— 链接配色（§7.2：青色链接色、hover/焦点态、可访问焦点框） ————————————
LINK_COLOR = "#7dd3fc"  # 青色链接色；取自 widgets.py 既有青色（TrendSparkline 默认线色 / POSITION_COLORS["GK"]）
LINK_COLOR_HOVER = "#bae6fd"  # hover 提亮（LINK_COLOR 的亮一阶，配合下划线）
LINK_COLOR_FOCUS = "#38bdf8"  # 键盘焦点框描边（青色系，与 LINK_COLOR 同族）

# —— 文本配色 ————————————————————————————————————————————
TEXT_COLOR = "#e8eef7"  # theme.py APP_STYLE `QWidget { color: ... }`
TEXT_COLOR_BRIGHT = "#f8fbff"  # theme.py APP_STYLE `QLabel#titleLabel`
TEXT_COLOR_MUTED = "#91a8c5"  # theme.py APP_STYLE `QLabel#subtitleLabel`
TEXT_COLOR_SOFT = "#cbd7e6"  # theme.py APP_STYLE `QListWidget#navList::item`

# —— 背景与边框 ————————————————————————————————————————————
BG_COLOR = "#0b1220"  # theme.py APP_STYLE `QWidget { background: ... }`
BG_COLOR_CARD = "#111c2e"  # theme.py APP_STYLE `QFrame#cardFrame`
BG_COLOR_INPUT = "#0f1b2d"  # theme.py APP_STYLE `QComboBox/QLineEdit/QTableWidget`
BORDER_COLOR = "#263b5b"  # theme.py APP_STYLE `QFrame#cardFrame` 边框
BORDER_COLOR_SOFT = "#223653"  # theme.py APP_STYLE `QComboBox` 边框

# —— 强调色 ——————————————————————————————————————————————
ACCENT_COLOR = "#1167d8"  # theme.py APP_STYLE `QPushButton` 背景
ACCENT_COLOR_HOVER = "#2784ff"  # theme.py APP_STYLE `QPushButton:hover`

# —— 表格配色 ————————————————————————————————————————————
HEADER_BG_COLOR = "#172942"  # theme.py APP_STYLE `QHeaderView::section` 背景
ALT_ROW_COLOR = "#13243a"  # theme.py APP_STYLE `QTableWidget alternate-background-color`
GRID_COLOR = "#23344d"  # theme.py APP_STYLE `QTableWidget gridline-color`
SELECTION_COLOR = "#155bb6"  # theme.py APP_STYLE `QTableWidget selection-background-color`
ROW_HOVER_COLOR = "#1d3555"  # theme.py APP_STYLE `QTableWidget::item:hover`

from football_simulator.ui_v2.components.empty_state import EmptyState
from football_simulator.ui_v2.components.entity_link import EntityLink
from football_simulator.ui_v2.components.entity_table import ColumnSpec, EntityTable
from football_simulator.ui_v2.components.filter_bar import FilterBar
from football_simulator.ui_v2.components.page_header import PageHeader

__all__ = [
    "ColumnSpec",
    "EmptyState",
    "EntityLink",
    "EntityTable",
    "FilterBar",
    "PageHeader",
]
