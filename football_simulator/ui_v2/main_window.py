from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2.pages.cups_page import CupsPage
from football_simulator.ui_v2.pages.dashboard_page import DashboardPage
from football_simulator.ui_v2.pages.draft_page import DraftPage
from football_simulator.ui_v2.pages.history_page import HistoryPage
from football_simulator.ui_v2.pages.matches_page import MatchCenterPage
from football_simulator.ui_v2.pages.players_page import PlayersPage
from football_simulator.ui_v2.pages.saves_page import SavesPage
from football_simulator.ui_v2.pages.season_overview_page import SeasonOverviewPage
from football_simulator.ui_v2.pages.standings_page import StandingsPage
from football_simulator.ui_v2.pages.teams_page import TeamsPage
from football_simulator.ui_v2.pages.transfers_page import TransfersPage
from football_simulator.ui_v2.pages.weekly_report_page import WeeklyReportPage
from football_simulator.ui_v2.services import SimulatorUIService


NAV_ITEMS = [
    ("首页", "dashboard"),
    ("本周战报", "weekly_report"),
    ("赛季总览", "season_overview"),
    ("比赛中心", "matches"),
    ("一级联赛", "premier_standings"),
    ("次级联赛", "second_standings"),
    ("杯赛中心", "cups"),
    ("球队中心", "teams"),
    ("球员中心", "players"),
    ("转会中心", "transfers"),
    ("选秀中心", "draft"),
    ("历史与荣誉", "history"),
    ("存档管理", "saves"),
]


