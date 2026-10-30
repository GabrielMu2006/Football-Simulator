"""应用外壳：侧栏、顶栏、路由、异步推进与安全关闭。"""

from __future__ import annotations

import traceback
import os
import sys
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.app.service import GameService, MODE_LABELS, MODES
from football_simulator.persistence_v2 import store
from football_simulator.ui import tokens as t
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.components import Badge, label
from football_simulator.ui.pages.competitions import (
    CompetitionsPage,
    CompetitionDetailPage,
    CompetitionEVPPage,
    SeasonPage,
)
from football_simulator.ui.pages.core import DashboardPage, MatchDetailPage, MatchesPage, WeeklyPage
from football_simulator.ui.pages.entities import (
    HistoryPage,
    PlayerProfilePage,
    PlayersPage,
    TeamProfilePage,
    TeamsPage,
)
from football_simulator.ui.pages.honors import (
    AnnualTop20Page,
    HistoryCompetitionPage,
    HonorsHubPage,
    PlayerHonorsPage,
    RecordsPage,
    TeamHonorsPage,
)
from football_simulator.ui.pages.tools import DraftPage, SavesPage, TransfersPage
from football_simulator.ui.pages.press import PressPage, ArticlePage
from football_simulator.ui.theme import APP_STYLE, apply_theme


class NavList(QListWidget):
    """按真实行高报告 sizeHint；窗口足够时九项全部可见。"""

    def sizeHint(self):
        self.ensurePolished()
        height = sum(self.sizeHintForRow(index) for index in range(self.count()))
        height += 2 * self.frameWidth() + 2
        base = super().sizeHint()
        return base.__class__(base.width(), max(height, 1))

    def minimumSizeHint(self):
        hint = self.sizeHint()
        if macos.enabled():
            # The scrollable sidebar must fit its viewport; QListWidget's
            # default 256px minimum would clip the right edge on macOS.
            hint.setWidth(0)
        return hint


NAV_ENTRIES = [
    ("dashboard", "总览"),
    ("matches", "比赛"),
    ("competitions", "赛事"),
    ("teams", "球队"),
    ("players", "球员"),
    ("history", "历史"),
]
TOOL_ENTRIES = [("transfers", "转会"), ("draft", "选秀"), ("saves", "存档")]

#: 设计基准尺寸（正常屏幕维持既有布局）。
DESIGN_WIDTH = 1440
DESIGN_HEIGHT = 900
DESIGN_MIN_WIDTH = 1200
DESIGN_MIN_HEIGHT = 760
#: 低可用区域（如 1080p 133%/150% 缩放）下的下限：主区纵向滚动、侧栏整体
#: 可滚动，保证导航与主操作仍可达。
FLOOR_MIN_WIDTH = 900
FLOOR_MIN_HEIGHT = 480
#: 窗口边框/任务栏余量，避免初始窗口贴边被裁切。
SCREEN_MARGIN = 16


def initial_window_geometry(available_width: int, available_height: int) -> tuple:
    """按屏幕可用区域计算 (最小宽, 最小高, 初始宽, 初始高)。

    全部使用 Qt 逻辑像素：Qt 6 已按 DPR 缩放，这里不再手工乘缩放系数，
    也不设置 QT_SCALE_FACTOR。可用区域小于设计下限时，下限降到可用区域
    （不低于 FLOOR_*），把纵向空间交给主区滚动与侧栏滚动，而不是让窗口
    超出屏幕、把底部按钮挤到屏幕外。
    """
    if available_width <= 0 or available_height <= 0:
        return DESIGN_MIN_WIDTH, DESIGN_MIN_HEIGHT, DESIGN_WIDTH, DESIGN_HEIGHT
    usable_width = max(FLOOR_MIN_WIDTH, int(available_width) - SCREEN_MARGIN)
    usable_height = max(FLOOR_MIN_HEIGHT, int(available_height) - SCREEN_MARGIN)
    min_width = min(DESIGN_MIN_WIDTH, usable_width)
    min_height = min(DESIGN_MIN_HEIGHT, usable_height)
    width = min(DESIGN_WIDTH, usable_width)
    height = min(DESIGN_HEIGHT, usable_height)
    return min_width, min_height, width, height

ROUTE_CLASSES = {
    "press": PressPage,
    "press_article": ArticlePage,
    "dashboard": DashboardPage,
    "weekly": WeeklyPage,
    "matches": MatchesPage,
    "match": MatchDetailPage,
    "competitions": CompetitionsPage,
    "competition": CompetitionDetailPage,
    "competition_evp": CompetitionEVPPage,
    "season": SeasonPage,
    "teams": TeamsPage,
    "team": TeamProfilePage,
    "players": PlayersPage,
    "player": PlayerProfilePage,
    "history": HistoryPage,
    "records": RecordsPage,
    "annual_top20": AnnualTop20Page,
    "honors": HonorsHubPage,
    "team_honors": TeamHonorsPage,
    "player_honors": PlayerHonorsPage,
    "history_competition": HistoryCompetitionPage,
    "transfers": TransfersPage,
    "draft": DraftPage,
    "saves": SavesPage,
}

