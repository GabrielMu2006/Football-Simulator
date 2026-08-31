"""转会中心（阶段 5 重写，实施方案 §8.8 转会部分 + §7.2 全局链接合同）。

Route：``Route("transfers", season=<n>)``（season 必填）。

- 页头：转会中心 + 赛季选择器（``navigate`` 新路由，形成可后退历史）；
- 待审核区（写流程核心）：``pending_transfer_review`` 非空且属于路由赛季时，
  每笔转会一张明细卡（编号 trade_id、甲队 ⇄ 乙队、双方球员列表（含位置/
  能力/身价）、总身价与差值）+ 行内“批准/拒绝”互斥按钮（默认批准）+
  底部“提交审核结果”→ ``context.service.apply_transfer_review(save_name,
  {trade_id: bool})``；提交成功后刷新（pending 清空、历史增长）并在状态条
  显示“已提交 N 笔（批准 M · 拒绝 K · 系统重算 J）”；失败弹 ``QMessageBox``。
  ``context.service`` 为 None 时卡片只读展示（数据回退 ``pending_actions``
  载荷，与快照同源），决策按钮与提交禁用；
- 转会历史区：所选赛季全部 ``transfer_history`` 行的全高 EntityTable（周/
  窗口/甲队/球员汇总/乙队/状态徽标/差值）。甲队、乙队与球员（逐名命中）可
  点：球员 → ``Route("player", player=real_player_id(显示名), season=<路由
  赛季>)``（转会 JSON 里可能只有显示名，统一用 ``data.real_player_id`` 转
  换）、球队 → ``Route("team", team=<稳定ID>, season=<路由赛季>)``；
  行激活不导航（审核操作只通过链接列与行内按钮完成，避免误触跳转）；
  状态列渲染徽标（玩家通过/系统重算通过/玩家拒绝/系统拒绝）。

滚动面归属（§8.2）：内容型页面 —— 单个外层 ``QScrollArea`` 是唯一纵向滚动
面；历史 EntityTable 按“表头 + 全部行”固定高度完整展开（纵向滚动条策略
AlwaysOff），不构成第二个纵向滚动面，禁止小框内滚动。

delegate 生命周期约定（重要，经崩溃报告与压力矩阵实证）：``setItemDelegate
ForColumn`` 不取得所有权，且 shiboken 会在最后一个 Python 引用消失时删除
C++ 对象（即使挂了 Qt parent）——旧视图的列映射随即悬空，之后任意 GC/绘制
SIGSEGV。因此本页所有 delegate 统一：``parent=view``（与视图同生命周期）+
页面持有 Python 引用（``self._table_delegates`` 只增不清，绝不提前清空）；
历史 EntityTable 与其 delegate 在 ``_build_ui`` 一次构建、跨刷新复用。

兼容说明：外壳在阶段 5 集成前仍以旧签名构造本页面并调用 ``set_snapshot``；
新契约页面不消费快照，构造器容忍旧位置参数、``set_snapshot`` 为显式空操作。

数据口径：转会与选秀数据天然只含真实球员（引擎语义）：转会移动真实球员、选秀录入真实球员，默认球员不参与，因此无需“只显示真实球员”过滤。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from football_simulator.data import real_player_id
from football_simulator.queries import base
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    LINK_COLOR,
    TEXT_COLOR_MUTED,
    ColumnSpec,
    EmptyState,
    EntityLink,
    EntityTable,
    FilterBar,
    PageHeader,
)
from football_simulator.ui_v2.components.team_crest import draw_team_crest
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase
from football_simulator.ui_v2.widgets import section_header

_ROW_HEIGHT = 32
_HEADER_HEIGHT = 36
_TABLE_BORDER = 2  # EntityTable 样式表上下各 1px 边框

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"
_BRIGHT_STYLE = "color: #f8fbff; background: transparent; font-weight: 700;"
_ACCENT_STYLE = "color: #7dd3fc; background: transparent; font-weight: 800;"

# 状态徽标配色（底色加深以承载亮色文字，沿用现有页面语义配色）。
_STATUS_COLORS = {
    "玩家通过": ("#16351f", "#4ade80"),
    "系统重算通过": ("#3d3413", "#f9c74f"),
    "玩家拒绝": ("#3f1d1d", "#f87171"),
    "系统拒绝": ("#31394a", "#a8b6cc"),
}
_STATUS_FALLBACK_COLOR = ("#31394a", "#a8b6cc")

_TRANSFER_COLUMNS = (
    ColumnSpec("week", "周", width=70, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("window", "窗口", width=80),
    ColumnSpec("team_a", "甲队", width=180),
    ColumnSpec("players", "球员", width=360),
    ColumnSpec("team_b", "乙队", width=180),
    ColumnSpec("status", "状态", width=140),
    ColumnSpec("gap", "差值", width=90, alignment=Qt.AlignmentFlag.AlignRight),
)


def _column_index(columns: Sequence[ColumnSpec], key: str) -> int:
    for index, column in enumerate(columns):
        if column.key == key:
            return index
    return 0


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


@dataclass(frozen=True)
class _HistoryRow:
    """转会历史行视图模型（一次转会一行；球员列为两侧球员汇总）。"""

    week: int
    window: str
    team_a: str
    team_b: str
    team_a_players: Tuple[str, ...]
    team_b_players: Tuple[str, ...]
    players: str
    status: str
    gap: float


@dataclass
class _TransfersData:
    """转会页一次刷新所需的全部只读数据。"""

    season_number: int
    seasons: Tuple[base.SeasonRef, ...]
    snapshot_season: Optional[int]
    pending: List[dict]
    history: List[dict]
    team_ids: Dict[str, int]


# -- 列级 delegate（与球队详情页 / 比赛详情页同一模式，页面内自持） ----------


class _LinkColumnDelegate(QStyledItemDelegate):
    """把一列文本渲染为实体链接（§7.2）：青色、hover 下划线、单击导航。"""

    def __init__(
        self,
        table: EntityTable,
        resolver,
        alignment: Qt.AlignmentFlag,
        parent: Optional[QWidget] = None,
        crest: bool = False,
    ) -> None:
        super().__init__(parent)
        self._table = table
        self._resolver = resolver
        self._alignment = Qt.AlignmentFlag(alignment)
        self._crest = crest

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = opt.font
        font.setUnderline(bool(opt.state & QStyle.State.State_MouseOver))
        painter.setFont(font)
        painter.setPen(QColor(LINK_COLOR))
        rect = opt.rect.adjusted(8, 0, -8, 0)
        if self._crest:
            crest_size = min(rect.height(), 32)
            crest_rect = QRect(
                rect.left(),
                rect.top() + (rect.height() - crest_size) // 2,
                crest_size,
                crest_size,
            )
            draw_team_crest(painter, crest_rect, str(text), size=crest_size)
            rect = QRect(
                rect.left() + crest_size + 6,
                rect.top(),
                max(0, rect.width() - crest_size - 6),
                rect.height(),
            )
        painter.drawText(rect, int(self._alignment | Qt.AlignmentFlag.AlignVCenter), str(text))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802 - Qt API
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
        ):
            self._activate(index)
        return super().editorEvent(event, model, option, index)

    def _activate(self, index) -> None:
        proxy = self._table.view.model()
        row = self._table.model.row_at(proxy.mapToSource(index).row())
        if row is None:
            return
        route = self._resolver(row)
        if route is not None and self._table.navigator is not None:
            self._table.navigator(route)


class _PlayerSummaryDelegate(QStyledItemDelegate):
    """转会历史“球员”列：每个球员名渲染为链接，单击命中具体球员路由。

    显示格式与行 DTO 的 ``players`` 汇总文本一致：甲队送出球员以“、”相连，
    与乙队送出球员以“ ⇄ ”相连。命中判定按各 token 的横向区间计算，与
    ``paint`` 的排版使用同一字体度量，保证单击位置与视觉一致。
    """

    def __init__(
        self,
        table: EntityTable,
        segments_resolver,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._table = table
        self._segments_resolver = segments_resolver

    @staticmethod
    def _content_left(option: QStyleOptionViewItem) -> int:
        return option.rect.left() + 8

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        segments = self._segments_for(index)
        if not segments:
            return
        painter.save()
        # 逐名绘制无矩形约束，显式裁剪到单元格，避免溢出到相邻列。
        painter.setClipRect(opt.rect)
        font = opt.font
        font.setUnderline(bool(opt.state & QStyle.State.State_MouseOver))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        baseline = opt.rect.center().y() + (metrics.ascent() - metrics.descent()) // 2
        x = self._content_left(opt)
        for text, route in segments:
            painter.setPen(QColor(LINK_COLOR) if route is not None else QColor(TEXT_COLOR_MUTED))
            painter.drawText(int(x), int(baseline), text)
            x += metrics.horizontalAdvance(text)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802 - Qt API
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
        ):
            self._activate(index, option, event.position().toPoint().x())
        return super().editorEvent(event, model, option, index)

    def _segments_for(self, index) -> List[Tuple[str, Optional[Route]]]:
        proxy = self._table.view.model()
        row = self._table.model.row_at(proxy.mapToSource(index).row())
        if row is None:
            return []
        return list(self._segments_resolver(row))

    def _activate(self, index, option, click_x: int) -> None:
        segments = self._segments_for(index)
        if not segments:
            return
        metrics = QFontMetrics(option.font)
        left = self._content_left(option)
        x = left
        for text, route in segments:
            width = metrics.horizontalAdvance(text)
            # 与 paint 的裁剪一致：只命中单元格内可见的部分。
            if route is not None and x <= click_x <= min(x + width, option.rect.right()):
                if self._table.navigator is not None:
                    self._table.navigator(route)
                return
            x += width


class _StatusBadgeDelegate(QStyledItemDelegate):
    """转会历史“状态”列：渲染为带底色的圆角徽标（玩家/系统四种结果）。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802 - Qt API
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            return
        background, foreground = _STATUS_COLORS.get(text, _STATUS_FALLBACK_COLOR)
        painter.save()
        font = opt.font
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = min(opt.rect.width() - 8, metrics.horizontalAdvance(text) + 16)
        chip = QRect(
            opt.rect.left() + 4,
            opt.rect.center().y() - (metrics.height() + 6) // 2,
            max(width, 0),
            metrics.height() + 6,
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(chip, 6, 6)
        painter.setPen(QColor(foreground))
        painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()


# -- 页面 -------------------------------------------------------------------


class TransfersPage(EntityPageBase):
    """转会中心：转会审核（写流程）+ 所选赛季转会历史。"""

    def __init__(self, context, parent: Optional[QWidget] = None, *_legacy_args: object) -> None:
        # 外壳在阶段 5 集成前仍用旧签名构造本页（多余位置参数在此被丢弃）。
        if not isinstance(parent, QWidget):
            parent = None
        self._season: int = 0
        self._pending_items: List[dict] = []
        self._decisions: Dict[str, Dict[str, QPushButton]] = {}
        self._submit_button: Optional[QPushButton] = None
        self._approve_all_button: Optional[QPushButton] = None
        self._reject_all_button: Optional[QPushButton] = None
        self._review_message: str = ""
        self._review_status_label: Optional[QLabel] = None
        self._status_label: Optional[QLabel] = None
        self._season_combo: Optional[QComboBox] = None
        self._team_ids: Dict[str, int] = {}
        self._history_hint: Optional[QLabel] = None
        self._history_all_rows: Optional[List[_HistoryRow]] = None
        # delegate 生命周期：页面引用列表只增不清（见模块级生命周期说明）。
        self._table_delegates: list = []
        super().__init__(context, parent)

    # -- UI 骨架（一次构建；可变区块在 refresh 中重建） ------------------------

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(16, 14, 16, 14)
        page_layout.setSpacing(0)

        self._stack = QStackedWidget()
        page_layout.addWidget(self._stack, 1)

        # 唯一外层纵向滚动面（内容型页面，区块完整展开）。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("transfersScroll")
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        scroll.setWidget(self._content)
        self._scroll = scroll
        self._stack.addWidget(scroll)

        # 页头 / 状态行 / 待审核区每次刷新重建；历史区一次构建跨刷新复用
        # （EntityTable 与 delegate 不随刷新销毁，见生命周期说明）。
        self._header_slot = QWidget(self._content)
        header_layout = QVBoxLayout(self._header_slot)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        self._status_slot = QWidget(self._content)
        status_layout = QVBoxLayout(self._status_slot)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        self._pending_slot = QWidget(self._content)
        pending_layout = QVBoxLayout(self._pending_slot)
        pending_layout.setContentsMargins(0, 0, 0, 0)
        pending_layout.setSpacing(0)
        content_layout.addWidget(self._header_slot)
        content_layout.addWidget(self._status_slot)
        content_layout.addWidget(self._pending_slot)
        content_layout.addWidget(self._build_history_section())
        content_layout.addStretch(1)

        self._empty = EmptyState(
            "还没有可用的存档数据",
            "当前存档还没有赛季数据。",
            "请先在顶部选择存档，然后点击“初始化赛季”创建第 1 赛季。",
        )
        self._stack.addWidget(self._empty)

    def _build_history_section(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(
            section_header(
                "转会历史",
                "所选赛季全部转会审核结果；单击球队/球员名打开详情（球员列逐名可点），"
                "状态列为审核结果徽标（玩家通过/系统重算通过/玩家拒绝/系统拒绝）。",
            )
        )

        # 历史筛选（UI#8）：赛季内支持球队/球员搜索与状态筛选。
        self._history_filter = FilterBar(on_search_changed=self._on_history_filters_changed)
        self._history_status_combo = self._history_filter.add_combo(
            "状态",
            ["全部状态", "玩家通过", "系统重算通过", "玩家拒绝", "系统拒绝"],
            "transfersHistoryStatusCombo",
        )
        self._history_status_combo.currentIndexChanged.connect(self._on_history_filters_changed)
        self._history_search = self._history_filter.add_search("搜索球队 / 球员…")
        self._history_filter.add_reset()
        layout.addWidget(self._history_filter)

        # 历史表一次构建：refresh 中替换行并按“表头 + 全部行”固定高度完整展开
        # （纵向滚动条 AlwaysOff，不构成第二个纵向滚动面，§8.2）。
        self._history_table = EntityTable(
            _TRANSFER_COLUMNS, navigator=self._context.navigate, parent=self
        )
        view = self._history_table.view
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        view.horizontalHeader().setFixedHeight(_HEADER_HEIGHT)

        def team_route(key: str):
            def resolver(row: _HistoryRow) -> Optional[Route]:
                team_id = self._team_ids.get(getattr(row, key))
                if team_id is None:
                    return None
                return Route("team", team=team_id, season=self._season)

            return resolver

        for key in ("team_a", "team_b"):
            index = _column_index(_TRANSFER_COLUMNS, key)
            delegate = _LinkColumnDelegate(
                self._history_table,
                team_route(key),
                _TRANSFER_COLUMNS[index].alignment,
                parent=view,
                crest=True,
            )
            view.setItemDelegateForColumn(index, delegate)
            self._table_delegates.append(delegate)

        players_index = _column_index(_TRANSFER_COLUMNS, "players")
        self._players_delegate = _PlayerSummaryDelegate(
            self._history_table, self._player_segments, parent=view
        )
        view.setItemDelegateForColumn(players_index, self._players_delegate)
        self._table_delegates.append(self._players_delegate)

        status_index = _column_index(_TRANSFER_COLUMNS, "status")
        self._status_delegate = _StatusBadgeDelegate(parent=view)
        view.setItemDelegateForColumn(status_index, self._status_delegate)
        self._table_delegates.append(self._status_delegate)

        layout.addWidget(self._history_table, 1)
        self._history_hint = QLabel("该赛季暂无转会记录。")
        self._history_hint.setObjectName("transferHistoryEmptyHint")
        self._history_hint.setStyleSheet(_MUTED_STYLE)
        layout.addWidget(self._history_hint)
        return frame

    def set_snapshot(self, snapshot: object) -> None:
        """外壳 ``_refresh_views`` 的遗留兼容入口（阶段 5 集成后移除）。

        新契约页面不消费快照：数据全部由 ``apply_route``/``refresh`` 按路由
        只读查询（或写服务快照）得到，这里刻意不做任何事，避免与路由刷新双写。
        """
        del snapshot

    # -- 数据刷新 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "transfers":
            return
        season = route.int_param("season")
        try:
            data = self._load_data(self.save_name(), season)
        except base.MissingSaveError as exc:
            self._show_empty("还没有可用的存档数据", str(exc), "请先在顶部选择存档，然后点击“初始化赛季”。")
            return
        except Exception as exc:  # 查询层异常统一进空状态
            self._show_empty("暂时无法加载转会数据", str(exc), None)
            return

        self._season = data.season_number
        self._team_ids = dict(data.team_ids)
        self._render(data)
        self._stack.setCurrentWidget(self._scroll)

    def route_context(self) -> dict:
        if self._season:
            return {"season": self._season}
        return {}

    # -- 只读取数 -------------------------------------------------------------

    def _load_data(self, save_name: str, season: Optional[int]) -> _TransfersData:
        service = self._context.service
        with base.open_read_connection(save_name) as conn:
            seasons = tuple(base.load_seasons(conn))
            if not seasons:
                raise base.MissingSaveError("存档还没有任何赛季数据。")
            if season is None:  # 路由 schema 保证必填；防御性回退当前赛季
                season = base.resolve_current_season(conn).season_number
            season = int(season)
            if all(ref.season_number != season for ref in seasons):
                raise KeyError(f"存档中不存在第 {season} 赛季。")

            team_ids = {
                str(row[0]): int(row[1]) for row in conn.execute("SELECT name, team_id FROM teams")
            }
            snapshot_season = self._meta_int(conn, "season_number")

            pending: List[dict] = []
            history: List[dict] = []
            service_ok = False
            if service is not None:
                try:
                    state = service.load_state(save_name)
                    snapshot = state.snapshot if state is not None else None
                    if snapshot is not None:
                        snapshot_season = int(snapshot.season_number)
                        pending = [dict(item) for item in snapshot.pending_transfer_review or []]
                        history = [dict(row) for row in snapshot.transfer_history or []]
                        service_ok = True
                except Exception:
                    service_ok = False  # 写服务不可用时回退只读查询
            if not service_ok:
                pending = [
                    json.loads(row["payload_json"])
                    for row in conn.execute(
                        "SELECT payload_json FROM pending_actions "
                        "WHERE type = 'transfer_review' ORDER BY ordinal"
                    )
                ]
                history = [
                    self._transfer_row_from_db(row)
                    for row in conn.execute(
                        "SELECT * FROM transfers WHERE season_number = ? ORDER BY transfer_row_id",
                        (season,),
                    )
                ]

        history = [row for row in history if int(row.get("season_number", season)) == season]
        return _TransfersData(
            season_number=season,
            seasons=seasons,
            snapshot_season=snapshot_season,
            pending=pending,
            history=history,
            team_ids=team_ids,
        )

    @staticmethod
    def _meta_int(conn, key: str) -> Optional[int]:
        row = conn.execute("SELECT value_json FROM save_meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return int(json.loads(row["value_json"]))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _transfer_row_from_db(row) -> dict:
        """transfers 表行 → 与快照 transfer_history 同形的字典。"""
        return {
            "season_number": int(row["season_number"]),
            "week_number": int(row["week_number"]),
            "window": row["window"],
            "trade_id": row["trade_id"],
            "team_a": row["team_a"],
            "team_b": row["team_b"],
            "team_a_players": json.loads(row["team_a_players_json"]),
            "team_b_players": json.loads(row["team_b_players_json"]),
            "team_a_total_value": float(row["team_a_total_value"]),
            "team_b_total_value": float(row["team_b_total_value"]),
            "value_gap": float(row["value_gap"]),
            "approved": bool(row["approved"]),
            "status": row["status"],
            "recalculated": bool(row["recalculated"]),
            "reason": row["reason"],
        }

    # -- 渲染 -----------------------------------------------------------------

    def _render(self, data: _TransfersData) -> None:
        self._rebuild_slot(self._header_slot, self._build_header(data))
        self._rebuild_slot(self._status_slot, self._build_status_row(data))
        self._rebuild_slot(self._pending_slot, self._build_pending_section(data))

        rows = [self._to_history_row(item) for item in data.history]
        self._history_all_rows = rows
        self._render_history_table()

    def _on_history_filters_changed(self, *_args) -> None:
        self._render_history_table()

    def _render_history_table(self) -> None:
        rows = self._history_all_rows or []
        filter_state = self._history_filter.state() if hasattr(self, "_history_filter") else {}
        search = str(filter_state.get("search") or "").strip().lower()
        status = str(filter_state.get("transfersHistoryStatusCombo") or "全部状态")
        if search:
            rows = [
                row
                for row in rows
                if search in row.team_a.lower()
                or search in row.team_b.lower()
                or search in " ".join(row.team_a_players + row.team_b_players).lower()
            ]
        if status != "全部状态":
            rows = [row for row in rows if row.status == status]
        if rows:
            assert self._history_hint is not None
            self._history_hint.setVisible(False)
            self._history_table.setVisible(True)
            self._history_table.set_rows(rows, route_for_row=None)  # 行激活不导航
            self._history_table.setFixedHeight(
                _HEADER_HEIGHT + len(rows) * _ROW_HEIGHT + _TABLE_BORDER
            )
        else:
            assert self._history_hint is not None
            self._history_table.setVisible(False)
            self._history_hint.setVisible(True)

    @staticmethod
    def _rebuild_slot(slot: QWidget, content: QWidget) -> None:
        layout = slot.layout()
        assert layout is not None
        _clear_layout(layout)
        layout.addWidget(content)

    def _build_header(self, data: _TransfersData) -> QWidget:
        selector = QWidget()
        selector_layout = QHBoxLayout(selector)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(6)
        caption = QLabel("赛季")
        caption.setStyleSheet(_MUTED_STYLE)
        combo = QComboBox()
        combo.setObjectName("transfersSeasonCombo")
        for ref in data.seasons:
            label = f"第 {ref.season_number} 赛季" + ("（进行中）" if not ref.is_completed else "（已结束）")
            combo.addItem(label, ref.season_number)
        index = combo.findData(data.season_number)
        combo.setCurrentIndex(max(0, index))
        combo.currentIndexChanged.connect(self._on_season_changed)
        self._season_combo = combo
        selector_layout.addWidget(caption)
        selector_layout.addWidget(combo)

        return PageHeader(
            "转会中心",
            breadcrumbs=[],
            navigator=self._context.navigate,
            actions=[selector],
        )

    def _build_status_row(self, data: _TransfersData) -> QWidget:
        pending_visible = bool(data.pending) and data.snapshot_season == data.season_number
        text = f"第 {data.season_number} 赛季 · 本赛季转会记录 {len(data.history)} 条"
        if pending_visible:
            text += f" · 待审核转会 {len(data.pending)} 笔"
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        status = QLabel(text)
        status.setObjectName("transfersStatusLabel")
        status.setStyleSheet(_ACCENT_STYLE)
        self._status_label = status
        layout.addWidget(status)
        layout.addStretch(1)
        return row

    def _on_season_changed(self, index: int) -> None:
        combo = self._season_combo
        if combo is None:
            return
        data = combo.itemData(index)
        if data is None:
            return
        season = int(data)
        if season == self._season:
            return
        self.navigate(Route("transfers", season=season))

    # -- 待审核区（写流程核心） -------------------------------------------------

    def _build_pending_section(self, data: _TransfersData) -> QWidget:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        frame.setProperty("block_role", "transferReviewBlock")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        service_available = self._context.service is not None
        # 待审核转会属于存档当前赛季；历史赛季视图不展示（避免误操作旧赛季）。
        pending_visible = bool(data.pending) and data.snapshot_season == data.season_number
        items = [dict(item) for item in data.pending] if pending_visible else []
        self._pending_items = items
        self._decisions = {}

        layout.addWidget(
            section_header(
                "转会审核",
                "冬窗/夏窗的转会提案在这里汇总：“批准”为默认选择；逐笔选择后点击"
                "“提交审核结果”，系统将重算双方阵容并写入转会历史。",
            )
        )

        if items:
            for index, item in enumerate(items, start=1):
                layout.addWidget(self._build_trade_card(index, item, data, service_available))
        else:
            hint = QLabel("暂无待审核的转会。模拟进入冬窗或夏窗之后，这里会出现交易提案。")
            hint.setObjectName("transferReviewEmptyHint")
            hint.setStyleSheet(_MUTED_STYLE)
            hint.setWordWrap(True)
            layout.addWidget(hint)

        # 底部：批量操作 + 提交 + 行内状态条
        actions_row = QWidget()
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)
        approve_all = QPushButton("全部批准")
        approve_all.setObjectName("transferApproveAllButton")
        approve_all.setEnabled(bool(items))
        approve_all.clicked.connect(lambda: self._set_all_decisions(True))
        reject_all = QPushButton("全部拒绝")
        reject_all.setObjectName("transferRejectAllButton")
        reject_all.setEnabled(bool(items))
        reject_all.clicked.connect(lambda: self._set_all_decisions(False))
        self._approve_all_button = approve_all
        self._reject_all_button = reject_all
        actions_layout.addWidget(approve_all)
        actions_layout.addWidget(reject_all)

        submit = QPushButton("提交审核结果")
        submit.setObjectName("transferSubmitButton")
        submit.setEnabled(bool(items) and service_available)
        submit.clicked.connect(self._on_submit)
        self._submit_button = submit
        actions_layout.addWidget(submit)
        if items and not service_available:
            note = QLabel("当前未启用写服务，审核卡为只读展示。")
            note.setStyleSheet(_MUTED_STYLE)
            actions_layout.addWidget(note)
        actions_layout.addStretch(1)
        status = QLabel(self._review_message)
        status.setObjectName("transferReviewStatusLabel")
        status.setStyleSheet(_ACCENT_STYLE)
        self._review_status_label = status
        self._review_message = ""
        actions_layout.addWidget(status)
        layout.addWidget(actions_row)
        return frame

    def _build_trade_card(self, index: int, item: dict, data: _TransfersData, service_available: bool) -> QWidget:
        trade_id = str(item.get("trade_id", f"trade_{index}"))
        team_a = str(item.get("team_a", ""))
        team_b = str(item.get("team_b", ""))
        card = QFrame()
        card.setObjectName("cardFrame")
        card.setProperty("block_role", f"transferCard_{trade_id}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = QLabel(f"交易提案 {index} · {trade_id}")
        title.setStyleSheet(_BRIGHT_STYLE)
        layout.addWidget(title)

        teams_row = QWidget()
        teams_layout = QHBoxLayout(teams_row)
        teams_layout.setContentsMargins(0, 0, 0, 0)
        teams_layout.setSpacing(8)
        for order, team_name in enumerate((team_a, team_b)):
            if order:
                separator = QLabel("⇄")
                separator.setStyleSheet(_MUTED_STYLE)
                teams_layout.addWidget(separator)
            team_id = data.team_ids.get(team_name)
            if team_id is not None:
                teams_layout.addWidget(
                    EntityLink(
                        team_name,
                        Route("team", team=team_id, season=data.season_number),
                        self._context.navigate,
                    )
                )
            else:
                label = QLabel(team_name)
                label.setStyleSheet(_BRIGHT_STYLE)
                teams_layout.addWidget(label)
        teams_layout.addStretch(1)
        layout.addWidget(teams_row)

        badges_row = QWidget()
        badges_layout = QHBoxLayout(badges_row)
        badges_layout.setContentsMargins(0, 0, 0, 0)
        badges_layout.setSpacing(8)
        value_a = float(item.get("team_a_total_value", 0.0))
        value_b = float(item.get("team_b_total_value", 0.0))
        gap = float(item.get("value_gap", 0.0))
        for text in (
            f"{team_a} 送出总身价 {value_a:.2f}M",
            f"{team_b} 送出总身价 {value_b:.2f}M",
            f"差值 {gap:.2f}M",
        ):
            badge = QLabel(text)
            badge.setStyleSheet(_MUTED_STYLE)
            badges_layout.addWidget(badge)
        badges_layout.addStretch(1)
        layout.addWidget(badges_row)

        sides_row = QWidget()
        sides_layout = QHBoxLayout(sides_row)
        sides_layout.setContentsMargins(0, 0, 0, 0)
        sides_layout.setSpacing(16)
        sides_layout.addWidget(
            self._build_trade_side("送出球员", team_a, item.get("team_a_players") or [], data), 1
        )
        sides_layout.addWidget(
            self._build_trade_side("送出球员", team_b, item.get("team_b_players") or [], data), 1
        )
        layout.addWidget(sides_row)

        decision_row = QWidget()
        decision_layout = QHBoxLayout(decision_row)
        decision_layout.setContentsMargins(0, 0, 0, 0)
        decision_layout.setSpacing(8)
        caption = QLabel("本笔决定：")
        caption.setStyleSheet(_MUTED_STYLE)
        decision_layout.addWidget(caption)
        decision_layout.addWidget(self._build_decision_widget(trade_id, service_available))
        decision_layout.addStretch(1)
        layout.addWidget(decision_row)
        return card

    def _build_trade_side(self, action: str, team_name: str, players: List[dict], data: _TransfersData) -> QWidget:
        side = QFrame()
        side.setObjectName("cardFrame")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 8, 10, 8)
        side_layout.setSpacing(6)
        title = QLabel(f"{team_name} {action}")
        title.setStyleSheet(_BRIGHT_STYLE)
        side_layout.addWidget(title)

        headers = ("球员", "位置", "能力", "身价")
        widths = (180, 60, 70, 90)
        grid_holder = QWidget()
        grid = QGridLayout(grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        for column, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet(f"font-size: 12px; font-weight: 700; {_MUTED_STYLE}")
            label.setFixedWidth(widths[column])
            grid.addWidget(label, 0, column)
        grid.setColumnStretch(len(headers), 1)
        for row_index, player in enumerate(players, start=1):
            name = str(player.get("name", ""))
            name_link = EntityLink(
                name,
                Route(
                    "player",
                    player=real_player_id(name),
                    season=data.season_number,
                ),
                self._context.navigate,
            )
            name_link.setFixedWidth(widths[0])
            grid.addWidget(name_link, row_index, 0)
            position = QLabel(str(player.get("position", "")))
            position.setStyleSheet(_MUTED_STYLE)
            position.setFixedWidth(widths[1])
            grid.addWidget(position, row_index, 1)
            ability = QLabel(str(int(player.get("ability", 0))))
            ability.setStyleSheet(_BRIGHT_STYLE)
            ability.setFixedWidth(widths[2])
            grid.addWidget(ability, row_index, 2)
            value = QLabel(f"{float(player.get('market_value', 0.0)):.2f}M")
            value.setStyleSheet(_MUTED_STYLE)
            value.setFixedWidth(widths[3])
            grid.addWidget(value, row_index, 3)
        side_layout.addWidget(grid_holder)
        return side

    def _build_decision_widget(self, trade_id: str, service_available: bool) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)
        approve_button = QPushButton("批准")
        approve_button.setObjectName("transferApproveButton")
        approve_button.setCheckable(True)
        approve_button.setChecked(True)  # 默认批准
        approve_button.setEnabled(service_available)
        reject_button = QPushButton("拒绝")
        reject_button.setObjectName("transferRejectButton")
        reject_button.setCheckable(True)
        reject_button.setEnabled(service_available)
        group = QButtonGroup(widget)
        group.setExclusive(True)
        group.addButton(approve_button)
        group.addButton(reject_button)
        layout.addWidget(approve_button)
        layout.addWidget(reject_button)
        self._decisions[trade_id] = {"approve": approve_button, "reject": reject_button}
        return widget

    def _set_all_decisions(self, approved: bool) -> None:
        for buttons in self._decisions.values():
            buttons["approve" if approved else "reject"].setChecked(True)
            buttons["reject" if approved else "approve"].setChecked(False)

    def _on_submit(self) -> None:
        service = self._context.service
        items = self._pending_items
        if service is None or not items:
            return
        decisions: Dict[str, bool] = {}
        for item in items:
            trade_id = str(item.get("trade_id", ""))
            buttons = self._decisions.get(trade_id)
            approved = bool(buttons["approve"].isChecked()) if buttons else True
            decisions[trade_id] = approved
        try:
            state = service.apply_transfer_review(self.save_name(), decisions)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"转会审核提交失败：{exc}")
            return
        self._review_message = self._submit_summary(state, decisions)
        self.refresh()

    @staticmethod
    def _submit_summary(state, decisions: Dict[str, bool]) -> str:
        """提交反馈：已提交 N 笔（批准 M · 拒绝 K · 系统重算 J）。

        系统结果从返回快照的转会历史尾部（本次提交追加的行）统计：
        玩家通过 = approved 且未重算；系统重算 = recalculated（也属于通过）；
        其余（玩家拒绝 / 系统拒绝）计入拒绝。
        """
        total = len(decisions)
        approved = sum(1 for value in decisions.values() if value)
        recalculated = 0
        snapshot = getattr(state, "snapshot", None) if state is not None else None
        if snapshot is not None and total:
            tail = list(snapshot.transfer_history or [])[-total:]
            recalculated = sum(1 for row in tail if row.get("recalculated"))
            approved = sum(
                1 for row in tail if row.get("approved") and not row.get("recalculated")
            )
        rejected = max(0, total - approved - recalculated)
        return f"已提交 {total} 笔（批准 {approved} · 拒绝 {rejected} · 系统重算 {recalculated}）"

    # -- 转会历史行 -------------------------------------------------------------

    def _to_history_row(self, item: dict) -> _HistoryRow:
        team_a_players = tuple(str(player.get("name", "")) for player in item.get("team_a_players") or [])
        team_b_players = tuple(str(player.get("name", "")) for player in item.get("team_b_players") or [])
        segments: List[str] = []
        for index, name in enumerate(team_a_players):
            if index:
                segments.append("、")
            segments.append(name)
        if team_b_players:
            segments.append(" ⇄ ")
            for index, name in enumerate(team_b_players):
                if index:
                    segments.append("、")
                segments.append(name)
        status = str(item.get("status") or ("玩家通过" if item.get("approved") else "玩家拒绝"))
        return _HistoryRow(
            week=int(item.get("week_number", 0)),
            window=str(item.get("window", "")),
            team_a=str(item.get("team_a", "")),
            team_b=str(item.get("team_b", "")),
            team_a_players=team_a_players,
            team_b_players=team_b_players,
            players="".join(segments),
            status=status,
            gap=float(item.get("value_gap", 0.0)),
        )

    def _player_segments(self, row: _HistoryRow) -> List[Tuple[str, Optional[Route]]]:
        """球员列的分段（文本 + 路由）；与 ``_to_history_row`` 的汇总文本一致。"""
        segments: List[Tuple[str, Optional[Route]]] = []
        for index, name in enumerate(row.team_a_players):
            if index:
                segments.append(("、", None))
            segments.append((name, self._player_route(name)))
        if row.team_b_players:
            segments.append((" ⇄ ", None))
            for index, name in enumerate(row.team_b_players):
                if index:
                    segments.append(("、", None))
                segments.append((name, self._player_route(name)))
        return segments

    def _player_route(self, name: str) -> Route:
        return Route(
            "player",
            player=real_player_id(name),
            season=self._season,
        )

    # -- 空状态 -----------------------------------------------------------------

    def _show_empty(self, title: str, description: str, hint: Optional[str]) -> None:
        assert self._empty is not None
        old = self._empty
        replacement = EmptyState(title, description, hint)
        self._stack.addWidget(replacement)
        self._stack.setCurrentWidget(replacement)
        self._empty = replacement
        if old is not None and old is not replacement:
            self._stack.removeWidget(old)
            old.deleteLater()