class MainWindow(QMainWindow):
    def __init__(self, service: SimulatorUIService) -> None:
        super().__init__()
        self.service = service
        self.snapshot: SaveSnapshot | None = None
        self.setWindowTitle("Football Simulator UI v2")
        self.resize(1680, 980)
        self.setMinimumSize(1440, 860)
        self._build_ui()
        self._load_save(self.service.current_save_name())

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(18)
        self.setCentralWidget(root)

        nav_panel = QFrame()
        nav_panel.setObjectName("navPanel")
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(16, 16, 16, 16)
        nav_layout.setSpacing(12)

        title = QLabel("Football Simulator")
        title.setObjectName("titleLabel")
        subtitle = QLabel("UI 增强版 v2")
        subtitle.setObjectName("subtitleLabel")
        nav_layout.addWidget(title)
        nav_layout.addWidget(subtitle)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        for label, key in NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.nav_list.addItem(item)
        self.nav_list.currentRowChanged.connect(self._change_page)
        nav_layout.addWidget(self.nav_list, 1)
        root_layout.addWidget(nav_panel, 0)

        content_wrapper = QVBoxLayout()
        content_wrapper.setSpacing(18)

        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(18, 14, 18, 14)
        status_layout.setSpacing(14)

        self.save_picker = QComboBox()
        self.save_picker.currentTextChanged.connect(self._load_save)
        self.refresh_save_choices()
        self.status_label = QLabel("还没有载入存档。")
        self.status_label.setObjectName("subtitleLabel")
        self.init_button = QPushButton("初始化赛季")
        self.init_button.clicked.connect(self._initialize_current_save)
        self.simulate_button = QPushButton("模拟下一周")
        self.simulate_button.clicked.connect(self._simulate_week)
        self.pending_button = QPushButton("处理待办")
        self.pending_button.clicked.connect(self._focus_pending_workflow)
        self.reload_button = QPushButton("刷新")
        self.reload_button.clicked.connect(lambda: self._load_save(self.save_picker.currentText()))

        status_layout.addWidget(QLabel("存档"))
        status_layout.addWidget(self.save_picker)
        status_layout.addWidget(self.status_label, 1)
        status_layout.addWidget(self.init_button)
        status_layout.addWidget(self.simulate_button)
        status_layout.addWidget(self.pending_button)
        status_layout.addWidget(self.reload_button)
        content_wrapper.addWidget(status_panel)

        content_panel = QFrame()
        content_panel.setObjectName("contentPanel")
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(18, 18, 18, 18)
        self.stack = QStackedWidget()
        self.dashboard_page = DashboardPage(self._open_match_center_latest)
        self.weekly_report_page = WeeklyReportPage(self._open_match_center_latest, self._focus_pending_workflow)
        self.players_page = PlayersPage(self._open_team)
        self.teams_page = TeamsPage(self.service, self._open_player)
        self.standings_page = StandingsPage(self._open_team)
        self.matches_page = MatchCenterPage(self._open_team, self._open_player)
        self.season_overview_page = SeasonOverviewPage(
            self.service,
            self._current_save_name,
            self._replace_snapshot,
            self._open_match_center_latest,
        )
        self.cups_page = CupsPage(self._open_team, self._open_player)
        self.transfers_page = TransfersPage(
            self.service,
            self._current_save_name,
            self._replace_snapshot,
            self._open_player,
        )
        self.draft_page = DraftPage(
            self.service,
            self._current_save_name,
            self._replace_snapshot,
        )
        self.history_page = HistoryPage(self._open_team, self._open_player)
        self.saves_page = SavesPage(
            self.service,
            self._current_save_name,
            self._replace_save_state,
        )
        self.page_by_key = {
            "dashboard": self.dashboard_page,
            "weekly_report": self.weekly_report_page,
            "season_overview": self.season_overview_page,
            "matches": self.matches_page,
            "premier_standings": self.standings_page,
            "second_standings": self.standings_page,
            "cups": self.cups_page,
            "teams": self.teams_page,
            "players": self.players_page,
            "transfers": self.transfers_page,
            "draft": self.draft_page,
            "history": self.history_page,
            "saves": self.saves_page,
        }
        for page in {
            self.dashboard_page,
            self.weekly_report_page,
            self.season_overview_page,
            self.matches_page,
            self.standings_page,
            self.cups_page,
            self.teams_page,
            self.players_page,
            self.transfers_page,
            self.draft_page,
            self.history_page,
            self.saves_page,
        }:
            self.stack.addWidget(page)
        content_layout.addWidget(self.stack)
        content_wrapper.addWidget(content_panel, 1)
        root_layout.addLayout(content_wrapper, 1)

        self.nav_list.setCurrentRow(0)

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

    def _change_page(self, index: int) -> None:
        item = self.nav_list.item(index)
        if item is None:
            return
        key = item.data(Qt.UserRole)
        page = self.page_by_key[key]
        self.stack.setCurrentWidget(page)
        if key == "premier_standings":
            self.standings_page.tabs.setCurrentIndex(0)
        elif key == "second_standings":
            self.standings_page.tabs.setCurrentIndex(1)

    def _load_save(self, save_name: str) -> None:
        if not save_name:
            return
        try:
            state = self.service.load_state(save_name)
        except Exception as exc:
            QMessageBox.critical(self, "Football Simulator UI v2", str(exc))
            return
        self.snapshot = state.snapshot
        self._refresh_views()
        self._focus_pending_workflow(silent=True)

    def _initialize_current_save(self) -> None:
        save_name = self.save_picker.currentText().strip()
        try:
            state = self.service.initialize(save_name)
        except Exception as exc:
            QMessageBox.critical(self, "Football Simulator UI v2", str(exc))
            return
        self.refresh_save_choices()
        self.snapshot = state.snapshot
        self._refresh_views()
        QMessageBox.information(self, "Football Simulator UI v2", f"已为存档 {save_name} 初始化新赛季。")

    def _simulate_week(self) -> None:
        save_name = self.save_picker.currentText().strip()
        try:
            result = self.service.simulate_week(save_name)
        except Exception as exc:
            QMessageBox.warning(self, "Football Simulator UI v2", str(exc))
            return
        self.snapshot = result.snapshot
        self._refresh_views()
        self._open_weekly_report()

    def _refresh_views(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            self.status_label.setText("当前存档还没有赛季数据。")
            self.pending_button.setEnabled(False)
        else:
            next_phase = snapshot.weeks[snapshot.current_week].label if snapshot.current_week < len(snapshot.weeks) else "赛季已结束"
            self.status_label.setText(
                f"第 {snapshot.season_number} 赛季 | 第 {snapshot.current_week}/{len(snapshot.weeks)} 周 | {next_phase}"
            )
            self.pending_button.setEnabled(
                bool(snapshot.pending_ability_review)
                or bool(snapshot.pending_transfer_review)
                or snapshot.pending_draft.get("status") == "awaiting_input"
            )
        self.dashboard_page.set_snapshot(snapshot)
        self.weekly_report_page.set_snapshot(snapshot)
        self.season_overview_page.set_snapshot(snapshot)
        self.matches_page.set_snapshot(snapshot)
        self.standings_page.set_snapshot(snapshot)
        self.cups_page.set_snapshot(snapshot)
        self.teams_page.set_snapshot(snapshot)
        self.players_page.set_snapshot(snapshot)
        self.transfers_page.set_snapshot(snapshot)
        self.draft_page.set_snapshot(snapshot)
        self.history_page.set_snapshot(snapshot)
        self.saves_page.set_snapshot(snapshot)

    def _current_save_name(self) -> str:
        return self.save_picker.currentText().strip()

    def _replace_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self.snapshot = snapshot
        self._refresh_views()
        self._focus_pending_workflow(silent=True)

    def _focus_pending_workflow(self, silent: bool = False) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        target_key = None
        notice = None
        if snapshot.pending_ability_review:
            target_key = "season_overview"
            notice = "当前有待处理的能力变动审核，已切换到“赛季总览”。"
        elif snapshot.pending_transfer_review:
            target_key = "transfers"
            notice = "当前有待处理的转会审核，已切换到“转会中心”。"
        elif snapshot.pending_draft.get("status") == "awaiting_input":
            target_key = "draft"
            notice = "当前赛季已结束，正在等待选秀录入，已切换到“选秀中心”。"
        if target_key is None:
            if not silent:
                QMessageBox.information(self, "Football Simulator UI v2", "当前没有待处理事项。")
            return

        for index in range(self.nav_list.count()):
            item = self.nav_list.item(index)
            if item.data(Qt.UserRole) == target_key:
                self.nav_list.setCurrentRow(index)
                break
        if not silent and notice:
            QMessageBox.information(self, "Football Simulator UI v2", notice)

    def _replace_save_state(self, save_name: str, snapshot: SaveSnapshot | None) -> None:
        current_index = self.save_picker.findText(save_name)
        if current_index == -1:
            self.refresh_save_choices()
            current_index = self.save_picker.findText(save_name)
        if current_index != -1:
            self.save_picker.setCurrentIndex(current_index)
        self.snapshot = snapshot
        self._refresh_views()
        self._focus_pending_workflow(silent=True)

    def _open_match_center_latest(self) -> None:
        for index in range(self.nav_list.count()):
            item = self.nav_list.item(index)
            if item.data(Qt.UserRole) == "matches":
                self.nav_list.setCurrentRow(index)
                break
        self.matches_page.focus_latest_week()

    def _open_weekly_report(self) -> None:
        for index in range(self.nav_list.count()):
            item = self.nav_list.item(index)
            if item.data(Qt.UserRole) == "weekly_report":
                self.nav_list.setCurrentRow(index)
                break

    def _open_team(self, team_name: str) -> None:
        for index in range(self.nav_list.count()):
            item = self.nav_list.item(index)
            if item.data(Qt.UserRole) == "teams":
                self.nav_list.setCurrentRow(index)
                break
        self.teams_page.focus_team(team_name)

    def _open_player(self, player_id: str | None, label: str | None) -> None:
        for index in range(self.nav_list.count()):
            item = self.nav_list.item(index)
            if item.data(Qt.UserRole) == "players":
                self.nav_list.setCurrentRow(index)
                break
        self.players_page.focus_player(player_id, label)
