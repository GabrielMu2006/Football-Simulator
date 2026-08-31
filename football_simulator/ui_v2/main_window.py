"""路由驱动的应用外壳（阶段 4，Agent D2）。

对应实施方案 §7.1 路由模型 / §7.2 全局链接合同 / §7.3 状态保留 / §8.1 应用外壳：

- ``Router``（纯 Python，见 ``navigation.py``）是页面切换的唯一事实源。侧栏
  点击、面包屑、待办跳转、搜索选中全部走 ``router.navigate(...)``；Router
  观察者回调负责切换 ``QStackedWidget``、同步侧栏高亮镜像、刷新顶部面包屑
  与后退/前进按钮状态。页面切换绝不遍历侧栏文本，也不以显示名为键。
- 侧栏收敛为 10 项一级导航；"本周战报"改为顶部栏按钮 + 带周次的 Route。
- 路由 → 页面注册表见 ``_load_page_class`` / ``_PARALLEL_PAGE_SPECS``：并行
  Agent 正在重写的 6 个实体页面（比赛/比赛详情/球队/球队详情/球员/球员详情）
  用延迟导入 + 回退模式加载；import 失败、类不存在或尚未继承
  ``EntityPageBase``（即仍是旧契约页面）时以 ``EmptyState("页面迁移中")``
  占位，保证外壳在任何时刻都可运行，页面落地后自动生效。
- 旧 legacy 页面保持原有构造参数与 ``set_snapshot`` 流程；它们的
  ``_open_team`` / ``_open_player`` / ``_open_match_center_latest`` 回调被
  全部实现为路由导航
  （不经过 Router，行为与旧版一致）。
- 滚动硬规则（§8.2）：外壳自身不含任何滚动容器；中央区域每个 Route 只有
  一个主内容面。
"""

from __future__ import annotations

import importlib
import traceback
from typing import Dict, Optional, Tuple, Type

from PySide6.QtCore import QPointF, QRectF, QSize, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPixmap, QPolygonF, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from football_simulator.queries import base as query_base
from football_simulator.queries.base import open_read_connection
from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2 import navigation
from football_simulator.ui_v2.components import EmptyState, EntityLink
from football_simulator.ui_v2.components.global_search import GlobalSearchBox
from football_simulator.ui_v2.navigation import Route, Router
from football_simulator.ui_v2.pages.entity_page_base import EntityPageBase, PageContext
from football_simulator.ui_v2.services import SimulatorUIService


# ---------------------------------------------------------------------------
# 导航与路由注册表
# ---------------------------------------------------------------------------

# §8.1：侧栏一级导航收敛为 10 项（"本周战报"移到顶部栏按钮）。
NAV_ITEMS = [
    ("首页", "dashboard"),
    ("赛季", "season_overview"),
    ("比赛", "matches"),
    ("赛事", "competition"),
    ("球队", "teams"),
    ("球员", "players"),
    ("转会", "transfers"),
    ("选秀", "draft"),
    ("历史", "history"),
    ("存档", "saves"),
]

_NAV_KEYS = tuple(key for _, key in NAV_ITEMS)

# 侧栏图标符号（几何/运动符号，Item 文本保持不变以兼容测试与高亮逻辑）。
_NAV_ICON_SYMBOLS = {
    "dashboard": "⌂",
    "season_overview": "▦",
    "matches": "⚽",
    "competition": "◆",
    "teams": "⚑",
    "players": "●",
    "transfers": "⇄",
    "draft": "★",
    "history": "▣",
    "saves": "▤",
}


def _nav_icon(symbol: str) -> QIcon:
    """生成 34×34 的圆角大图标（深色底 + 青色符号）。"""
    size = 34
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#122238"))
    painter.drawRoundedRect(QRectF(1.0, 1.0, size - 2.0, size - 2.0), 10.0, 10.0)
    painter.setPen(QColor("#7dd3fc"))
    font = QFont()
    font.setPixelSize(20)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(
        QRectF(0.0, 0.0, size, size),
        Qt.AlignmentFlag.AlignCenter,
        symbol,
    )
    painter.end()
    return QIcon(pixmap)