NAV_PARENT = {
    "press": "press",
    "press_article": "press",
    "dashboard": "dashboard",
    "weekly": "dashboard",
    "match": "matches",
    "matches": "matches",
    "competition": "competitions",
    "competition_evp": "competitions",
    "competitions": "competitions",
    "season": "competitions",
    "team": "teams",
    "teams": "teams",
    "player": "players",
    "players": "players",
    "history": "history",
    "records": "history",
    "annual_top20": "history",
    "honors": "history",
    "team_honors": "history",
    "player_honors": "history",
    "history_competition": "history",
    "transfers": "transfers",
    "draft": "draft",
    "saves": "saves",
}


class AdvanceWorker(QThread):
    week_done = Signal(str, int, object)
    failed = Signal(str, str)
    run_finished = Signal(str)

    def __init__(self, service: GameService, save_name: str, token: int, mode: str) -> None:
        super().__init__()
        self.service = service
        self.save_name = save_name
        self.token = token
        self.mode = mode
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            while not self._stop:
                result = self.service.advance_week(self.save_name)
                self.week_done.emit(self.save_name, self.token, result)
                if self._stop:
                    break
                if result.get("pause"):
                    break
                snapshot = self.service.snapshot(self.save_name)
                if snapshot["season_complete"] or not snapshot["actions"]["actions"]["advance"]:
                    break
                if self.mode != "auto":
                    break
        except Exception as exc:  # noqa: BLE001 - 错误必须安全传递到主线程
            self.failed.emit(self.save_name, f"{exc}\n{traceback.format_exc(limit=4)}")
        finally:
            self.run_finished.emit(self.save_name)


