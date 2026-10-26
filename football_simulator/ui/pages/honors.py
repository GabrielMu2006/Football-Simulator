"""历史纪录与独立荣誉馆页面。"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from football_simulator.app import queries, records
from football_simulator.game import rules
from football_simulator.ui import tokens as t
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.components import (
    Badge,
    DataTable,
    EmptyState,
    EntityLink,
    PageHeader,
    PageScroll,
    SectionTitle,
    StatGrid,
    card,
    label,
)
from football_simulator.ui.pages.base import BasePage
from football_simulator.ui.trophies import TROPHY_IDS, TrophyImage
from football_simulator.ui.team_crest import TeamCrest


class TrophyGrid(QWidget):
    """可重排卡片网格：连续缩窄时只重排现有卡片，不刷新页面、不丢滚动位置。

    奖杯陈列与冠军球队列表共用。最小宽度只按一张卡片计算，否则"最小宽度由列数
    决定 → 父级无法压窄 → 收不到 resize → 永不重排"会互相锁死。
    """

    def __init__(self, *, max_columns: int = 4, minimum_card: int = 240) -> None:
        super().__init__()
        self._cards = []
        self._columns = 0
        self._max_columns = max_columns
        self._minimum_card = minimum_card
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(t.S12)

    def add_card(self, widget: QWidget) -> None:
        self._cards.append(widget)
        self._reflow(force=True)

    def _reflow(self, *, force: bool = False) -> None:
        columns = min(self._max_columns, max(1, (self.width() + t.S12) // (self._minimum_card + t.S12)))
        if columns == self._columns and not force:
            return
        while self.grid.count():
            self.grid.takeAt(0)
        for col in range(self._max_columns):
            self.grid.setColumnStretch(col, 1 if col < columns else 0)
        for index, widget in enumerate(self._cards):
            self.grid.addWidget(widget, index // columns, index % columns)
        self._columns = columns

    def minimumSizeHint(self):
        """最小宽度只按单张卡片报告。

        否则会出现"最小宽度由 4 列决定 → 父级无法把宽度压小 → resizeEvent 收不到更窄
        的宽度 → 永远不重排到更少列"的死锁；Windows 字体更宽时就会表现为页面水平溢出。
        宽度真的变窄后 resizeEvent 会把列数重排到合适值。
        """
        from PySide6.QtCore import QSize

        hint = super().minimumSizeHint()
        if not self._cards:
            return hint
        widest = max(card.minimumSizeHint().width() for card in self._cards)
        return QSize(widest, hint.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()


class RecordsPage(BasePage):
    route_name = "records"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)
        self.scope = "all"
        self.metric = "goals"
        self.competition = ""
        self.player_scope = macos.default_player_scope()
        self.page = 1
        self.include_in_progress = False
        self.season: Optional[int] = None

    def _reload(self) -> None:
        if self.snapshot:
            self.refresh(self.snapshot, self.params if hasattr(self, "params") else {})

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        self.scroll.reset()
        back = QPushButton("← 历史")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.open_route("history"))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        self.scroll.layout.addWidget(PageHeader("历史", "Records", "职业统计覆盖两级联赛、升级附加赛与六杯；同值共享名次"))
        # filters
        filters = QHBoxLayout()
        self.scope_box = QComboBox()
        self.scope_box.addItem("累计榜", "all")
        self.scope_box.addItem("单届纪录", "edition")
        self.scope_box.setCurrentIndex(1 if self.scope == "edition" else 0)
        self.metric_box = QComboBox()
        for key, text in records.METRICS.items():
            self.metric_box.addItem(text, key)
        self.metric_box.setCurrentIndex(max(0, self.metric_box.findData(self.metric)))
        self.competition_box = QComboBox()
        self.competition_box.addItem("全部九项赛事", "")
        for comp in rules.ALL_COMPETITION_IDS:
            self.competition_box.addItem(rules.competition_label(comp), comp)
        self.competition_box.setCurrentIndex(max(0, self.competition_box.findData(self.competition)))
        self.real_box = QComboBox()
        self.real_box.addItem("全部球员", "all")
        self.real_box.addItem("仅真实球员", "real")
        self.real_box.setCurrentIndex(1 if self.player_scope == "real" else 0)
        self.include_box = QCheckBox("含进行中")
        self.include_box.setChecked(self.include_in_progress)
        for widget in (self.scope_box, self.metric_box, self.competition_box, self.real_box):
            filters.addWidget(widget)
        filters.addWidget(self.include_box)
        filters.addStretch(1)
        self.scroll.layout.addLayout(filters)
        self.scope_box.currentIndexChanged.connect(lambda _=0: self._changed())
        self.metric_box.currentIndexChanged.connect(lambda _=0: self._changed())
        self.competition_box.currentIndexChanged.connect(lambda _=0: self._changed())
        self.real_box.currentIndexChanged.connect(lambda _=0: self._changed())
        self.include_box.toggled.connect(lambda _=False: self._changed())

        archived = [int(row["season_number"]) for row in world.get("archives", [])]
        seasons = sorted(set(archived), reverse=True)
        if self.scope == "edition":
            if self.include_in_progress and int(world["season_number"]) not in seasons:
                seasons.insert(0, int(world["season_number"]))
            season_box = QComboBox()
            if seasons:
                for season in seasons:
                    season_box.addItem(f"第 {season} 赛季", season)
            else:
                season_box.addItem("尚无已归档届次", 0)
            if self.season is None and seasons:
                self.season = seasons[0]
            idx = season_box.findData(self.season)
            if idx < 0 and seasons:
                idx = 0
                self.season = seasons[0]
            season_box.setCurrentIndex(max(0, idx))
            season_box.currentIndexChanged.connect(lambda _: self._season_changed(season_box.currentData()))
            filters.addWidget(season_box)
            # include_in_progress adds the current (possibly unarchived) edition above.
        self._render(world, seasons)

    def _changed(self) -> None:
        self.scope = self.scope_box.currentData() or "all"
        self.metric = self.metric_box.currentData() or "goals"
        self.competition = self.competition_box.currentData() or ""
        self.player_scope = self.real_box.currentData() or "all"
        self.include_in_progress = self.include_box.isChecked()
        self.page = 1
        self._reload()

    def _season_changed(self, season) -> None:
        self.season = int(season or 0) or None
        self._reload()

    def _render(self, world: dict, seasons: List[int]) -> None:
        query_scope = "all"
        season_number = None
        if self.scope == "edition":
            query_scope = "edition" if self.competition else "season"
            season_number = self.season
            if self.include_in_progress:
                # explicit current season is included only when selected in the season box
                pass
        result = records.leaderboards(
            self.save_name,
            competition_id=self.competition or None,
            scope=query_scope,
            metric=self.metric,
            include_in_progress=self.include_in_progress,
            player_scope=self.player_scope,
            page=self.page,
            page_size=30,
            season_number=season_number,
        )
        summary = f"指标：{result['metric_label']} · 共 {result['total']} 条 · 第 {result['page']}/{result['pages']} 页"
        if self.scope == "edition" and season_number:
            summary += f" · 第 {season_number} 赛季"
        self.scroll.layout.addWidget(label(summary, "Meta"))
        if result["empty"]:
            self.scroll.layout.addWidget(EmptyState("暂无记录", ""))
        else:
            table = DataTable()
            columns = [
                {"key": "rank", "label": "名次", "width": 54, "numeric": True},
                {"key": "player", "label": "球员", "stretch": True, "kind": "link"},
                {"key": "team_label", "label": "球队", "width": 220, "kind": "link", "icon_key": "club", "icon_size": 24},
                {"key": "appearances", "label": "出场", "width": 58, "numeric": True},
                {"key": "goals", "label": "进球", "width": 54, "numeric": True},
                {"key": "assists", "label": "助攻", "width": 54, "numeric": True},
            ]
            if self.scope == "edition":
                columns.append({"key": "season", "label": "赛季", "width": 64, "numeric": True})
            table.set_columns(columns)
            rows = []
            for row in result["rows"]:
                team = world["teams"].get(row.get("team_id") or "", {})
                rows.append({**row, "club": team.get("club_en", ""), "team_label": team.get("club_en", row.get("team_id") or "—")})
            table.set_rows(rows)
            table.cell_activated.connect(lambda row, key: self._activated(row, key))
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setFixedHeight(36 + 38 * len(rows) + 4)
            self.scroll.layout.addWidget(table)
            pager = QHBoxLayout()
            prev = QPushButton("上一页")
            nxt = QPushButton("下一页")
            prev.setEnabled(result["page"] > 1)
            nxt.setEnabled(result["page"] < result["pages"])
            prev.clicked.connect(lambda: self._turn(-1))
            nxt.clicked.connect(lambda: self._turn(1))
            pager.addWidget(prev)
            pager.addWidget(nxt)
            pager.addStretch(1)
            self.scroll.layout.addLayout(pager)
        links = QHBoxLayout()
        for name, label_text in (("records", "历史纪录"), ("honors", "荣誉馆")):
            button = QPushButton(label_text)
            button.setObjectName("Secondary")
            button.clicked.connect(lambda _=False, route=name: self.open_route(route))
            links.addWidget(button)
        links.addStretch(1)
        self.scroll.layout.addLayout(links)
        self.scroll.layout.addStretch(1)

    def _turn(self, delta: int) -> None:
        self.page = max(1, self.page + delta)
        self._reload()

    def _activated(self, row: dict, key: str) -> None:
        if key == "player":
            self.open_route("player_honors", player_id=row["player_id"])
        elif key == "team_label":
            self.open_route("team_honors", team_id=row["team_id"])


from football_simulator.ui.pages.competitions import CompetitionDetailPage


class HistoryCompetitionPage(CompetitionDetailPage):
    route_name = "history_competition"

    def __init__(self, app) -> None:
        if macos.enabled():
            super().__init__(app)
            return
        BasePage.__init__(self, app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        layout.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        if macos.enabled():
            return super().refresh(snapshot, params)
        season = int(params.get("season") or 0)
        competition = params.get("competition_id") or rules.PREMIER
        archive = next((row for row in world.get("archives", []) if int(row.get("season_number", 0)) == season), None)
        self.scroll.reset()
        back = QPushButton("← 返回之前页面")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.app.go_back())
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        title = f"第 {season} 赛季 · {rules.competition_label(competition)}"
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        if competition in TROPHY_IDS:
            header_layout.addWidget(TrophyImage(competition, 112), 0, Qt.AlignTop)
        header_layout.addWidget(PageHeader("历史", title, f"{rules.display_name(competition, 'zh')} · 往届赛季"), 1)
        self.scroll.layout.addWidget(header)
        if archive is None:
            self.scroll.layout.addWidget(EmptyState("没有该赛季档案", "只能查看已归档赛季。"))
            self.scroll.layout.addStretch(1)
            return
        champion = (archive.get("cup_champions") or {}).get(competition)
        if competition in (rules.PREMIER, rules.SECOND):
            table = archive.get("premier_table" if competition == rules.PREMIER else "second_table") or []
            champion = table[0]["team_id"] if table else None
        champion_name = (world["teams"].get(champion) or {}).get("club_en", "—") if champion else "—"
        self.scroll.layout.addWidget(label(f"冠军：{champion_name}", "CardTitle"))
        placements = (archive.get("cup_placements") or {}).get(competition) or {}
        if placements:
            order = sorted(((team_id, rank) for team_id, rank in placements.items()), key=lambda pair: (pair[1], pair[0]))
            rows = [{"rank": rank, "team": (world["teams"].get(team_id) or {}).get("club_en", team_id), "team_id": team_id} for team_id, rank in order]
            table = DataTable()
            table.set_columns([{"key": "rank", "label": "名次", "width": 60, "numeric": True}, macos.team_column("team")])
            table.set_rows(rows)
            table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]))
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setFixedHeight(36 + 38 * len(rows) + 4)
            self.scroll.layout.addWidget(table)
        stats = records.leaderboards(self.save_name, competition_id=competition, scope="edition", metric="goals", include_in_progress=False, page_size=500, season_number=season, player_scope=self.player_scope)
        scope = macos.player_scope_box(self.player_scope, self.set_player_scope) if macos.enabled() else None
        self.scroll.layout.addWidget(SectionTitle("该届进球榜", "按赛季记录", scope))
        if stats["empty"]:
            self.scroll.layout.addWidget(label("该届没有可核对记录。", "Meta"))
        else:
            table = DataTable()
            table.set_columns([
                {"key": "rank", "label": "名次", "width": 54, "numeric": True},
                {"key": "player", "label": "球员", "stretch": True, "kind": "link"},
                {"key": "goals", "label": "进球", "width": 54, "numeric": True},
                {"key": "assists", "label": "助攻", "width": 54, "numeric": True},
            ])
            table.set_rows(stats["rows"])
            table.cell_activated.connect(lambda row, key: self.open_route("player_honors", player_id=row["player_id"]))
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setFixedHeight(36 + 38 * len(stats["rows"]) + 4)
            if macos.enabled():
                macos.paginate_table(table, stats["rows"], self.scroll.layout, self.detail_state(f"{competition}:{season}").setdefault("scorers", {}))
            else:
                self.scroll.layout.addWidget(table)
        self.scroll.layout.addStretch(1)


class TeamHonorLeaderboard(QFrame):
    """Lifetime standings share the same settled totals as each macOS team hall."""

    def __init__(self, world: dict, state: dict, open_team) -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S12)
        layout.addWidget(SectionTitle("球队荣誉分总榜"))
        self.data = records.cumulative_team_honors(world)
        seasons = self.data["seasons"]
        caption = f"跨赛季累计 · 已结算 {len(seasons)} 个赛季" if seasons else "暂无已结算赛季"
        layout.addWidget(label(caption, "FactText"))
        table = DataTable()
        table.setObjectName("TeamHonorTotals")
        table.set_columns([
            {"key": "rank", "label": "名次", "width": 64, "numeric": True},
            macos.team_column("team", "球队", size=28),
            {"key": "trophies", "label": "奖杯数", "width": 96, "numeric": True},
            {"key": "points", "label": "累计荣誉分", "width": 140, "numeric": True},
        ])
        table.cell_activated.connect(lambda row, key: open_team(row["team_id"]))
        macos.paginate_table(table, self.data["rows"], layout, state)
        self.table = table


class HonorsHubPage(BasePage):
    route_name = "honors"

    def __init__(self, app) -> None:
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        layout.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        self.scroll.reset()
        back = QPushButton("← 历史")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.open_route("history"))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        self.scroll.layout.addWidget(PageHeader("历史", "荣誉馆", "冠军与纪录"))
        totals = {}
        if macos.enabled():
            board = TeamHonorLeaderboard(world, self.detail_state("team_honor_totals"), lambda tid: self.open_route("team_honors", team_id=tid))
            self.scroll.layout.addWidget(board)
            totals = {row["team_id"]: row["points"] for row in board.data["rows"]}
            self._latest_honors(world)
        self.scroll.layout.addWidget(SectionTitle("冠军球队", "按冠军数量排序"))
        if not macos.enabled():
            self.scroll.layout.addWidget(SectionTitle("赛事奖杯", "两级联赛与六项杯赛；点击查看对应赛事"))
        rows = []
        for team_id, team in world["teams"].items():
            data = records.team_honors(world, team_id)
            if data["total_titles"]:
                rows.append({"team": team["club_en"], "team_id": team_id, "titles": data["total_titles"], "points": totals.get(team_id, data["honor_points"])})
        if not rows:
            self.scroll.layout.addWidget(EmptyState("尚无冠军", "冠军产生后会在这里列出球队。"))
        else:
            # 卡片数量随冠军数增长；固定三列会让最小宽度顶出视口，因此复用可重排
            # 网格：列数按实际宽度决定，最小宽度只算一张卡片。
            grid = TrophyGrid(max_columns=3, minimum_card=260)
            for row in sorted(rows, key=lambda item: (-item["titles"], item["team"]))[:6]:
                frame = card()
                layout = QVBoxLayout(frame)
                head = QHBoxLayout()
                head.addWidget(TeamCrest(row["team"], 24), 0, Qt.AlignVCenter)
                head.addWidget(label(row["team"], "CardTitle", wrap=True), 1)
                layout.addLayout(head)
                layout.addWidget(label(f"{row['titles']} 座冠军 · {row['points']} 荣誉分", "Meta"))
                link = EntityLink("查看球队荣誉馆", "team_honors", row["team_id"])
                link.clicked.connect(lambda _=False, tid=row["team_id"]: self.open_route("team_honors", team_id=tid))
                layout.addWidget(link, 0, Qt.AlignLeft)
                grid.add_card(frame)
            self.scroll.layout.addWidget(grid)
            if len(rows) > 6:
                all_link = EntityLink(f"其余 {len(rows) - 6} 支有冠军的球队 · 查看纪录", "records", "records")
                all_link.clicked.connect(lambda: self.open_route("records"))
                self.scroll.layout.addWidget(all_link, 0, Qt.AlignLeft)
        if macos.enabled():
            self.scroll.layout.addWidget(SectionTitle("赛事分类", "八座奖杯"))
            trophies = TrophyGrid()
            for competition in TROPHY_IDS:
                frame = card(alt=True)
                layout = QVBoxLayout(frame)
                layout.setContentsMargins(t.S12, t.S10, t.S12, t.S12)
                layout.addWidget(TrophyImage(competition, 96), 0, Qt.AlignHCenter)
                layout.addWidget(label(rules.display_name(competition, "en"), "CardTitle", wrap=True))
                link = EntityLink("查看赛事", "competition", competition)
                link.clicked.connect(lambda _=False, cid=competition: self.open_route("competition", competition_id=cid))
                layout.addWidget(link, 0, Qt.AlignLeft)
                trophies.add_card(frame)
            self.scroll.layout.addWidget(trophies)
        self.scroll.layout.addStretch(1)

    def _latest_honors(self, world: dict) -> None:
        """Most recent decided titles, newest first; reads only stored championships."""
        entries = []
        for key, entry in (world["season"].get("championships") or {}).items():
            entries.append({
                "season": entry.get("season_number"),
                "competition": entry.get("competition_id"),
                "team_id": entry.get("champion_team_id"),
                "week": entry.get("decided_week"),
            })
        for archive in world.get("archives", []):
            for entry in (archive.get("championships") or {}).values():
                entries.append({
                    "season": archive.get("season_number") or entry.get("season_number"),
                    "competition": entry.get("competition_id"),
                    "team_id": entry.get("champion_team_id"),
                    "week": entry.get("decided_week"),
                })
        if not entries:
            return
        entries.sort(key=lambda row: (-int(row.get("season") or 0), -int(row.get("week") or 0)))
        self.scroll.layout.addWidget(SectionTitle("最新荣誉", "最近产生的冠军"))
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S10, t.S16, t.S10)
        layout.setSpacing(t.S4)
        for row in entries[:5]:
            line = QHBoxLayout()
            line.setSpacing(t.S10)
            trophy = TrophyImage(row["competition"], 40)
            line.addWidget(trophy, 0, Qt.AlignVCenter)
            team = (world["teams"].get(row["team_id"]) or {}).get("club_en", "—")
            line.addWidget(label(f"{team} · {rules.display_name(row['competition'], 'en')}", "FactText", wrap=True), 1)
            line.addWidget(label(f"第 {row['season']} 赛季", "Meta"), 0)
            layout.addLayout(line)
        self.scroll.layout.addWidget(frame)


class _TrophySlot(QFrame):
    def __init__(self, world: dict, team_id: str, title: dict) -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.S16, t.S12, t.S16, t.S16)
        layout.setSpacing(t.S8)
        layout.setAlignment(Qt.AlignTop)
        image = TrophyImage(title["competition_id"], 160, muted=not title["champion"])
        layout.addWidget(image, 0, Qt.AlignHCenter)
        layout.addWidget(label(title["label"], "CardTitle", wrap=True))
        if title["champion"]:
            layout.addWidget(label(f"{title['count']} 次 · " + "、".join(f"S{season}" for season in title["seasons"]), "Strong", wrap=True))
            layout.addWidget(label("冠军" if image.status["ready"] else image.status["reason"], "Meta", wrap=True))
        else:
            layout.addWidget(label("尚未夺冠" if title.get("active", True) else "尚未举办", "Meta"))


class TeamTrophyCollection(QWidget):
    """A reusable trophy cabinet, separate from season-by-season results."""

    def __init__(self, world: dict, team_id: str, open_route) -> None:
        super().__init__()
        self.setObjectName("TeamTrophyCollection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.S16)
        titles = []
        seasons = []
        for competition in TROPHY_IDS:
            facts = [row for row in records.competition_champions(world, competition) if row["team_id"] == team_id]
            years = [row["season"] for row in facts]
            seasons.extend(years)
            titles.append({"competition_id": competition, "label": rules.display_name(competition, "en"),
                           "count": len(facts), "champion": bool(facts), "seasons": years,
                           "active": rules.cup_active(competition, int(world["season_number"])) if competition in rules.CUP_IDS else True})
        total = sum(title["count"] for title in titles)
        points = next(row["points"] for row in records.cumulative_team_honors(world)["rows"] if row["team_id"] == team_id)
        self.data = {"total_titles": total, "honor_points": points, "titles": titles}
        layout.addWidget(StatGrid([
            (str(total), "冠军总数"), (str(points), "累计荣誉分"),
            (f"S{min(seasons)}" if seasons else "—", "首次夺冠"),
            (f"S{max(seasons)}" if seasons else "—", "最近夺冠"),
        ]))
        layout.addWidget(SectionTitle("冠军奖杯"))
        trophies = TrophyGrid()
        for title in sorted(titles, key=lambda item: (not item["champion"], -item["count"], item["label"])):
            slot = _TrophySlot(world, team_id, title)
            for season in title["seasons"]:
                button = QPushButton(f"第 {season} 赛季")
                button.setObjectName("Ghost")
                if season == int(world["season_number"]):
                    button.clicked.connect(lambda _=False, comp=title["competition_id"]: open_route("competition", competition_id=comp))
                else:
                    button.clicked.connect(lambda _=False, comp=title["competition_id"], year=season: open_route("history_competition", competition_id=comp, season=year))
                slot.layout().addWidget(button)
            trophies.add_card(slot)
        layout.addWidget(trophies)


class TeamHonorsPage(BasePage):
    route_name = "team_honors"

    def __init__(self, app) -> None:
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        layout.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        team_id = params.get("team_id")
        self.scroll.reset()
        team = world["teams"].get(team_id or "")
        if not team:
            self.scroll.layout.addWidget(EmptyState("球队不存在", "该球队 ID 不在当前世界中。"))
            return
        data = records.team_honors(world, team_id)
        if macos.enabled():
            data["honor_points"] = next(row["points"] for row in records.cumulative_team_honors(world)["rows"] if row["team_id"] == team_id)
        back = QPushButton("← 球队详情")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.open_route("team", team_id=team_id))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        header = QWidget()
        h = QHBoxLayout(header)
        h.addWidget(TeamCrest(team["club_en"], 84), 0, Qt.AlignTop)
        h.addWidget(PageHeader("球队荣誉馆", team["club_en"], f"{team.get('name_zh', '')} · 冠军按夺冠时注册阵容登记"), 1)
        self.scroll.layout.addWidget(header)
        if macos.enabled():
            self.scroll.layout.addWidget(TeamTrophyCollection(world, team_id, self.open_route))
            self.scroll.layout.addStretch(1)
            return
        self.scroll.layout.addWidget(StatGrid([
            (str(data["total_titles"]), "冠军总数"),
            (str(data["honor_points"]) + (" · 待结算" if data.get("points_pending") else ""), "累计荣誉分"),
            (f"S{data['first_season']}" if data["first_season"] else "—", "首次夺冠"),
            (f"S{data['latest_season']}" if data["latest_season"] else "—", "最近夺冠"),
        ]))
        titles = list(data["titles"])
        if macos.enabled():
            titles = sorted(titles, key=lambda title: (0 if title["titles"] else 1, -len(title["seasons"]), title["label"]))
        trophies = TrophyGrid()
        for index, title in enumerate(titles):
            slot = _TrophySlot(world, team_id, title)
            if title["titles"]:
                for season in title["seasons"][:4]:
                    button = QPushButton(f"S{season} 赛季")
                    button.setObjectName("Ghost")
                    button.setFixedHeight(28)
                    if int(season) == int(world["season_number"]):
                        button.clicked.connect(lambda _=False, comp=title["competition_id"]: self.open_route("competition", competition_id=comp))
                    else:
                        button.clicked.connect(lambda _=False, comp=title["competition_id"], s=season: self.open_route("history_competition", competition_id=comp, season=s))
                    slot.layout().addWidget(button)
            trophies.add_card(slot)
        self.scroll.layout.addWidget(trophies)
        if not data["total_titles"]:
            self.scroll.layout.addWidget(label("这支球队尚未赢得任何冠军；亚军与四强不会显示成冠军奖杯。", "Meta", wrap=True))
        self.scroll.layout.addStretch(1)


class PlayerHonorsPage(BasePage):
    route_name = "player_honors"

    def __init__(self, app) -> None:
        super().__init__(app)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        layout.addWidget(self.scroll)
        if macos.enabled():
            self.setStyleSheet(
                f"QLabel#Eyebrow, QLabel#MetricLabel, QLabel#Meta, QLabel#PageSubtitle, QLabel#FactText, QLabel#Strong {{ font-size: 16px; color: {t.TEXT}; }}"
                "QTableWidget, QPushButton, QComboBox { font-size: 16px; }"
                "QHeaderView::section { font-size: 16px; }"
            )

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        player_id = params.get("player_id")
        self.scroll.reset()
        player = world["players"].get(player_id or "")
        if not player:
            self.scroll.layout.addWidget(EmptyState("球员不存在", "该球员 ID 不在当前世界中。"))
            return
        data = records.player_honors(world, player_id)
        back = QPushButton("← 球员详情")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.open_route("player", player_id=player_id))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        name = queries.player_name(world, player_id)
        self.scroll.layout.addWidget(PageHeader("球员荣誉馆", name, f"{len(data['awards'])} 项个人奖 · {len(data['championships'])} 座随队冠军"))
        if macos.enabled():
            team_id = player.get("team_id")
            if team_id:
                team_link = EntityLink(world["teams"].get(team_id, {}).get("club_en", "—") + " →", "team", team_id)
                team_link.clicked.connect(lambda _=False, tid=team_id: self.open_route("team", team_id=tid))
                self.scroll.layout.addWidget(team_link, 0, Qt.AlignLeft)
        self.scroll.layout.addWidget(StatGrid([
            (str(len(data["awards"])), "个人奖"),
            (str(len(data["championships"])), "随队冠军"),
            (str(len(data["top20"])), "Top20 入榜"),
        ]))
        if macos.enabled():
            self._readable_honors(world, player_id, data)
            return
        self.scroll.layout.addWidget(SectionTitle("个人奖项", "按赛季记录"))
        if not data["awards"]:
            self.scroll.layout.addWidget(label("暂无个人奖项。", "Meta"))
        for row in data["awards"]:
            self.scroll.layout.addWidget(label(f"S{row['season_number']} · {row['competition_label']} · {row['award_label']}（{row.get('value')}）", "Meta"))
        self.scroll.layout.addWidget(SectionTitle("随队冠军", "按夺冠时注册阵容授予，0 出场也有"))
        if not data["championships"]:
            self.scroll.layout.addWidget(label("暂无随队冠军。", "Meta"))
        medals = TrophyGrid(max_columns=2, minimum_card=360)
        for row in data["championships"]:
            frame = card()
            layout = QHBoxLayout(frame)
            layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
            layout.addWidget(TrophyImage(row["competition_id"], 112))
            body = QVBoxLayout()
            body.addWidget(label(row["competition_label"], "CardTitle", wrap=True))
            body.addWidget(label(f"S{row['season_number']} · 第 {row['decided_week']} 周夺冠", "Meta", wrap=True))
            team_link = EntityLink(row["team_name"], "team", row["champion_team_id"])
            team_link.clicked.connect(lambda _=False, tid=row["champion_team_id"]: self.open_route("team", team_id=tid))
            body.addWidget(team_link, 0, Qt.AlignLeft)
            button = QPushButton("查看该届赛事")
            button.setObjectName("Ghost")
            if int(row["season_number"]) == int(world["season_number"]):
                button.clicked.connect(lambda _=False, cid=row["competition_id"]: self.open_route("competition", competition_id=cid))
            else:
                button.clicked.connect(lambda _=False, cid=row["competition_id"], season=row["season_number"]: self.open_route("history_competition", competition_id=cid, season=season))
            body.addWidget(button, 0, Qt.AlignLeft)
            layout.addLayout(body, 1)
            medals.add_card(frame)
        if data["championships"]:
            self.scroll.layout.addWidget(medals)
        self.scroll.layout.addWidget(SectionTitle("年度 Top20", "独立类别，不与冠军或三奖混合"))
        annual = records.annual_rank_rows(world, player_id)
        if not annual:
            self.scroll.layout.addWidget(label("暂无年度排名记录。", "Meta"))
        for row in annual:
            rank_text = f"年度第 {row['rank']} 名" if row.get("rank") else "名次未记录"
            self.scroll.layout.addWidget(label(f"第 {row['season']} 赛季 · {rank_text} · {row['team']}", "Meta"))
        self.scroll.layout.addStretch(1)

    def _readable_honors(self, world: dict, player_id: str, data: dict) -> None:
        """Primary facts use tables/cards, with a full body-size type hierarchy."""
        awards = []
        for row in sorted(data["awards"], key=lambda row: (-int(row["season_number"]), row["competition_label"], row["award_label"])):
            team = world["teams"].get(row.get("team_id"), {})
            value = row.get("value")
            result = "—" if value is None else (f"{float(value):.2f}" if row["award_type"] == "mvp" else str(value))
            awards.append({**row, "season_text": f"第 {row['season_number']} 季", "result": result,
                           "team": team.get("club_en", "—"), "club": team.get("club_en", "")})
        self._honor_table("个人奖项", "PlayerHonorAwards", awards, [
            {"key": "season_text", "label": "赛季", "width": 88},
            {"key": "competition_label", "label": "赛事", "stretch": True},
            {"key": "award_label", "label": "奖项", "width": 100},
            {"key": "result", "label": "成绩", "width": 80, "numeric": True},
            macos.team_column("team", "当届球队", size=26),
        ], player_id, "暂无个人奖项")

        self.scroll.layout.addWidget(SectionTitle("随队冠军"))
        medals = TrophyGrid(max_columns=2, minimum_card=500)
        medals.setObjectName("PlayerChampionships")
        for row in sorted(data["championships"], key=lambda row: (-int(row["season_number"]), row["competition_label"])):
            frame = card()
            layout = QHBoxLayout(frame)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S16)
            layout.addWidget(TrophyImage(row["competition_id"], 112))
            body = QVBoxLayout()
            body.setSpacing(t.S8)
            body.addWidget(label(row["competition_label"], "CardTitle", wrap=True))
            body.addWidget(label(f"第 {row['season_number']} 赛季 · 冠军", "Strong", wrap=True))
            body.addWidget(label(f"第 {row['decided_week']} 周夺冠", "FactText", wrap=True))
            team_row = QHBoxLayout()
            team_row.addWidget(TeamCrest(row["team_name"], 28))
            team_link = EntityLink(row["team_name"], "team", row["champion_team_id"])
            team_link.clicked.connect(lambda _=False, tid=row["champion_team_id"]: self.open_route("team", team_id=tid))
            team_row.addWidget(team_link)
            team_row.addStretch(1)
            body.addLayout(team_row)
            button = QPushButton("查看该届赛事")
            button.setObjectName("Secondary")
            if int(row["season_number"]) == int(world["season_number"]):
                button.clicked.connect(lambda _=False, cid=row["competition_id"]: self.open_route("competition", competition_id=cid))
            else:
                button.clicked.connect(lambda _=False, cid=row["competition_id"], season=row["season_number"]: self.open_route("history_competition", competition_id=cid, season=season))
            body.addWidget(button, 0, Qt.AlignLeft)
            layout.addLayout(body, 1)
            medals.add_card(frame)
        if data["championships"]:
            self.scroll.layout.addWidget(medals)
        else:
            self.scroll.layout.addWidget(label("暂无随队冠军", "FactText"))

        annual = [{**row, "season_text": f"第 {row['season']} 季",
                   "rank_text": f"年度第 {row['rank']} 名" if row.get("rank") else "名次未记录",
                   "club": world["teams"].get(row.get("team_id"), {}).get("club_en", "")}
                  for row in records.annual_rank_rows(world, player_id)]
        self._honor_table("年度 Top20", "PlayerHonorAnnual", annual, [
            {"key": "season_text", "label": "赛季", "width": 110},
            {"key": "rank_text", "label": "年度名次", "width": 160},
            macos.team_column("team", "当届球队", size=26),
        ], player_id, "暂无年度排名记录")
        self.scroll.layout.addStretch(1)

    def _honor_table(self, title, name, rows, columns, player_id, empty_text) -> None:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S12)
        layout.addWidget(SectionTitle(title))
        table = DataTable()
        table.setObjectName(name)
        table.set_columns(columns)
        table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]) if key == "team" and row.get("team_id") else None)
        macos.paginate_table(table, rows, layout, self.detail_state(player_id).setdefault(name, {}), default_size=10)
        if not rows:
            layout.addWidget(label(empty_text, "FactText"))
        self.scroll.layout.addWidget(frame)


class AnnualTop20Page(BasePage):
    """A dedicated edition view of frozen annual ranks; never recompute scores."""
    route_name = "annual_top20"

    def __init__(self, app):
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)
        self.season = None

    def refresh(self, snapshot, params):
        self.snapshot = snapshot
        world = self.app.world()
        self.scroll.reset()
        back = QPushButton("← 历史")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.open_route("history"))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        self.scroll.layout.addWidget(PageHeader("历史", "年度 Top20", ""))
        editions = {int(a["season_number"]): a.get("top20") or [] for a in world.get("archives", [])}
        current = int(world["season_number"])
        if world["season"].get("finalized") and current not in editions:
            editions[current] = (world["season"].get("awards") or {}).get("top20") or []
        seasons = sorted(editions, reverse=True)
        if not seasons:
            self.scroll.layout.addWidget(EmptyState("年度榜单尚未揭晓", "首个赛季结算后公布。"))
            return
        selected = int(params.get("season") or self.season or seasons[0])
        if selected not in editions:
            selected = seasons[0]
        self.season = selected
        selector = QComboBox()
        selector.setObjectName("AnnualTop20Season")
        selector.setAccessibleName("年度 Top20 赛季")
        for number in seasons:
            selector.addItem(f"第 {number} 赛季", number)
        selector.setCurrentIndex(selector.findData(selected))
        # Route params preserve the selected edition through F5 and Back.
        selector.currentIndexChanged.connect(lambda _: self.open_route("annual_top20", season=selector.currentData()))
        self.scroll.layout.addWidget(selector, 0, Qt.AlignLeft)
        if self.app.press:
            reports=self.app.press.related_button(season=selected,kind='top20')
            if reports:self.scroll.layout.addWidget(reports,0,Qt.AlignLeft)
        panel = card()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.addWidget(SectionTitle(f"第 {selected} 赛季 · Top20"))
        table = DataTable()
        table.setObjectName("AnnualTop20")
        table.set_columns([
            {"key": "rank", "label": "#", "width": 52, "numeric": True},
            {"key": "label", "label": "球员", "stretch": True, "kind": "link"},
            macos.team_column("team", "当届球队"),
        ])
        raw = editions[selected]
        # Labels only: calculation fields never reach visible table cells.
        rows = [{key: row.get(key) for key in ("rank", "label", "player_id", "team_id", "team")} for row in raw]
        for row in rows:
            row["team"] = row.get("team") or queries.team_short(world, row.get("team_id"))
        table.set_rows(macos.with_team_clubs(world, rows))
        table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]) if key == "team" and row.get("team_id") else self.open_route("player", player_id=row["player_id"]))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(40 + 38 * len(rows))
        layout.addWidget(table)
        if not rows:
            layout.addWidget(label("该赛季没有年度榜单。", "Body", wrap=True))
        self.scroll.layout.addWidget(panel)
        self.scroll.layout.addStretch(1)
