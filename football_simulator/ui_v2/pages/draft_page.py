"""选秀中心（阶段 5 重写，实施方案 §8.8 选秀部分 + §7.2 全局链接合同）。

Route：``Route("draft", season=<n>)``（season 必填）。

- 页头：选秀中心 + 赛季选择器（``navigate`` 新路由，形成可后退历史）；
- 待选秀（``pending_draft.status == 'awaiting_input'`` 且属于路由赛季）：
  说明文案（候选 N 人、按一级联赛排名逆序轮询、位置配额限制、能力随机、
  初始身价固定 30.00M）+ 配置候选列表只读展示 + “确认开始选秀”按钮 →
  ``context.service.apply_draft(save_name, [])``（配置候选池语义，与引擎
  现行为一致：优先消耗配置候选，不足部分本届自动跳过）；成功后刷新并显示
  ``last_draft`` 结果；失败弹 ``QMessageBox``。``context.service`` 为 None
  时只读展示（数据回退 ``pending_actions`` 载荷，与快照同源）并禁用确认；
- 选秀结果区：``results`` 表（轮次/球队/新秀/位置/能力/身价 30.0）；
  球队 → ``Route("team", team=<稳定ID>, season=<路由赛季>)``、新秀 →
  ``Route("player", player=real_player_id(显示名), season=<路由赛季>)``；
  行激活不导航。轮次按引擎的逆序轮询顺序重放计算（当前赛季用快照积分榜
  倒序、已归档赛季用归档名次倒序重放；无法重建顺序时显示占位符，不虚构）；
- 数据口径说明：引擎快照（``last_draft``）仅保留当前赛季，跨赛季结果读取
  自存档数据库的 ``drafts`` 日志表；没有日志的赛季显示空状态，不虚构数据。

滚动面归属（§8.2）：内容型页面 —— 单个外层 ``QScrollArea`` 是唯一纵向滚动
面；结果 EntityTable 按“表头 + 全部行”固定高度完整展开（纵向滚动条策略
AlwaysOff），不构成第二个纵向滚动面，禁止小框内滚动。

delegate 生命周期约定（重要，经崩溃报告与压力矩阵实证）：``setItemDelegate
ForColumn`` 不取得所有权，且 shiboken 会在最后一个 Python 引用消失时删除
C++ 对象（即使挂了 Qt parent）。因此本页所有 delegate 统一：``parent=view``
（与视图同生命周期）+ 页面持有 Python 引用（``self._table_delegates`` 只增
不清，绝不提前清空）；结果 EntityTable 与其 delegate 一次构建、跨刷新复用。

兼容说明：外壳在阶段 5 集成前仍以旧签名构造本页面并调用 ``set_snapshot``；
新契约页面不消费快照，构造器容忍旧位置参数、``set_snapshot`` 为显式空操作。

数据口径：转会与选秀数据天然只含真实球员（引擎语义）：转会移动真实球员、选秀录入真实球员，默认球员不参与，因此无需“只显示真实球员”过滤。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
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

from football_simulator.data import load_save_config, real_player_id
from football_simulator.queries import base, competition_queries
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    LINK_COLOR,
    TEXT_COLOR_MUTED,
    ColumnSpec,
    EmptyState,
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

_DRAFT_COLUMNS = (
    ColumnSpec("round", "轮次", width=80, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("team_name", "球队", width=190),
    ColumnSpec("player_name", "新秀", width=210),
    ColumnSpec("position", "位置", width=70),
    ColumnSpec("ability", "能力", width=80, alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("market_value_text", "身价", width=100, alignment=Qt.AlignmentFlag.AlignRight),
)

# 引擎阵容位置配额（FORMATION_RULES），说明文案使用。
_POSITION_QUOTA_TEXT = "GK 1 · DF 4 · MF 3 · FW 3"


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
class _DraftResultRow:
    """选秀结果行视图模型。"""

    round: Optional[int]
    team_name: str
    player_name: str
    position: str
    ability: int
    market_value: float
    market_value_text: str


@dataclass
class _DraftData:
    """选秀页一次刷新所需的全部只读数据。"""

    season_number: int
    seasons: Tuple[base.SeasonRef, ...]
    snapshot_season: Optional[int]
    pending: dict
    draft_log: Optional[dict]
    team_ids: Dict[str, int]
    candidates: List[dict]
    draft_order: Tuple[str, ...]


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


# -- 页面 -------------------------------------------------------------------


class DraftPage(EntityPageBase):
    """选秀中心：待选秀确认（写流程）+ 该赛季选秀结果。"""

    def __init__(self, context, parent: Optional[QWidget] = None, *_legacy_args: object) -> None:
        # 外壳在阶段 5 集成前仍用旧签名构造本页（多余位置参数在此被丢弃）。
        if not isinstance(parent, QWidget):
            parent = None
        self._season: int = 0
        self._pending: dict = {}
        self._candidate_preview: List[dict] = []
        self._confirm_button: Optional[QPushButton] = None
        self._result_message: str = ""
        self._status_label: Optional[QLabel] = None
        self._season_combo: Optional[QComboBox] = None
        self._team_ids: Dict[str, int] = {}
        self._results_hint: Optional[QLabel] = None
        self._results_all_rows: Optional[List[_DraftResultRow]] = None
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
        scroll.setObjectName("draftScroll")
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        scroll.setWidget(self._content)
        self._scroll = scroll
        self._stack.addWidget(scroll)

        # 页头 / 状态行 / 待选秀区每次刷新重建；结果区一次构建跨刷新复用。
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
        content_layout.addWidget(self._build_results_section())
        content_layout.addStretch(1)

        self._empty = EmptyState(
            "还没有可用的存档数据",
            "当前存档还没有赛季数据。",
            "请先在顶部选择存档，然后点击“初始化赛季”创建第 1 赛季。",
        )
        self._stack.addWidget(self._empty)

    def _build_results_section(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(
            section_header(
                "选秀结果",
                "本届新秀按两联赛统一倒序轮询分配到各队（位置配额限制下可能跳过）;"
                "单击球队/新秀名打开详情。引擎快照仅保留当前赛季，历史赛季结果读取自"
                "存档数据库中的选秀日志，没有日志的赛季显示空状态。",
            )
        )

        # 结果筛选（UI#8）：按球队/新秀搜索 + 位置筛选。
        self._results_filter = FilterBar(on_search_changed=self._on_results_filters_changed)
        self._results_position_combo = self._results_filter.add_combo(
            "位置",
            ["全部位置", "GK", "DF", "MF", "FW"],
            "draftResultsPositionCombo",
        )
        self._results_position_combo.currentIndexChanged.connect(self._on_results_filters_changed)
        self._results_search = self._results_filter.add_search("搜索球队 / 新秀…")
        self._results_filter.add_reset()
        layout.addWidget(self._results_filter)

        # 结果表一次构建：refresh 中替换行并按“表头 + 全部行”固定高度完整展开
        # （纵向滚动条 AlwaysOff，不构成第二个纵向滚动面，§8.2）。
        self._results_table = EntityTable(
            _DRAFT_COLUMNS, navigator=self._context.navigate, parent=self
        )
        view = self._results_table.view
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        view.horizontalHeader().setFixedHeight(_HEADER_HEIGHT)

        team_index = _column_index(_DRAFT_COLUMNS, "team_name")
        self._team_delegate = _LinkColumnDelegate(
            self._results_table,
            self._team_route_for_row,
            _DRAFT_COLUMNS[team_index].alignment,
            parent=view,
            crest=True,
        )
        view.setItemDelegateForColumn(team_index, self._team_delegate)
        self._table_delegates.append(self._team_delegate)

        player_index = _column_index(_DRAFT_COLUMNS, "player_name")
        self._player_delegate = _LinkColumnDelegate(
            self._results_table,
            self._player_route_for_row,
            _DRAFT_COLUMNS[player_index].alignment,
            parent=view,
        )
        view.setItemDelegateForColumn(player_index, self._player_delegate)
        self._table_delegates.append(self._player_delegate)

        layout.addWidget(self._results_table, 1)
        self._results_hint = QLabel("该赛季没有选秀记录。")
        self._results_hint.setObjectName("draftResultsEmptyHint")
        self._results_hint.setStyleSheet(_MUTED_STYLE)
        layout.addWidget(self._results_hint)
        return frame

    def set_snapshot(self, snapshot: object) -> None:
        """外壳 ``_refresh_views`` 的遗留兼容入口（阶段 5 集成后移除）。"""
        del snapshot

    # -- 数据刷新 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "draft":
            return
        season = route.int_param("season")
        try:
            data = self._load_data(self.save_name(), season)
        except base.MissingSaveError as exc:
            self._show_empty("还没有可用的存档数据", str(exc), "请先在顶部选择存档，然后点击“初始化赛季”。")
            return
        except Exception as exc:  # 查询层异常统一进空状态
            self._show_empty("暂时无法加载选秀数据", str(exc), None)
            return

        self._season = data.season_number
        self._team_ids = dict(data.team_ids)
        self._pending = dict(data.pending) if data.pending else {}
        self._candidate_preview = [dict(item) for item in data.candidates]
        self._render(data)
        self._stack.setCurrentWidget(self._scroll)

    def route_context(self) -> dict:
        if self._season:
            return {"season": self._season}
        return {}

    # -- 只读取数 -------------------------------------------------------------

    def _load_data(self, save_name: str, season: Optional[int]) -> _DraftData:
        service = self._context.service
        service_snapshot = None
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

            pending: dict = {}
            service_ok = False
            if service is not None:
                try:
                    state = service.load_state(save_name)
                    service_snapshot = state.snapshot if state is not None else None
                    if service_snapshot is not None:
                        snapshot_season = int(service_snapshot.season_number)
                        pending = dict(service_snapshot.pending_draft or {})
                        service_ok = True
                except Exception:
                    service_ok = False  # 写服务不可用时回退只读查询
            if not service_ok:
                row = conn.execute(
                    "SELECT payload_json FROM pending_actions WHERE type = 'draft' LIMIT 1"
                ).fetchone()
                pending = json.loads(row["payload_json"]) if row is not None else {}

            # 选秀日志：drafts 表按赛季保留（引擎 last_draft 仅覆盖当前赛季）。
            draft_log: Optional[dict] = None
            log_row = conn.execute(
                "SELECT log_json FROM drafts WHERE season_number = ? LIMIT 1", (season,)
            ).fetchone()
            if log_row is not None:
                try:
                    draft_log = json.loads(log_row["log_json"])
                except (TypeError, ValueError):
                    draft_log = None

            awaiting = (
                pending.get("status") == "awaiting_input"
                and int(pending.get("season_number", -1) or -1) == season
            )
            candidates = self._preview_config_candidates(save_name, conn, service_snapshot, pending) if awaiting else []
            draft_order = self._draft_order(conn, service_snapshot, season)

        return _DraftData(
            season_number=season,
            seasons=seasons,
            snapshot_season=snapshot_season,
            pending=pending if awaiting else {},
            draft_log=draft_log,
            team_ids=team_ids,
            candidates=candidates,
            draft_order=draft_order,
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
    def _preview_config_candidates(save_name: str, conn, service_snapshot, pending: dict) -> List[dict]:
        """配置候选池预览：与引擎 ``_config_draft_candidates`` 同一语义——

        从 ``draft_pool_index`` 起顺序取配置候选、跳过已入库球员，至计划人数。
        """
        try:
            count = int(pending.get("candidate_count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return []
        start_row = conn.execute(
            "SELECT value_json FROM save_meta WHERE key = 'draft_pool_index'"
        ).fetchone()
        try:
            start = int(json.loads(start_row["value_json"])) if start_row is not None else 0
        except (TypeError, ValueError):
            start = 0
        existing = set()
        if service_snapshot is not None:
            existing = {profile.name for profile in service_snapshot.real_player_pool or []}
        else:
            existing = {
                str(row[0]) for row in conn.execute("SELECT name FROM real_player_pool")
            }
        try:
            config = load_save_config(save_name)
        except Exception:
            return []
        candidates: List[dict] = []
        index = max(0, start)
        templates = config.draft_players
        while index < len(templates) and len(candidates) < count:
            template = templates[index]
            index += 1
            if template.name in existing:
                continue
            candidates.append({"name": template.name, "position": template.position})
        return candidates

    @staticmethod
    def _draft_order(conn, service_snapshot, season: int) -> Tuple[str, ...]:
        """重建选秀顺序（一级联赛名次倒序），用于结果轮次重放。

        - 当前赛季且快照可用：快照 ``premier_table`` 与引擎选秀时完全一致；
        - 已归档赛季：归档的一级联赛名次倒序；
        - 重建失败返回空元组（轮次列显示占位符，不虚构）。
        """
        if service_snapshot is not None and int(service_snapshot.season_number) == season:
            return tuple(
                [row.team.name for row in reversed(service_snapshot.second_table)]
                + [row.team.name for row in reversed(service_snapshot.premier_table)]
            )
        try:
            archive = competition_queries.load_archive(conn, season)
        except Exception:
            archive = None
        if archive:
            second_order = archive.get("second_order") or []
            premier_order = archive.get("premier_order") or []
            if second_order or premier_order:
                return tuple(
                    list(reversed([str(name) for name in second_order]))
                    + list(reversed([str(name) for name in premier_order]))
                )
        return ()

    @staticmethod
    def _round_numbers(results: List[dict], draft_order: Tuple[str, ...]) -> List[Optional[int]]:
        """按引擎逆序轮询顺序重放各顺位的轮次。

        结果按引擎迭代顺序记录；顺位在选秀顺序中向后搜索（跨过顺序末尾自动
        换圈），``slot // 队数 + 1`` 即轮次。找不到目标球队时显示占位符。
        """
        order = list(draft_order)
        total = len(order)
        if total == 0:
            return [None] * len(results)
        position_of = {team: index for index, team in enumerate(order)}
        rounds: List[Optional[int]] = []
        slot = -1
        for result in results:
            team = str(result.get("team_name", ""))
            target = position_of.get(team)
            if target is None:
                rounds.append(None)
                continue
            candidate = slot + 1
            while candidate % total != target % total:
                candidate += 1
            rounds.append(candidate // total + 1)
            slot = candidate
        return rounds

    # -- 渲染 -----------------------------------------------------------------

    def _render(self, data: _DraftData) -> None:
        self._rebuild_slot(self._header_slot, self._build_header(data))
        self._rebuild_slot(self._status_slot, self._build_status_row(data))
        self._rebuild_slot(self._pending_slot, self._build_pending_section(data))
        self._render_results(data)

    @staticmethod
    def _rebuild_slot(slot: QWidget, content: QWidget) -> None:
        layout = slot.layout()
        assert layout is not None
        _clear_layout(layout)
        layout.addWidget(content)

    def _build_header(self, data: _DraftData) -> QWidget:
        selector = QWidget()
        selector_layout = QHBoxLayout(selector)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(6)
        caption = QLabel("赛季")
        caption.setStyleSheet(_MUTED_STYLE)
        combo = QComboBox()
        combo.setObjectName("draftSeasonCombo")
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
            "选秀中心",
            breadcrumbs=[],
            navigator=self._context.navigate,
            actions=[selector],
        )

    def _build_status_row(self, data: _DraftData) -> QWidget:
        awaiting = bool(data.pending)
        results = (data.draft_log or {}).get("results") or []
        if awaiting:
            count = data.pending.get("candidate_count", 0)
            text = f"第 {data.season_number} 赛季 · 等待选秀录入（计划 {count} 人）"
        elif results:
            text = f"第 {data.season_number} 赛季 · 本届选秀结果 {len(results)} 人"
        else:
            text = f"第 {data.season_number} 赛季 · 没有选秀记录"
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        status = QLabel(text)
        status.setObjectName("draftStatusLabel")
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
        self.navigate(Route("draft", season=season))

    # -- 待选秀区（写流程核心） -------------------------------------------------

    def _build_pending_section(self, data: _DraftData) -> QWidget:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        frame.setProperty("block_role", "draftPendingBlock")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        service_available = self._context.service is not None
        awaiting = bool(data.pending)
        self._pending = dict(data.pending) if awaiting else {}

        layout.addWidget(
            section_header(
                "待处理选秀",
                "选秀在第 49 周（赛季末结算）后开放；确认后写入本届结果并分配新秀。",
            )
        )

        if awaiting:
            count = int(data.pending.get("candidate_count", 0) or 0)
            description = QLabel(
                f"本届计划选秀 {count} 人。确认后系统按一级联赛排名逆序轮询为各队分配新秀，"
                f"并受位置配额（{_POSITION_QUOTA_TEXT}）限制；新秀能力由系统随机生成，"
                "初始身价固定为 30.00M。本页按配置候选池执行（与引擎现行为一致），"
                "配置候选不足时不足部分本届自动跳过。"
            )
            description.setObjectName("draftAwaitingDescription")
            description.setStyleSheet(_MUTED_STYLE)
            description.setWordWrap(True)
            layout.addWidget(description)
            layout.addWidget(self._build_candidate_grid(data.candidates, count))
        else:
            hint = QLabel("当前没有待处理的选秀。")
            hint.setObjectName("draftPendingEmptyHint")
            hint.setStyleSheet(_MUTED_STYLE)
            hint.setWordWrap(True)
            layout.addWidget(hint)

        actions_row = QWidget()
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)
        confirm = QPushButton("确认开始选秀")
        confirm.setObjectName("draftConfirmButton")
        confirm.setEnabled(awaiting and service_available)
        confirm.clicked.connect(self._on_confirm)
        self._confirm_button = confirm
        actions_layout.addWidget(confirm)
        if awaiting and not service_available:
            note = QLabel("当前未启用写服务，页面为只读展示。")
            note.setStyleSheet(_MUTED_STYLE)
            actions_layout.addWidget(note)
        actions_layout.addStretch(1)
        status = QLabel(self._result_message)
        status.setObjectName("draftStatusLabelMessage")
        status.setStyleSheet(_ACCENT_STYLE)
        self._result_status_label = status
        self._result_message = ""
        actions_layout.addWidget(status)
        layout.addWidget(actions_row)
        return frame

    def _build_candidate_grid(self, candidates: List[dict], count: int) -> QWidget:
        holder = QFrame()
        holder.setObjectName("cardFrame")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        note = QLabel(
            f"配置候选池（只读预览，共 {len(candidates)} 人"
            + (f"；少于计划人数 {count} 人" if len(candidates) < count else "")
            + "）："
        )
        note.setStyleSheet(_MUTED_STYLE)
        layout.addWidget(note)

        grid_holder = QWidget()
        grid = QGridLayout(grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        for column, header in enumerate(("姓名", "位置")):
            label = QLabel(header)
            label.setStyleSheet(f"font-size: 12px; font-weight: 700; {_MUTED_STYLE}")
            label.setFixedWidth(180 if column == 0 else 60)
            grid.addWidget(label, 0, column)
        for row_index, candidate in enumerate(candidates, start=1):
            name = QLabel(str(candidate.get("name", "")))
            name.setStyleSheet(_BRIGHT_STYLE)
            name.setFixedWidth(180)
            grid.addWidget(name, row_index, 0)
            position = QLabel(str(candidate.get("position", "")))
            position.setStyleSheet(_MUTED_STYLE)
            position.setFixedWidth(60)
            grid.addWidget(position, row_index, 1)
        layout.addWidget(grid_holder)
        return holder

    def _on_confirm(self) -> None:
        service = self._context.service
        pending = self._pending
        if service is None or pending.get("status") != "awaiting_input":
            return
        try:
            # 空 prospects = 配置候选池语义（与引擎现行为一致）。
            service.apply_draft(self.save_name(), [])
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"选秀确认失败：{exc}")
            return
        self._result_message = "选秀已完成，本届新秀已按逆序轮询分配到各球队。"
        self.refresh()

    # -- 选秀结果区 -------------------------------------------------------------

    def _render_results(self, data: _DraftData) -> None:
        log = data.draft_log or {}
        results = list(log.get("results") or [])
        assert self._results_hint is not None
        if not results:
            self._results_table.setVisible(False)
            self._results_hint.setVisible(True)
            return
        self._results_hint.setVisible(False)
        self._results_table.setVisible(True)
        rounds = self._round_numbers(results, data.draft_order)
        rows = [
            _DraftResultRow(
                round=rounds[index] if index < len(rounds) else None,
                team_name=str(item.get("team_name", "")),
                player_name=str(item.get("name", "")),
                position=str(item.get("position", "")),
                ability=int(item.get("ability", 0)),
                market_value=float(item.get("market_value", 0.0)),
                market_value_text=f"{float(item.get('market_value', 0.0)):.2f}M",
            )
            for index, item in enumerate(results)
        ]
        self._results_all_rows = rows
        self._render_results_table()

    def _on_results_filters_changed(self, *_args) -> None:
        self._render_results_table()

    def _render_results_table(self) -> None:
        rows = self._results_all_rows or []
        filter_state = self._results_filter.state() if hasattr(self, "_results_filter") else {}
        search = str(filter_state.get("search") or "").strip().lower()
        position = str(filter_state.get("draftResultsPositionCombo") or "全部位置")
        if search:
            rows = [
                row
                for row in rows
                if search in row.team_name.lower() or search in row.player_name.lower()
            ]
        if position != "全部位置":
            rows = [row for row in rows if row.position == position]
        if rows:
            assert self._results_hint is not None
            self._results_hint.setVisible(False)
            self._results_table.setVisible(True)
            self._results_table.set_rows(rows, route_for_row=None)  # 行激活不导航
            self._results_table.setFixedHeight(
                _HEADER_HEIGHT + len(rows) * _ROW_HEIGHT + _TABLE_BORDER
            )
        else:
            assert self._results_hint is not None
            self._results_table.setVisible(False)
            self._results_hint.setVisible(True)

    def _team_route_for_row(self, row: _DraftResultRow) -> Optional[Route]:
        team_id = self._team_ids.get(row.team_name)
        if team_id is None:
            return None
        return Route("team", team=team_id, season=self._season)

    def _player_route_for_row(self, row: _DraftResultRow) -> Route:
        return Route("player", player=real_player_id(row.player_name), season=self._season)

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