class _ArrowButton(QPushButton):
    """大箭头导航按钮（后退/前进）：圆角底 + 粗箭头，悬停高亮。"""

    def __init__(self, direction: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._direction = direction  # -1 左箭头（后退）/ +1 右箭头（前进）
        self.setFixedSize(48, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        hovered = enabled and self.underMouse()
        painter.setPen(Qt.PenStyle.NoPen)
        if not enabled:
            painter.setBrush(QColor("#1a2536"))
        elif hovered:
            painter.setBrush(QColor("#1f3a5f"))
        else:
            painter.setBrush(QColor("#122238"))
        painter.drawRoundedRect(QRectF(1.5, 1.5, self.width() - 3, self.height() - 3), 12.0, 12.0)

        if not enabled:
            arrow_color = QColor("#5a6b80")
        elif hovered:
            arrow_color = QColor("#ffffff")
        else:
            arrow_color = QColor("#7dd3fc")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(arrow_color)
        cx = self.width() // 2
        cy = self.height() // 2
        head_half = 12.0
        shaft_half = 7.0
        shaft_len = 16.0

        if self._direction < 0:
            tip = QPointF(cx - 10, cy)
            base_x = cx + 6
            shaft_rect = QRectF(base_x - 2, cy - shaft_half, shaft_len + 4, shaft_half * 2)
        else:
            tip = QPointF(cx + 10, cy)
            base_x = cx - 6
            shaft_rect = QRectF(base_x + 2 - shaft_len, cy - shaft_half, shaft_len + 4, shaft_half * 2)

        head = QPolygonF(
            [tip, QPointF(base_x, cy - head_half), QPointF(base_x, cy + head_half)]
        )
        painter.drawPolygon(head)
        painter.drawRect(shaft_rect)

# 实体详情路由 → 所属一级导航（侧栏高亮镜像用）。
_SIDEBAR_PARENT_BY_ROUTE = {
    "match": "matches",
    "team": "teams",
    "player": "players",
}

# 切存档时视为"实体详情路由"：路由参数指向旧存档的实体 ID，需导航回主页。
_ENTITY_DETAIL_ROUTES = frozenset(_SIDEBAR_PARENT_BY_ROUTE)

# 并行 Agent 正在重写的实体页面（延迟加载 + 回退占位）。
# 全部实现统一构造签名 __init__(context: PageContext, parent=None)。
_PARALLEL_PAGE_SPECS: Dict[str, Tuple[str, str]] = {
    "matches": ("matches_page", "MatchCenterPage"),
    "match": ("match_detail_page", "MatchDetailPage"),
    "teams": ("teams_page", "TeamsPage"),
    "team": ("team_profile_page", "TeamProfilePage"),
    "players": ("players_page", "PlayersPage"),
    "player": ("player_profile_page", "PlayerProfilePage"),
    # 阶段 5：新页面落地前 _load_page_class 返回 None（legacy 类不继承
    # EntityPageBase），自动回退到已装配的 legacy 页面。
    "competition": ("competition_page", "CompetitionPage"),
    "dashboard": ("dashboard_page", "DashboardPage"),
    "weekly_report": ("weekly_report_page", "WeeklyReportPage"),
    "season_overview": ("season_overview_page", "SeasonOverviewPage"),
    "transfers": ("transfers_page", "TransfersPage"),
    "draft": ("draft_page", "DraftPage"),
    "history": ("history_page", "HistoryPage"),
    "saves": ("saves_page", "SavesPage"),
}

_PAGE_CLASS_CACHE: Dict[Tuple[str, str], Optional[type]] = {}


def _load_page_class(module_name: str, class_name: str) -> Optional[type]:
    """延迟加载并行 Agent 的实体页面类。

    - import 失败 / 类不存在 / 类尚未继承 ``EntityPageBase``（即该模块仍是
      旧契约页面，尚未迁移）→ 返回 ``None``，调用方显示"页面迁移中"占位；
    - 结果按 (module, class) 缓存，避免重复导入开销。
    """

    cache_key = (module_name, class_name)
    if cache_key in _PAGE_CLASS_CACHE:
        return _PAGE_CLASS_CACHE[cache_key]
    cls: Optional[type] = None
    try:
        module = importlib.import_module(f"football_simulator.ui_v2.pages.{module_name}")
        candidate = getattr(module, class_name, None)
    except Exception:
        candidate = None
    if isinstance(candidate, type) and issubclass(candidate, EntityPageBase):
        cls = candidate
    _PAGE_CLASS_CACHE[cache_key] = cls
    return cls


def _snapshot_has_pending(snapshot: SaveSnapshot) -> bool:
    return bool(snapshot.pending_ability_review) or bool(
        snapshot.pending_transfer_review
    ) or snapshot.pending_draft.get("status") == "awaiting_input"


class _SimulateWorker(QThread):
    """后台线程批量推进（§12.5：模拟期间 GUI 不冻结）。

    mode：
    - one：只推进一周（保持旧行为）；
    - until_pending：连续推进到出现能力/转会/选秀待办或赛季结束；
    - until_season_end：连续推进到第 52 周/赛季结束，可被待办拦停。
    状态机的 SQLite 事务由线程内自建连接完成；期间 UI 端查询会按
    busy_timeout 短暂等待，不会静默覆盖。
    """

    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    stopped = Signal()

    def __init__(
        self,
        service: "SimulatorUIService",
        save_name: str,
        mode: str = "one",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._save_name = save_name
        self._mode = mode

    def run(self) -> None:
        result = None
        try:
            while True:
                result = self._service.simulate_week(self._save_name)
                total = len(result.snapshot.weeks)
                current = result.snapshot.current_week
                self.progress.emit(current, total, result.week.label)
                if self._mode == "one":
                    break
                if result.season_completed_now:
                    break
                if _snapshot_has_pending(result.snapshot):
                    break
                if self.isInterruptionRequested():
                    break
        except Exception as exc:  # noqa: BLE001 - 与外壳错误对话框口径一致
            self.failed.emit(str(exc))
            return
        if result is None:
            self.failed.emit("模拟没有产生结果。")
            return
        self.succeeded.emit(result)


class MainWindow(QMainWindow):
    """侧栏 + 顶部上下文栏 + 路由分发的桌面数据工作台外壳。"""

    def __init__(self, service: SimulatorUIService) -> None:
        super().__init__()
        self.service = service
        self.snapshot: SaveSnapshot | None = None
        self.router = Router()
        self.router.observe(self._on_route_changed)
        self._pages: Dict[str, QWidget] = {}
        self._nav_syncing = False
        self._init_in_progress = False
        self._simulate_in_progress = False
        self._simulate_mode = "one"
        self.setWindowTitle("Football Simulator UI v2")
        self.resize(1680, 980)
        self.setMinimumSize(1440, 860)
        self._build_ui()
        self.router.navigate(Route("dashboard"))
        self._load_save(self.service.current_save_name())

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_menu_bar()
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(18)
        self.setCentralWidget(root)

        root_layout.addWidget(self._build_nav_panel(), 0)
        root_layout.addLayout(self._build_content_area(), 1)

    def _build_menu_bar(self) -> None:
        """系统菜单栏（UI#10）：文件 / 推进 / 视图 / 帮助。"""
        self.menu_bar = QMenuBar(self)
        self.setMenuBar(self.menu_bar)

        def add(menu, text, callback, shortcut=None):
            action = menu.addAction(text)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            return action

        file_menu = self.menu_bar.addMenu("文件")
        add(file_menu, "新建存档", lambda: self.router.navigate(Route("saves")))
        add(file_menu, "打开存档目录", self._open_save_directory)
        file_menu.addSeparator()
        add(file_menu, "退出", lambda: self.close(), "Ctrl+Q")

        advance_menu = self.menu_bar.addMenu("推进")
        add(advance_menu, "模拟下一周", lambda: self._start_simulation("one"), "Ctrl+Return")
        add(advance_menu, "模拟到下一待办", lambda: self._start_simulation("until_pending"), "Ctrl+Shift+Return")
        add(advance_menu, "模拟到赛季末", lambda: self._start_simulation("until_season_end"), "Ctrl+Alt+Return")
        add(advance_menu, "本周战报", self._open_weekly_report, "Ctrl+Shift+W")

        view_menu = self.menu_bar.addMenu("视图")
        add(view_menu, "刷新", lambda: self._load_save(self._current_save_name()), "Ctrl+R")

        help_menu = self.menu_bar.addMenu("帮助")
        add(help_menu, "打开存档目录", self._open_save_directory)
        add(help_menu, "关于 Football Simulator", self._show_about)

    def _open_save_directory(self) -> None:
        try:
            directory = self.service.save_directory()
        except Exception:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于 Football Simulator UI v2",
            "Football Simulator UI v2\n本地足球联赛模拟器（数据工作台）\n"
            "路线：主队观赛模式 · 双级联赛 · 三杯赛 · 转会/选秀/荣誉",
        )

    def _build_nav_panel(self) -> QFrame:
        nav_panel = QFrame()
        nav_panel.setObjectName("navPanel")
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(20, 20, 20, 20)
        nav_layout.setSpacing(14)

        title = QLabel("Football Simulator")
        title.setObjectName("titleLabel")
        subtitle = QLabel("UI 增强版 v2")
        subtitle.setObjectName("subtitleLabel")
        nav_layout.addWidget(title)
        nav_layout.addWidget(subtitle)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setIconSize(QSize(34, 34))
        for label, key in NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            item.setIcon(_nav_icon(_NAV_ICON_SYMBOLS[key]))
            self.nav_list.addItem(item)
        # 侧栏只是导航入口和高亮镜像：点击 → router.navigate；高亮由 Router
        # 观察者写回（_nav_syncing 防止程序化同步触发二次导航）。
        self.nav_list.currentRowChanged.connect(self._on_nav_row_changed)
        self.nav_list.itemClicked.connect(self._on_nav_item_clicked)
        nav_layout.addWidget(self.nav_list, 1)
        return nav_panel

    def _build_content_area(self) -> QVBoxLayout:
        content_wrapper = QVBoxLayout()
        content_wrapper.setSpacing(18)
        content_wrapper.addWidget(self._build_top_bar(), 0)

        content_panel = QFrame()
        content_panel.setObjectName("contentPanel")
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(18, 18, 18, 18)
        self.stack = QStackedWidget()
        self._build_pages()
        content_layout.addWidget(self.stack)
        content_wrapper.addWidget(content_panel, 1)
        return content_wrapper

    def _build_top_bar(self) -> QFrame:
        """顶部上下文栏（§8.1）：两行 —— 导航行 + 状态/操作行。"""
        bar = QFrame()
        bar.setObjectName("statusPanel")
        self.top_bar = bar
        bar_layout = QVBoxLayout(bar)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        bar_layout.setSpacing(8)

        # 第一行：后退 / 前进 / 面包屑 / 全局搜索
        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self.back_button = _ArrowButton(-1)
        self.back_button.setToolTip("后退")
        self.back_button.setEnabled(False)
        self.back_button.clicked.connect(self.router.back)
        self.forward_button = _ArrowButton(1)
        self.forward_button.setToolTip("前进")
        self.forward_button.setEnabled(False)
        self.forward_button.clicked.connect(self.router.forward)
        self.breadcrumb_bar = QWidget()
        self.breadcrumb_layout = QHBoxLayout(self.breadcrumb_bar)
        self.breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
        self.breadcrumb_layout.setSpacing(6)
        self.search_box = GlobalSearchBox(
            self._current_save_name,
            self._current_season,
            self.router.navigate,
        )
        self.search_box.setFixedWidth(260)
        # 全局搜索快捷键：Ctrl+K / Cmd+K（macOS 下 Meta 即 Command）。
        self._search_shortcuts = [
            QShortcut(QKeySequence("Ctrl+K"), self),
            QShortcut(QKeySequence("Meta+K"), self),
        ]
        for shortcut in self._search_shortcuts:
            shortcut.activated.connect(self._focus_global_search)
        nav_row.addWidget(self.back_button)
        nav_row.addWidget(self.forward_button)
        nav_row.addWidget(self.breadcrumb_bar, 1)
        nav_row.addWidget(self.search_box, 0)
        bar_layout.addLayout(nav_row)

        # 第二行：存档选择器 / 赛季周次状态 / 待办入口 / 模拟 / 本周战报 / 刷新
        control_row = QHBoxLayout()
        control_row.setSpacing(10)
        control_row.addWidget(QLabel("存档"))
        self.save_picker = QComboBox()
        self.save_picker.setMinimumWidth(150)
        self.save_picker.currentTextChanged.connect(self._load_save)
        self.refresh_save_choices()
        self.status_label = QLabel("还没有载入存档。")
        self.status_label.setObjectName("subtitleLabel")
        self.init_button = QPushButton("初始化赛季")
        self.init_button.clicked.connect(self._initialize_current_save)
        self.simulate_button = QPushButton("模拟下一周")
        self.simulate_button.setObjectName("simulateButton")
        self.simulate_button.clicked.connect(self._on_simulate_clicked)
        self.weekly_button = QPushButton("本周战报")
        self.weekly_button.setToolTip("查看最近一个已模拟周次的战报")
        self.weekly_button.clicked.connect(self._open_weekly_report)
        # 批量推进菜单：到下一待办 / 到赛季末（用户确认 #9）。
        self.advance_button = QToolButton()
        self.advance_button.setObjectName("advanceButton")
        self.advance_button.setText("推进 ▾")
        self.advance_button.setToolTip("批量推进：到下一待办 / 到赛季末")
        self.advance_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.advance_menu = QMenu(self.advance_button)
        self.advance_menu.addAction("模拟到下一待办", lambda: self._start_simulation("until_pending"))
        self.advance_menu.addAction("模拟到赛季末", lambda: self._start_simulation("until_season_end"))
        self.advance_button.setMenu(self.advance_menu)
        self.advance_button.clicked.connect(lambda: self._start_simulation("until_pending"))
        self.pending_button = QPushButton("处理待办")
        self.pending_button.setEnabled(False)
        self.pending_button.clicked.connect(self._focus_pending_workflow)
        self.reload_button = QPushButton("刷新")
        self.reload_button.clicked.connect(lambda: self._load_save(self._current_save_name()))
        control_row.addWidget(self.save_picker)
        control_row.addWidget(self.status_label, 1)
        control_row.addWidget(self.init_button)
        control_row.addWidget(self.pending_button)
        control_row.addWidget(self.simulate_button)
        control_row.addWidget(self.advance_button)
        control_row.addWidget(self.weekly_button)
        control_row.addWidget(self.reload_button)
        bar_layout.addLayout(control_row)
        return bar

    def _focus_global_search(self) -> None:
        """聚焦全局搜索框并全选已有文本（Cmd/Ctrl+K）。"""
        self.search_box.line_edit.setFocus()
        self.search_box.line_edit.selectAll()

    def _build_pages(self) -> None:
        """初始化页面装配。所有路由页面均为 EntityPageBase 新契约，由
        ``_create_page`` 经延迟注册表按需构造并注入 PageContext。"""
        self._pages: Dict[str, QWidget] = {}
        self._page_context = PageContext(
            save_name_provider=self._current_save_name,
            navigate=self.router.navigate,
            route_provider=lambda: self.router.current,
            page_state_get=self.router.get_page_state,
            page_state_set=self.router.set_page_state,
            service=self.service,
            request_save_reload=self._load_save,
        )

    # ------------------------------------------------------------------
    # 路由分发（Router 是页面切换的唯一事实源）
    # ------------------------------------------------------------------

    def _on_route_changed(self, route: Route, cause: str) -> None:
        self._apply_route(route)
        self.back_button.setEnabled(self.router.can_back)
        self.forward_button.setEnabled(self.router.can_forward)

    def _apply_route(self, route: Route) -> None:
        page = self._ensure_page(route.name)
        self.stack.setCurrentWidget(page)
        self._apply_to_page(page, route)
        self._sync_sidebar(route)
        self._update_breadcrumbs(route)

    def _apply_to_page(self, page: QWidget, route: Route) -> None:
        """把路由应用到页面：实体页走新契约，competition hub 走页签选择，
        其余 legacy 页面按旧契约 set_snapshot。页面异常不拖垮外壳。"""
        if isinstance(page, EntityPageBase) or hasattr(page, "apply_route"):
            try:
                page.apply_route(route)
            except Exception:
                traceback.print_exc()
        elif hasattr(page, "set_snapshot"):
            try:
                page.set_snapshot(self.snapshot)
            except Exception:
                traceback.print_exc()

    def _ensure_page(self, route_name: str) -> QWidget:
        page = self._pages.get(route_name)
        if page is not None:
            return page
        page = self._create_page(route_name)
        self._pages[route_name] = page
        self.stack.addWidget(page)
        return page

    def _create_page(self, route_name: str) -> QWidget:
        """路由 → 页面注册表：

        - legacy 路由（dashboard/weekly_report/season_overview/competition/
          transfers/draft/history/saves）→ 已装配的 legacy 页面实例；
        - matches/match/teams/team/players/player → 延迟加载并行页面并注入
          PageContext；加载失败（页面未落地 / 仍是旧契约）→ 占位页。
        """
        spec = _PARALLEL_PAGE_SPECS.get(route_name)
        if spec is not None:
            cls = _load_page_class(*spec)
            if cls is not None:
                try:
                    return cls(self._page_context)
                except Exception:
                    traceback.print_exc()
        return EmptyState(
            "页面迁移中",
            "该页面正在迁移到新的导航外壳，功能将在迁移完成后自动恢复。",
        )

    # ------------------------------------------------------------------
    # 侧栏（导航入口 + 高亮镜像）
    # ------------------------------------------------------------------

    def _on_nav_row_changed(self, index: int) -> None:
        if self._nav_syncing:
            return
        self._navigate_sidebar_row(index)

    def _on_nav_item_clicked(self, item: QListWidgetItem) -> None:
        # 鼠标点击"当前已高亮"的行时 currentRowChanged 不会触发，这里兜底；
        # 与行变化同时触发时由 Router 的同路由去重保证只导航一次。
        if self._nav_syncing:
            return
        self._navigate_sidebar_row(self.nav_list.row(item))

    def _navigate_sidebar_row(self, index: int) -> None:
        item = self.nav_list.item(index)
        if item is None:
            return
        route = self._sidebar_route(str(item.data(Qt.UserRole)))
        if route is not None:
            self.router.navigate(route)

    def _sidebar_route(self, key: str) -> Optional[Route]:
        """一级导航的默认路由（带当前赛季 / 当前赛事参数）。"""
        season = self._current_season()
        if key == "dashboard":
            return Route("dashboard")
        if key == "season_overview":
            return Route("season_overview", season=season)
        if key == "matches":
            return Route("matches", season=season)
        if key == "competition":
            return Route("competition", competition=query_base.COMPETITION_PREMIER, season=season)
        if key == "teams":
            return Route("teams")
        if key == "players":
            return Route("players")
        if key == "transfers":
            return Route("transfers", season=season)
        if key == "draft":
            return Route("draft", season=season)
        if key == "history":
            if self.snapshot is not None:
                return Route("history", season=season)
            return Route("history")
        if key == "saves":
            return Route("saves")
        return None

    def _sidebar_row_for_route(self, route: Route) -> int:
        key = _SIDEBAR_PARENT_BY_ROUTE.get(route.name, route.name)
        if key not in _NAV_KEYS:
            return -1
        for index in range(self.nav_list.count()):
            item = self.nav_list.item(index)
            if item is not None and item.data(Qt.UserRole) == key:
                return index
        return -1

    def _sync_sidebar(self, route: Route) -> None:
        self._nav_syncing = True
        try:
            self.nav_list.setCurrentRow(self._sidebar_row_for_route(route))
        finally:
            self._nav_syncing = False

    # ------------------------------------------------------------------
    # 面包屑
    # ------------------------------------------------------------------

    def _update_breadcrumbs(self, route: Route) -> None:
        context: dict = {}
        page = self._pages.get(route.name)
        if page is not None and hasattr(page, "route_context"):
            try:
                context = page.route_context() or {}
            except Exception:
                context = {}
        while self.breadcrumb_layout.count():
            item = self.breadcrumb_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for index, crumb in enumerate(navigation.breadcrumbs(route, context)):
            if index:
                separator = QLabel("›")
                separator.setObjectName("subtitleLabel")
                self.breadcrumb_layout.addWidget(separator)
            if crumb.route is not None:
                self.breadcrumb_layout.addWidget(
                    EntityLink(crumb.label, crumb.route, self.router.navigate)
                )
            else:
                current = QLabel(crumb.label)
                current.setStyleSheet("font-weight: 700; background: transparent;")
                self.breadcrumb_layout.addWidget(current)
        self.breadcrumb_layout.addStretch(1)

    # ------------------------------------------------------------------
    # 存档生命周期
    # ------------------------------------------------------------------

    def refresh_save_choices(self) -> None:
        current = self.save_picker.currentText()
        self.save_picker.blockSignals(True)
        self.save_picker.clear()
        saves = self.service.available_saves()
        if not saves:
            saves = [self.service.current_save_name()]
        self.save_picker.addItems(saves)
        if current and current in saves:
            self.save_picker.setCurrentText(current)
        self.save_picker.blockSignals(False)

    def _load_save(self, save_name: str) -> None:
        if not save_name:
            return
        try:
            state = self.service.load_state(save_name)
        except Exception as exc:
            QMessageBox.critical(self, "Football Simulator UI v2", str(exc))
            return
        if self.save_picker.findText(save_name) == -1:
            self.save_picker.blockSignals(True)
            self.save_picker.addItem(save_name)
            self.save_picker.setCurrentText(save_name)
            self.save_picker.blockSignals(False)
        elif self.save_picker.currentText() != save_name:
            self.save_picker.blockSignals(True)
            self.save_picker.setCurrentText(save_name)
            self.save_picker.blockSignals(False)
        self.snapshot = state.snapshot
        # §7.3：切换存档必须清空旧存档的页面状态，禁止旧实体历史残留。
        self.router.clear_page_states()
        current = self.router.current
        if current is not None and current.name in _ENTITY_DETAIL_ROUTES:
            # 实体详情路由指向旧存档 ID，回到主页而不是原地刷新。
            self.router.navigate(Route("dashboard"))
        elif current is not None:
            self._apply_route(current)
        self._refresh_views()
        self._focus_pending_workflow(silent=True)

    def _initialize_current_save(self) -> None:
        if self._init_in_progress:
            return
        self._init_in_progress = True
        self.init_button.setEnabled(False)
        save_name = self._current_save_name()
        # 数据安全（用户确认 #2）：赛季进行中被初始化会丢弃进度，必须强确认。
        if self.snapshot is not None and not self.snapshot.season_complete:
            answer = QMessageBox.warning(
                self,
                "初始化赛季",
                f"当前存档第 {self.snapshot.season_number} 赛季尚未结束"
                f"（已进行到第 {self.snapshot.current_week} 周）。\n"
                "重新初始化将放弃该赛季（不归档）并直接进入下一赛季。确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._init_in_progress = False
                self.init_button.setEnabled(True)
                return
        try:
            state = self.service.initialize(save_name, force=True)
        except Exception as exc:
            QMessageBox.critical(self, "Football Simulator UI v2", str(exc))
            return
        finally:
            self._init_in_progress = False
            self.init_button.setEnabled(True)
        self.refresh_save_choices()
        self.snapshot = state.snapshot
        self._refresh_views()
        QMessageBox.information(self, "Football Simulator UI v2", f"已为存档 {save_name} 初始化新赛季。")

    def _on_simulate_clicked(self) -> None:
        # 模拟正在运行 → 点击=请求取消；有待办 → 点击=前往待办处理。
        if self._simulate_in_progress:
            if self._simulate_worker is not None:
                self._simulate_worker.requestInterruption()
                self.simulate_button.setText("正在取消…")
            return
        if self.snapshot is not None and _snapshot_has_pending(self.snapshot):
            self._focus_pending_workflow()
            return
        self._simulate_week()

    def _simulate_week(self) -> None:
        self._start_simulation("one")

    def _start_simulation(self, mode: str) -> None:
        if self._simulate_in_progress:
            return
        if self.snapshot is None:
            QMessageBox.warning(self, "Football Simulator UI v2", "当前存档还没有赛季数据，请先初始化赛季。")
            return
        if mode != "one" and _snapshot_has_pending(self.snapshot):
            # 已有待办时先引导处理，避免“按到待办却报错”。
            self._focus_pending_workflow()
            return
        self._simulate_in_progress = True
        self._simulate_mode = mode
        self.simulate_button.setEnabled(False)
        self.simulate_button.setText("正在模拟…")
        self.advance_button.setEnabled(False)
        self._simulate_worker = _SimulateWorker(
            self.service, self._current_save_name(), mode, self
        )
        self._simulate_worker.succeeded.connect(self._on_simulate_succeeded)
        self._simulate_worker.failed.connect(self._on_simulate_failed)
        self._simulate_worker.progress.connect(self._on_simulate_progress)
        self._simulate_worker.start()

    def _on_simulate_progress(self, week: int, total: int, phase: str) -> None:
        self.status_label.setText(f"正在模拟第 {week}/{total} 周 · {phase}")
        self.simulate_button.setText(f"模拟中 {week}/{total}…")

    def _on_simulate_succeeded(self, result: object) -> None:
        self._simulate_worker = None
        self._simulate_in_progress = False
        self.simulate_button.setEnabled(True)
        self.advance_button.setEnabled(True)
        self.simulate_button.setText("模拟下一周")
        self.snapshot = result.snapshot
        self._refresh_views()
        # 用户确认 #9：单步不再自动打开战报；批量到赛季末/待办给出结果页。
        if self._simulate_mode == "until_season_end":
            if result.season_completed_now:
                self._open_weekly_report()
            elif _snapshot_has_pending(result.snapshot):
                self._focus_pending_workflow()
        elif self._simulate_mode == "until_pending":
            if _snapshot_has_pending(result.snapshot):
                self._focus_pending_workflow()
            elif result.season_completed_now:
                self._open_weekly_report()

    def _on_simulate_failed(self, message: str) -> None:
        self._simulate_worker = None
        self._simulate_in_progress = False
        self.simulate_button.setEnabled(True)
        self.advance_button.setEnabled(True)
        self.simulate_button.setText("模拟下一周")
        QMessageBox.warning(self, "Football Simulator UI v2", message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        # 模拟线程未结束时等待其完成（单周 ≤1 秒量级），避免销毁运行中的线程。
        worker = getattr(self, "_simulate_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(5000)
        super().closeEvent(event)

    def _refresh_views(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            self.status_label.setText("当前存档还没有赛季数据。")
            self.pending_button.setText("处理待办")
            self.pending_button.setEnabled(False)
        else:
            next_phase = snapshot.weeks[snapshot.current_week].label if snapshot.current_week < len(snapshot.weeks) else "赛季已结束"
            self.status_label.setText(
                f"第 {snapshot.season_number} 赛季 | 第 {snapshot.current_week}/{len(snapshot.weeks)} 周 | {next_phase}"
            )
            pending_count = self._pending_count(snapshot)
            self.pending_button.setText(f"处理待办({pending_count})" if pending_count else "处理待办")
            self.pending_button.setEnabled(pending_count > 0)
            if pending_count:
                # 有待办时模拟按钮变为“前往待办”，不再让用户点模拟报错。
                self.simulate_button.setEnabled(True)
                self.simulate_button.setText(f"→ 处理待办({pending_count})")
                self.advance_button.setEnabled(False)
            else:
                self.simulate_button.setEnabled(not self._simulate_in_progress)
                self.simulate_button.setText("模拟下一周")
                self.advance_button.setEnabled(not self._simulate_in_progress)
        # legacy 页面保持原有 set_snapshot 流程（matches/teams/players 已迁移，
        # 由并行页面按路由自行查询，不再吃快照）。
        # 当前若停留在迁移后的实体页面，强制按新数据重建。
        page = self.stack.currentWidget()
        if isinstance(page, EntityPageBase) and page.current_route() is not None:
            try:
                page.refresh()
            except Exception:
                traceback.print_exc()

    @staticmethod
    def _pending_count(snapshot: SaveSnapshot | None) -> int:
        if snapshot is None:
            return 0
        count = len(snapshot.pending_ability_review) + len(snapshot.pending_transfer_review)
        if snapshot.pending_draft.get("status") == "awaiting_input":
            count += 1
        return count

    def _current_save_name(self) -> str:
        return self.save_picker.currentText().strip()

    def _current_season(self) -> int:
        """当前赛季号；无赛季数据时返回 0（仅作路由参数占位）。"""
        return self.snapshot.season_number if self.snapshot is not None else 0

    def _focus_pending_workflow(self, silent: bool = False) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        season = snapshot.season_number
        route: Route | None = None
        notice: str | None = None
        if snapshot.pending_ability_review:
            route = Route("season_overview", season=season)
            notice = "当前有待处理的能力变动审核，已切换到“赛季总览”。"
        elif snapshot.pending_transfer_review:
            route = Route("transfers", season=season)
            notice = "当前有待处理的转会审核，已切换到“转会中心”。"
        elif snapshot.pending_draft.get("status") == "awaiting_input":
            route = Route("draft", season=season)
            notice = "当前进入夏窗前选秀流程，正在等待选秀录入，已切换到“选秀中心”。"
        if route is None:
            if not silent:
                QMessageBox.information(self, "Football Simulator UI v2", "当前没有待处理事项。")
            return
        self.router.navigate(route)
        if not silent and notice:
            QMessageBox.information(self, "Football Simulator UI v2", notice)

    # ------------------------------------------------------------------
    # legacy 回调适配（路由导航）
    # ------------------------------------------------------------------

    def _open_weekly_report(self) -> None:
        week = self.snapshot.current_week if self.snapshot is not None else 1
        self.router.navigate(Route("weekly_report", week=week))

    def _team_route(self, team_name: str) -> Route:
        """球队显示名 → 稳定 team_id 路由；解析失败退回球队列表（不弹错）。"""
        if team_name and self.snapshot is not None:
            try:
                with open_read_connection(self._current_save_name()) as conn:
                    row = conn.execute(
                        "SELECT team_id FROM teams WHERE name = ?",
                        (team_name,),
                    ).fetchone()
                if row is not None:
                    return Route("team", team=int(row["team_id"]), season=self.snapshot.season_number)
            except Exception:
                pass
        return Route("teams")

    def _player_route(self, player_id: str | None, label: str | None) -> Route:
        """player_id / 显示名 → 稳定 player 路由；解析失败退回球员列表。"""
        if self.snapshot is None:
            return Route("players")
        season = self.snapshot.season_number
        pid = str(player_id).strip() if player_id else ""
        if pid.startswith("real::"):
            return Route("player", player=pid, season=season)
        if label:
            candidate = query_base.canonical_player_id_for_name(str(label))
            if self._player_id_exists(candidate):
                return Route("player", player=candidate, season=season)
        return Route("players")

    def _player_id_exists(self, player_id: str) -> bool:
        try:
            with open_read_connection(self._current_save_name()) as conn:
                row = conn.execute(
                    "SELECT 1 FROM players WHERE player_id = ? LIMIT 1",
                    (player_id,),
                ).fetchone()
            return row is not None
        except Exception:
            return False
