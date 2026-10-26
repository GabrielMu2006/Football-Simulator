"""赛事目录、赛事详情与赛季日历页面。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from football_simulator.app import queries, records
from football_simulator.game import rules
from football_simulator.ui import tokens as t
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui import competition_boards as boards
from football_simulator.ui.presentation import bracket as bracket_view
from football_simulator.ui.presentation import swiss as swiss_view
from football_simulator.ui.components import (
    BracketBoard,
    Badge,
    DataTable,
    EmptyState,
    EntityLink,
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
from football_simulator.ui.trophies import TROPHY_IDS, TrophyImage
from football_simulator.ui.team_crest import TeamCrest


class CompetitionsPage(BasePage):
    route_name = "competitions"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        self.scroll.reset()
        world = self.app.world()
        data = queries.competitions_page(world)
        if macos.enabled():
            matches = [m for m in world["season"]["fixtures"] if m["competition"] == rules.PLAYOFF]
            winner = world["season"].get("promotion_playoff", {}).get("winner")
            data["entries"].append({"id": rules.PLAYOFF, "label": rules.display_name(rules.PLAYOFF), "active": bool(matches),
                "status": "已结束" if winner else "进行中" if matches else "待确定",
                "detail": "次级联赛第 3–6 名争夺最后一个升级名额；第 46–49 周进行。",
                "played": sum(m["status"] == "completed" for m in matches), "total": 6,
                "champion": self.app.team_name(winner) if winner else None})
        self.scroll.layout.addWidget(
            PageHeader(
                f"第 {data['season']} 赛季 · 第 {data['week']} 周",
                "赛事",
                "" if macos.enabled() else "联赛看积分，杯赛看阶段、签表与晋级规则；未举办的赛事不会显示假赛程。",
            )
        )
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(t.S16)
        columns = 3 if self.width() >= 1560 else 2
        for index, entry in enumerate(data["entries"]):
            grid.addWidget(self._competition_card(world, entry), index // columns, index % columns)
        for index in range(columns):
            grid.setColumnStretch(index, 1)
        self.scroll.layout.addWidget(grid_widget)
        self.scroll.layout.addStretch(1)

    def _competition_card(self, world: dict, entry: dict) -> QWidget:
        whole_card = macos.enabled()
        frame = macos.NavigationCard(entry["label"]) if whole_card else card()
        frame.setProperty("competitionId", entry["id"])
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S18, t.S20, t.S18)
        layout.setSpacing(t.S8)
        top = QHBoxLayout()
        if entry["id"] in TROPHY_IDS:
            top.addWidget(TrophyImage(entry["id"], 64, muted=not entry["active"]))
        top.addWidget(label(entry["label"], "CardTitle", wrap=True), 1)
        badge = Badge(entry["status"], _status_variant(entry["status"]))
        if whole_card:
            top.addWidget(badge, 0, Qt.AlignVCenter)
        else:
            top.addWidget(badge)
        layout.addLayout(top)
        layout.addWidget(label(entry["detail"], "Meta", wrap=True))
        progress = f"{entry['played']} / {entry['total']} 场" if entry["active"] else f"计划 {entry['total']} 场"
        layout.addWidget(label(progress, "Meta"))
        if entry.get("champion"):
            layout.addWidget(label(f"冠军：{entry['champion']}", "Strong"))
        if whole_card:
            layout.addStretch(1)
            footer = QHBoxLayout()
            footer.addWidget(label("查看赛事", "CardAction"))
            footer.addStretch(1)
            footer.addWidget(label("→", "CardAction"))
            layout.addLayout(footer)
            frame.clicked.connect(lambda _=False, competition_id=entry["id"]: self.open_route("competition", competition_id=competition_id))
            for child in frame.findChildren(QWidget):
                child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        else:
            link = EntityLink("查看赛事", "competition", entry["id"])
            link.clicked.connect(lambda _=False, competition_id=entry["id"]: self.open_route("competition", competition_id=competition_id))
            layout.addWidget(link, 0, Qt.AlignLeft)
            layout.addStretch(1)
        return frame


class CompetitionDetailPage(BasePage):
    route_name = "competition"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        self.scroll.reset()
        competition_id = params.get("competition_id") or rules.PREMIER
        world = self.app.world()
        selected = max(1, min(int(world["season_number"]), int(params.get("season") or world["season_number"])))
        if macos.enabled():
            from football_simulator.app import edition_history
            live_world = world
            self._display_world = edition_history.display_world(world, self.save_name, competition_id, selected)
            selector = QComboBox()
            selector.setObjectName("CompetitionSeason")
            selector.setAccessibleName("赛事赛季")
            for number in range(int(live_world["season_number"]), 0, -1):
                text = f"第 {number} 赛季" + (" · 当季" if number == int(live_world["season_number"]) else "")
                selector.addItem(text, number)
            selector.setCurrentIndex(max(0, selector.findData(selected)))
            selector.currentIndexChanged.connect(lambda _: self.open_route("competition", competition_id=competition_id, season=selector.currentData()))
            self.scroll.layout.addWidget(selector, 0, Qt.AlignRight)
            if self._display_world is None:
                self.scroll.layout.addWidget(PageHeader("历史赛事", rules.display_name(competition_id), f"第 {selected} 赛季"))
                self.scroll.layout.addWidget(EmptyState("本届没有比赛记录", "该赛季未举办这项赛事。" if competition_id in rules.CUP_IDS and not rules.cup_active(competition_id, selected) else "尚无本届可查看的比赛。"))
                return
            world = self._display_world
        data = queries.competition_detail(world, self.save_name, competition_id, live_standings=macos.enabled())
        back = QPushButton("← 返回之前页面" if self.route_name == "history_competition" else "← 赛事目录")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.app.go_back() if self.route_name == "history_competition" else self.open_route("competitions"))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        if data["kind"] == "league":
            self._render_league(world, data)
        elif data["kind"] == "cup":
            self._render_cup(world, data)
        elif data["kind"] == "playoff":
            self._render_playoff(world, data)
        else:
            self.scroll.layout.addWidget(EmptyState("未知赛事", "该赛事 ID 不在当前规则注册表中。"))

    def _render_league(self, world: dict, data: dict) -> None:
        if macos.enabled():
            self._render_league_macos(world, data)
            return
        subtitle = data["description"]
        if data.get("champion"):
            subtitle += f" 冠军：{data['champion']}。"
        self.scroll.layout.addWidget(self._trophy_header(data, subtitle))
        self.scroll.layout.addWidget(
            self._table_card(
                "积分榜",
                "积分→净胜球→进球→相互战绩→上季排名",
                [
                    {"key": "rank", "label": "#", "width": 42, "numeric": True},
                    macos.team_column("team"),
                    {"key": "played", "label": "赛", "width": 44, "numeric": True},
                    {"key": "wins", "label": "胜", "width": 44, "numeric": True},
                    {"key": "draws", "label": "平", "width": 44, "numeric": True},
                    {"key": "losses", "label": "负", "width": 44, "numeric": True},
                    {"key": "goals_for", "label": "进", "width": 48, "numeric": True},
                    {"key": "goals_against", "label": "失", "width": 48, "numeric": True},
                    {"key": "goal_diff", "label": "净胜", "width": 56, "numeric": True},
                    {"key": "points", "label": "积分", "width": 56, "numeric": True},
                ],
                data["table"],
                lambda row, key: self.open_route("team", team_id=row["team_id"]),
                league=data["id"],
            )
        )
        rounds_widget = QWidget()
        layout = QVBoxLayout(rounds_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.S12)
        if macos.enabled():
            state = self.detail_state(f"{data['id']}:{data['season']}")
            rounds = data["rounds"]
            upcoming = next((row for row in rounds if any(match["status"] != "completed" for match in row["matches"])), None)
            default = upcoming or (rounds[-1] if rounds else None)
            chosen = state.get("round", default["round"] if default else None)
            selector = QComboBox()
            selector.setObjectName("LeagueRound")
            selector.setAccessibleName("选择联赛轮次")
            for row in rounds:
                played = sum(match["status"] == "completed" for match in row["matches"])
                selector.addItem(f"第 {row['round']} 轮 · {played}/{len(row['matches'])} 场已完成", row["round"])
            selector.setCurrentIndex(max(0, selector.findData(chosen)))
            def select_round(_index):
                state["round"] = selector.currentData()
                self.app._refresh_current_page(restore_scroll=True)
            selector.currentIndexChanged.connect(select_round)
            layout.addWidget(SectionTitle("联赛赛程", "按轮次选择查看，保留全部比赛记录", selector))
            row = next((row for row in rounds if row["round"] == selector.currentData()), None)
            if row:
                layout.addWidget(self._round_card(world, row))
            self.scroll.layout.addWidget(rounds_widget)
            if data.get("awards"):
                self.scroll.layout.addWidget(self._awards_card(data["awards"], pending=not world["season"].get("finalized")))
            return
        current = self.snapshot.get("week", 1)
        visible = [round_data for round_data in data["rounds"] if any(not m["status"] == "completed" for m in round_data["matches"])]
        chosen = visible[:3] if visible else data["rounds"][-3:]
        for round_data in chosen:
            layout.addWidget(self._round_card(world, round_data))
        layout.addWidget(SectionTitle("全部轮次", "按轮次倒序展示最近安排"))
        for round_data in reversed(data["rounds"]):
            if round_data in chosen:
                continue
            layout.addWidget(label(f"第 {round_data['round']} 轮 · {len(round_data['matches'])} 场", "Meta"))
        self.scroll.layout.addWidget(rounds_widget)
        if data.get("awards"):
            self.scroll.layout.addWidget(self._awards_card(data["awards"]))

    def _render_league_macos(self, world: dict, data: dict) -> None:
        key = f"{data['id']}:{data['season']}"
        state = self.detail_state(key)
        state.setdefault("section", "overview")
        subtitle = f"{data['label']} · 第 {data['season']} 赛季"
        if data.get("champion"):
            subtitle += f" · 冠军 {data['champion']}"
        self.scroll.layout.addWidget(self._trophy_header(data, subtitle))
        tabs_row = QHBoxLayout()
        tabs = macos.section_tabs(
            [("积分榜", "overview"), ("赛程", "matches"), ("球员榜", "players"), ("个人奖项", "awards"), ("历届冠军", "champions"), ("历史积分榜", "history")],
            state["section"],
            lambda section: self.set_detail_section(key, section),
        )
        tabs_row.addWidget(tabs, 1)
        rules_button = QPushButton("赛事规则")
        rules_button.setObjectName("Ghost")
        rules_button.setCheckable(True)
        rules_button.setChecked(bool(state.get("rules_open")))
        rules_button.toggled.connect(lambda checked, state=state: self._toggle_league_rules(state, checked))
        tabs_row.addWidget(rules_button, 0)
        holder = QWidget()
        holder.setLayout(tabs_row)
        self.scroll.layout.addWidget(holder)
        if state.get("rules_open"):
            self.scroll.layout.addWidget(self._league_rules_card(data))
        section = state["section"]
        if section == "overview":
            self.scroll.layout.addWidget(
                self._table_card(
                    "积分榜",
                    "",
                    [
                        {"key": "rank", "label": "#", "width": 40, "numeric": True},
                        {"key": "team", "label": "球队", "stretch": True, "kind": "link", "icon_key": "club", "icon_size": 24},
                        {"key": "played", "label": "赛", "width": 42, "numeric": True},
                        {"key": "wins", "label": "胜", "width": 42, "numeric": True},
                        {"key": "draws", "label": "平", "width": 42, "numeric": True},
                        {"key": "losses", "label": "负", "width": 42, "numeric": True},
                        {"key": "goals_for", "label": "进", "width": 46, "numeric": True},
                        {"key": "goals_against", "label": "失", "width": 46, "numeric": True},
                        {"key": "goal_diff", "label": "净胜", "width": 54, "numeric": True},
                        {"key": "points", "label": "积分", "width": 52, "numeric": True},
                    ],
                    data["table"],
                    lambda row, column: self.open_route("team", team_id=row["team_id"]),
                    league=data["id"],
                )
            )
        elif section == "matches":
            self.scroll.layout.addWidget(self._league_matches_card(data, state))
        elif section == "players":
            self.scroll.layout.addWidget(
                boards.DualLeaderboard(world, queries.competition_leaders(world, data["id"]), competition=data["id"])
            )
        elif section == "awards":
            self.scroll.layout.addWidget(self._awards_card(data["awards"], pending=not world["season"].get("finalized")))
        elif section == "champions":
            self.scroll.layout.addWidget(self._league_champions_card(data, state))
        else:
            self.scroll.layout.addWidget(self._league_history_card(world, data, state))
        self.scroll.layout.addStretch(1)

    def _league_champions_card(self, data: dict, state: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S12)
        layout.addWidget(SectionTitle("历届冠军"))
        # Use the live world's full history even when viewing an earlier edition.
        rows = [{**row, "edition": "查看积分榜"}
                for row in records.competition_champions(self.app.world(), data["id"])]
        if not rows:
            layout.addWidget(EmptyState("尚无联赛冠军", "本届联赛结束后，冠军会出现在这里。"))
            return frame
        table = DataTable()
        table.setObjectName("LeagueChampions")
        table.set_columns([
            {"key": "season", "label": "赛季", "width": 96, "numeric": True},
            macos.team_column("team", "冠军球队", size=28),
            {"key": "edition", "label": "赛季回顾", "width": 140, "kind": "link"},
        ])

        def open_champion(row, column):
            if column == "team":
                self.open_route("team", team_id=row["team_id"])
            else:
                self.detail_state(f"{data['id']}:{row['season']}")["section"] = "overview"
                self.open_route("competition", competition_id=data["id"], season=row["season"])

        table.cell_activated.connect(open_champion)
        table.row_activated.connect(lambda row: open_champion(row, "edition"))
        macos.paginate_table(table, rows, layout, state.setdefault("champion_pages", {}))
        return frame

    def _toggle_league_rules(self, state: dict, checked: bool) -> None:
        state["rules_open"] = checked
        self.app._refresh_current_page(restore_scroll=True)

    def _league_rules_card(self, data: dict) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle("赛事规则", data["label"]))
        layout.addWidget(label("排序：积分 → 净胜球 → 进球 → 相互战绩 → 上季排名。", "Meta", wrap=True))
        if data["id"] == rules.PREMIER:
            layout.addWidget(label("第 4 / 8 / 12 / 16 名后为分区线；第 18–20 名为降级区。", "Meta", wrap=True))
        else:
            layout.addWidget(label("第 1–2 名直接升级；第 3–6 名进入升级附加赛。", "Meta", wrap=True))
        return frame

    def _league_matches_card(self, data: dict, state: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S10)
        rounds = data["rounds"]
        upcoming = next((row for row in rounds if any(match["status"] != "completed" for match in row["matches"])), None)
        default = upcoming or (rounds[-1] if rounds else None)
        chosen = state.get("round", default["round"] if default else None)
        selector = QComboBox()
        selector.setObjectName("LeagueRound")
        selector.setAccessibleName("选择联赛轮次")
        for row in rounds:
            played = sum(match["status"] == "completed" for match in row["matches"])
            selector.addItem(f"第 {row['round']} 轮 · {played}/{len(row['matches'])} 场已完成", row["round"])
        selector.setCurrentIndex(max(0, selector.findData(chosen)))
        state["round"] = selector.currentData()

        def select_round(_index):
            state["round"] = selector.currentData()
            self.app._refresh_current_page(restore_scroll=True)

        selector.currentIndexChanged.connect(select_round)
        layout.addWidget(SectionTitle("赛程", "", selector))
        row = next((row for row in rounds if row["round"] == selector.currentData()), None)
        if row:
            layout.addWidget(self._round_card(self.app.world(), row))
        return frame

    def _league_history_card(self, world: dict, data: dict, state: dict) -> QWidget:
        history = queries.archived_league_table(world, data["id"], state.get("history_season"))
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S10)
        selector = QComboBox()
        selector.setAccessibleName("选择历史赛季")
        if history["seasons"]:
            for season in history["seasons"]:
                selector.addItem(f"第 {season} 赛季", season)
            selector.setCurrentIndex(max(0, selector.findData(history["season"])))
            state["history_season"] = selector.currentData()

            def select_season(_index):
                state["history_season"] = selector.currentData()
                self.app._refresh_current_page(restore_scroll=True)

            selector.currentIndexChanged.connect(select_season)
            layout.addWidget(SectionTitle("历史积分榜", "读取已归档的完整最终表", selector))
        else:
            layout.addWidget(SectionTitle("历史积分榜", "读取已归档的完整最终表"))
        if history["empty"]:
            layout.addWidget(label(history.get("reason") or "暂无已结束赛季", "Meta", wrap=True))
            return frame
        if history.get("champion"):
            chip = QHBoxLayout()
            chip.addWidget(TrophyImage("premier" if history["table_key"] == "premier" else "second", 36), 0, Qt.AlignVCenter)
            chip.addWidget(label(f"第 {history['season']} 赛季冠军：{history['champion']['team']}", "CardTitle"), 1)
            layout.addLayout(chip)
        table = DataTable()
        table.set_columns([
            {"key": "rank", "label": "#", "width": 40, "numeric": True},
            {"key": "team", "label": "球队", "stretch": True, "kind": "link", "icon_key": "club", "icon_size": 24},
            {"key": "played", "label": "赛", "width": 42, "numeric": True},
            {"key": "wins", "label": "胜", "width": 42, "numeric": True},
            {"key": "draws", "label": "平", "width": 42, "numeric": True},
            {"key": "losses", "label": "负", "width": 42, "numeric": True},
            {"key": "goals_for", "label": "进", "width": 46, "numeric": True},
            {"key": "goals_against", "label": "失", "width": 46, "numeric": True},
            {"key": "goal_diff", "label": "净胜", "width": 54, "numeric": True},
            {"key": "points", "label": "积分", "width": 52, "numeric": True},
        ])
        table.set_rows(history["rows"])
        table.set_league_style(history["table_key"])
        table.cell_activated.connect(lambda row, column: self.open_route("team", team_id=row["team_id"]))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(38 + 38 * len(history["rows"]) + 4)
        layout.addWidget(table)
        if not history.get("complete"):
            layout.addWidget(label("该届档案不完整，名次按归档顺序显示。", "Meta", wrap=True))
        return frame

    def _round_card(self, world: dict, round_data: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S14, t.S16, t.S14)
        layout.setSpacing(t.S6)
        layout.addWidget(label(f"第 {round_data['round']} 轮", "CardTitle"))
        for row in round_data["matches"]:
            match_row = MatchRowCard(world, row)
            match_row.clicked.connect(lambda match_id: self.open_route("match", match_id=match_id))
            layout.addWidget(match_row)
        return frame

    def _render_cup(self, world: dict, data: dict) -> None:
        subtitle = data.get("bracket_note", "") if not macos.enabled() else data.get("current_stage", "")
        if not data["active"]:
            self.scroll.layout.addWidget(self._trophy_header(data, subtitle))
            self.scroll.layout.addWidget(
                self._notice_card(
                    "本届未举办",
                    data.get("inactive_reason", "赛事尚未开放。") + " 本届不生成签表、不占用场地；可在赛事目录查看联赛。",
                )
            )
            self.scroll.layout.addStretch(1)
            return
        subtitle = " · ".join(part for part in (subtitle, f"{data.get('entrants', 0)} 队", f"{data['match_count']} 场") if part)
        self.scroll.layout.addWidget(self._trophy_header(data, subtitle))
        self.scroll.layout.addWidget(self._phase_card(data))
        if macos.enabled():
            key = f"{data['id']}:{data['season']}"
            state = self.detail_state(key)
            tabs_row = QHBoxLayout()
            tabs = macos.section_tabs(
                [("阶段与签表", "overview"), ("赛程与结果", "matches"), ("球员榜", "players"), ("个人奖项", "awards"), ("历届冠军", "history")],
                state["section"], lambda section: self.set_detail_section(key, section),
            )
            tabs_row.addWidget(tabs, 1)
            rules_button = QPushButton("赛事规则")
            rules_button.setObjectName("Ghost")
            rules_button.setCheckable(True)
            rules_button.setChecked(bool(state.get("rules_open")))
            rules_button.toggled.connect(lambda checked, state=state: self._toggle_rules(state, checked))
            tabs_row.addWidget(rules_button, 0)
            holder = QWidget()
            holder.setLayout(tabs_row)
            self.scroll.layout.addWidget(holder)
            if state.get("rules_open"):
                self.scroll.layout.addWidget(self._rules_card(data))
            if state["section"] != "overview":
                self._cup_section(world, data, state)
                self.scroll.layout.addStretch(1)
                return
        winners = []
        if data.get("champion"):
            winners.append(("冠军", data["champion"]))
        if data.get("runner_up"):
            winners.append(("亚军", data["runner_up"]))
        if winners and macos.enabled():
            self.scroll.layout.addWidget(self._champion_hero(world, data))
        elif winners:
            stat = StatGrid([(value, caption) for caption, value in winners])
            self.scroll.layout.addWidget(stat)
        if macos.enabled() and self._bracket_first(data):
            bracket_block = self._bracket_block(world, data)
            if bracket_block is not None:
                self.scroll.layout.addWidget(bracket_block)
        if data.get("groups"):
            groups = QWidget()
            grid = QGridLayout(groups)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(t.S16)
            knockout_started = bool(data.get("knockout"))
            for index, group in enumerate(data["groups"]):
                zones, legend = self._group_zones(data["id"], group, knockout_started)
                grid.addWidget(
                    self._table_card(
                        group["name"] + " 积分榜",
                        "",
                        [
                            {"key": "rank", "label": "#", "width": 34, "numeric": True},
                            {"key": "team", "label": "球队", "stretch": True, "kind": "link", "icon_key": "club", "icon_size": 24},
                            {"key": "played", "label": "赛", "width": 38, "numeric": True},
                            {"key": "goal_diff", "label": "净胜", "width": 48, "numeric": True},
                            {"key": "points", "label": "分", "width": 44, "numeric": True},
                        ],
                        group["table"],
                        lambda row, key: self.open_route("team", team_id=row["team_id"]),
                        zones=zones,
                        legend=legend,
                    ),
                    index // 2,
                    index % 2,
                )
            self.scroll.layout.addWidget(groups)
        if data.get("swiss_rows") and macos.enabled():
            state = self.detail_state(f"{data['id']}:{data['season']}")
            state.setdefault("swiss_view", "table")
            toggle_row = QHBoxLayout()
            toggle_row.addWidget(
                macos.section_tabs(
                    [("积分表", "table"), ("对阵图", "graph")],
                    state["swiss_view"],
                    lambda view, state=state: self._set_swiss_view(state, view),
                ),
                1,
            )
            holder = QWidget()
            holder.setLayout(toggle_row)
            self.scroll.layout.addWidget(holder)
            if state["swiss_view"] == "graph":
                model = swiss_view.swiss_graph(world)
                if model:
                    panel = boards.GraphPanel(boards.SwissBoard(world, model), height=520)
                    panel.view.match_requested.connect(lambda match_id: self.open_route("match", match_id=match_id))
                    panel.view.team_requested.connect(lambda team_id: self.open_route("team", team_id=team_id))
                    panel.view.stage_requested.connect(
                        lambda stage, state=state: self._focus_swiss_stage(state, stage)
                    )
                    self.scroll.layout.addWidget(panel)
                    summary = f"第 {model['current_round']} 轮 · 晋级 {len(model['qualified'])} · 淘汰 {len(model['eliminated'])}"
                    self.scroll.layout.addWidget(label(summary, "Meta"))
            else:
                self.scroll.layout.addWidget(
                    self._table_card(
                        "瑞士轮与 BU",
                        "",
                        [
                            {"key": "seed", "label": "种子", "width": 52, "numeric": True},
                            {"key": "team", "label": "球队", "stretch": True, "kind": "link", "icon_key": "club", "icon_size": 24},
                            {"key": "record", "label": "战绩", "width": 54, "numeric": True},
                            {"key": "bu", "label": "BU", "width": 52, "numeric": True},
                            {"key": "status", "label": "资格状态", "width": 132, "kind": "status"},
                        ],
                        data["swiss_rows"],
                        lambda row, key: self.open_route("team", team_id=row["team_id"]),
                    )
                )
        elif data.get("swiss_rows"):
            self.scroll.layout.addWidget(
                self._table_card(
                    "瑞士轮与 BU",
                    "三胜晋级、三负淘汰；BU=所有对手当前胜场减负场之和；出线队按 3–0/3–1/3–2 分档。",
                    [
                        {"key": "seed", "label": "种子", "width": 52, "numeric": True},
                        macos.team_column("team"),
                        {"key": "record", "label": "战绩", "width": 54, "numeric": True},
                        {"key": "bu", "label": "BU", "width": 52, "numeric": True},
                        {"key": "status", "label": "资格状态", "width": 132, "kind": "status"},
                    ],
                    data["swiss_rows"],
                    lambda row, key: self.open_route("team", team_id=row["team_id"]),
                )
            )
            for round_data in ([] if macos.enabled() else data.get("swiss_rounds", [])):
                frame = card(alt=True)
                layout = QVBoxLayout(frame)
                layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
                layout.setSpacing(t.S4)
                layout.addWidget(label(f"瑞士轮第 {round_data['round']} 轮 · 第 {round_data['week']} 周", "CardTitle"))
                for pairing in round_data["pairings"]:
                    row = QHBoxLayout()
                    row.addWidget(label(pairing["home"], "Strong"), 1)
                    row.addWidget(label(pairing.get("score", "未赛"), "ScoreSmall"), 0)
                    row.addWidget(label(pairing["away"], "Strong"), 1)
                    if pairing.get("winner"):
                        row.addWidget(label(f"胜：{pairing['winner']}", "Meta"), 0)
                    layout.addLayout(row)
                if round_data.get("rematch_reason"):
                    layout.addWidget(label(round_data["rematch_reason"], "Meta", wrap=True))
                self.scroll.layout.addWidget(frame)
        if data.get("seed_rows"):
            self.scroll.layout.addWidget(
                self._table_card(
                    "种子与资格来源",
                    "按上季总荣誉分排种子，同分看上季联赛排名。",
                    [
                        {"key": "seed", "label": "种子", "width": 48, "numeric": True},
                        macos.team_column("team"),
                        {"key": "sources", "label": "资格来源", "stretch": True},
                    ],
                    [{**row, "sources": "、".join(row["sources"])} for row in data["seed_rows"]],
                    lambda row, key: self.open_route("team", team_id=row["team_id"]),
                )
            )
        if data.get("direct"):
            frame = card(alt=True)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
            layout.setSpacing(t.S6)
            layout.addWidget(SectionTitle("直通八强", "登峰组前四与涅槃组第一直接进入八强；其余进入突围签池"))
            names = "、".join(row["team"] for row in data["direct"])
            layout.addWidget(label(names, "Strong", wrap=True))
            self.scroll.layout.addWidget(frame)
        if data.get("playin"):
            frame = card()
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
            layout.setSpacing(t.S6)
            layout.addWidget(SectionTitle("突围赛", "两个签池公开抽签；主场按上季联赛排名，不按签池"))
            for tie in data["playin"]:
                layout.addWidget(self._tie_row(world, tie))
            self.scroll.layout.addWidget(frame)
        if data.get("bracket_rounds") and not macos.enabled():
            for round_data in data["bracket_rounds"]:
                frame = card()
                layout = QVBoxLayout(frame)
                layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
                layout.setSpacing(t.S6)
                layout.addWidget(label(round_data["label"], "CardTitle"))
                for tie in round_data["ties"]:
                    layout.addWidget(self._tie_row(world, tie))
                self.scroll.layout.addWidget(frame)
        if data.get("knockout") and not macos.enabled():
            for round_data in data["knockout"]:
                frame = card()
                layout = QVBoxLayout(frame)
                layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
                layout.setSpacing(t.S6)
                layout.addWidget(label(round_data["label"], "CardTitle"))
                for tie in round_data["ties"]:
                    layout.addWidget(self._tie_row(world, tie))
                self.scroll.layout.addWidget(frame)
        if data.get("loss_paths") and not macos.enabled():
            self.scroll.layout.addWidget(
                self._table_card(
                    "双败路径",
                    "胜者组冠军进入总决赛且无重置；落败阶段决定同战绩主客场。",
                    [
                        macos.team_column("team"),
                        {"key": "loss_stage", "label": "落败阶段", "width": 160},
                    ],
                    [
                        {**row, "loss_stage": "未败（胜者组）" if row["loss_stage"] is None else f"第 {row['loss_stage']} 阶段"}
                        for row in data["loss_paths"]
                    ],
                    lambda row, key: self.open_route("team", team_id=row["team_id"]),
                )
            )
        if data.get("placement_rows"):
            self.scroll.layout.addWidget(
                self._table_card(
                    "最终名次与荣誉分",
                    "每个最终层级只计一次，跨赛事累加；次级联赛 0 分。",
                    [
                        {"key": "rank", "label": "名次", "width": 52, "numeric": True},
                        macos.team_column("team"),
                        {"key": "tier_label", "label": "层级", "width": 170},
                        {"key": "points", "label": "荣誉分", "width": 70, "numeric": True},
                    ],
                    data["placement_rows"],
                    lambda row, key: self.open_route("team", team_id=row["team_id"]),
                )
            )
        if macos.enabled() and not self._bracket_first(data):
            block = self._bracket_block(world, data)
            if block is not None:
                self.scroll.layout.addWidget(block)
        if data.get("awards") and not macos.enabled():
            self.scroll.layout.addWidget(self._awards_card(data["awards"]))
        self.scroll.layout.addStretch(1)

    @staticmethod
    def _bracket_first(data: dict) -> bool:
        """Decided cup, or knockout already under way: show the path before tables."""
        if data.get("champion"):
            return True
        for round_data in data.get("knockout") or []:
            for tie in round_data.get("ties") or []:
                if tie.get("winner_id") or tie.get("match"):
                    return True
        for round_data in data.get("bracket_rounds") or []:
            if round_data.get("ties"):
                return True
        return False

    def _bracket_block(self, world: dict, data: dict) -> QWidget:
        model = bracket_view.cup_bracket(world, data["id"], data)
        if not model:
            return None
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("淘汰赛签表", "" if macos.enabled() else self._bracket_hint(model)))
        state_key = data["id"] if int(data["season"]) == int(self.app.world()["season_number"]) else f"{data['id']}:bracket:{data['season']}"
        state = self.detail_state(state_key)
        mode = state.get("bracket_view", "rounds" if model.get("mode") == "groups" else "panorama")
        if macos.enabled() or model.get("mode") == "groups":
            view_row = QHBoxLayout()
            view_row.addWidget(
                macos.section_tabs(
                    [("轮次视图", "rounds"), ("全景签表", "panorama")],
                    mode,
                    lambda view, data=data, state=state: self._set_bracket_view(data, state, view),
                ),
                1,
            )
            holder = QWidget()
            holder.setLayout(view_row)
            layout.addWidget(holder)
            if mode == "panorama":
                if data["id"] == "confluence":
                    view = boards.RedrawCupPanorama(world, model)
                elif model.get("mode") == "groups":
                    view = boards.DoubleElimPanorama(world, model)
                else:
                    view = boards.KnockoutPanorama(world, model)
                panel = boards.GraphPanel(view, height=470)
                panel.view.match_requested.connect(lambda match_id: self.open_route("match", match_id=match_id))
                panel.view.team_requested.connect(lambda team_id: self.open_route("team", team_id=team_id))
                panel.view.stage_requested.connect(
                    lambda stage, data=data, state=state: self._focus_bracket_stage(data, state, stage)
                )
                layout.addWidget(panel)
                return frame
        board = BracketBoard(
            world,
            model,
            round_key=state.get("bracket_round", ""),
            group_key=state.get("bracket_group", ""),
        )

        def remember_selection(_key: str = "", board=board, state=state) -> None:
            # Always persist the board's final resolved pair; a path jump may
            # change group and round together, and construction cannot emit.
            group, round_key = board.selection()
            state["bracket_group"] = group
            state["bracket_round"] = round_key

        board.round_changed.connect(remember_selection)
        board.group_changed.connect(remember_selection)
        board.match_requested.connect(lambda match_id: self.open_route("match", match_id=match_id))
        remember_selection()
        layout.addWidget(board)
        return frame

    def _set_bracket_view(self, data: dict, state: dict, view: str) -> None:
        state["bracket_view"] = view if view in ("rounds", "panorama") else "rounds"
        self.app._refresh_current_page(restore_scroll=True)

    def _focus_bracket_stage(self, data: dict, state: dict, stage: str) -> None:
        """Panorama stage header → same real stage in the round view."""
        context = getattr(self, "_display_world", None) or self.app.world()
        detail = queries.competition_detail(context, self.save_name, data["id"], live_standings=macos.enabled())
        model = bracket_view.cup_bracket(context, data["id"], detail)
        if not model:
            return
        for column in model.get("columns") or []:
            if column.get("key") == stage:
                state["bracket_view"] = "rounds"
                state["bracket_round"] = stage
                state["bracket_group"] = ""
                self.app._refresh_current_page(restore_scroll=True)
                return
        for group in model.get("groups") or []:
            for round_data in group.get("rounds") or []:
                if round_data.get("key") == stage:
                    state["bracket_view"] = "rounds"
                    state["bracket_group"] = group["key"]
                    state["bracket_round"] = stage
                    self.app._refresh_current_page(restore_scroll=True)
                    return

    def _champion_hero(self, world: dict, data: dict) -> QWidget:
        frame = card(alt=True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S14, t.S20, t.S14)
        layout.setSpacing(t.S16)
        layout.addWidget(TrophyImage(data["id"], 72), 0, Qt.AlignVCenter)
        team_id = data.get("champion_id")
        if team_id:
            layout.addWidget(TeamCrest((world["teams"].get(team_id) or {}).get("club_en", ""), 52), 0, Qt.AlignVCenter)
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(label(data["champion"], "PageTitle"))
        caption = "冠军"
        if data.get("runner_up"):
            caption += f" · 决赛对手 {data['runner_up']}"
        column.addWidget(label(caption, "Meta"))
        layout.addLayout(column, 1)
        if team_id:
            link = EntityLink("查看球队荣誉馆", "team_honors", team_id)
            link.clicked.connect(lambda _=False, tid=team_id: self.open_route("team_honors", team_id=tid))
            layout.addWidget(link, 0, Qt.AlignVCenter)
        return frame

    def _set_swiss_view(self, state: dict, view: str) -> None:
        state["swiss_view"] = view if view in ("table", "graph") else "table"
        self.app._refresh_current_page(restore_scroll=True)

    def _focus_swiss_stage(self, state: dict, stage: str) -> None:
        """Show this Swiss round's real fixtures in the existing stage-filtered list."""
        state["swiss_view"] = "graph"
        state["section"] = "matches"
        state["stage"] = stage
        self.app._refresh_current_page(restore_scroll=False)

    @staticmethod
    def _group_zones(competition: str, group: dict, finished: bool):
        """Row tints and the short header chips; never paints a zone that is not in the rules."""
        zones = []
        legend = []
        name = group.get("name", "")
        for row in group.get("table") or []:
            rank = int(row.get("rank") or 0)
            zone = None
            if competition == "aureole":
                if rank <= 2:
                    zone = ("当前晋级区" if not finished else "晋级八强", t.SUCCESS_BG, t.SUCCESS)
            elif competition == "ascendant":
                peak = "登峰" in name
                if peak:
                    if rank <= 4:
                        zone = ("当前直通区" if not finished else "直通八强", t.SUCCESS_BG, t.SUCCESS)
                    elif rank <= 6:
                        zone = ("当前突围区" if not finished else "突围赛", t.WARNING_BG, t.ACCENT)
                else:
                    if rank == 1:
                        zone = ("当前直通区" if not finished else "直通八强", t.SUCCESS_BG, t.SUCCESS)
                    elif rank <= 5:
                        zone = ("当前突围区" if not finished else "突围赛", t.WARNING_BG, t.ACCENT)
                    else:
                        zone = ("当前淘汰区" if not finished else "淘汰", t.DANGER_BG, t.DANGER)
            if zone:
                text, bg, border = zone
                zones.append({"bg": bg, "border": border, "label": text})
                legend.append((text, bg))
            else:
                zones.append(None)
        return zones, legend

    def _cup_section(self, world: dict, data: dict, state: dict) -> None:
        section = state["section"]
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S12)
        if section == "awards":
            self.scroll.layout.addWidget(self._awards_card(data["awards"], pending=not world["season"].get("finalized")))
            return
        if section == "matches":
            selector = QComboBox()
            selector.setAccessibleName("选择杯赛阶段")
            selector.addItem("全部阶段", "")
            stages = dict((m["stage"], m["stage_text"]) for m in data["fixtures"])
            for stage, title in stages.items():
                selector.addItem(title, stage)
            selector.setCurrentIndex(max(0, selector.findData(state.get("stage", ""))))
            def select_stage(_index):
                state["stage"] = selector.currentData()
                self.app._refresh_current_page(restore_scroll=True)
            selector.currentIndexChanged.connect(select_stage)
            layout.addWidget(SectionTitle("赛程与结果", "点击比分查看比赛；点击球队查看详情", selector))
            stage = state.get("stage", "")
            rows = [row for row in data["fixtures"] if not stage or row["stage"] == stage]
            columns = [
                {"key": "week", "label": "周", "width": 44, "numeric": True},
                {"key": "stage_text", "label": "阶段", "width": 138},
                {"key": "home_name", "label": "主队", "stretch": True, "kind": "link", "team_side": "home", "icon_key": "home_name"},
                {"key": "score", "label": "比分", "width": 80, "kind": "match", "alignment": Qt.AlignCenter},
                {"key": "away_name", "label": "客队", "stretch": True, "kind": "link", "team_side": "away", "icon_key": "away_name"},
                {"key": "status_text", "label": "状态", "width": 64},
            ]
            callback = lambda row, key: self.open_route("team", team_id=row["home_team_id"] if key == "home_name" else row["away_team_id"]) if key in ("home_name", "away_name") else self.open_route("match", match_id=row["match_id"])
            paging = state.setdefault("match_pages", {}).setdefault(stage, {})
        elif section == "players":
            layout.addWidget(SectionTitle("当季球员榜", ""))
            self.scroll.layout.addWidget(frame)
            self.scroll.layout.addWidget(
                boards.DualLeaderboard(world, queries.competition_leaders(world, data["id"]), competition=data["id"])
            )
            return
        else:
            layout.addWidget(SectionTitle("历届冠军", "仅统计已结束赛季"))
            rows = []
            for archive in reversed(world.get("archives", [])):
                title = next((r for r in (archive.get("championships") or {}).values() if r["competition_id"] == data["id"]), None)
                if title:
                    rows.append({"season": archive["season_number"], "team_id": title["champion_team_id"], "team": self.app.team_name(title["champion_team_id"])})
            columns = [{"key": "season", "label": "赛季", "width": 70, "numeric": True}, macos.team_column("team", "冠军球队")]
            callback = lambda row, key: self.open_route("team_honors", team_id=row["team_id"])
            paging = state.setdefault("history_pages", {})
        table = DataTable()
        table.set_columns(columns)
        table.cell_activated.connect(callback)
        if section == "matches":
            table.row_activated.connect(lambda row: self.open_route("match", match_id=row["match_id"]))
        if macos.enabled() and any(column.get("icon_key") == "club" for column in columns):
            # The champions table bypasses _table_card; decorate the complete
            # row set before paging so every page has crest data.
            rows = macos.with_team_clubs(world, rows)
        macos.paginate_table(table, rows, layout, paging)
        if not rows:
            layout.addWidget(label("暂无符合条件的记录；赛事开始或归档后会自动更新。", "Meta", wrap=True))
        self.scroll.layout.addWidget(frame)

    def _render_playoff(self, world: dict, data: dict) -> None:
        self.scroll.layout.addWidget(PageHeader(f"第 {data['season']} 赛季", data["label"], "次级联赛第 3–6 名 · 半决赛与决赛均为主客场两回合"))
        if not data["active"]:
            self.scroll.layout.addWidget(self._notice_card("参赛球队尚未确定", "联赛结束后确定四队，第 46–49 周进行附加赛；胜者获得最后一个升级名额。"))
        if data["winner"]:
            self.scroll.layout.addWidget(label("升级成功：" + data["winner"], "CardTitle"))
        if macos.enabled():
            model = bracket_view.playoff_bracket(world, data)
            if model:
                self.scroll.layout.addWidget(SectionTitle("附加赛签表", self._bracket_hint(model)))
                board = BracketBoard(world, model)
                board.match_requested.connect(lambda match_id: self.open_route("match", match_id=match_id))
                self.scroll.layout.addWidget(board)
                self.scroll.layout.addStretch(1)
                return
        for round_data in data["rounds"]:
            frame = card()
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.addWidget(label(round_data["label"], "CardTitle"))
            for tie in round_data["ties"]:
                layout.addWidget(self._tie_row(world, tie))
            self.scroll.layout.addWidget(frame)
        self.scroll.layout.addStretch(1)

    def _phase_card(self, data: dict) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
        layout.setSpacing(t.S4)
        title = f"{data['current_stage']} · 已完成 {data['completed_matches']}/{data['match_count']} 场"
        layout.addWidget(SectionTitle(title, "下一场" if data.get("next_match") else ""))
        next_match = data.get("next_match")
        if next_match:
            line = f"下一场：第 {next_match['week']} 周 · {next_match['home_name']} vs {next_match['away_name']} · {next_match['stage_text']}"
            if next_match.get("venue_mode") == "neutral":
                line += f" · 中立场 {next_match['venue']}"
            layout.addWidget(label(line, "Strong", wrap=True))
        elif data.get("champion"):
            layout.addWidget(label(f"冠军：{data['champion']} · 亚军：{data.get('runner_up') or '待定'}", "Strong"))
        else:
            layout.addWidget(label("本届赛程已结束，等待归档确认。", "Meta", wrap=True))
        return frame

    def _notice_card(self, title: str, body: str, action=None) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle(title, "本赛事尚未开始"))
        layout.addWidget(label(body, "Meta", wrap=True))
        if action is not None:
            layout.addWidget(action, 0, Qt.AlignLeft)
        return frame

    @staticmethod
    def _bracket_hint(model: dict) -> str:
        if model.get("pending"):
            return model["pending"]
        if model.get("completed") and model.get("champion"):
            return f"冠军 {model['champion']}"
        return "点击对阵查看比赛；连线表示已确定的晋级关系"

    def _toggle_rules(self, state: dict, checked: bool) -> None:
        state["rules_open"] = checked
        self.app._refresh_current_page(restore_scroll=True)

    def _rules_card(self, data: dict) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle("赛事规则", rules.display_name(data["id"], "zh")))
        layout.addWidget(label(data.get("bracket_note") or "", "Meta", wrap=True))
        if data.get("neutral_venue"):
            layout.addWidget(label(f"决赛中立场：{data['neutral_venue']}（双方无主场加成）", "Meta", wrap=True))
        return frame

    def _trophy_header(self, data: dict, subtitle: str) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.S16)
        layout.addWidget(TrophyImage(data["id"], 112), 0, Qt.AlignTop)
        layout.addWidget(PageHeader(f"第 {data['season']} 赛季", data["label"], f"{rules.display_name(data['id'], 'zh')} · {subtitle}"), 1)
        if self.app.press:
            reports=self.app.press.related_button(season=data['season'],competition=data['id'])
            if reports:layout.addWidget(reports,0,Qt.AlignTop)
        return holder

    def _tie_row(self, world: dict, tie: dict) -> QWidget:
        row_widget = QWidget()
        layout = QVBoxLayout(row_widget)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(2)
        if tie.get("two_leg"):
            line = QHBoxLayout()
            first = EntityLink(tie["teams"][0], "team", tie["team_ids"][0])
            first.clicked.connect(lambda _=False, team_id=tie["team_ids"][0]: self.open_route("team", team_id=team_id))
            second = EntityLink(tie["teams"][1], "team", tie["team_ids"][1])
            second.clicked.connect(lambda _=False, team_id=tie["team_ids"][1]: self.open_route("team", team_id=team_id))
            line.addWidget(first, 1)
            line.addWidget(label("两回合", "Meta"), 0)
            line.addWidget(second, 1)
            layout.addLayout(line)
            for leg in tie["legs"]:
                leg_row = QHBoxLayout()
                leg_row.addWidget(label(f"第 {leg['leg']} 回合", "Meta"), 0)
                text = f"{leg['home_name']} {leg['score']} {leg['away_name']}"
                if macos.enabled() and leg.get("home_pk") is not None:
                    text += f" · 点球 {leg['home_pk']}–{leg['away_pk']}"
                link = EntityLink(text, "match", leg["match_id"])
                link.clicked.connect(lambda _=False, mid=leg["match_id"]: self.open_route("match", match_id=mid))
                leg_row.addWidget(link, 1)
                layout.addLayout(leg_row)
            if macos.enabled():
                completed = [leg for leg in tie["legs"] if leg["status"] == "completed"]
                if not completed:
                    caption = "两回合尚未开赛"
                elif len(completed) == 1:
                    goals = [sum(leg["total_home"] if leg["home_team_id"] == tid else leg["total_away"] for leg in completed) for tid in tie["team_ids"]]
                    caption = f"暂时总分：{tie['teams'][0]} {goals[0]}–{goals[1]} {tie['teams'][1]} · 次回合未赛"
                else:
                    caption = f"总比分：{tie['aggregate_text']} · 晋级：{tie['winner']}"
                layout.addWidget(label(caption, "Strong", wrap=True))
            else:
                layout.addWidget(label(f"总比分：{tie['aggregate_text']} · 晋级：{tie['winner']}", "Strong"))
        else:
            match = tie.get("match")
            if match:
                row = MatchRowCard(self.app.world(), match)
                row.clicked.connect(lambda match_id: self.open_route("match", match_id=match_id))
                layout.addWidget(row)
            if tie.get("reason"):
                layout.addWidget(label(tie["reason"], "Meta", wrap=True))
            if tie.get("winner") and tie.get("winner") != "待定":
                layout.addWidget(label(f"晋级：{tie['winner']}", "Meta"))
        return row_widget

    def _table_card(self, title: str, subtitle: str, columns: List[dict], rows: List[dict], on_link, *, league: str = "", zones=None, legend=None) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle(title, subtitle))
        if legend:
            layout.addLayout(self._zone_legend(legend))
        table = DataTable()
        table.set_columns(columns)
        display_rows = rows
        if macos.enabled() and any(column.get("icon_key") == "club" for column in columns) and any(row.get("team_id") for row in rows):
            # P2-01: team columns carry icon_key=club, but cup DTOs only own a
            # team_id.  Decorate a copy from that explicit id; never mutate the
            # query result and never guess a team from the display name.
            display_rows = macos.with_team_clubs(self.app.world(), rows)
        table.set_rows(display_rows)
        table.set_league_style(league)
        if zones:
            table.set_zone_rows(zones)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 38 * max(1, len(rows)) + 4)
        table.cell_activated.connect(on_link)
        layout.addWidget(table)
        return frame

    @staticmethod
    def _zone_legend(legend) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(t.S10)
        seen = set()
        for label_text, color in legend:
            if label_text in seen:
                continue
            seen.add(label_text)
            chip = label(label_text, "Meta")
            chip.setStyleSheet(f"color: {t.TEXT_STRONG}; background: {color}; border-radius: 6px; padding: 1px 7px;")
            row.addWidget(chip, 0)
        row.addStretch(1)
        return row

    def _awards_card(self, awards: dict, *, pending: bool = False) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        if pending:
            layout.addWidget(SectionTitle("个人奖项尚未颁发", "赛季结算时按正式规则确认；进行中的数据可在球员榜查看。"))
            for title in ("射手王", "助攻王", "赛事 MVP"):
                layout.addWidget(label(title + " · 待赛季结算", "Meta"))
            return frame
        layout.addWidget(SectionTitle("个人奖项", f"MVP 最低出场 {awards.get('mvp_minimum') or '—'} 场"))
        for label_text, key in (("射手王", "top_scorer"), ("助攻王", "assist_leader"), ("赛事 MVP", "mvp")):
            people = awards.get(key) or []
            row = QHBoxLayout()
            row.setSpacing(t.S10)
            row.addWidget(label(label_text, "Meta"), 0)
            if not people:
                row.addWidget(label("空缺", "Meta"), 1)
                layout.addLayout(row)
                continue
            names = QVBoxLayout()
            names.setSpacing(2)
            for item in people:
                line = QHBoxLayout()
                line.setSpacing(t.S8)
                player_id = item.get("player_id")
                if macos.enabled() and player_id:
                    link = EntityLink(item.get("label", "—"), "player", player_id)
                    link.clicked.connect(lambda _=False, pid=player_id: self.open_route("player", player_id=pid))
                    line.addWidget(link, 0)
                else:
                    line.addWidget(label(item.get("label", "—"), "Strong"), 0)
                if item.get("value") is not None:
                    line.addWidget(label(str(item["value"]), "Meta"), 0)
                if macos.enabled() and item.get("team_id"):
                    team_link = EntityLink(self.app.team_name(item["team_id"]), "team", item["team_id"])
                    team_link.clicked.connect(lambda _=False, tid=item["team_id"]: self.open_route("team", team_id=tid))
                    line.addWidget(team_link, 0)
                line.addStretch(1)
                names.addLayout(line)
            row.addLayout(names, 1)
            layout.addLayout(row)
        return frame


