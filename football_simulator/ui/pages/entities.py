"""球队/球员目录与详情、历史档案页面。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from football_simulator.app import queries, records
from football_simulator.game import rules
from football_simulator.ui import tokens as t
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.presentation import roles as role_view
from football_simulator.ui.components import (
    CompactMatchRow,
    Badge,
    DataTable,
    EmptyState,
    EntityLink,
    KeyValue,
    MatchRowCard,
    PageHeader,
    PageScroll,
    SectionTitle,
    StatGrid,
    card,
    hline,
    label,
)
from football_simulator.ui.pages.base import BasePage
from football_simulator.ui.team_crest import TeamCrest
from football_simulator.ui import transfer_history


class TeamsPage(BasePage):
    route_name = "teams"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(t.S32, t.S24, t.S32, t.S24)
        outer.setSpacing(t.S12)
        outer.addWidget(PageHeader("观察", "球队目录", "按联赛、排名、荣誉与近况比较 40 支球队"))
        filters = QHBoxLayout()
        self.division = QComboBox()
        self.division.addItem("全部联赛", "")
        self.division.addItem("一级联赛", "一级联赛")
        self.division.addItem("次级联赛", "次级联赛")
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索球队…")
        self.search.setClearButtonEnabled(True)
        self.division.currentIndexChanged.connect(lambda _: self.refresh(self.snapshot, {}))
        self.search.textChanged.connect(lambda _: self.refresh(self.snapshot, {}))
        filters.addWidget(self.division)
        filters.addWidget(self.search, 1)
        outer.addLayout(filters)
        self.table = DataTable()
        columns = [
            {"key": "rank", "label": "名次", "width": 52, "numeric": True},
            {"key": "team", "label": "球队", "stretch": True, "kind": "link", "icon_key": "club", "icon_size": 32},
            {"key": "division", "label": "联赛", "width": 90},
            {"key": "played", "label": "赛", "width": 42, "numeric": True},
            {"key": "points", "label": "积分", "width": 48, "numeric": True},
            {"key": "goal_diff", "label": "净胜", "width": 48, "numeric": True},
            {"key": "honor_points", "label": "荣誉分", "width": 60, "numeric": True},
            {"key": "stadium", "label": "主场", "width": 190, "optional": True, "show_at": 1560},
        ]
        if not macos.enabled():
            columns.insert(2, {"key": "club", "label": "英文名", "width": 190, "optional": True, "show_at": 1440})
        self.table.set_columns(columns)
        self.table.row_activated.connect(lambda row: self.open_route("team", team_id=row["team_id"]))
        self.table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]))
        outer.addWidget(self.table, 1)
        self.empty = EmptyState("没有符合条件的球队", "调整筛选条件。")
        outer.addWidget(self.empty)
        self.empty.hide()

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        data = queries.teams_page(world, self.save_name)
        rows = data["rows"]
        division = self.division.currentData() or ""
        needle = self.search.text().strip().lower()
        if division:
            rows = [row for row in rows if row["division"] == division]
        if needle:
            rows = [row for row in rows if needle in row["search_text"].lower()]
        self.table.set_rows(rows)
        self.table.hide_optional_columns(self.window().width())
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)


def _hero_stat_widget(value: str, caption: str) -> QWidget:
    holder = QWidget()
    holder.setLayout(_hero_stat(value, caption))
    return holder


def _hero_stat(value: str, caption: str) -> QVBoxLayout:
    """Small number + caption used by team and player first screens."""
    column = QVBoxLayout()
    column.setSpacing(0)
    column.addWidget(label(value, "HeroStat"))
    column.addWidget(label(caption, "RoleMetricLabel"))
    return column


class TeamProfilePage(BasePage):
    route_name = "team"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        self.scroll.reset()
        team_id = params.get("team_id")
        world = self.app.world()
        data = queries.team_profile(world, self.save_name, team_id) if team_id else None
        if not data:
            self.scroll.layout.addWidget(EmptyState("球队不存在", "该球队 ID 不在当前世界中。", self._back()))
            return
        if macos.enabled():
            fixtures = sorted(
                (fixture for fixture in world["season"]["fixtures"] if team_id in (fixture["home_team_id"], fixture["away_team_id"])),
                key=lambda fixture: (int(fixture["week"]), fixture["match_id"]),
            )
            data["recent"] = [queries.fixture_dto(world, fixture) for fixture in fixtures if fixture["status"] == "completed"][::-1]
            data["upcoming"] = [queries.fixture_dto(world, fixture) for fixture in fixtures if fixture["status"] != "completed"]
        back = QPushButton("← 球队目录")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.open_route("teams"))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(t.S20)
        header_layout.addWidget(TeamCrest(data["club"], 84), 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(t.S4)
        team_zh = world["teams"][team_id].get("name_zh") or ""
        info.addWidget(PageHeader(data["division"], data["club"], f"{team_zh} · {data['stadium']}"))
        header_layout.addLayout(info, 1)
        action = QPushButton("取消关注" if data["watch"] else "关注球队")
        action.setObjectName("Secondary")
        action.clicked.connect(lambda: self.app.toggle_watch(team_id))
        header_layout.addWidget(action, 0, Qt.AlignTop)
        self.scroll.layout.addWidget(header)
        table = data.get("table") or {}
        if macos.enabled():
            self.scroll.layout.addWidget(self._team_hero(world, data, team_id))
        else:
            self.scroll.layout.addWidget(
                StatGrid(
                    [
                        (str(data.get("rank") or "—"), "联赛名次"),
                        (str(table.get("points", 0)), "积分"),
                        (str(table.get("goal_diff", 0)), "净胜球"),
                        (str(data["honor_total"]), "本季荣誉分"),
                        (str(len([row for row in data["roster"] if row["is_real"]])), "真实球员"),
                    ]
                )
            )
        if macos.enabled():
            self._render_mac_sections(world, data)
            self.scroll.layout.addStretch(1)
            return
        self.scroll.layout.addWidget(
            self._roster_card(world, data["roster"])
        )
        two = QHBoxLayout()
        two.setSpacing(t.S16)
        two.addWidget(self._matches_card("近期赛果", data["recent"], team_id=team_id), 1)
        two.addWidget(self._matches_card("后续赛程", data["upcoming"], upcoming=True), 1)
        self.scroll.layout.addLayout(two)
        self.scroll.layout.addWidget(self._honors_card(data))
        honors_link = QPushButton("查看全部荣誉")
        honors_link.setObjectName("Secondary")
        honors_link.clicked.connect(lambda: self.open_route("team_honors", team_id=team_id))
        self.scroll.layout.addWidget(honors_link, 0, Qt.AlignLeft)
        if data["transfers"]:
            self.scroll.layout.addWidget(self._transfers_card(world, data["transfers"]))
        self.scroll.layout.addStretch(1)

    def _team_hero(self, world: dict, data: dict, team_id: str) -> QWidget:
        """Rank, points, a named gap, recent W/D/L and the next fixture."""
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S10)
        table = data.get("table") or {}
        points = int(table.get("points") or 0)
        played = int(table.get("played") or 0)
        rank = data.get("rank")
        rows = QHBoxLayout()
        rows.setSpacing(t.S24)
        rows.addLayout(_hero_stat(str(rank) if rank else "—", "联赛名次"))
        rows.addLayout(_hero_stat(str(points), "积分"))
        rows.addLayout(_hero_stat(str(table.get("goal_diff", 0)), "净胜球"))
        gap_text, gap_caption = self._gap_text(world, data, team_id)
        if gap_text:
            rows.addLayout(_hero_stat(gap_text, gap_caption))
        rows.addLayout(self._wdl_block(world, data, team_id))
        rows.addStretch(1)
        layout.addLayout(rows)
        if played == 0:
            layout.addWidget(label("本赛季还没有出场记录。", "Meta", wrap=True))
        if data.get("upcoming"):
            next_fixture = data["upcoming"][0]
            layout.addWidget(SectionTitle("下一场", f"第 {next_fixture['week']} 周"))
            row = CompactMatchRow(world, next_fixture)
            row.clicked_match.connect(lambda match_id: self.open_route("match", match_id=match_id))
            layout.addWidget(row)
        return frame

    def _gap_text(self, world: dict, data: dict, team_id: str):
        rank = data.get("rank")
        table = data.get("table") or {}
        if not rank:
            return "", ""
        from football_simulator.game import world as game_world

        tables = world["season"].get("final_tables") or game_world.league_tables(world)
        key = "premier" if data.get("division") == "一级联赛" else "second"
        rows = tables.get(key) or []
        points = int(table.get("points") or 0)
        if key == "premier":
            if rank == 1:
                if len(rows) >= 2:
                    return role_view.signed_gap(points - int(rows[1]["points"])), "领先第 2 名"
                return "领跑", "积分榜"
            leader = int(rows[0]["points"]) if rows else points
            return role_view.signed_gap(points - leader), "距榜首"
        if rank <= 2:
            return "直接升级区", "名次"
        chase = rows[1] if len(rows) >= 2 else None
        if chase is None:
            return "", ""
        return role_view.signed_gap(points - int(chase["points"])), "距直接升级区"

    def _wdl_block(self, world: dict, data: dict, team_id: str) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(label("近 5 场", "RoleMetricLabel"))
        row = QHBoxLayout()
        row.setSpacing(t.S4)
        recent = [dto for dto in (data.get("recent") or []) if dto.get("status") == "completed"][:5]
        if not recent:
            row.addWidget(label("—", "Meta"))
        for dto in recent:
            result = macos.team_result(dto, team_id)
            if not result:
                continue
            _, symbol, color, background = result
            badge = label(symbol, "Badge")
            badge.setProperty("variant", "neutral")
            badge.setStyleSheet(f"color: {color}; background: {background}; border-radius: 7px; padding: 2px 7px; font-weight: 700;")
            badge.setToolTip(f"第 {dto['week']} 周 · {dto['home_name']} {dto['score']} {dto['away_name']}")
            row.addWidget(badge)
        holder = QWidget()
        holder.setLayout(row)
        column.addWidget(holder)
        return column

    def _render_mac_sections(self, world: dict, data: dict) -> None:
        team_id = data["team_id"]
        state = self.detail_state(team_id)
        self.scroll.layout.addWidget(macos.section_tabs(
            [("概览", "overview"), ("阵容", "squad"), ("赛程与赛果", "fixtures"), ("历季战绩", "history"), ("转会", "transfers"), ("荣誉", "honors")],
            state["section"], lambda section: self.set_detail_section(team_id, section),
        ))
        section = state["section"]
        if section == "squad":
            self.scroll.layout.addWidget(self._roster_card(world, data["roster"]))
        elif section == "transfers":
            rows = transfer_history.ordered_transfers(world, team_id=team_id)
            self.scroll.layout.addWidget(transfer_history.history_card(self, world, rows, state.setdefault("transfers", {}), team_id=team_id))
        elif section == "history":
            self.scroll.layout.addWidget(self._season_results_card(world, team_id))
        elif section == "honors":
            from football_simulator.ui.pages.honors import TeamTrophyCollection
            self.scroll.layout.addWidget(TeamTrophyCollection(world, team_id, self.open_route))
        else:
            full = section == "fixtures"
            matches = QHBoxLayout()
            matches.setSpacing(t.S16)
            matches.addWidget(self._recent_matches_card(world, data, state), 1)
            matches.addWidget(self._matches_card("后续赛程", data["upcoming"] if full else data["upcoming"][:3], upcoming=True, paging=state.setdefault("upcoming", {}) if full else None), 1)
            self.scroll.layout.addLayout(matches)
            if not full:
                link = QPushButton("查看全部赛程与赛果 →")
                link.setObjectName("Ghost")
                link.clicked.connect(lambda: self.set_detail_section(team_id, "fixtures"))
                self.scroll.layout.addWidget(link, 0, Qt.AlignLeft)

    def _recent_matches_card(self, world: dict, data: dict, state: dict) -> QWidget:
        paging = state.setdefault("recent", {})
        paging["page_size"] = 5
        competitions = {row["competition"] for row in data["recent"] + data["upcoming"]}
        selected = paging.get("competition", "")
        if selected not in competitions:
            selected = ""
        paging["competition"] = selected
        selector = QComboBox()
        selector.setObjectName("TeamRecentCompetition")
        selector.setAccessibleName("近期赛果赛事")
        selector.setMaximumWidth(240)
        selector.addItem("全部赛事", "")
        for competition in rules.ALL_COMPETITION_IDS:
            if competition in competitions:
                selector.addItem(rules.display_name(competition, "en"), competition)
        selector.setCurrentIndex(max(0, selector.findData(selected)))

        def changed(_index):
            paging.update(competition=selector.currentData(), page=1)
            self.app._refresh_current_page(restore_scroll=True)

        selector.currentIndexChanged.connect(changed)
        rows = [row for row in data["recent"] if not selected or row["competition"] == selected]
        frame = self._matches_card("近期赛果", rows, team_id=data["team_id"], paging=paging,
                                   control=selector, page_size=5, fixed_size=True)
        holder = QWidget()
        holder.setObjectName("TeamRecentResults")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(frame)
        return holder

    def _back(self) -> QWidget:
        button = QPushButton("返回球队目录")
        button.clicked.connect(lambda: self.open_route("teams"))
        return button

    def _season_results_card(self, world: dict, team_id: str) -> QWidget:
        from football_simulator.app import profile_history
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S12)
        state = self.detail_state(team_id)
        view = state.setdefault("history_view", "matrix")
        def view_changed(key):
            state["history_view"] = key
            self.app._refresh_current_page(restore_scroll=False)
        layout.addWidget(macos.section_tabs([("多赛季总览", "matrix"), ("单赛季详情", "detail")], view, view_changed))
        if view == "matrix":
            layout.addWidget(SectionTitle("历季战绩"))
            table = DataTable()
            table.setObjectName("TeamSeasonMatrix")
            headings = {"premier": "Premier\nDivision", "second": "Second\nDivision", "playoff": "Promotion\nPlay-off"}
            table.set_columns([
                {"key": "season", "label": "赛季", "width": 54, "numeric": True},
                *[{"key": comp, "label": headings.get(comp) or rules.display_name(comp, "en").replace(" Cup", "\nCup"),
                   "width": 96 if comp == rules.PLAYOFF else 78, "kind": "link"} for comp in profile_history.COMPETITIONS],
                {"key": "honor_points", "label": "赛季结束\n总荣誉分", "width": 92, "numeric": True},
            ])
            table.setStyleSheet("QHeaderView::section { font-size: 12px; padding: 6px 2px; }")
            table.horizontalHeader().setFixedHeight(52)
            def open_matrix_event(row, key):
                if key not in profile_history.COMPETITIONS or row.get(key) in ("未举办", "未参赛"):
                    return
                if row["season"] == int(world["season_number"]):
                    self.open_route("competition", competition_id=key)
                else:
                    self.open_route("history_competition", competition_id=key, season=row["season"])
            table.cell_activated.connect(open_matrix_event)
            macos.paginate_table(table, profile_history.team_season_matrix(world, self.save_name, team_id), layout, state.setdefault("history_matrix", {}))
            return frame
        selected = state.setdefault("history_season", int(world["season_number"]))
        box = QComboBox()
        box.setObjectName("TeamHistorySeason")
        box.setAccessibleName("球队战绩赛季")
        box.addItem("全部赛季", 0)
        for season in profile_history.season_options(world):
            box.addItem(f"第 {season} 赛季", season)
        box.setCurrentIndex(max(0, box.findData(selected)))
        def changed(_index):
            state["history_season"] = box.currentData()
            state["history_rows"] = {}
            self.app._refresh_current_page(restore_scroll=True)
        box.currentIndexChanged.connect(changed)
        layout.addWidget(SectionTitle("历季战绩", "", box))
        table = DataTable()
        table.setObjectName("TeamSeasonResults")
        table.set_columns([
            {"key": "season", "label": "赛季", "width": 58, "numeric": True},
            {"key": "label", "label": "赛事", "stretch": True, "kind": "link"},
            {"key": "result", "label": "成绩", "width": 156},
            *[{"key": key, "label": title, "width": 46, "numeric": True} for key, title in (("played", "场"), ("wins", "胜"), ("draws", "平"), ("losses", "负"), ("goals_for", "进球"), ("goals_against", "失球"))],
        ])
        rows = profile_history.team_season_results(world, self.save_name, team_id)
        if selected:
            rows = [row for row in rows if row["season"] == selected]
        def open_event(row, _key):
            if row["season"] == int(world["season_number"]):
                self.open_route("competition", competition_id=row["competition"])
            else:
                self.open_route("history_competition", competition_id=row["competition"], season=row["season"])
        table.cell_activated.connect(open_event)
        macos.paginate_table(table, rows, layout, state.setdefault("history_rows", {}))
        return frame

    def _roster_card(self, world: dict, roster: List[dict]) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        scope = macos.player_scope_box(self.player_scope, self.set_player_scope) if macos.enabled() else None
        layout.addWidget(SectionTitle("阵容与产出", "本季数据" if macos.enabled() else "默认球员与真实球员身份不同；0 场保持 0 场", scope))
        roster = macos.visible_players(roster, world, self.player_scope)
        table = DataTable()
        table.set_columns(
            [
                {"key": "name", "label": "球员", "stretch": True, "kind": "link"},
                {"key": "position", "label": "位置", "width": 58},
                {"key": "ability", "label": "能力", "width": 52, "numeric": True},
                {"key": "appearances", "label": "出场", "width": 52, "numeric": True},
                {"key": "goals", "label": "球", "width": 38, "numeric": True},
                {"key": "assists", "label": "助", "width": 38, "numeric": True},
                {"key": "rating", "label": "评分", "width": 62, "numeric": True, "decimals": 1},
                {"key": "market_value", "label": "身价", "width": 72, "numeric": True, "decimals": 2, "optional": True, "show_at": 1440},
            ]
        )
        table.set_rows(
            [
                {**row, "rating": row["rating"] if row["rating"] is not None else "—", "market_value": row["market_value"] if row["market_value"] is not None else "—"}
                for row in roster
            ]
        )
        table.cell_activated.connect(lambda row, key: self.open_route("player", player_id=row["player_id"]))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 38 * len(roster) + 4)
        layout.addWidget(table)
        if macos.enabled() and not roster:
            layout.addWidget(label("当前范围没有球员，可切换为全部球员查看完整阵容。", "Meta", wrap=True))
        return frame

    def _matches_card(self, title: str, rows: List[dict], *, upcoming: bool = False, team_id: Optional[str] = None, paging: Optional[dict] = None,
                      control: Optional[QWidget] = None, page_size: int = 4, fixed_size: bool = False) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S14, t.S16, t.S14)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle(title, "", control) if control is not None else label(title, "CardTitle"))
        if macos.enabled() and not upcoming:
            layout.addWidget(label("本队视角 · 绿：胜 / 灰：平 / 红：负", "Meta", wrap=True))
        if not rows:
            layout.addWidget(label("没有记录。", "Meta"))
        def build(dto):
            row = MatchRowCard(self.app.world(), dto, perspective_team_id=team_id if not upcoming else None)
            row.clicked.connect(lambda match_id: self.open_route("match", match_id=match_id))
            return row
        if paging is not None and rows:
            pager = macos.paginate_widgets(rows, layout, paging, build, default_size=page_size)
            if fixed_size:
                pager.sizes.hide()
        else:
            for dto in rows:
                layout.addWidget(build(dto))
        layout.addStretch(1)
        return frame

    def _honors_card(self, data: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle("荣誉", f"次级联赛所有名次 0 分；冠军仍保留 {rules.competition_label('zenith')} 资格"))
        if not data["honors"] and not data["archived_honors"]:
            layout.addWidget(label("尚无荣誉记录。", "Meta"))
        for row in data["archived_honors"][-10:]:
            layout.addWidget(label(f"第 {row['season']} 赛季 · {_honor_label(row)} · {row['points']} 分", "Meta", wrap=True))
        for row in data["honors"]:
            layout.addWidget(label(f"本季 · {_honor_label(row)} · {row['points']} 分", "Meta", wrap=True))
        return frame

    def _transfers_card(self, world: dict, rows: List[dict]) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle("转会", "逐场记录，含当时所属球队"))
        for row in rows:
            other = row["team_b"] if row["team_a"] != self.params.get("team_id") else row["team_a"]
            names = row.get("labels_a", []) + row.get("labels_b", [])
            layout.addWidget(label(f"第 {row['season']} 赛季第 {row['week']} 周 · 与 {self.app.team_name(other)} · {'、'.join(names)}", "Meta", wrap=True))
        return frame


class PlayersPage(BasePage):
    route_name = "players"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(t.S32, t.S24, t.S32, t.S24)
        outer.setSpacing(t.S12)
        outer.addWidget(PageHeader("观察", "球员目录", "评分按结算记录"))
        filters = QHBoxLayout()
        self.position = QComboBox()
        self.position.addItem("全部位置", "")
        for position in ("GK", "DF", "MF", "FW"):
            self.position.addItem(position, position)
        self.team = QComboBox()
        self.team.addItem("全部球队", "")
        self.kind = QComboBox()
        self.kind.addItem("全部球员", "all")
        self.kind.addItem("仅真实球员", "real")
        self.kind.setCurrentIndex(self.kind.findData(self.player_scope))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索球员…")
        self.search.setClearButtonEnabled(True)
        for widget in (self.position, self.team, self.kind, self.search):
            filters.addWidget(widget)
        self.search.setMinimumWidth(180)
        outer.addLayout(filters)
        self.table = DataTable()
        self.table.set_columns(
            [
                {"key": "name", "label": "球员", "stretch": True, "kind": "link"},
                macos.team_column("team", "球队"),
                {"key": "position", "label": "位置", "width": 58},
                {"key": "ability", "label": "能力", "width": 50, "numeric": True},
                {"key": "appearances", "label": "出场", "width": 50, "numeric": True},
                {"key": "goals", "label": "球", "width": 38, "numeric": True},
                {"key": "assists", "label": "助", "width": 38, "numeric": True},
                {"key": "rating", "label": "评分", "width": 62, "numeric": True, "decimals": 1},
                {"key": "market_value", "label": "身价", "width": 72, "numeric": True, "decimals": 2, "optional": True, "show_at": 1440},
            ]
        )
        self.table.cell_activated.connect(self._activated)
        self.table.row_activated.connect(lambda row: self.open_route("player", player_id=row["player_id"]))
        for widget in (self.position, self.team, self.kind):
            widget.currentIndexChanged.connect(lambda _: self._rebuild())
        self.search.textChanged.connect(lambda _: self._rebuild())
        outer.addWidget(self.table, 1)
        self.empty = EmptyState("没有符合条件的球员", "调整筛选条件。")
        outer.addWidget(self.empty)
        self.empty.hide()

    def _rebuild(self) -> None:
        if self.snapshot:
            self.refresh(self.snapshot, {})

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        if self.team.count() == 1:
            for team_id in sorted(world["teams"], key=lambda tid: self.app.team_name(tid)):
                self.team.addItem(self.app.team_name(team_id), team_id)
        data = queries.players_page(
            world,
            self.save_name,
            position=self.position.currentData() or None,
            team_id=self.team.currentData() or None,
            search=self.search.text().strip(),
            include_default=self.kind.currentData() != "real",
        )
        rows = data["rows"]
        self.table.set_rows(
            [
                {**row, "rating": row["rating"] if row["rating"] is not None else "—", "market_value": row["market_value"] if row["market_value"] is not None else "—"}
                for row in rows
            ]
        )
        self.table.hide_optional_columns(self.window().width())
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)

    def _activated(self, row: dict, key: str) -> None:
        if key == "team":
            self.open_route("team", team_id=row["team_id"])
        else:
            self.open_route("player", player_id=row["player_id"])


class PlayerProfilePage(BasePage):
    route_name = "player"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        self.scroll.reset()
        player_id = params.get("player_id")
        world = self.app.world()
        data = queries.player_profile(world, self.save_name, player_id) if player_id else None
        if not data:
            self.scroll.layout.addWidget(EmptyState("球员不存在", "该球员 ID 不在当前世界中。", self._back()))
            return
        back = QPushButton("← 球员目录")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.open_route("players"))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.S20)
        pos = Badge(data["position"], "accent")
        pos.setFixedHeight(38)
        layout.addWidget(pos, 0, Qt.AlignTop)
        if data.get("team_id"):
            layout.addWidget(TeamCrest(self.app.world()["teams"][data["team_id"]]["club_en"], 42), 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(t.S4)
        if data["team_id"]:
            role = "真实球员" if data["is_real"] else "默认球员"
            info.addWidget(PageHeader("球员" if macos.enabled() else data["team"], data["name"], f"{role} · 当前阵容与当季产出"))
            if macos.enabled():
                team_link = EntityLink(data["team"] + " →", "team", data["team_id"])
                team_link.setAccessibleName("所属球队：" + data["team"])
                team_link.clicked.connect(lambda: self.open_route("team", team_id=data["team_id"]))
                info.addWidget(team_link, 0, Qt.AlignLeft)
        else:
            info.addWidget(PageHeader("历史身份", data["name"], "已离开当前阵容；逐场与奖项历史仍归属此球员"))
        if macos.enabled():
            from football_simulator.app.profile_history import player_origin
            origin = player_origin(world, player_id)
            origin_label = label(origin["label"], "PlayerOrigin", wrap=True)
            origin_label.setStyleSheet(f"font-size: 17px; font-weight: 600; color: {t.PRIMARY};")
            info.addWidget(origin_label)
        header.setToolTip(f"稳定 ID：{data['player_id']}")
        layout.addLayout(info, 1)
        watch = QPushButton("取消关注" if data["watch"] else "关注球员")
        watch.setObjectName("Secondary")
        watch.clicked.connect(lambda: self.app.toggle_watch(player_id))
        layout.addWidget(watch, 0, Qt.AlignTop)
        self.scroll.layout.addWidget(header)
        honors_link = QPushButton("查看全部荣誉")
        honors_link.setObjectName("Secondary")
        honors_link.clicked.connect(lambda: self.open_route("player_honors", player_id=player_id))
        if macos.enabled():
            layout.addWidget(honors_link, 0, Qt.AlignTop)
        else:
            self.scroll.layout.addWidget(honors_link, 0, Qt.AlignLeft)
        stats = data["stats"]
        if macos.enabled():
            self.scroll.layout.addWidget(self._player_hero(data, stats))
        else:
            self.scroll.layout.addWidget(
                StatGrid(
                    [
                        (str(data["ability"]), "当前能力"),
                        (str(stats.get("appearances", 0)), "实际出场"),
                        (str(stats.get("goals", 0)), "进球"),
                        (str(stats.get("assists", 0)), "助攻"),
                        (f"{data['rating']:.1f}" if data["rating"] is not None else "—", "当季评分"),
                        (f"{data['market_value']:.2f}" if data["market_value"] is not None else "—", "身价"),
                    ]
                )
            )
        if macos.enabled():
            self._render_mac_sections(world, data)
            self.scroll.layout.addStretch(1)
            return
        self.scroll.layout.addWidget(self._settlement_card(data))
        self.scroll.layout.addWidget(self._career_totals_card(world, player_id))
        self.scroll.layout.addWidget(self._splits_card(world, data))
        self.scroll.layout.addWidget(self._match_log_card(world, data["match_log"]))
        if data["career"]:
            self.scroll.layout.addWidget(self._career_card(data["career"]))
        if data["awards"]:
            self.scroll.layout.addWidget(self._awards_card(data["awards"]))
        if data["transfers"]:
            self.scroll.layout.addWidget(self._transfers_card(data["transfers"]))
        self.scroll.layout.addStretch(1)

    def _player_hero(self, data: dict, stats: dict) -> QWidget:
        """Position-relevant season metrics; identity stays text, numbers stay small."""
        frame = card()
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(t.S20, t.S14, t.S20, t.S14)
        outer.setSpacing(t.S6)
        row = QHBoxLayout()
        row.setSpacing(t.S24)
        row.addWidget(_hero_stat_widget(str(data["ability"]), "当前能力"))
        for metric in role_view.role_metrics(data["position"], stats, data["rating"]):
            row.addWidget(_hero_stat_widget(metric["value"], metric["label"]))
        row.addWidget(_hero_stat_widget(role_view.valuation_text(data["market_value"]), "估值"))
        row.addStretch(1)
        outer.addLayout(row)
        if int(stats.get("appearances") or 0) == 0:
            outer.addWidget(label(role_view.missing_note(data["position"]), "Meta", wrap=True))
        return frame

    def _render_mac_sections(self, world: dict, data: dict) -> None:
        player_id = data["player_id"]
        state = self.detail_state(player_id)
        self.scroll.layout.addWidget(macos.section_tabs(
            [("概览", "overview"), ("生涯统计", "career"), ("赛季数据", "season"), ("逐场记录", "matches"), ("转会", "transfers"), ("荣誉", "honors")],
            state["section"], lambda section: self.set_detail_section(player_id, section),
        ))
        section = state["section"]
        if section == "career":
            self.scroll.layout.addWidget(self._career_totals_card(world, player_id))
        elif section == "season":
            self.scroll.layout.addWidget(self._season_stats_card(world, data["player_id"]))
        elif section == "matches":
            rows = queries._player_match_log(world, player_id, limit=len(world["season"]["fixtures"]))
            self.scroll.layout.addWidget(self._match_log_card(world, rows))
        elif section == "transfers":
            rows = transfer_history.ordered_transfers(world, player_id=player_id)
            self.scroll.layout.addWidget(transfer_history.history_card(self, world, rows, state.setdefault("transfers", {}), player_id=player_id))
        elif section == "honors":
            self.scroll.layout.addWidget(self._awards_card(data["awards"]))
        else:
            totals = records.career_totals(self.save_name, player_id)
            self.scroll.layout.addWidget(StatGrid([(str(totals[key]), caption) for key, caption in (("appearances", "生涯出场"), ("goals", "生涯进球"), ("assists", "生涯助攻"))]))
            self.scroll.layout.addWidget(self._settlement_card(data))

    def _back(self) -> QWidget:
        button = QPushButton("返回球员目录")
        button.clicked.connect(lambda: self.open_route("players"))
        return button

    def _season_stats_card(self, world: dict, player_id: str) -> QWidget:
        from football_simulator.app import profile_history
        state = self.detail_state(player_id)
        selected = state.setdefault("stats_season", int(world["season_number"]))
        data = profile_history.player_season_stats(world, self.save_name, player_id, selected)
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S16)
        box = QComboBox()
        box.setObjectName("PlayerStatsSeason")
        box.setAccessibleName("球员数据赛季")
        for season in data["options"]:
            box.addItem(f"第 {season} 赛季", season)
        box.setCurrentIndex(max(0, box.findData(selected)))
        def changed(_index):
            state["stats_season"] = box.currentData()
            self.app._refresh_current_page(restore_scroll=True)
        box.currentIndexChanged.connect(changed)
        layout.addWidget(SectionTitle(f"第 {selected} 赛季 · 各项赛事", "", box))
        layout.addWidget(StatGrid([(str(data["totals"][key]), title) for key, title in (("appearances", "出场"), ("goals", "进球"), ("assists", "助攻"))]))
        table = DataTable()
        table.setObjectName("PlayerSeasonStats")
        table.set_columns([
            {"key": "label", "label": "赛事", "stretch": True},
            macos.team_column("team", "当季效力球队"),
            *[{"key": key, "label": title, "width": 72, "numeric": True} for key, title in (("appearances", "出场"), ("goals", "进球"), ("assists", "助攻"))],
        ])
        table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]) if key == "team" and row.get("team_id") else None)
        macos.paginate_table(table, data["team_rows"], layout, state.setdefault("stats_pages", {}).setdefault(str(selected), {}))
        return frame

    def _settlement_card(self, data: dict) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S14, t.S20, t.S14)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle("评分与身价口径", data["freeze_note"]))
        winter = data.get("winter_settlement") or {}
        final = data.get("final_settlement") or {}
        layout.addWidget(label(f"冬季结算：评分 {winter.get('rating', '—')} · 身价 {winter.get('market_value', '—')}", "Meta"))
        layout.addWidget(label(f"年度结算：评分 {final.get('rating', '—')} · 身价 {final.get('market_value', '—')}", "Meta"))
        layout.addWidget(label(f"初始身价：{data.get('initial_market_value') if data.get('initial_market_value') is not None else '未结算'} · 当前能力 {data['ability']}", "Meta"))
        return frame



    def _career_totals_card(self, world: dict, player_id: str) -> QWidget:
        totals = records.career_totals(self.save_name, player_id)
        splits = records.career_splits(self.save_name, player_id)
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("职业生涯总计", "生涯总计、分赛事与分赛季"))
        layout.addWidget(
            label(
                f"出场 {totals['appearances']} · 进球 {totals['goals']} · 助攻 {totals['assists']}",
                "CardTitle",
            )
        )
        comp_table = DataTable()
        comp_table.set_columns(
            [
                {"key": "label", "label": "赛事", "stretch": True},
                {"key": "appearances", "label": "出场", "width": 56, "numeric": True},
                {"key": "goals", "label": "球", "width": 42, "numeric": True},
                {"key": "assists", "label": "助", "width": 42, "numeric": True},
            ]
        )
        comp_table.set_rows(splits["competition"] or [{"label": "暂无出场", "appearances": 0, "goals": 0, "assists": 0}])
        comp_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        comp_table.setFixedHeight(36 + (38 if macos.enabled() else 36) * max(1, len(splits["competition"])) + 4)
        if macos.enabled():
            layout.addWidget(label("按赛事", "CardTitle"))
        layout.addWidget(comp_table)
        season_table = DataTable()
        season_table.set_columns(
            [
                {"key": "season", "label": "赛季", "width": 64, "numeric": True},
                *([macos.team_column("team", "当季效力球队")] if macos.enabled() else []),
                {"key": "appearances", "label": "出场", "width": 56, "numeric": True},
                {"key": "goals", "label": "球", "width": 42, "numeric": True},
                {"key": "assists", "label": "助", "width": 42, "numeric": True},
            ]
        )
        season_table.set_rows(splits["season"] or [{"season": 0, "appearances": 0, "goals": 0, "assists": 0}])
        season_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        season_table.setFixedHeight(36 + 36 * max(1, len(splits["season"])) + 4)
        if macos.enabled():
            layout.addWidget(label("按赛季", "CardTitle"))
            from football_simulator.app.profile_history import player_team_seasons
            rows = player_team_seasons(world, self.save_name, player_id)
            season_table.setObjectName("PlayerCareerTeamSeasons")
            season_table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]) if key == "team" else None)
            state = self.detail_state(player_id).setdefault("career_seasons", {})
            macos.paginate_table(season_table, rows, layout, state)
        else:
            layout.addWidget(season_table)
        return frame

    def _splits_card(self, world: dict, data: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("赛事与球队分段", "个人总计 = 各赛事合计；逐场归属保留转会前后"))
        table = DataTable()
        table.set_columns(
            [
                {"key": "label", "label": "赛事", "stretch": True},
                {"key": "appearances", "label": "出场", "width": 56, "numeric": True},
                {"key": "goals", "label": "球", "width": 40, "numeric": True},
                {"key": "assists", "label": "助", "width": 40, "numeric": True},
                {"key": "rating", "label": "评分", "width": 62, "numeric": True, "decimals": 1},
            ]
        )
        rows = data["competition_splits"] or []
        table.set_rows(rows)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 38 * max(1, len(rows)) + 4)
        layout.addWidget(table)
        if data["team_splits"]:
            if macos.enabled():
                layout.addWidget(label("本季所属球队", "CardTitle"))
                for row in data["team_splits"]:
                    link = EntityLink(f"{row['team']} · {row['appearances']} 场 →", "team", row["team_id"])
                    link.clicked.connect(lambda _=False, tid=row["team_id"]: self.open_route("team", team_id=tid))
                    layout.addWidget(link, 0, Qt.AlignLeft)
            else:
                splits = "  ·  ".join(f"{row['team']} {row['appearances']} 场" for row in data["team_splits"])
                layout.addWidget(label("球队分段：" + splits, "Meta", wrap=True))
        return frame

    def _match_log_card(self, world: dict, rows: List[dict]) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("逐场记录", "0 产出但出场也会出现在这里"))
        if not rows:
            layout.addWidget(label("本季尚未出场。", "Meta"))
            return frame
        table = DataTable()
        if macos.enabled():
            fixtures = {fixture["match_id"]: fixture for fixture in world["season"]["fixtures"]}
            display_rows = []
            for row in rows:
                fixture = fixtures[row["match_id"]]
                home = row["team_id"] == fixture["home_team_id"]
                scores = queries.score_text(fixture).split("–")
                display_rows.append({**row,
                    "score": "–".join(scores if home else scores[::-1]),
                    "venue": "中立" if fixture.get("venue_mode") == "neutral" else "主" if home else "客",
                    "opponent_id": fixture["away_team_id"] if home else fixture["home_team_id"],
                })
            rows = display_rows
            table.set_columns([
                {"key": "week", "label": "周", "width": 48, "numeric": True},
                {"key": "label", "label": "赛事", "width": 170},
                {"key": "venue", "label": "场地", "width": 56},
                {"key": "opponent", "label": "对手", "stretch": True, "kind": "link"},
                {"key": "score", "label": "本队比分", "width": 84, "kind": "match", "alignment": Qt.AlignCenter},
                {"key": "goals", "label": "进球", "width": 60, "numeric": True},
                {"key": "assists", "label": "助攻", "width": 60, "numeric": True},
            ])
            table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["opponent_id"]) if key == "opponent" else self.open_route("match", match_id=row["match_id"]))
            table.row_activated.connect(lambda row: self.open_route("match", match_id=row["match_id"]))
            state = self.detail_state(self.params["player_id"]).setdefault("matches", {})
            macos.paginate_table(table, rows, layout, state)
            return frame
        table.set_columns(
            [
                {"key": "week", "label": "周", "width": 40, "numeric": True},
                {"key": "label", "label": "赛事", "width": 130},
                {"key": "opponent", "label": "对手", "width": 130},
                {"key": "score", "label": "比分", "stretch": True},
                {"key": "goals", "label": "球", "width": 38, "numeric": True},
                {"key": "assists", "label": "助", "width": 38, "numeric": True},
            ]
        )
        table.set_rows(rows)
        table.row_activated.connect(lambda row: self.open_route("match", match_id=row["match_id"]))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 38 * len(rows) + 4)
        layout.addWidget(table)
        return frame

    def _career_card(self, rows: List[dict]) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("生涯分季", "按赛季记录"))
        table = DataTable()
        table.set_columns(
            [
                {"key": "season", "label": "赛季", "width": 64, "numeric": True},
                *([macos.team_column("team", "当季效力球队")] if macos.enabled() else []),
                {"key": "appearances", "label": "出场", "width": 56, "numeric": True},
                {"key": "goals", "label": "球", "width": 42, "numeric": True},
                {"key": "assists", "label": "助", "width": 42, "numeric": True},
            ]
        )
        table.set_rows(rows)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 38 * len(rows) + 4)
        layout.addWidget(table)
        return frame

    def _awards_card(self, rows: List[dict]) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        if macos.enabled():
            # UIP-030 / P2-02: annual ranking is a read-only fact table.  The
            # stored rank is the only rank; score/value is never a fallback.
            # records.player_honors already dedupes archive/current by season.
            annual = records.annual_rank_rows(self.app.world(), self.params.get("player_id") or "")
            medals = [row for row in rows if row.get("competition") != "top20"]
            # An archived season is also present in current awards until the
            # next season starts.  Keep the archive copy only, by stable
            # season/competition/award identity, not by display text.
            seen_medals = set()
            unique_medals = []
            for row in medals:
                key = (row.get("season"), row.get("competition"), row.get("award"))
                if key in seen_medals:
                    continue
                seen_medals.add(key)
                unique_medals.append(row)
            medals = unique_medals
            # Keep the annual timeline compact beside the event awards on a
            # wide desktop; preserve the same tables and paging on reflow.
            annual_card = card()
            annual_layout = QVBoxLayout(annual_card)
            annual_layout.setContentsMargins(t.S20, t.S18, t.S20, t.S18)
            annual_layout.setSpacing(t.S12)
            annual_layout.addWidget(SectionTitle("年度排名"))
            if annual:
                table = DataTable()
                table.set_columns([
                    {"key": "season", "label": "赛季", "width": 70, "numeric": True},
                    {"key": "rank_text", "label": "名次", "width": 120},
                    macos.team_column("team", "当届球队", size=22),
                ])
                yearly = [
                    {**row, "rank_text": f"年度第 {row['rank']} 名" if row.get("rank") else "名次未记录"}
                    for row in annual
                ]
                table.set_rows(yearly)
                table.cell_activated.connect(
                    lambda row, key: self.open_route("team", team_id=row["team_id"])
                    if key == "team" and row.get("team_id") else None
                )
                table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                table.setFixedHeight(38 + 38 * len(yearly) + 4)
                annual_layout.addWidget(table)
            else:
                annual_layout.addWidget(label("暂无年度排名记录", "Meta", wrap=True))
            medal_card = card()
            medal_layout = QVBoxLayout(medal_card)
            medal_layout.setContentsMargins(t.S20, t.S18, t.S20, t.S18)
            medal_layout.setSpacing(t.S12)
            medal_layout.addWidget(SectionTitle("赛事奖项"))
            table = DataTable()
            table.set_columns([
                {"key": "season", "label": "赛季", "width": 56, "numeric": True},
                {"key": "competition_label", "label": "赛事", "stretch": True},
                {"key": "award", "label": "奖项", "width": 108},
                {"key": "value", "label": "数值", "width": 56, "numeric": True},
            ])
            data = [{**row, "competition_label": rules.competition_label(row["competition"]) if row["competition"] in rules.ALL_COMPETITION_IDS else row["competition"]} for row in medals]
            macos.paginate_table(table, data, medal_layout, self.detail_state(self.params["player_id"]).setdefault("awards", {}))
            if not medals:
                medal_layout.addWidget(label("暂无个人奖项", "Meta", wrap=True))
            # The legacy branch below retains its existing layout on Windows.
            frame.deleteLater()
            panels = macos.AdaptivePanels(annual_card, medal_card)
            panels.setStyleSheet("QLabel#Meta { font-size: 16px; } QHeaderView::section { font-size: 16px; }")
            return panels
        for row in rows:
            competition = rules.competition_label(row["competition"]) if row["competition"] in rules.CUP_IDS else row["competition"]
            layout.addWidget(label(f"第 {row['season']} 赛季 · {competition} · {row['award']}（{row.get('value')}）", "Meta", wrap=True))
        return frame

    def _transfers_card(self, rows: List[dict]) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle("转会记录", "冬窗转会后可继续代表新队参加同一届杯赛"))
        for row in rows:
            layout.addWidget(label(f"第 {row['season']} 赛季第 {row['week']} 周 · {self.app.team_name(row['team_a'])} ⇄ {self.app.team_name(row['team_b'])}", "Meta"))
        return frame


class HistoryPage(BasePage):
    route_name = "history"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)
        self.season: Optional[int] = None

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        options = queries.history_page(world, self.save_name, None).get("options", [])
        available = {option["season"] for option in options}
        if self.season not in available:
            self.season = None
        data = queries.history_page(world, self.save_name, params.get("season") or self.season)
        self.scroll.reset()
        self.scroll.layout.addWidget(PageHeader("历史", "历史与荣誉", "查看该赛季的冠军、积分、奖项与荣誉分"))
        sections = QHBoxLayout()
        for route, text in (("records", "历史纪录"), ("honors", "荣誉馆")):
            button = QPushButton(text)
            button.setObjectName("Secondary")
            button.clicked.connect(lambda _=False, r=route: self.open_route(r))
            sections.addWidget(button)
        if macos.enabled():
            total_button = QPushButton("球队荣誉总榜")
            total_button.setObjectName("Secondary")
            total_button.clicked.connect(lambda: self.open_route("honors"))
            sections.addWidget(total_button)
            annual_button = QPushButton("年度 Top20")
            annual_button.setObjectName("Secondary")
            annual_button.clicked.connect(lambda: self.open_route("annual_top20"))
            sections.addWidget(annual_button)
        sections.addStretch(1)
        self.scroll.layout.addLayout(sections)
        if data["empty"]:
            self.scroll.layout.addWidget(EmptyState("还没有已归档赛季", "完成第 1 赛季并结束夏窗后，这里会出现正式档案。"))
            return
        selector = QHBoxLayout()
        self.season_box = QComboBox()
        for option in data["options"]:
            self.season_box.addItem(option["label"], option["season"])
        index = self.season_box.findData(data["selected"])
        if index >= 0:
            self.season_box.setCurrentIndex(index)
        self.season_box.currentIndexChanged.connect(self._season_changed)
        selector.addWidget(label("赛季", "Meta"))
        selector.addWidget(self.season_box)
        selector.addStretch(1)
        self.scroll.layout.addLayout(selector)
        archive = data["archive"]
        self.season = archive["season"]
        if macos.enabled():
            state = self.__dict__.setdefault("history_section", {"section": "overview"})
            self.scroll.layout.addWidget(macos.section_tabs(
                [("概览", "overview"), ("积分榜", "tables"), ("奖项", "awards"), ("荣誉分", "honors"), ("总荣誉榜", "all_honors"), ("代表比赛", "matches")],
                state["section"], self._set_history_section,
            ))
            section = state["section"]
            if section == "all_honors":
                from football_simulator.ui.pages.honors import TeamHonorLeaderboard
                self.scroll.layout.addWidget(TeamHonorLeaderboard(world, self.detail_state("team_honor_totals"), lambda tid: self.open_route("team_honors", team_id=tid)))
            if section in ("overview", "awards", "matches"):
                self.scroll.layout.addWidget(self._champions_card(world, archive))
            if section in ("overview", "tables"):
                self.scroll.layout.addWidget(self._tables_card(world, archive))
            if section in ("overview", "honors"):
                self.scroll.layout.addWidget(self._honors_card(world, archive))
            if section in ("overview", "awards"):
                self.scroll.layout.addWidget(self._awards_card(archive))
            if section == "matches":
                if archive.get("representative_matches"):
                    frame = card()
                    layout = QVBoxLayout(frame)
                    layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
                    layout.setSpacing(t.S6)
                    layout.addWidget(SectionTitle("代表比赛", f"第 {archive['season']} 赛季"))
                    for row in archive["representative_matches"][:10]:
                        link = EntityLink(f"第 {row['week']} 周 · {row['label']} · {row['home']} {row['score']} {row['away']}", "match", row["match_id"])
                        link.clicked.connect(lambda _=False, mid=row["match_id"]: self.open_route("match", match_id=mid))
                        layout.addWidget(link)
                    self.scroll.layout.addWidget(frame)
                else:
                    self.scroll.layout.addWidget(label("该赛季没有代表性比赛记录。", "Meta", wrap=True))
            self.scroll.layout.addStretch(1)
            return
        self.scroll.layout.addWidget(self._champions_card(world, archive))
        self.scroll.layout.addWidget(self._tables_card(world, archive))
        self.scroll.layout.addWidget(self._honors_card(world, archive))
        self.scroll.layout.addWidget(self._awards_card(archive))
        if archive.get("representative_matches"):
            frame = card()
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S6)
            layout.addWidget(SectionTitle("代表比赛", "历届赛季"))
            for row in archive["representative_matches"][:6]:
                link = EntityLink(f"第 {row['week']} 周 · {row['label']} · {row['home']} {row['score']} {row['away']}", "match", row["match_id"])
                link.clicked.connect(lambda _=False, mid=row["match_id"]: self.open_route("match", match_id=mid))
                layout.addWidget(link)
            self.scroll.layout.addWidget(frame)
        self.scroll.layout.addStretch(1)

    def _set_history_section(self, key: str) -> None:
        self.history_section = {"section": key}
        self.refresh(self.snapshot, {"season": self.season})

    def _season_changed(self) -> None:
        self.season = self.season_box.currentData()
        if self.snapshot:
            self.refresh(self.snapshot, {"season": self.season})

    def _champions_card(self, world: dict, archive: dict) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle(f"第 {archive['season']} 赛季冠军", "联赛与杯赛冠军均来自正式归档"))
        league = (archive.get("premier_table") or [{}])[0]
        if league:
            columns = QHBoxLayout()
            columns.addWidget(label("一级联赛", "Meta"), 1)
            columns.addWidget(label(league.get("team", "—"), "Strong"), 0)
            layout.addLayout(columns)
        for row in archive["cup_champions"]:
            columns = QHBoxLayout()
            columns.addWidget(label(row["label"], "Meta"), 1)
            columns.addWidget(label(row["team"], "Strong"), 0)
            layout.addLayout(columns)
        if not archive["cup_champions"]:
            layout.addWidget(label("本季没有杯赛冠军（第 1 赛季无杯赛）；一级联赛冠军如上。", "Meta"))
        transition = archive.get("transition") or {}
        if transition.get("promoted"):
            layout.addWidget(label("升级：" + "、".join(self.app.team_name(tid) for tid in transition["promoted"]), "Meta", wrap=True))
        if transition.get("relegated"):
            layout.addWidget(label("降级：" + "、".join(self.app.team_name(tid) for tid in transition["relegated"]), "Meta", wrap=True))
        return frame

    def _tables_card(self, world: dict, archive: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("最终积分榜", "归档名次；次级联赛不产生球队荣誉分"))
        for league, label_text, rows in ((rules.PREMIER, "一级联赛", archive["premier_table"]), (rules.SECOND, "次级联赛", archive["second_table"])):
            layout.addWidget(label(label_text, "CardTitle"))
            if macos.enabled():
                layout.addWidget(label(macos.standings_legend(league), "Meta", wrap=True))
            table = DataTable()
            table.set_columns(
                [
                    {"key": "rank", "label": "#", "width": 36, "numeric": True},
                    macos.team_column("team"),
                    {"key": "played", "label": "赛", "width": 42, "numeric": True},
                    {"key": "wins", "label": "胜", "width": 42, "numeric": True},
                    {"key": "draws", "label": "平", "width": 42, "numeric": True},
                    {"key": "losses", "label": "负", "width": 42, "numeric": True},
                    {"key": "goal_diff", "label": "净胜", "width": 50, "numeric": True},
                    {"key": "points", "label": "积分", "width": 50, "numeric": True},
                ]
            )
            table.set_rows(rows)
            table.set_league_style(league)
            table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]))
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setFixedHeight(36 + 38 * len(rows) + 4)
            layout.addWidget(table)
        return frame

    def _honors_card(self, world: dict, archive: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("球队荣誉积分", "每个最终层级一次，跨赛事累加"))
        table = DataTable()
        table.set_columns(
            [
                macos.team_column("team", "球队"),
                {"key": "points", "label": "总荣誉分", "width": 90, "numeric": True},
            ]
        )
        table.set_rows(archive["honor_totals"])
        table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 38 * min(12, len(archive["honor_totals"])) + 4)
        if macos.enabled():
            macos.paginate_table(table, archive["honor_totals"], layout, self.detail_state(f"history:{archive['season']}").setdefault("honors", {}))
        else:
            layout.addWidget(table)
        if not macos.enabled() and len(archive["honor_totals"]) > 12:
            layout.addWidget(label(f"其余 {len(archive['honor_totals']) - 12} 支球队详见球队页。", "Meta"))
        return frame

    def _awards_card(self, archive: dict) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle("赛季奖项", "" if macos.enabled() else "年度 Top20 与各赛事个人奖"))
        for competition, values in (archive.get("awards") or {}).get("competitions", {}).items():
            label_text = rules.competition_label(competition) if competition in rules.CUP_IDS else rules.display_name(competition)
            layout.addWidget(label(f"{label_text} · 射手 {values.get('top_scorer') or '空缺'} · 助攻 {values.get('assist_leader') or '空缺'} · MVP {values.get('mvp') or '空缺'}", "Meta", wrap=True))
        top20 = archive.get("top20") or []
        if top20 and not macos.enabled():
            # UIP-030：只展示已保存的名次与当届球队，不展示任何计算。
            layout.addWidget(SectionTitle("年度 Top20", ""))
            table = DataTable()
            table.set_columns(
                [
                    {"key": "rank", "label": "#", "width": 40, "numeric": True},
                    {"key": "label", "label": "球员", "stretch": True, "kind": "link"},
                    macos.team_column("team", "当届球队"),
                ]
            )
            table.set_rows(macos.with_team_clubs(self.app.world(), top20))
            table.cell_activated.connect(
                lambda row, key: self.open_route("team", team_id=row["team_id"]) if key == "team" else self.open_route("player", player_id=row["player_id"])
            )
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setFixedHeight(36 + 38 * len(top20) + 4)
            layout.addWidget(table)
        return frame


def _honor_label(row: dict) -> str:
    competition = row["competition"]
    if competition in rules.CUP_IDS:
        return rules.competition_label(competition) + " · " + str(row.get("tier", ""))
    return rules.display_name(competition) + " · " + str(row.get("tier", ""))
