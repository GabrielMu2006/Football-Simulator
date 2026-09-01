"""比赛详情页（阶段 4，实施方案 §8.7：已赛完整赛后报告 / 未赛赛前页）。

Route：``Route("match", match=<match_id>)``。未赛与已赛是同一个稳定实体页。

滚动硬规则（§8.2）：
- 内容型详情页：外层单 ``QScrollArea`` 是本页唯一纵向滚动面；
- 关键事件用 QLabel 列表按原始顺序完整展开（不用 ``QTextEdit``，不截断）；
- 球员数据表 22 行（含六项全 0 的出场行，对应"球队参赛即当时注册阵容记
  出场"口径）完整展开：``EntityTable`` 按"表头 + 全部行"固定高度、纵向滚动
  条策略 AlwaysOff，不构成第二个纵向滚动面；
- 未赛比赛没有事件/球员数据小节（引擎未模拟，不虚构预测/阵容/伤停内容），
  显示"未赛"状态与两队当前赛季联赛摘要（``get_team_season_profile`` 的
  ``standings_row``，查询失败时兜底为解释性文案）。

上一场/下一场（§8.7"限定在当前筛选上下文中"）：``get_match_neighbors`` 的
上下文取该场比赛自身的赛事——详情路由不携带赛事参数，比赛自身赛事即当前
浏览上下文；``competition=None`` 会跨赛事跳转，不符合限定语义。

链接合同（§7.2）：主客队 → ``Route("team", team=<id>, season=<比赛赛季>)``；
球员列 → ``Route("player", player=<id>, season=<比赛赛季>)``；球员/球队列单击
可点（列级 delegate，青色 + hover 反馈），行激活（双击 / Enter）打开该行球员。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base
from football_simulator.queries import match_queries, team_queries
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import (
    LINK_COLOR,
    TEXT_COLOR_MUTED,
    TEXT_COLOR_SOFT,
    ColumnSpec,
    EmptyState,
    EntityLink,
    EntityTable,
    PageHeader,
)
from football_simulator.ui_v2.components.team_crest import TeamCrest
from football_simulator.ui_v2.design_tokens import ACCENT_SOFT, EVENT_TEXT_COLOR, SUCCESS_HIGHLIGHT
from football_simulator.ui_v2.navigation import Route
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase, PageContext
from football_simulator.ui_v2.widgets import section_header

_ROW_HEIGHT = 32
_HEADER_HEIGHT = 36
_TABLE_BORDER = 2  # EntityTable 样式表上下各 1px 边框

_PLAYER_COLUMNS = (
    ColumnSpec("player_name", "球员", width=210),
    ColumnSpec("team_name", "球队", width=180),
    ColumnSpec("appeared", "出场", alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("goals", "进球", alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("assists", "助攻", alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("chances_created", "创造机会", alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("successful_defenses", "成功防守", alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("successful_saves", "成功扑救", alignment=Qt.AlignmentFlag.AlignRight),
    ColumnSpec("clean_sheets", "零封", alignment=Qt.AlignmentFlag.AlignRight),
)

_MUTED_STYLE = f"color: {TEXT_COLOR_MUTED}; background: transparent;"
_BRIGHT_STYLE = "color: #f8fbff; background: transparent;"
_EVENT_STYLE = f"color: {EVENT_TEXT_COLOR}; background: transparent;"
_EVENT_GOAL_STYLE = f"color: {SUCCESS_HIGHLIGHT}; background: transparent; font-weight: 800;"
_EVENT_SAVE_STYLE = f"color: {ACCENT_SOFT}; background: transparent;"
_EVENT_DEFENSE_STYLE = f"color: {TEXT_COLOR_SOFT}; background: transparent;"


@dataclass(frozen=True)
class _PlayerRow:
    """球员数据行视图模型（仅引擎已有的六项统计 + 出场口径）。"""

    player_id: str
    player_name: str
    team_id: int
    team_name: str
    appeared: int
    goals: int
    assists: int
    chances_created: int
    successful_defenses: int
    successful_saves: int
    clean_sheets: int


def _to_player_row(line: match_queries.MatchPlayerLine) -> _PlayerRow:
    return _PlayerRow(
        player_id=line.player.player_id,
        player_name=line.player.display_name,
        team_id=line.team.team_id,
        team_name=line.team.display_name,
        appeared=1,
        goals=line.goals,
        assists=line.assists,
        chances_created=line.chances_created,
        successful_defenses=line.successful_defenses,
        successful_saves=line.successful_saves,
        clean_sheets=line.clean_sheets,
    )


# -- 列级链接 delegate（与球队详情页同一模式，页面内自持） ---------------------


class _LinkColumnDelegate(QStyledItemDelegate):
    """把一列文本渲染为实体链接（§7.2）：青色、hover 下划线、单击导航。

    行的其余区域仍走 EntityTable 默认行为（双击/Enter 打开行主路由），
    因此单击该列单元格时既有链接导航、又不影响整行选择。
    """

    def __init__(
        self,
        table: EntityTable,
        resolver: Callable[[object], Optional[Route]],
        alignment: Qt.AlignmentFlag,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._table = table
        self._resolver = resolver
        self._alignment = Qt.AlignmentFlag(alignment)

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
        font = opt.font
        font.setUnderline(bool(opt.state & QStyle.State.State_MouseOver))
        painter.setFont(font)
        painter.setPen(QColor(LINK_COLOR))
        rect = opt.rect.adjusted(8, 0, -8, 0)
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


class MatchDetailPage(EntityPageBase):
    """比赛详情：比分板 + 关键事件 + 球员数据（已赛）/ 两队赛季摘要（未赛）。"""

    def __init__(self, context: PageContext, parent: Optional[QWidget] = None) -> None:
        self._detail: Optional[match_queries.MatchDetail] = None
        self._match_label: str = ""
        self._season: Optional[int] = None
        self._neighbor_ids: Tuple[Optional[str], Optional[str]] = (None, None)
        self._prev_button: Optional[QPushButton] = None
        self._next_button: Optional[QPushButton] = None
        self._home_link: Optional[EntityLink] = None
        self._away_link: Optional[EntityLink] = None
        self._score_label: Optional[QLabel] = None
        self._info_label: Optional[QLabel] = None
        self._event_labels: List[QLabel] = []
        self._player_table: Optional[EntityTable] = None
        self._summary_links: List[EntityLink] = []
        self._summary_lines: Dict[int, QLabel] = {}
        self._error_state: Optional[EmptyState] = None
        self._table_delegates: list = []  # 页面生命周期内只增不清（见生命周期说明）
        super().__init__(context, parent)

    # -- UI 骨架（一次构建；内容在 refresh 中重建） ---------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(0)
        self._page_stack = QStackedWidget()
        layout.addWidget(self._page_stack, 1)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("matchDetailScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._page_stack.addWidget(self._scroll)

        self._error_state = EmptyState(
            "比赛不存在",
            "存档中找不到该比赛。可能该链接指向了另一个存档。",
        )
        self._page_stack.addWidget(self._error_state)

    # -- 数据刷新 -----------------------------------------------------------

    def refresh(self) -> None:
        route = self.current_route()
        if route is None or route.name != "match":
            return
        match_id = str(route.params.get("match", ""))
        try:
            with base.open_read_connection(self.save_name()) as conn:
                detail = match_queries.get_match_detail(conn, match_id)
                prev_id, next_id = match_queries.get_match_neighbors(
                    conn, match_id, competition=detail.match.competition
                )
                summaries = (
                    None
                    if detail.match.is_completed
                    else self._team_summaries(conn, detail.match)
                )
        except base.MissingSaveError:
            self._detail = None
            self._show_error(
                "未初始化存档",
                "当前还没有可用的存档数据库。请先在“存档”页新建或选择一个存档。",
            )
            return
        except KeyError:
            self._detail = None
            self._show_error(
                "比赛不存在",
                f"存档中找不到比赛 {match_id!r}。可能该链接指向了另一个存档。",
            )
            return
        self._detail = detail
        self._season = detail.match.season_number
        self._neighbor_ids = (prev_id, next_id)
        self._rebuild_content(detail, prev_id, next_id, summaries)

    def route_context(self) -> dict:
        if self._detail is None:
            return {}
        match = self._detail.match
        return {
            "match_label": self._match_label,
            "season": match.season_number,
            "week": match.week_number,
        }

    # -- 赛前页摘要查询（standings_row；失败兜底为 None） ---------------------

    @staticmethod
    def _team_summaries(conn, match: match_queries.MatchRow) -> Dict[int, Optional[dict]]:
        summaries: Dict[int, Optional[dict]] = {}
        for ref in (match.home, match.away):
            try:
                profile = team_queries.get_team_season_profile(conn, ref.team_id, match.season_number)
                standings = profile.standings_row
                summaries[ref.team_id] = {
                    "played": standings.played,
                    "wins": standings.wins,
                    "draws": standings.draws,
                    "losses": standings.losses,
                    "points": standings.points,
                    "rank": standings.rank,
                }
            except Exception:  # noqa: BLE001 - 摘要属锦上添花，任何查询失败都兜底为文案
                summaries[ref.team_id] = None
        return summaries

    # -- 内容重建 -------------------------------------------------------------

    def _rebuild_content(
        self,
        detail: match_queries.MatchDetail,
        prev_id: Optional[str],
        next_id: Optional[str],
        summaries: Optional[Dict[int, Optional[dict]]],
    ) -> None:
        match = detail.match
        completed = match.is_completed
        if completed:
            label = f"{match.home.display_name} {match.home_goals}-{match.away_goals} {match.away.display_name}"
        else:
            label = f"{match.home.display_name} vs {match.away.display_name}"
        self._match_label = label

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 16)
        layout.setSpacing(12)

        prev_button = QPushButton("← 上一场")
        prev_button.setObjectName("matchPrevButton")
        prev_button.setEnabled(prev_id is not None)
        prev_button.setToolTip("查看上一场比赛" if prev_id is not None else "没有上一场比赛")
        prev_button.clicked.connect(lambda _checked=False, target=prev_id: self._open_neighbor(target))
        next_button = QPushButton("下一场 →")
        next_button.setObjectName("matchNextButton")
        next_button.setEnabled(next_id is not None)
        next_button.setToolTip("查看下一场比赛" if next_id is not None else "没有下一场比赛")
        next_button.clicked.connect(lambda _checked=False, target=next_id: self._open_neighbor(target))
        self._prev_button = prev_button
        self._next_button = next_button

        context = {
            "match_label": label,
            "season": match.season_number,
            "week": match.week_number,
        }
        header = PageHeader(
            label,
            breadcrumbs=[],
            navigator=self._context.navigate,
            actions=[prev_button, next_button],
        )
        layout.addWidget(header)

        self._home_link = None
        self._away_link = None
        self._summary_links = []
        self._summary_lines = {}
        self._event_labels = []
        self._player_table = None
        layout.addWidget(self._build_scoreboard(match, completed))

        if completed:
            # 关键事件 / 球员数据 拆成两个页签；页签内容完整展开（表格不滚动）。
            self._match_tabs = QTabWidget(self)
            events_page = QWidget(self._match_tabs)
            events_layout = QVBoxLayout(events_page)
            events_layout.setContentsMargins(0, 0, 0, 0)
            events_layout.setSpacing(4)
            self._build_events(events_layout, detail.key_events)
            events_layout.addStretch(1)
            self._match_tabs.addTab(events_page, "关键事件")

            player_page = QWidget(self._match_tabs)
            player_layout = QVBoxLayout(player_page)
            player_layout.setContentsMargins(0, 0, 0, 0)
            player_layout.setSpacing(6)
            header_row = QHBoxLayout()
            header_row.addWidget(
                section_header(
                    "球员数据",
                    "两队当时注册阵容的全部出场记录（含六项全 0 行）；单击球员/球队名可继续跳转。",
                ),
                1,
            )
            header_row.addWidget(self._make_real_only_check(), 0, Qt.AlignmentFlag.AlignBottom)
            player_layout.addLayout(header_row)
            self._player_table = self._build_player_table(self._filtered_player_lines(detail.player_lines))
            player_layout.addWidget(self._player_table)
            player_layout.addStretch(1)
            self._match_tabs.addTab(player_page, "球员数据")
            layout.addWidget(self._match_tabs, 1)
        else:
            layout.addWidget(
                section_header(
                    "两队本赛季摘要",
                    f"第 {match.season_number} 赛季联赛口径：赛 / 胜 / 平 / 负 / 积分。该比赛尚未进行，不提供预测、阵容或伤停内容。",
                )
            )
            layout.addWidget(self._build_team_summaries(match, summaries or {}))

        layout.addStretch(1)
        self._replace_scroll_content(content)
        self._page_stack.setCurrentIndex(0)

    # -- 比分板 ---------------------------------------------------------------

    def _build_scoreboard(self, match: match_queries.MatchRow, completed: bool) -> QFrame:
        frame = QFrame()
        frame.setObjectName("cardFrame")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(16)

        home_caption = QLabel("主队")
        home_caption.setStyleSheet(_MUTED_STYLE)
        home_link = EntityLink(
            match.home.display_name,
            Route("team", team=match.home.team_id, season=match.season_number),
            self._context.navigate,
        )
        home_font = home_link.font()
        home_font.setPointSize(15)
        home_font.setBold(True)
        home_link.setFont(home_font)
        home_box = QVBoxLayout()
        home_box.setContentsMargins(0, 0, 0, 0)
        home_box.setSpacing(2)
        home_box.addWidget(home_caption)
        home_box.addWidget(TeamCrest(match.home.display_name, size=64))
        home_box.addWidget(home_link)
        home_holder = QWidget()
        home_holder.setLayout(home_box)
        row.addWidget(home_holder, 1)

        center_box = QVBoxLayout()
        center_box.setContentsMargins(0, 0, 0, 0)
        center_box.setSpacing(2)
        if completed:
            score_label = QLabel(f"{match.home_goals} - {match.away_goals}")
            score_label.setStyleSheet("font-size: 30px; font-weight: 900; color: #f8fbff; background: transparent;")
            status_caption = QLabel("已赛")
        else:
            score_label = QLabel("未赛")
            score_label.setStyleSheet(
                f"font-size: 24px; font-weight: 900; color: {TEXT_COLOR_MUTED}; background: transparent;"
            )
            status_caption = QLabel("该比赛尚未进行")
        score_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        status_caption.setStyleSheet(_MUTED_STYLE)
        status_caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        center_box.addWidget(score_label)
        center_box.addWidget(status_caption)
        center_holder = QWidget()
        center_holder.setLayout(center_box)
        row.addWidget(center_holder, 0)
        self._score_label = score_label

        away_caption = QLabel("客队")
        away_caption.setStyleSheet(_MUTED_STYLE)
        away_caption.setAlignment(Qt.AlignmentFlag.AlignRight)
        away_link = EntityLink(
            match.away.display_name,
            Route("team", team=match.away.team_id, season=match.season_number),
            self._context.navigate,
        )
        away_font = away_link.font()
        away_font.setPointSize(15)
        away_font.setBold(True)
        away_link.setFont(away_font)
        away_box = QVBoxLayout()
        away_box.setContentsMargins(0, 0, 0, 0)
        away_box.setSpacing(2)
        away_box.addWidget(away_caption)
        away_box.addWidget(TeamCrest(match.away.display_name, size=64))
        away_box.addWidget(away_link)
        away_holder = QWidget()
        away_holder.setLayout(away_box)
        row.addWidget(away_holder, 1)

        outer.addLayout(row)

        info_text = (
            f"{match.competition} · 第 {match.round_number} 轮 · 第 {match.week_number} 周 · "
            f"第 {match.season_number} 赛季 · {'已赛' if completed else '未赛'}"
        )
        info_label = QLabel(info_text)
        info_label.setStyleSheet(_MUTED_STYLE)
        info_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(info_label)
        self._info_label = info_label
        self._home_link = home_link
        self._away_link = away_link
        return frame

    # -- 关键事件（QLabel 列表完整展开） ---------------------------------------

    def _build_events(self, layout: QVBoxLayout, events: List[str]) -> None:
        if not events:
            note = QLabel("该场比赛没有记录到关键事件。")
            note.setStyleSheet(_MUTED_STYLE)
            layout.addWidget(note)
            return
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(4, 0, 0, 0)
        holder_layout.setSpacing(4)
        for index, text in enumerate(events, start=1):
            label = QLabel(f"{index}. {text}")
            label.setWordWrap(True)
            # 视觉区分事件类型：进球高亮、扑救/化解弱色强调；文本内容保持不变。
            if "进球" in text:
                label.setStyleSheet(_EVENT_GOAL_STYLE)
            elif "扑出" in text or "扑救" in text:
                label.setStyleSheet(_EVENT_SAVE_STYLE)
            elif "化解" in text or "成功防守" in text:
                label.setStyleSheet(_EVENT_DEFENSE_STYLE)
            else:
                label.setStyleSheet(_EVENT_STYLE)
            holder_layout.addWidget(label)
            self._event_labels.append(label)
        layout.addWidget(holder)

    def _make_real_only_check(self) -> QCheckBox:
        """球员数据“只显示真实球员”复选框：状态跨刷新保持（存于页面实例）。"""
        check = getattr(self, "_real_only_check", None)
        if check is None:
            check = QCheckBox("只显示真实球员")
            check.setObjectName("playerRealOnlyCheck")
            check.setToolTip("隐藏默认球员的出场行（真实球员始终显示）")
            check.setChecked(True)  # 默认只显示真实球员
            check.toggled.connect(self._on_real_only_toggled)
            self._real_only_check = check
        return check

    def _on_real_only_toggled(self, _checked: bool) -> None:
        # 只重拉当前比赛数据并重建内容，保持复选框状态与滚动位置语义。
        self.refresh()

    def _filtered_player_lines(self, lines):
        if self._make_real_only_check().isChecked():
            return [line for line in lines if line.player.is_real]
        return lines

    # -- 球员数据表（22 行完整展开，唯一性外层滚动负责翻看） ---------------------

    def _build_player_table(self, lines: List[match_queries.MatchPlayerLine]) -> EntityTable:
        rows = [_to_player_row(line) for line in lines]
        table = EntityTable(_PLAYER_COLUMNS, navigator=self._context.navigate, parent=self)
        view = table.view
        view.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        view.horizontalHeader().setFixedHeight(_HEADER_HEIGHT)
        # 完整展开：固定高度 = 表头 + 全部行 + 边框；纵向滚动条策略关闭，
        # 不构成第二个纵向滚动面（§8.2 规则 3）。
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.set_rows(rows, route_for_row=self._player_route_for_row)
        # delegate 生命周期：parent=view（与视图同生命周期）+ 页面引用列表
        # 只增不清（shiboken 会在引用消失时删除对象，提前清空会让旧视图的
        # 列映射悬空；见 team_profile_page 的生命周期说明）。
        player_delegate = _LinkColumnDelegate(
            table,
            lambda row: Route("player", player=row.player_id, season=int(self._season or 0)),
            Qt.AlignmentFlag.AlignLeft,
            view,
        )
        view.setItemDelegateForColumn(0, player_delegate)
        team_delegate = _LinkColumnDelegate(
            table,
            lambda row: Route("team", team=row.team_id, season=int(self._season or 0)),
            Qt.AlignmentFlag.AlignLeft,
            view,
        )
        view.setItemDelegateForColumn(1, team_delegate)
        self._table_delegates.extend((player_delegate, team_delegate))
        table.setFixedHeight(_HEADER_HEIGHT + len(rows) * _ROW_HEIGHT + _TABLE_BORDER)
        return table

    def _player_route_for_row(self, row: _PlayerRow):
        return Route("player", player=row.player_id, season=int(self._season or 0))

    # -- 赛前页：两队当前赛季摘要 ----------------------------------------------

    def _build_team_summaries(
        self, match: match_queries.MatchRow, summaries: Dict[int, Optional[dict]]
    ) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        for side, ref in (("主队", match.home), ("客队", match.away)):
            frame = QFrame()
            frame.setObjectName("cardFrame")
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(14, 12, 14, 12)
            inner.setSpacing(6)
            caption = QLabel(side)
            caption.setStyleSheet(_MUTED_STYLE)
            inner.addWidget(caption)
            link = EntityLink(
                ref.display_name,
                Route("team", team=ref.team_id, season=match.season_number),
                self._context.navigate,
            )
            font = link.font()
            font.setPointSize(13)
            font.setBold(True)
            link.setFont(font)
            inner.addWidget(link)
            summary = summaries.get(ref.team_id)
            if summary is None:
                line = QLabel("暂无积分榜数据")
                line.setStyleSheet(_MUTED_STYLE)
            else:
                rank = summary.get("rank")
                rank_text = "" if rank is None else f" · 排名第 {rank}"
                line = QLabel(
                    f"赛 {summary['played']} · 胜 {summary['wins']} · 平 {summary['draws']}"
                    f" · 负 {summary['losses']} · 积分 {summary['points']}{rank_text}"
                )
                line.setStyleSheet(_BRIGHT_STYLE)
            line.setWordWrap(True)
            inner.addWidget(line)
            row.addWidget(frame, 1)
            self._summary_links.append(link)
            self._summary_lines[ref.team_id] = line
        return holder

    # -- 交互 -----------------------------------------------------------------

    def _open_neighbor(self, match_id: Optional[str]) -> None:
        if match_id:
            self.navigate(Route("match", match=match_id))

    # -- 内容替换与错误状态 ------------------------------------------------------

    def _replace_scroll_content(self, widget: QWidget) -> None:
        old = self._scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._scroll.setWidget(widget)

    def _show_error(self, title: str, description: str) -> None:
        if self._error_state is not None:
            self._page_stack.removeWidget(self._error_state)
            self._error_state.deleteLater()
        self._error_state = EmptyState(title, description)
        self._page_stack.addWidget(self._error_state)
        self._page_stack.setCurrentWidget(self._error_state)