class SeasonPage(BasePage):
    route_name = "season"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        self.params = params
        self.scroll.reset()
        world = self.app.world()
        data = queries.season_overview(world)
        self.scroll.layout.addWidget(
            PageHeader(
                f"第 {data['season']} 赛季 · 第 {data['week']} 周",
                "赛季日历",
                "当前周、休赛窗口与结算周已标出",
            )
        )
        counts = data["counts"]
        self.scroll.layout.addWidget(
            StatGrid(
                [
                    (f"{counts['league']}", "联赛场次"),
                    (f"{counts['cups']}", "杯赛场次"),
                    (f"{counts['playoff']}", "附加赛"),
                    (f"{counts['completed']}", "已完赛"),
                    (f"{counts['scheduled']}", "未赛"),
                ]
            )
        )
        if data["pending"].get("ability_review") or data["pending"].get("transfer_review") or data["pending"].get("draft"):
            pending = card(alt=True)
            layout = QVBoxLayout(pending)
            layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
            layout.addWidget(label("待办事项", "CardTitle"))
            if data["pending"].get("ability_review"):
                layout.addWidget(label(f"能力审核 {len(data['pending']['ability_review'])} 项", "Meta"))
            if data["pending"].get("transfer_review"):
                layout.addWidget(label(f"转会审核 {len(data['pending']['transfer_review'])} 笔", "Meta"))
            if data["pending"].get("draft"):
                layout.addWidget(label("选秀等待录入", "Meta"))
            self.scroll.layout.addWidget(pending)
        transitions = data.get("transition") or {}
        if transitions.get("promoted") or transitions.get("relegated"):
            summary = card()
            layout = QVBoxLayout(summary)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S6)
            layout.addWidget(SectionTitle("赛季结算", "第 49 周全部赛事结束"))
            if transitions.get("promoted"):
                layout.addWidget(label("升级：" + "、".join(self.app.team_name(tid) for tid in transitions["promoted"]), "Strong", wrap=True))
            if transitions.get("relegated"):
                layout.addWidget(label("降级：" + "、".join(self.app.team_name(tid) for tid in transitions["relegated"]), "Strong", wrap=True))
            if transitions.get("playoff_winner"):
                layout.addWidget(label("升级附加赛胜者：" + self.app.team_name(transitions["playoff_winner"]), "Meta"))
            self.scroll.layout.addWidget(summary)
        if macos.enabled():
            state = self.__dict__.setdefault("calendar_state", {"scope": "window"})
            tabs = macos.section_tabs(
                [("当前阶段", "window"), ("全年 52 周", "all")],
                state["scope"],
                self._set_calendar_scope,
            )
            self.scroll.layout.addWidget(tabs)
            weeks = data["weeks"]
            if state["scope"] == "window":
                current = int(data["week"])
                low, high = max(1, current - 3), min(52, current + 5)
                weeks = [week for week in weeks if low <= week["week"] <= high]
                note = f"第 {low}–{high} 周 · 当前第 {current} 周"
            else:
                note = "全年 52 周"
            self.scroll.layout.addWidget(self._timeline(weeks, data["week"], world, note))
            self.scroll.layout.addWidget(label("点击已完赛周次打开周报。", "Meta"))
            self.scroll.layout.addStretch(1)
            return
        self.scroll.layout.addWidget(
            self._timeline(data["weeks"], data["week"], world)
        )
        self.scroll.layout.addWidget(label("点击已完赛周次打开周报。", "Meta"))
        self.scroll.layout.addStretch(1)

    def _set_calendar_scope(self, scope: str) -> None:
        self.calendar_state = {"scope": scope}
        self.refresh(self.snapshot, self.params)

    def _timeline(self, weeks: List[dict], current: int, world: dict, note: str = "当前周与休赛窗口已标出") -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S4)
        layout.addWidget(SectionTitle("赛季日历", note))
        for week in weeks:
            row = QHBoxLayout()
            row.setContentsMargins(0, 1, 0, 1)
            number = label(f"{week['week']:02d}", "Strong")
            number.setFixedWidth(34)
            row.addWidget(number)
            status = Badge(week["status"], _status_variant(week["status"]))
            row.addWidget(status, 0)
            text = week["label"]
            row.addWidget(label(text, "Meta", wrap=True), 1)
            if week["status"] in ("已完赛", "当前") and week["league_round"]:
                link = EntityLink("周报", "weekly", str(week["week"]))
                link.clicked.connect(lambda _=False, w=week["week"]: self.open_route("weekly", week=w))
                row.addWidget(link, 0)
            layout.addLayout(row)
            if week["week"] in (24, 49):
                layout.addWidget(hline())
        return frame


def _status_variant(status: str) -> str:
    return {
        "已完赛": "neutral",
        "当前": "success",
        "未开始": "info",
        "进行中": "success",
        "已结束": "neutral",
        "未开放": "neutral",
    }.get(status, "neutral")