class MainWindow(QMainWindow):
    #: 兼容旧的类属性引用；值来自模块级常量（同一份设计基准）。
    DESIGN_WIDTH = DESIGN_WIDTH
    DESIGN_HEIGHT = DESIGN_HEIGHT
    DESIGN_MIN_WIDTH = DESIGN_MIN_WIDTH
    DESIGN_MIN_HEIGHT = DESIGN_MIN_HEIGHT
    FLOOR_MIN_WIDTH = FLOOR_MIN_WIDTH
    FLOOR_MIN_HEIGHT = FLOOR_MIN_HEIGHT
    SCREEN_MARGIN = SCREEN_MARGIN

    def __init__(self, service: GameService) -> None:
        super().__init__()
        self.service = service
        self._save_name: Optional[str] = None
        self._world: Optional[dict] = None
        self._snapshot: Optional[dict] = None
        self._pages: Dict[str, QWidget] = {}
        self._back: list = []
        self._forward: list = []
        self._current_route: Optional[Tuple[str, dict]] = None
        self._generation = 0
        self._worker: Optional[AdvanceWorker] = None
        self._finishing_press = False
        self._worker_started = False
        self._last_error = ""
        self._resetting_pages = False
        self._route_transitioning = False
        self._season_transitioning = False
        self._view_states: Dict[tuple, list] = {}
        self._route_generation = 0
        self.ui_drafts: Dict[str, Dict[str, bool]] = {}
        self.press = None
        self._nav_entries = list(NAV_ENTRIES)
        # Availability is macOS-only; collection/generation still requires
        # explicit opt-in for each save. Stable bundles are separate artifacts.
        if os.environ.get('FS_PRESS_DEV') != '0' and sys.platform == 'darwin':
            from football_simulator.ui.press_controller import PressController
            self.press = PressController(self)
            self._nav_entries.append(('press', '报刊'))
        from football_simulator.version import APP_VERSION
        self.setWindowTitle(f"Football Simulator V{APP_VERSION} — 联赛观察" if self.press else "Football Simulator — 联赛观察")
        self._apply_initial_geometry()
        apply_theme(QApplication.instance())
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._install_shortcuts()
        initial = self.service.current_save_name()
        self._save_name = None
        if initial and store.database_path(initial).exists():
            try:
                self._save_name = initial
                if macos.enabled():
                    self.service.prepare_save(initial)
                self._reload_world()
                self.navigate("dashboard")
            except Exception as exc:  # 旧格式/损坏存档：明确提示，不删除、不迁移
                self._save_name = None
                self._world = None
                self._snapshot = None
                self.navigate("saves")
                self._show_error(f"存档“{initial}”不是当前新格式，已拒绝读取：{exc}")
        else:
            self.navigate("saves")
        self._refresh_chrome()

    # -- 构造 ------------------------------------------------------------

    def _apply_initial_geometry(self) -> None:
        """按当前屏幕可用区域限制初始尺寸。

        正常屏幕维持既有的 1200×760 下限与 1440×900 初始尺寸；当可用区域更小
        （Windows 1080p 在 150%/200% 缩放下很常见）时下限降到工作区，主区纵向
        滚动、侧栏整体可滚动，避免固定最小高度把按钮推到屏幕外。Qt 6 已按
        DPR 处理高 DPI，这里只使用逻辑像素，不手工换算或设置 QT_SCALE_FACTOR。
        """
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        width = available.width() if available is not None else 0
        height = available.height() if available is not None else 0
        min_width, min_height, target_width, target_height = initial_window_geometry(width, height)
        self.setMinimumSize(min_width, min_height)
        self.resize(target_width, target_height)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        layout.addLayout(right, 1)

    def _build_sidebar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Sidebar")
        frame.setFixedWidth(204)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # 侧栏整体可滚动：可用高度不足时，工具与观察模式仍可达；
        # 正常尺寸下内容完整显示，不出现滚动条。
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setObjectName("SidebarScroll")
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setFrameShape(QFrame.NoFrame)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.sidebar_scroll.viewport().setAutoFillBackground(False)
        container = QWidget()
        container.setObjectName("SidebarBody")
        container.setAutoFillBackground(False)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 18, 10, 18)
        layout.setSpacing(t.S8)
        brand = QLabel("FOOTBALL\nSIMULATOR")
        brand.setObjectName("Eyebrow")
        brand.setWordWrap(False)
        layout.addWidget(brand)
        layout.addSpacing(t.S12)
        self.nav = NavList()
        self.nav.setObjectName("NavList")
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for key, text in self._nav_entries:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, key)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.nav)
        layout.addSpacing(t.S8)
        layout.addWidget(label("工具", "Eyebrow"))
        self.tools = NavList()
        self.tools.setObjectName("NavList")
        self.tools.setFrameShape(QFrame.NoFrame)
        self.tools.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tools.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for key, text in TOOL_ENTRIES:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, key)
            self.tools.addItem(item)
        self.tools.currentRowChanged.connect(self._on_tool)
        self.tools.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.tools)
        layout.addStretch(1)
        self.mode_box = QComboBox()
        for mode in MODES:
            self.mode_box.addItem(MODE_LABELS[mode], mode)
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        layout.addWidget(label("观察模式", "Meta"))
        layout.addWidget(self.mode_box)
        self.status_line = label("就绪", "Meta", wrap=True)
        layout.addWidget(self.status_line)
        self.sidebar_scroll.setWidget(container)
        outer.addWidget(self.sidebar_scroll)
        return frame

    def _build_topbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("TopBar")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S10, t.S20, t.S10)
        layout.setSpacing(t.S8)
        row1 = QHBoxLayout()
        self.back_button = QPushButton("←")
        self.forward_button = QPushButton("→")
        for button in (self.back_button, self.forward_button):
            button.setObjectName("Ghost")
            button.setFixedWidth(36)
        self.back_button.clicked.connect(self.go_back)
        self.forward_button.clicked.connect(self.go_forward)
        row1.addWidget(self.back_button)
        row1.addWidget(self.forward_button)
        self.breadcrumb = label("总览", "Meta")
        row1.addWidget(self.breadcrumb, 0)
        row1.addStretch(1)
        row1.addWidget(label("存档", "Meta"))
        self.save_box = QComboBox()
        self.save_box.setMinimumWidth(180)
        self.save_box.currentIndexChanged.connect(self._save_changed)
        row1.addWidget(self.save_box)
        self.season_button = QPushButton("第 1 赛季 · 第 1 周")
        self.season_button.setObjectName("Secondary")
        self.season_button.clicked.connect(lambda: self.navigate("season"))
        row1.addWidget(self.season_button)
        row2 = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索球队或真实球员（Ctrl+K）" if macos.enabled() else "搜索球队或球员（Ctrl+K）")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._search_activated)
        row2.addWidget(self.search, 1)
        self.error_label = label("", "ErrorText", wrap=True)
        self.error_label.hide()
        row2.addWidget(self.error_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(110)
        self.progress.hide()
        row2.addWidget(self.progress)
        self.stop_button = QPushButton("完成本周后停止")
        self.stop_button.setObjectName("Secondary")
        self.stop_button.clicked.connect(self._request_stop)
        self.stop_button.hide()
        row2.addWidget(self.stop_button)
        self.primary = QPushButton("推进一周")
        self.primary.setMinimumWidth(150)
        self.primary.clicked.connect(self._primary_action)
        row2.addWidget(self.primary)
        layout.addLayout(row1)
        layout.addLayout(row2)
        self.breadcrumb_row = row1
        return frame

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.go_back)
        QShortcut(QKeySequence("Alt+Right"), self, activated=self.go_forward)
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.refresh_all())

    # -- 世界与快照 ------------------------------------------------------

    @property
    def has_save(self) -> bool:
        return bool(self._save_name and store.database_path(self._save_name).exists())

    def current_save_name(self) -> Optional[str]:
        return self._save_name

    def world(self) -> dict:
        if self._world is None:
            if not self.has_save:
                return {"teams": {}, "players": {}, "season": {"fixtures": [], "pending": {}}, "season_number": 1}
            self._world = self.service.load_world(self._save_name)
        return self._world

    def team_name(self, team_id: str) -> str:
        world = self.world()
        team = world.get("teams", {}).get(team_id)
        return team["club_en"] if team else "未知球队"

    def team_label(self, team_id: str) -> str:
        world = self.world()
        team = world.get("teams", {}).get(team_id)
        if not team:
            return "未知球队"
        return team["club_en"]

    def _reload_world(self) -> None:
        self._world = self.service.load_world(self._save_name) if self.has_save else None
        self._snapshot = self.service.snapshot(self._save_name) if self.has_save else None

    # -- 路由 ------------------------------------------------------------

    def navigate(self, name: str, **params) -> None:
        if name in ('press', 'press_article') and self.press is None:
            return
        if name not in ROUTE_CLASSES or self._route_transitioning:
            return
        if name not in ("saves",) and not self.has_save:
            name, params = "saves", {}
        if self._current_route is not None and self._current_route != (name, params):
            self._back.append(self._current_route)
            self._forward.clear()
        self._apply_route(name, params)

    def _route_state_key(self, name: str, params: dict) -> tuple:
        import json

        return (self._save_name, name, json.dumps(params, ensure_ascii=False, sort_keys=True, default=str))

    def _save_view_state(self) -> None:
        if self._current_route is None:
            return
        page = self._pages.get(self._current_route[0])
        if page is None:
            return
        try:
            self._view_states[self._route_state_key(*self._current_route)] = _capture_scroll(page)
        except RuntimeError:
            return

    def _apply_route(self, name: str, params: dict) -> None:
        if self._route_transitioning:
            return
        self._route_transitioning = True
        nav_was_blocked = self.nav.blockSignals(True)
        tools_was_blocked = self.tools.blockSignals(True)
        try:
            if self._current_route is not None and self._current_route != (name, params):
                self._save_view_state()
            self._route_generation += 1
            generation = self._route_generation
            state_key = self._route_state_key(name, params)
            self._current_route = (name, params)
            self.error_label.setText("")
            self.error_label.hide()
            page = self._pages.get(name)
            if page is not None:
                try:
                    from shiboken6 import isValid

                    if not isValid(page):
                        page = None
                except ImportError:
                    pass
            if page is None:
                page = ROUTE_CLASSES[name](self)
                self._pages[name] = page
            # 存档切换/延迟删除后，页面对象可能仍在缓存但已不在 stack 中；导航时
            # 必须确保它重新成为当前页，避免出现空白内容区。
            if self.stack.indexOf(page) < 0:
                self.stack.addWidget(page)
            self.stack.setCurrentWidget(page)
            page.params = dict(params)
            try:
                snapshot = self.service.snapshot(self._save_name) if self.has_save else None
                if snapshot is not None:
                    self._snapshot = snapshot
                page.refresh(snapshot, params)
            except Exception as exc:  # noqa: BLE001 - 页面错误必须可见
                self._show_error(f"页面加载失败：{exc}")
            saved = self._view_states.get(state_key)
            if saved:
                QTimer.singleShot(
                    0,
                    lambda: _restore_scroll(page, saved) if generation == self._route_generation else None,
                )
            else:
                QTimer.singleShot(
                    0,
                    lambda: _restore_scroll(page, []) if generation == self._route_generation else None,
                )
            self._sync_nav(name)
            self._update_breadcrumb(name, params)
            self._refresh_chrome()
            page.setFocus()
        finally:
            self.nav.blockSignals(nav_was_blocked)
            self.tools.blockSignals(tools_was_blocked)
            self._route_transitioning = False

    def _route_parent(self, name: str) -> str:
        return NAV_PARENT.get(name, "dashboard")

    def _sync_nav(self, name: str) -> None:
        parent = self._route_parent(name)
        entries = [key for key, _ in self._nav_entries]
        tools = [key for key, _ in TOOL_ENTRIES]
        if parent in entries:
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(entries.index(parent))
            self.nav.blockSignals(False)
            self.tools.blockSignals(True)
            self.tools.setCurrentRow(-1)
            self.tools.blockSignals(False)
        elif parent in tools:
            self.tools.blockSignals(True)
            self.tools.setCurrentRow(tools.index(parent))
            self.tools.blockSignals(False)
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(-1)
            self.nav.blockSignals(False)

    def _on_nav(self, row: int) -> None:
        if row < 0 or self._resetting_pages or self._route_transitioning:
            return
        key = self.nav.item(row).data(Qt.UserRole)
        self.navigate(key)

    def _on_tool(self, row: int) -> None:
        if row < 0 or self._resetting_pages or self._route_transitioning:
            return
        key = self.tools.item(row).data(Qt.UserRole)
        self.navigate(key)

    def _update_breadcrumb(self, name: str, params: dict) -> None:
        parent = self._route_parent(name)
        texts = {key: text for key, text in [*self._nav_entries, *TOOL_ENTRIES]}
        trail = ["总览"] if parent == "dashboard" else [texts.get(parent, parent)]
        if name != parent:
            titles = {
                "weekly": "周报",
                "match": "比赛详情",
                "competition": "赛事详情",
                "team": "球队详情",
                "player": "球员详情",
                "season": "赛季",
                "records": "历史纪录",
                "honors": "荣誉馆",
                "team_honors": "球队荣誉馆",
                "player_honors": "球员荣誉馆",
                "history_competition": "历史赛事",
                "annual_top20": "年度 Top20",
                "press_article": "阅读",
            }
            trail.append(titles.get(name, name))
            if name == 'weekly' and self.press:
                trail[-1] = '赛况'
        self.breadcrumb.setText(" / ".join(trail))

    def go_back(self) -> None:
        if not self._back:
            return
        name, params = self._back.pop()
        self._forward.append(self._current_route)
        self._apply_route(name, params)

    def go_forward(self) -> None:
        if not self._forward:
            return
        name, params = self._forward.pop()
        self._back.append(self._current_route)
        self._apply_route(name, params)

    # -- 顶栏状态 --------------------------------------------------------

    def _refresh_chrome(self) -> None:
        self._sync_save_box()
        if self.press and self.has_save:
            try:
                unread = self.press.library().unread_count()
                for index in range(self.nav.count()):
                    item = self.nav.item(index)
                    if item.data(Qt.UserRole) == 'press':
                        item.setText(f'报刊 · {unread} 未读' if unread else '报刊')
            except Exception:
                pass  # The press page reports its own errors without blocking play.
        if self.mode_box.currentData() is not None and self._snapshot:
            mode = self._snapshot.get("mode")
            index = self.mode_box.findData(mode)
            if index >= 0:
                self.mode_box.blockSignals(True)
                self.mode_box.setCurrentIndex(index)
                self.mode_box.blockSignals(False)
        if self._snapshot:
            next_week = int(self._snapshot['week'])
            if int(self._snapshot['season']) and (self._snapshot.get('season_complete')):
                season_text = f"第 {self._snapshot['season']} 赛季 · 赛季结束"
            elif next_week <= 1:
                season_text = f"第 {self._snapshot['season']} 赛季 · 首周待赛"
            else:
                season_text = f"第 {self._snapshot['season']} 赛季 · 已完成第 {next_week - 1} 周"
            self.season_button.setText(season_text)
            action = self._snapshot["primary_action"]
            self.primary.setText(action["label"])
            self.primary.setEnabled(action["enabled"] and not self._worker_running())
            self.primary.setToolTip(action.get("reason", ""))
            self.back_button.setEnabled(bool(self._back))
            self.forward_button.setEnabled(bool(self._forward))
            pending = self._snapshot["pending"]
            bits = []
            if pending.get("ability_review"):
                bits.append(f"能力审核 {pending['ability_review']}")
            if pending.get("transfer_review"):
                bits.append(f"转会 {pending['transfer_review']}")
            if pending.get("draft"):
                bits.append("选秀")
            if bits:
                self.status_line.setText("待办：" + " · ".join(bits))
            else:
                completed = max(0, int(self._snapshot['week']) - 1)
                if macos.enabled() and self._snapshot.get('season_complete'):
                    completed = int(self._snapshot['week'])
                self.status_line.setText(f"已完成第 {completed} 周 · 就绪")
        else:
            self.season_button.setText("未初始化")
            self.primary.setText("新建存档")
            self.primary.setEnabled(True)
            self.status_line.setText("还没有已初始化的存档")

    def _sync_save_box(self) -> None:
        rows = [item for item in self.service.list_saves() if item["initialized"]]
        self.save_box.blockSignals(True)
        self.save_box.clear()
        for item in rows:
            self.save_box.addItem(f"{item['save_name']} · 第 {item['season']} 季", item["save_name"])
        index = self.save_box.findData(self._save_name)
        if index >= 0:
            self.save_box.setCurrentIndex(index)
        self.save_box.setVisible(bool(rows))
        self.save_box.blockSignals(False)

    # -- 主操作与异步 ----------------------------------------------------

    def _primary_action(self) -> None:
        if self._worker_running():
            return
        if not self.has_save:
            self.navigate("saves")
            return
        action = (self._snapshot or {}).get("primary_action", {}).get("id")
        if action == "advance":
            self._start_worker()
        elif action in ("resolve_ability", "resolve_transfers"):
            self.navigate("transfers")
        elif action == "run_draft":
            self.navigate("draft")
        elif action == "start_next_season":
            self.start_next_season()
        else:
            self.refresh_all()

    def _start_worker(self) -> None:
        if self._worker_running() or not self.has_save:
            return
        mode = (self._snapshot or {}).get("mode", "key_pause")
        token = self._generation
        worker = AdvanceWorker(self.service, self._save_name, token, mode)
        worker.week_done.connect(self._on_week_done)
        worker.failed.connect(self._on_worker_failed)
        worker.run_finished.connect(self._on_worker_finished)
        self._worker = worker
        self._worker_started = False
        self.stop_button.show()
        self.stop_button.setText("完成本周后停止")
        self.progress.show()
        self.primary.setEnabled(False)
        self.status_line.setText("正在推进…")
        self._last_error = ""
        self.error_label.setText("")
        if self.press:
            self.press.begin_simulation()
        worker.start()

    def _worker_running(self) -> bool:
        return self._finishing_press or (self._worker is not None and self._worker.isRunning())

    def _request_stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_stop()
            self.stop_button.setText("将在本周完成后停止…")
            self.stop_button.setEnabled(False)

    def _on_week_done(self, save_name: str, token: int, result: dict) -> None:
        # 工作线程仍在提交下一周；这里只更新进度文案，避免与写事务并发读。
        if save_name != self._save_name or token != self._generation:
            return
        self.status_line.setText(f"第 {result['week']} 周已完成")

    def _on_worker_failed(self, save_name: str, message: str) -> None:
        if save_name != self._save_name:
            return
        self._last_error = message
        self._show_error("推进失败：" + message.splitlines()[0])
        self._reload_world()
        self._refresh_current_page(restore_scroll=False)
        self._refresh_chrome()

    def _on_worker_finished(self, save_name: str) -> None:
        worker = self._worker
        self._worker = None
        self.stop_button.hide()
        self.stop_button.setEnabled(True)
        if worker is not None:
            worker.deleteLater()
        if self.press:
            self._finishing_press = True
            self.status_line.setText("比赛已结束，正在撰写报刊…")
            self.press.finish_simulation(lambda: self._finish_advance(save_name))
        else:
            self._finish_advance(save_name)

    def _finish_advance(self, save_name: str) -> None:
        self._finishing_press = False
        self.progress.hide()
        self.progress.setRange(0, 0)
        if save_name == self._save_name:
            self._reload_world()
            self._refresh_current_page(restore_scroll=False)
            self._refresh_chrome()
            if not self._last_error:
                self.status_line.setText("报刊生成已暂停，请在报刊中心查看" if self.press and self.press.auto_paused else "就绪")

    def start_next_season(self) -> bool:
        """页内与顶栏共用的合法换季入口；确认/前置条件/提交只有这一份。"""
        if getattr(self, "_season_transitioning", False):
            return False
        if not self.has_save:
            self.navigate("saves")
            return False
        if self._worker_running():
            QMessageBox.information(self, "正在推进", "请先完成或停止当前推进，再开始下一赛季。")
            return False
        pending = (self._snapshot or {}).get("pending") or {}
        if any(bool(value) for value in pending.values()):
            QMessageBox.information(self, "仍有待办", "请先处理能力审核、选秀或转会待办，再开始下一赛季。")
            self.refresh_all()
            return False
        actions = (self._snapshot or {}).get("actions") or {}
        allowed = actions.get("actions") or actions
        if not allowed.get("start_next_season"):
            QMessageBox.information(self, "当前不能换季", "赛季尚未完成或当前状态不允许开始下一赛季。")
            self.refresh_all()
            return False
        answer = QMessageBox.question(
            self,
            "开始下一赛季",
            "本赛季成绩已冻结。开始下一赛季会按升降级与上季排名生成新赛季赛程与杯赛签表。",
        )
        if answer != QMessageBox.Yes:
            return False
        self._season_transitioning = True
        try:
            self.service.start_next_season(self._save_name)
        except Exception as exc:
            self._season_transitioning = False
            self._show_error(f"开始新赛季失败：{exc}")
            return False
        self._season_transitioning = False
        self._reload_world()
        self._reset_pages()
        self.navigate("dashboard")
        self.status_line.setText("新赛季已开始")
        return True

    # 兼容旧调用名；两处入口都走公开动作。
    def _start_next_season(self) -> bool:
        return self.start_next_season()

    # -- 存档 ------------------------------------------------------------

    def _save_changed(self, index: int) -> None:
        if index < 0:
            return
        save_name = self.save_box.itemData(index)
        if save_name and save_name != self._save_name:
            self.open_save(save_name)

    def _save_changed_guard(self) -> bool:
        if self._worker_running():
            QMessageBox.information(self, "正在推进", "请先停止当前推进，再切换存档。")
            self._sync_save_box()
            return False
        return True

    def _reset_pages(self) -> None:
        """清空页面与导航历史；期间屏蔽侧栏/堆栈信号，避免焦点变化触发中途导航。"""
        self._resetting_pages = True
        self.nav.blockSignals(True)
        self.tools.blockSignals(True)
        self.stack.blockSignals(True)
        try:
            self._pages.clear()
            while self.stack.count():
                widget = self.stack.widget(0)
                if macos.enabled():
                    widget.hide()
                self.stack.removeWidget(widget)
                # Keep macOS pages owned by the stack until DeferredDelete.
                # Reparenting a complete cached page to None creates a native
                # top-level lifetime transition; Cocoa/AX can still reference
                # its descendants during the season-confirmation event.
                if not macos.enabled():
                    widget.setParent(None)
                widget.deleteLater()
            self.nav.setCurrentRow(-1)
            self.tools.setCurrentRow(-1)
        finally:
            self.stack.blockSignals(False)
            self.nav.blockSignals(False)
            self.tools.blockSignals(False)
            self._resetting_pages = False
        self._back.clear()
        self._forward.clear()
        self._view_states.clear()
        self._current_route = None

    def open_save(self, save_name: str) -> None:
        if not self._save_changed_guard():
            return
        if not store.database_path(save_name).exists():
            self.navigate("saves")
            return
        try:
            if macos.enabled():
                self.service.prepare_save(save_name)
            self.service.set_current_save(save_name)
            self._save_name = save_name
            self._generation += 1
            self._reload_world()
        except Exception as exc:
            self._show_error(f"打开存档失败：{exc}")
            return
        self._reset_pages()
        self.navigate("dashboard")

    def create_save(self, name: str) -> None:
        self.service.create_save(name)
        self._save_name = name
        self.service.set_current_save(name)
        self._reload_world()
        # Do not rebuild the clicked save form during its accessibility action.
        if self._current_route is None or self._current_route[0] != "saves":
            self.navigate("saves")
        else:
            # Cocoa may still be serving the button's accessibility action as
            # this slot returns. Let it finish before changing the table model.
            def show_created():
                if self._current_route and self._current_route[0] == "saves" and self._save_name == name:
                    self._pages["saves"].show_created_save()
                    self._refresh_chrome()
                    self.status_line.setText("存档已建立，请初始化赛季")
            QTimer.singleShot(600, show_created)
        self.status_line.setText("存档已建立，请初始化赛季")

    def initialize_save(self, name: str) -> None:
        self.service.initialize_save(name)
        self._save_name = name
        self._generation += 1
        self._reload_world()
        self._reset_pages()
        self.navigate("dashboard")
        self.status_line.setText("第 1 赛季已初始化")

    def delete_save(self, name: str) -> None:
        self.service.delete_save(name)
        if name == self._save_name:
            self._save_name = self.service.current_save_name()
            self._reload_world()
            self._reset_pages()
            self.navigate("dashboard" if self.has_save else "saves")
        self.refresh_all()

    def restore_trash(self, path, name: str) -> None:
        self.service.restore_trash(path, name)
        self.refresh_all()

    def empty_trash(self) -> None:
        self.service.empty_trash()
        self.refresh_all()

    def export_save(self, name: str, path) -> None:
        self.service.export_save(name, path)

    def import_save(self, path, name: str) -> None:
        self.service.import_save(path, name)
        self._save_name = name
        self._reload_world()
        self._reset_pages()
        self.navigate("dashboard")

    # -- 其他命令 --------------------------------------------------------

    def get_ui_draft(self, key: str, defaults: Dict[str, bool]) -> Dict[str, bool]:
        draft = self.ui_drafts.get(key)
        if draft is None:
            draft = dict(defaults)
            self.ui_drafts[key] = draft
        else:
            for item_key, value in defaults.items():
                draft.setdefault(item_key, value)
        return dict(draft)

    def set_ui_draft(self, key: str, decisions: Dict[str, bool]) -> None:
        self.ui_drafts[key] = dict(decisions)

    def clear_ui_draft(self, key: str) -> None:
        self.ui_drafts.pop(key, None)

    def apply_ability_review(self, decisions: Dict[str, bool]) -> None:
        self.service.apply_ability_review(self._save_name, decisions)
        self.refresh_all()

    def apply_transfers(self, decisions: Dict[str, bool]) -> None:
        self.service.apply_transfer_reviews(self._save_name, decisions)
        self.refresh_all()

    def apply_draft(self, selected=None) -> None:
        self.service.apply_draft(self._save_name, selected)
        self.refresh_all()

    def toggle_watch(self, entity_id: str) -> None:
        self.service.toggle_watch(self._save_name, entity_id)
        self.refresh_all()

    def open_pending(self, action: str) -> None:
        if action in ("resolve_ability", "resolve_transfers"):
            self.navigate("transfers")
        elif action == "run_draft":
            self.navigate("draft")
        else:
            self._primary_action()

    def _mode_changed(self, index: int) -> None:
        if not self.has_save or index < 0:
            return
        mode = self.mode_box.itemData(index)
        try:
            self.service.set_mode(self._save_name, mode)
        except Exception as exc:
            self._show_error(f"切换模式失败：{exc}")
            return
        self._reload_world()
        self._refresh_chrome()
        self.status_line.setText(f"观察模式：{MODE_LABELS.get(mode, mode)}")

    # -- 刷新 ------------------------------------------------------------

    def refresh_all(self) -> None:
        # 刷新保留当前路由、页签、筛选与滚动；只有切档才清理跨档状态。
        self._reload_world()
        if self._current_route is None:
            self.navigate("dashboard" if self.has_save else "saves")
            return
        self._refresh_current_page(restore_scroll=True)

    def _refresh_current_page(self, restore_scroll: bool = True) -> None:
        if self._route_transitioning:
            return
        self._route_transitioning = True
        nav_was_blocked = self.nav.blockSignals(True)
        tools_was_blocked = self.tools.blockSignals(True)
        try:
            name, params = self._current_route or ("dashboard", {})
            page = self._pages.get(name)
            if page is None:
                # 释放保护后再走正常导航，避免 _apply_route 被重入保护拦截。
                self._route_transitioning = False
                self.nav.blockSignals(nav_was_blocked)
                self.tools.blockSignals(tools_was_blocked)
                self.navigate(name, **params)
                return
            scroll_state = _capture_scroll(page) if restore_scroll else []
            try:
                page.refresh(self._snapshot, params)
            except Exception as exc:  # noqa: BLE001
                self._show_error(f"刷新失败：{exc}")
            if scroll_state:
                QTimer.singleShot(0, lambda: _restore_scroll(page, scroll_state))
            self._refresh_chrome()
        finally:
            self.nav.blockSignals(nav_was_blocked)
            self.tools.blockSignals(tools_was_blocked)
            self._route_transitioning = False

    def _show_error(self, message: str) -> None:
        self._last_error = message
        self.error_label.setText(message)
        self.error_label.show()
        self.status_line.setText("出错：" + message.splitlines()[0])

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _search_activated(self) -> None:
        text = self.search.text().strip().lower()
        if not text or not self.has_save:
            return
        world = self.world()
        for team_id, team in world["teams"].items():
            if text in (team.get("name_zh") or "").lower() or text in team["club_en"].lower():
                self.navigate("team", team_id=team_id)
                return
        for player_id, data in world["players"].items():
            if macos.enabled() and not data.get("is_real"):
                continue
            name = (data.get("name") or "").lower()
            if name and text in name:
                self.navigate("player", player_id=player_id)
                return
        self._show_error(f"没有找到与“{self.search.text().strip()}”匹配的球队或球员。")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.press and self.press.running:
            self.press.prepare_close()
            self.status_line.setText('正在保存当前段落，完成本次请求后关闭；其余部分下次续写。')
            event.ignore()
            return
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_stop()
            if not self._worker.wait(15000):
                QMessageBox.warning(self, "正在保存", "当前推进仍在提交事务，窗口将在完成后关闭。")
                self._worker.wait()
        if self.press:self.press.timer.stop()
        event.accept()


def _capture_scroll(page) -> list:
    from PySide6.QtWidgets import QAbstractScrollArea, QTableWidget

    state = []
    for area in page.findChildren(QAbstractScrollArea):
        row = area.currentRow() if isinstance(area, QTableWidget) else -1
        state.append((area.verticalScrollBar().value(), row))
    return state


def _restore_scroll(page, state) -> None:
    from PySide6.QtWidgets import QAbstractScrollArea, QTableWidget

    areas = page.findChildren(QAbstractScrollArea)
    for index, area in enumerate(areas):
        try:
            value = state[index][0] if index < len(state) else 0
            area.verticalScrollBar().setValue(int(value))
            if isinstance(area, QTableWidget):
                row = state[index][1] if index < len(state) else -1
                if 0 <= int(row) < area.rowCount():
                    area.selectRow(int(row))
        except (RuntimeError, IndexError, TypeError):
            continue
