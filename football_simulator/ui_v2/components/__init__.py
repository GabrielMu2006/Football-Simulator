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

# 颜色 tokens 已收敛到 football_simulator.ui_v2.design_tokens（本包保持原导出名）。
from football_simulator.ui_v2.design_tokens import (
    ACCENT_COLOR,
    ACCENT_COLOR_HOVER,
    ALT_ROW_COLOR,
    BG_COLOR,
    BG_COLOR_CARD,
    BG_COLOR_INPUT,
    BORDER_COLOR,
    BORDER_COLOR_SOFT,
    DANGER_BG,
    DANGER_COLOR,
    GRID_COLOR,
    HEADER_BG_COLOR,
    LINK_COLOR,
    LINK_COLOR_FOCUS,
    LINK_COLOR_HOVER,
    NEUTRAL_BG,
    NEUTRAL_COLOR,
    ROW_HOVER_COLOR,
    SELECTION_COLOR,
    SUCCESS_BG,
    SUCCESS_COLOR,
    TEXT_COLOR,
    TEXT_COLOR_BRIGHT,
    TEXT_COLOR_MUTED,
    TEXT_COLOR_SOFT,
    WARNING_BG,
    WARNING_COLOR,
)

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
