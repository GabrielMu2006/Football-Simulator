"""赛季总览（阶段 5 重写，实施方案 §8.4 + 能力审核写流程）。

Route：``Route("season_overview", season=<n>)``（season 必填）。

- 页头：第 N 赛季 + 状态（进行中 x/52 周 · 当前阶段 / 已结束）+ 赛季选择器
  （多赛季时 ``navigate`` 新路由，形成可后退历史）；
- 待办区（写流程核心）：
  * 能力变动审核（``pending_ability_review``）：表格列出
    球员/位置/当前能力/新能力/变化（+绿 -红徽标），每行“保留/采纳”选择
    （默认采纳；语义：能力越高越好，“采纳”= 采用 new_ability），
    底部“提交审核结果”→ ``context.service.apply_ability_review(save_name,
    {name: is_approved})``；成功后刷新并显示“已提交 N 项（采纳 M 项）”，
    失败弹 ``QMessageBox``；提交后待办清空、显示“暂无待审核能力变动”；
  * 转会待办：计数 + “前往转会中心”（``Route("transfers", season=...)``）；
  * 选秀待办：计数 + “前往选秀中心”（``Route("draft", season=...)``）；
- 赛季时间线：完整 52 周网格（已赛周高亮、当前周标记、冬窗/夏窗/附加赛/
  杯赛周着色），第 n 周可点 → ``Route("weekly_report", week=n)``；
- 赛季级状态：已结束（归档存在）→ 各项冠军（球队可点，
  ``history_queries.get_season_archive_detail``）；进行中 → 两个联赛榜首摘要。

安全约束：写流程只在 ``context.service`` 非 None 时启用；所有 service 调用
均 try/except；无 service 时审核表只读展示（数据回退到 pending_actions
载荷，与快照同源），提交按钮禁用。

滚动面归属（§8.2）：内容型页面 —— 单个外层 ``QScrollArea`` 是唯一纵向滚动
面；内部审核表 / 时间线 / 冠军区全部按内容完整展开，无小滚动区。

兼容说明：外壳在阶段 5 集成前仍以旧签名构造本页面并调用 ``set_snapshot``；
新契约页面不消费快照，构造器容忍旧位置参数、``set_snapshot`` 为显式空操作。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base, competition_queries, history_queries
from football_simulator.schedule import TOTAL_WEEKS, build_week_calendar
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import TEXT_COLOR_BRIGHT, TEXT_COLOR_MUTED, EntityLink, EmptyState, PageHeader
from football_simulator.ui_v2.design_tokens import (
    DANGER_COLOR,
    LINK_COLOR,
    SUCCESS_HIGHLIGHT,
    WEEK_CUP_BG,
    WEEK_CURRENT_BG,
    WEEK_LEAGUE_BG,
    WEEK_PLAYED_BG,
    WEEK_PLAYOFF_BG,
    WEEK_SUMMER_BG,
    WEEK_WINTER_BG,
)
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase
from football_simulator.ui_v2.widgets import section_header

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"
_BRIGHT_STYLE = f"color: {TEXT_COLOR_BRIGHT}; background: transparent; font-weight: 700;"
_ACCENT_STYLE = f"color: {LINK_COLOR}; background: transparent; font-weight: 800;"
_POSITIVE_STYLE = f"color: {SUCCESS_HIGHLIGHT}; background: transparent; font-weight: 800;"
_NEGATIVE_STYLE = f"color: {DANGER_COLOR}; background: transparent; font-weight: 800;"

# 联赛赛历固定为 38 轮；build_week_calendar 只使用轮数与固定常量推导 52 周日历，
# 与 initialize_save_state 构建的赛历完全一致（确定性，无随机源）。
_WEEK_CALENDAR = build_week_calendar([[] for _ in range(38)])

# 时间线周次着色（沿用旧页的语义配色；底色加深以承载链接文字）。
_WEEK_KIND_COLORS = {
    "winter_break": WEEK_WINTER_BG,
    "summer_break": WEEK_SUMMER_BG,
    "promotion_playoff": WEEK_PLAYOFF_BG,
    "cup_week": WEEK_CUP_BG,
    "league_week": WEEK_LEAGUE_BG,
}
_PLAYED_COLOR = WEEK_PLAYED_BG
_CURRENT_COLOR = WEEK_CURRENT_BG
# 周格清晰配色（与图例一致，保证色块在深色背景可见）。
_BRIGHT_WEEK_COLORS = {
    "league_week": "#3b6ea5",
    "cup_week": "#7a5fc0",
    "winter_break": "#3a7d4e",
    "summer_break": "#a06a2c",
    "promotion_playoff": "#b08a2e",
}
_BRIGHT_PLAYED_COLOR = "#2e6db4"
_BRIGHT_CURRENT_COLOR = "#1167d8"
_TIMELINE_COLUMNS = 13

_REVIEW_HEADERS = ("球员", "位置", "当前能力", "新能力", "变化", "决定")
_REVIEW_COLUMN_WIDTHS = (210, 70, 90, 90, 80)


@dataclass
class _SeasonData:
    """赛季页一次刷新所需的全部只读数据。"""

    season_number: int
    status: str  # active / completed
    is_current: bool  # 是否为存档当前（active）赛季
    simulated_weeks: int  # 仅当前赛季有意义
    played_weeks: Tuple[int, ...]  # 该赛季已有已完成比赛的周次
    pending_ability: List[dict] = field(default_factory=list)
    pending_transfer: int = 0
    pending_draft: int = 0
    current_season_number: int = 0  # 转会/选秀路由的赛季参数（存档当前赛季）
    seasons: Tuple[base.SeasonRef, ...] = ()
    archive: Optional[history_queries.SeasonArchiveDetail] = None
    standings: Dict[str, Tuple[competition_queries.StandingRow, ...]] = field(default_factory=dict)
    team_ids: Dict[str, int] = field(default_factory=dict)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            # 避免 setParent(None) 产生临时顶层窗口（macOS 全屏退场触发点之一）。
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


class SeasonOverviewPage(EntityPageBase):
    """赛季总览：待办审核（写流程）+ 52 周时间线 + 赛季级状态。"""

    def __init__(self, context, parent: Optional[QWidget] = None, *_legacy_args: object) -> None:
        # 外壳在阶段 5 集成前仍用旧签名构造本页（多余位置参数在此被丢弃）。
        if not isinstance(parent, QWidget):
            parent = None
        self._season: int = 0
        self._review_items: List[dict] = []
        self._review_buttons: Dict[str, Dict[str, QPushButton]] = {}
        self._review_submit: Optional[QPushButton] = None
        self._review_hint: Optional[QLabel] = None
        self._review_status_label: Optional[QLabel] = None
        self._review_message: str = ""
        self._status_label: Optional[QLabel] = None
        self._season_combo: Optional[QComboBox] = None
        self._week_links: Dict[int, EntityLink] = {}
        self._transfer_link: Optional[EntityLink] = None
        self._draft_link: Optional[EntityLink] = None
        self._champion_links: List[EntityLink] = []
        self._empty: Optional[EmptyState] = None
        super().__init__(context, parent)

    # -- UI 骨架（一次构建；内容在 refresh 中重建） ---------------------------

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(16, 14, 16, 14)
        page_layout.setSpacing(0)

        self._stack = QStackedWidget()
        page_layout.addWidget(self._stack, 1)

        # 唯一外层纵向滚动面（内容型页面，区块完整展开）。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("seasonScroll")
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(12)
        scroll.setWidget(self._content)
        self._scroll = scroll
        self._stack.addWidget(scroll)

        self._empty = EmptyState(
            "还没有可用的存档数据",
            "当前存档还没有赛季数据。",
            "请先在顶部选择存档，然后点击“初始化赛季”创建第 1 赛季。",
        )
        self._stack.addWidget(self._empty)

    def set_snapshot(self, snapshot: object) -> None:
        """外壳 ``_refresh_views`` 的遗留兼容入口（阶段 5 集成后移除）。

        新契约页面不消费快照：数据全部由 ``apply_route``/``refresh`` 按路由
        只读查询得到；写流程所需的待办在 ``refresh`` 内经 service/只读查询
        获取，这里刻意不做任何事，避免与路由刷新双写。
        """
        del snapshot

    # -- 数据刷新 -------------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "season_overview":
            return
        season = route.int_param("season")
        navigate = getattr(self._context, "navigate", None)
        try:
            data = self._load_data(self.save_name(), season)
        except base.MissingSaveError as exc:
            self._show_empty("还没有可用的存档数据", str(exc), "请先在顶部选择存档，然后点击“初始化赛季”。")
            return
        except KeyError as exc:
            self._show_empty("赛季不存在", str(exc), "请使用赛季选择器切换到有效的赛季。")
            return
        except Exception as exc:  # 查询层异常统一进空状态
            self._show_empty("暂时无法加载赛季数据", str(exc), None)
            return

        self._season = data.season_number
        self._render(data, navigate)
        self._stack.setCurrentWidget(self._scroll)

    def route_context(self) -> dict:
        if self._season:
            return {"season": self._season}
        return {}

    # -- 只读取数 -------------------------------------------------------------

    def _load_data(self, save_name: str, season: Optional[int]) -> _SeasonData:
        with base.open_read_connection(save_name) as conn:
            seasons = tuple(base.load_seasons(conn))
            if season is None:
                season = base.resolve_current_season(conn).season_number
            season = int(season)
            match = next((ref for ref in seasons if ref.season_number == season), None)
            if match is None:
                raise KeyError(f"存档中不存在第 {season} 赛季。")
            season_id = base.season_id_for(conn, season)

            meta = {
                str(row["key"]): json.loads(row["value_json"])
                for row in conn.execute("SELECT key, value_json FROM save_meta")
            }
            meta_season = meta.get("season_number")
            current_season_number = (
                int(meta_season) if meta_season is not None else base.resolve_current_season(conn).season_number
            )
            is_current = match.status == "active" and season == current_season_number
            simulated_weeks = int(meta.get("current_week") or 0) if is_current else 0
            played_weeks = tuple(
                int(row[0])
                for row in conn.execute(
                    """
                    SELECT DISTINCT week_number FROM matches
                    WHERE season_id = ? AND status = 'completed'
                    ORDER BY week_number
                    """,
                    (season_id,),
                )
            )

            pending_ability = self._load_pending_ability(conn, save_name)
            counts = {
                str(row["type"]): int(row["total"])
                for row in conn.execute("SELECT type, COUNT(*) AS total FROM pending_actions GROUP BY type")
            }

            archive: Optional[history_queries.SeasonArchiveDetail] = None
            try:
                archive = history_queries.get_season_archive_detail(conn, season)
            except KeyError:
                archive = None

            standings: Dict[str, Tuple[competition_queries.StandingRow, ...]] = {}
            if archive is None:
                standings = {
                    category: competition_queries.league_standings_rows(
                        conn, season_id, season, category
                    )
                    for category in ("premier", "second")
                }

            team_ids = {
                str(row[0]): int(row[1]) for row in conn.execute("SELECT name, team_id FROM teams")
            }

        return _SeasonData(
            season_number=season,
            status=match.status,
            is_current=is_current,
            simulated_weeks=simulated_weeks,
            played_weeks=played_weeks,
            pending_ability=pending_ability,
            pending_transfer=counts.get("transfer_review", 0),
            pending_draft=counts.get("draft", 0),
            current_season_number=current_season_number,
            seasons=seasons,
            archive=archive,
            standings=standings,
            team_ids=team_ids,
        )

    def _load_pending_ability(self, conn, save_name: str) -> List[dict]:
        """待审核能力变动：优先 service 快照（写流程同源），回退只读载荷。

        两条路径读取的是同一份 ``pending_actions`` 数据，元素含
        name/position/old_ability/new_ability/delta。
        """
        service = self._context.service
        if service is not None:
            try:
                state = service.load_state(save_name)
                snapshot = state.snapshot if state is not None else None
                if snapshot is not None:
                    return [dict(item) for item in snapshot.pending_ability_review or []]
            except Exception:
                pass  # 写服务不可用时回退只读载荷
        try:
            rows = conn.execute(
                "SELECT payload_json FROM pending_actions WHERE type = 'ability_review' ORDER BY ordinal"
            ).fetchall()
        except Exception:
            return []
        return [json.loads(row["payload_json"]) for row in rows]

    # -- 渲染 -----------------------------------------------------------------

    def _render(self, data: _SeasonData, navigate) -> None:
        _clear_layout(self._content_layout)
        self._week_links = {}
        self._champion_links = []
        self._transfer_link = None
        self._draft_link = None

        self._content_layout.addWidget(self._build_header(data, navigate))
        self._content_layout.addWidget(self._build_status_row(data))
        self._content_layout.addWidget(self._build_pending_section(data, navigate))
        self._content_layout.addWidget(self._build_timeline_section(data, navigate))
        self._content_layout.addWidget(self._build_season_state_section(data, navigate))
        self._content_layout.addStretch(1)

    def _build_header(self, data: _SeasonData, navigate) -> QWidget:
        selector = QWidget()
        selector_layout = QHBoxLayout(selector)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(6)
        caption = QLabel("赛季")
        caption.setStyleSheet(_MUTED_STYLE)
        combo = QComboBox()
        combo.setObjectName("seasonOverviewSeasonCombo")
        for ref in data.seasons:
            label = f"第 {ref.season_number} 赛季" + ("（进行中）" if not ref.is_completed else "（已结束）")
            combo.addItem(label, ref.season_number)
        index = combo.findData(data.season_number)
        combo.setCurrentIndex(max(0, index))
        combo.currentIndexChanged.connect(self._on_season_changed)
        self._season_combo = combo
        selector_layout.addWidget(caption)
        selector_layout.addWidget(combo)

        header = PageHeader(
            f"第 {data.season_number} 赛季",
            breadcrumbs=[],
            navigator=navigate,
            actions=[selector],
        )
        return header

    def _build_status_row(self, data: _SeasonData) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        status = QLabel(self._status_text(data))
        status.setObjectName("seasonStatusLabel")
        status.setStyleSheet(_ACCENT_STYLE)
        self._status_label = status
        layout.addWidget(status)
        layout.addStretch(1)
        return row

    @staticmethod
    def _status_text(data: _SeasonData) -> str:
        if data.is_current and data.status == "active":
            week = min(max(data.simulated_weeks, 0), TOTAL_WEEKS)
            if data.simulated_weeks >= TOTAL_WEEKS:
                return f"状态：进行中 · 已模拟 {week}/{TOTAL_WEEKS} 周 · 本周阶段：{_week_label(data.simulated_weeks)}"
            phase = _week_label(data.simulated_weeks + 1)
            return f"状态：进行中 · 已模拟 {week}/{TOTAL_WEEKS} 周 · 当前阶段：{phase}"
        return "状态：已结束（完整 52 周赛历）"

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
        self.navigate(Route("season_overview", season=season))

    # -- 待办区（写流程核心） ---------------------------------------------------

    def _build_pending_section(self, data: _SeasonData, navigate) -> QWidget:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(section_header("待办与审核", "能力审核在本页完成；转会与选秀待办前往对应中心处理。"))

        # -- 能力变动审核 ----------------------------------------------------
        review_frame = QFrame()
        review_frame.setObjectName("cardFrame")
        review_frame.setProperty("block_role", "abilityReviewBlock")
        review_layout = QVBoxLayout(review_frame)
        review_layout.setContentsMargins(12, 10, 12, 10)
        review_layout.setSpacing(8)
        review_layout.addWidget(
            section_header(
                "能力变动审核",
                "能力越高越好：“采纳”= 采用新能力（默认），“保留”= 维持当前能力。",
            )
        )

        items = data.pending_ability
        self._review_items = [dict(item) for item in items]
        self._review_buttons = {}
        service_available = self._context.service is not None

        if items:
            grid_holder = QWidget()
            grid = QGridLayout(grid_holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            for column, title in enumerate(_REVIEW_HEADERS):
                label = QLabel(title)
                label.setStyleSheet(f"font-size: 12px; font-weight: 700; {_MUTED_STYLE}")
                if column < len(_REVIEW_COLUMN_WIDTHS):
                    label.setFixedWidth(_REVIEW_COLUMN_WIDTHS[column])
                grid.addWidget(label, 0, column)
            grid.setColumnStretch(len(_REVIEW_HEADERS), 1)

            for row_index, item in enumerate(items, start=1):
                name = str(item.get("name", ""))
                position = str(item.get("position", ""))
                old_ability = int(item.get("old_ability", 0))
                new_ability = int(item.get("new_ability", 0))
                delta = int(item.get("delta", new_ability - old_ability))

                player_id = base.canonical_player_id_for_name(name)
                name_link = EntityLink(
                    name,
                    Route("player", player=player_id, season=data.current_season_number),
                    navigate,
                )
                name_link.setFixedWidth(_REVIEW_COLUMN_WIDTHS[0])
                grid.addWidget(name_link, row_index, 0)

                position_label = QLabel(position)
                position_label.setStyleSheet(_MUTED_STYLE)
                position_label.setFixedWidth(_REVIEW_COLUMN_WIDTHS[1])
                grid.addWidget(position_label, row_index, 1)

                old_label = QLabel(str(old_ability))
                old_label.setStyleSheet(_BRIGHT_STYLE)
                old_label.setFixedWidth(_REVIEW_COLUMN_WIDTHS[2])
                grid.addWidget(old_label, row_index, 2)

                new_label = QLabel(str(new_ability))
                new_label.setStyleSheet(_BRIGHT_STYLE)
                new_label.setFixedWidth(_REVIEW_COLUMN_WIDTHS[3])
                grid.addWidget(new_label, row_index, 3)

                delta_label = QLabel(f"{delta:+d}")
                delta_label.setStyleSheet(_POSITIVE_STYLE if delta > 0 else (_NEGATIVE_STYLE if delta < 0 else _MUTED_STYLE))
                delta_label.setFixedWidth(_REVIEW_COLUMN_WIDTHS[4])
                grid.addWidget(delta_label, row_index, 4)

                decision = self._build_decision_widget(name)
                grid.addWidget(decision, row_index, 5)
            review_layout.addWidget(grid_holder)
        else:
            hint = QLabel("暂无待审核能力变动")
            hint.setObjectName("reviewEmptyHint")
            hint.setStyleSheet(_MUTED_STYLE)
            self._review_hint = hint
            review_layout.addWidget(hint)

        # 底部：提交按钮 + 行内状态条
        actions_row = QWidget()
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)
        submit = QPushButton("提交审核结果")
        submit.setObjectName("reviewSubmitButton")
        submit.setEnabled(bool(items) and service_available)
        submit.clicked.connect(self._on_submit_reviews)
        self._review_submit = submit
        actions_layout.addWidget(submit)
        if items and not service_available:
            note = QLabel("当前未启用写服务，审核表为只读展示。")
            note.setStyleSheet(_MUTED_STYLE)
            actions_layout.addWidget(note)
        actions_layout.addStretch(1)
        status = QLabel(self._review_message)
        status.setObjectName("reviewStatusLabel")
        status.setStyleSheet(_ACCENT_STYLE)
        self._review_status_label = status
        self._review_message = ""
        actions_layout.addWidget(status)
        review_layout.addWidget(actions_row)
        layout.addWidget(review_frame)

        # -- 转会 / 选秀待办 ---------------------------------------------------
        season_for_routes = data.current_season_number or data.season_number
        transfer_row = QWidget()
        transfer_layout = QHBoxLayout(transfer_row)
        transfer_layout.setContentsMargins(0, 0, 0, 0)
        transfer_layout.setSpacing(10)
        transfer_label = QLabel(f"转会待办：{data.pending_transfer} 笔等待处理")
        transfer_label.setStyleSheet(_BRIGHT_STYLE)
        transfer_layout.addWidget(transfer_label)
        transfer_link = EntityLink(
            "前往转会中心",
            Route("transfers", season=season_for_routes),
            navigate,
        )
        transfer_link.setObjectName("transferPendingLink")
        self._transfer_link = transfer_link
        transfer_layout.addWidget(transfer_link)
        transfer_layout.addStretch(1)
        layout.addWidget(transfer_row)

        draft_row = QWidget()
        draft_layout = QHBoxLayout(draft_row)
        draft_layout.setContentsMargins(0, 0, 0, 0)
        draft_layout.setSpacing(10)
        draft_label = QLabel(f"选秀待办：{data.pending_draft} 项等待录入")
        draft_label.setStyleSheet(_BRIGHT_STYLE)
        draft_layout.addWidget(draft_label)
        draft_link = EntityLink(
            "前往选秀中心",
            Route("draft", season=season_for_routes),
            navigate,
        )
        draft_link.setObjectName("draftPendingLink")
        self._draft_link = draft_link
        draft_layout.addWidget(draft_link)
        draft_layout.addStretch(1)
        layout.addWidget(draft_row)
        return frame

    def _build_decision_widget(self, name: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)
        keep_button = QPushButton("保留")
        keep_button.setObjectName("reviewKeepButton")
        keep_button.setCheckable(True)
        approve_button = QPushButton("采纳")
        approve_button.setObjectName("reviewApproveButton")
        approve_button.setCheckable(True)
        approve_button.setChecked(True)  # 默认采纳
        group = QButtonGroup(widget)
        group.setExclusive(True)
        group.addButton(keep_button)
        group.addButton(approve_button)
        layout.addWidget(keep_button)
        layout.addWidget(approve_button)
        self._review_buttons[name] = {"keep": keep_button, "approve": approve_button}
        return widget

    def _on_submit_reviews(self) -> None:
        service = self._context.service
        items = self._review_items
        if service is None or not items:
            return
        decisions: Dict[str, bool] = {}
        approved_count = 0
        for item in items:
            name = str(item.get("name", ""))
            buttons = self._review_buttons.get(name)
            approved = bool(buttons["approve"].isChecked()) if buttons else False
            decisions[name] = approved
            approved_count += 1 if approved else 0
        try:
            service.apply_ability_review(self.save_name(), decisions)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", f"能力审核提交失败：{exc}")
            return
        self._review_message = f"已提交 {len(decisions)} 项（采纳 {approved_count} 项）"
        self.refresh()
        # 让外壳重新加载快照：待办角标/模拟按钮立即更新，无需手动点刷新。
        reload_hook = self._context.request_save_reload
        if reload_hook is not None:
            try:
                reload_hook(self.save_name())
            except Exception:
                pass

    # -- 赛季时间线 -------------------------------------------------------------

    def _build_timeline_section(self, data: _SeasonData, navigate) -> QWidget:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(
            section_header(
                "赛季时间线",
                "完整 52 周赛历：单击任一周打开该周战报；已赛周高亮，冬窗/夏窗/附加赛/杯赛周按类型着色。",
            )
        )

        played = set(data.played_weeks)
        current_week = data.simulated_weeks if data.is_current else 0
        grid_holder = QWidget()
        grid = QGridLayout(grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for offset in range(TOTAL_WEEKS):
            week_number = offset + 1
            row, column = divmod(offset, _TIMELINE_COLUMNS)
            text = f"W{week_number}"
            if data.is_current and week_number == min(max(current_week, 1), TOTAL_WEEKS) and current_week > 0:
                text += "（当前）"
            link = EntityLink(text, Route("weekly_report", week=week_number), navigate)
            kind = _WEEK_CALENDAR[offset].kind
            color = _BRIGHT_WEEK_COLORS.get(kind)
            if data.is_current and current_week > 0 and week_number == min(max(current_week, 1), TOTAL_WEEKS):
                background = _BRIGHT_CURRENT_COLOR
            elif week_number in played:
                background = _BRIGHT_PLAYED_COLOR
            else:
                background = color or "transparent"
            link.setStyleSheet(
                f"QLabel#entityLink {{ color: #7dd3fc; background: {background};"
                " border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 6px;"
                " padding: 3px 8px; font-weight: 700; }"
            )
            self._week_links[week_number] = link
            grid.addWidget(link, row, column)
        for column in range(_TIMELINE_COLUMNS):
            grid.setColumnStretch(column, 1)
        layout.addWidget(grid_holder)
        layout.addWidget(self._build_timeline_legend())
        return frame

    def _build_timeline_legend(self) -> QWidget:
        legend = QWidget()
        layout = QHBoxLayout(legend)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        legend_colors = {
            "已赛周": "#2e6db4",
            "当前周": "#1167d8",
            "联赛周": "#3b6ea5",
            "杯赛周": "#7a5fc0",
            "冬窗": "#3a7d4e",
            "夏窗": "#a06a2c",
            "升级附加赛": "#b08a2e",
        }
        entries = tuple((label_text, legend_colors[label_text]) for label_text in (
            "已赛周", "当前周", "联赛周", "杯赛周", "冬窗", "夏窗", "升级附加赛",
        ))
        for label_text, color in entries:
            chip = QLabel(label_text)
            chip.setStyleSheet(
                f"background: {color}; color: #f2f6ff;"
                " border: 1px solid rgba(255, 255, 255, 0.22); border-radius: 7px;"
                " padding: 4px 11px; font-size: 12px; font-weight: 700;"
            )
            layout.addWidget(chip)
        layout.addStretch(1)
        return legend

    # -- 赛季级状态 -------------------------------------------------------------

    def _build_season_state_section(self, data: _SeasonData, navigate) -> QWidget:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        if data.archive is not None:
            layout.addWidget(section_header("赛季冠军", "该赛季已结束；单击球队名打开球队页，单击赛事名打开赛事页。"))
            archive = data.archive
            rows: List[Tuple[str, str, str]] = [
                ("一级联赛冠军", base.COMPETITION_PREMIER,
                 archive.premier_order[0].team.display_name if archive.premier_order else ""),
                ("次级联赛冠军", base.COMPETITION_SECOND,
                 archive.second_order[0].team.display_name if archive.second_order else ""),
                ("优胜者杯冠军", base.COMPETITION_WINNERS_CUP, archive.cup_champions.winners_cup or ""),
                ("挑战杯冠军", base.COMPETITION_CHALLENGE_CUP, archive.cup_champions.challenge_cup or ""),
                ("超级杯冠军", base.COMPETITION_SUPER_CUP, archive.cup_champions.super_cup or ""),
            ]
            for row_index, (title, competition, champion) in enumerate(rows, start=1):
                title_label = QLabel(title)
                title_label.setStyleSheet(_MUTED_STYLE)
                title_label.setFixedWidth(120)
                layout.addWidget(self._honor_row(title_label, competition, champion, data, navigate))
        else:
            layout.addWidget(section_header("当前积分榜榜首", "该赛季仍在进行中；单击球队名打开球队页，“进入赛事”打开赛事页。"))
            for competition, rows in (
                (base.COMPETITION_PREMIER, data.standings.get("premier") or ()),
                (base.COMPETITION_SECOND, data.standings.get("second") or ()),
            ):
                top = rows[0] if rows else None
                caption = QLabel(competition)
                caption.setStyleSheet(_BRIGHT_STYLE)
                caption.setFixedWidth(120)
                if top is None:
                    layout.addWidget(self._honor_row(caption, competition, "", data, navigate))
                    continue
                summary = QLabel(f"积分 {top.points} · 已赛 {top.played} 场")
                summary.setStyleSheet(_MUTED_STYLE)
                layout.addWidget(self._honor_row(caption, competition, top.team_name, data, navigate, tail=summary))
        return frame

    def _honor_row(self, lead: QWidget, competition: str, champion: str, data: _SeasonData, navigate, tail: Optional[QWidget] = None) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(lead)
        if champion:
            team_id = data.team_ids.get(champion)
            if team_id is not None:
                link = EntityLink(champion, Route("team", team=team_id, season=data.season_number), navigate)
                self._champion_links.append(link)
                layout.addWidget(link)
            else:
                label = QLabel(champion)
                label.setStyleSheet(_BRIGHT_STYLE)
                layout.addWidget(label)
        else:
            empty = QLabel("—")
            empty.setStyleSheet(_MUTED_STYLE)
            layout.addWidget(empty)
        if tail is not None:
            layout.addWidget(tail)
        competition_link = EntityLink(
            "进入赛事",
            Route("competition", competition=competition, season=data.season_number),
            navigate,
        )
        layout.addWidget(competition_link)
        layout.addStretch(1)
        return row

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


def _week_label(week_number: int) -> str:
    """第 n 周的赛历 label（1..52；越界归入“赛季已结束”）。"""
    if 1 <= week_number <= len(_WEEK_CALENDAR):
        return _WEEK_CALENDAR[week_number - 1].label
    return "赛季已结束"
