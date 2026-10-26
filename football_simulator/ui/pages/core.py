"""首页、周报、比赛中心与比赛详情。"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from football_simulator.app import queries
from football_simulator.game import rules
from football_simulator.ui import tokens as t
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.presentation import copy as copy_text
from football_simulator.ui.presentation import events as event_view
from football_simulator.ui.presentation import focus as focus_view
from football_simulator.ui.trophies import TrophyImage
from football_simulator.ui.team_crest import TeamCrest
from football_simulator.ui.components import (
    Badge,
    CompactMatchRow,
    DataTable,
    EmptyState,
    EntityLink,
    FocusScoreCard,
    KeyValue,
    MatchRowCard,
    PageHeader,
    PageScroll,
    SectionTitle,
    card,
    hline,
    label,
)
from football_simulator.ui.pages.base import BasePage


class DashboardPage(BasePage):
    route_name = "dashboard"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        scroll_value = self.scroll.verticalScrollBar().value()
        self.scroll.reset()
        if not self.save_name:
            self.scroll.layout.addWidget(EmptyState("还没有存档", "先创建并初始化一个世界，再开始观察联赛。"))
            return
        world = self.app.world()
        data = queries.dashboard(world, self.save_name)
        slot = data.get("next_week_slots") or {}
        if data.get("season_complete") or data.get("finalized"):
            progress = f"第 {data['season']} 赛季 · 赛季结束"
        elif slot.get("break_type") == "winter":
            progress = f"第 {data['season']} 赛季 · 冬窗（第 {data['week']} 周）"
        elif slot.get("break_type") == "summer":
            progress = f"第 {data['season']} 赛季 · 夏窗（第 {data['week']} 周）"
        elif data["week_started"]:
            progress = f"第 {data['season']} 赛季 · 已完成第 {data['completed_week']} 周 · 待推进第 {data['next_week']} 周"
        else:
            progress = f"第 {data['season']} 赛季 · 尚未开赛"
        header = PageHeader(progress, "本周观察", "本周赛果与积分变化" if macos.enabled() else copy_text.HOME_SUBTITLE)
        header_row = QWidget()
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(t.S12)
        header_layout.addWidget(header, 1)
        report_week = data["week"] if macos.enabled() and data["season_complete"] else data["last_week"]
        self.weekly_button = QPushButton(
            (f"查看第 {report_week} 周赛况" if getattr(self.app, 'press', None) else f"查看第 {report_week} 周周报") if report_week >= 1 else "尚无周报"
        )
        self.weekly_button.setObjectName("Secondary")
        self.weekly_button.setEnabled(report_week >= 1)
        self.weekly_button.clicked.connect(lambda: self.open_route("weekly", week=max(1, report_week)))
        header_layout.addWidget(self.weekly_button, 0, Qt.AlignTop)
        self.scroll.layout.addWidget(header_row)
        if getattr(self.app, 'press', None):
            self.scroll.layout.addWidget(self._press_card())
        if data["pending"]:
            self.scroll.layout.addWidget(self._pending_card(data["pending"]))
        if data["season_complete"] or data["finalized"]:
            self.scroll.layout.addWidget(self._season_end_card(data))
        if macos.enabled():
            self.scroll.layout.addWidget(self._week_competitions_card(world, data))
        watch_ids = [row["id"] for row in data.get("watchlist", []) if row.get("type") == "team"]
        candidate = focus_view.focus_candidate(world, data, watch_team_ids=watch_ids) if macos.enabled() else None
        focus_match = None
        if candidate and candidate.get("match_id"):
            focus_match = next((row for row in data["last_results"] if row["match_id"] == candidate["match_id"]), None)
            if focus_match is None:
                detail_candidate = queries.match_detail(world, self.save_name, candidate["match_id"]) or {}
                focus_match = detail_candidate.get("fixture")
        if focus_match is None:
            focus_match = self._focus_match(world, data)
        focus = FocusScoreCard()
        if focus_match:
            detail = queries.match_detail(world, self.save_name, focus_match["match_id"]) or {}
            event_rows = event_view.display_events(detail, world, scope=self.player_scope) if macos.enabled() else None
            if candidate and candidate.get("match_id") == focus_match["match_id"]:
                meaning = candidate.get("story") or candidate.get("consequence") or ""
            elif macos.enabled():
                meaning = self._result_meaning(focus_match)
            else:
                focus_story = next(
                    (
                        story
                        for story in data["stories"]
                        if story.get("match_id") == focus_match["match_id"]
                        and int(story.get("season") or data["season"]) == int(data["season"])
                        and (not story.get("competition") or story.get("competition") == focus_match["competition"])
                    ),
                    None,
                )
                meaning = focus_story.get("text", "") if focus_story else self._result_meaning(focus_match)
            focus_dto = detail.get("fixture") or focus_match
            focus.set_match(world, focus_dto, events=detail.get("events") or [], meaning=meaning, tie=detail.get("tie"), event_rows=event_rows)
            focus.detail_requested.connect(lambda match_id: self.open_route("match", match_id=match_id))
        available_width = self.width() or (self.window().width() if self.window() else 0)
        # 1180 在 Windows 的 Segoe UI 字宽下会让双列布局比视口宽约 24px（右列被裁）。
        # 1200 留出字体度量差异的余量；低于该宽度使用单列，布局完全不变。
        wide_estimate = available_width >= 1200
        if macos.enabled():
            facts = focus_view.short_facts(world, data, limit=3, exclude_match=(focus_match or {}).get("match_id"))
            stories = self._fact_rows(facts) if facts else None
        else:
            story_source = [story for story in data["stories"] if story.get("match_id") != (focus_match or {}).get("match_id")]
            story_data = {**data, "stories": story_source}
            stories = self._story_cards(story_data)
        standings = self._standings_card(world, data, compact=wide_estimate)
        next_card = self._next_week_card(data)
        watch_card = self._watch_card(data)
        wide = wide_estimate
        if wide and focus_match:
            columns = QHBoxLayout()
            columns.setSpacing(t.S20)
            left = QVBoxLayout()
            left.setSpacing(t.S12)
            left.addWidget(focus)
            if stories is not None:
                left.addWidget(SectionTitle("本周重点", "赛果、积分与晋级相关的真实变化" if macos.enabled() else "最多 3 条，均链接到真实比赛和积分依据"))
                left.addWidget(stories)
            left.addStretch(1)
            right = QVBoxLayout()
            right.setSpacing(t.S12)
            right.addWidget(standings)
            right.addWidget(next_card)
            right.addWidget(watch_card)
            right.addStretch(1)
            columns.addLayout(left, 2)
            columns.addLayout(right, 1)
            self.scroll.layout.addLayout(columns)
        else:
            if focus_match:
                self.scroll.layout.addWidget(focus)
            if stories is not None:
                self.scroll.layout.addWidget(SectionTitle("本周重点", "赛果、积分与晋级相关的真实变化" if macos.enabled() else "最多 3 条，均链接到真实比赛和积分依据"))
                self.scroll.layout.addWidget(stories)
            self.scroll.layout.addWidget(standings)
            lower = QHBoxLayout()
            lower.setSpacing(t.S20)
            lower.addWidget(next_card, 3)
            lower.addWidget(watch_card, 2)
            self.scroll.layout.addLayout(lower)
        self.scroll.verticalScrollBar().setValue(scroll_value)
        self._wide_mode = wide_estimate

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        snapshot = getattr(self, "snapshot", None)
        if snapshot is None:
            return
        wide = self.width() >= 1180
        previous = getattr(self, "_wide_mode", None)
        if previous is None or previous == wide:
            return
        self._wide_mode = wide
        scroll_value = self.scroll.verticalScrollBar().value()
        QTimer.singleShot(0, lambda: self._relayout_after_resize(scroll_value))

    def _relayout_after_resize(self, scroll_value: int) -> None:
        if not self.isVisible() or getattr(self, "snapshot", None) is None:
            return
        self.refresh(self.snapshot, getattr(self, "params", {}))
        self.scroll.verticalScrollBar().setValue(scroll_value)

    # -- 卡片 ------------------------------------------------------------

    def _press_card(self):
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S10)
        layout.addWidget(label('最新刊物', 'SectionTitle'))
        latest = self.app.press.library().latest()
        if latest:
            from football_simulator.ui.pages.press import plain
            layout.addWidget(label(f"第 {latest['season']} 赛季 · 第 {latest['week']} 周", 'PressIssue'))
            layout.addWidget(plain(latest['title'], 'CardTitle'))
            layout.addWidget(plain(latest['summary']))
        link = QPushButton('阅读最新报道 →' if latest else '进入报刊 →')
        link.setObjectName('Secondary')
        link.setMinimumHeight(44)
        link.clicked.connect(lambda:self.open_route('press_article',report_id=latest['report_id']) if latest and latest.get('kind','weekly')!='weekly'
                             else self.open_route('press_article',season=latest['season'],week=latest['week']) if latest else self.open_route('press'))
        layout.addWidget(link, 0, Qt.AlignLeft)
        return frame

    @staticmethod
    def _result_meaning(dto: dict) -> str:
        if dto.get("status") != "completed":
            return "本场尚未进行。"
        text = f"本场 {dto['home_name']} {dto['total_home']}–{dto['total_away']} {dto['away_name']}（{dto['status_text']}）。"
        if dto.get("home_pk") is not None:
            text += f" 点球 {dto['home_pk']}–{dto['away_pk']} 决胜。"
        return text

    def _focus_match(self, world: dict, data: dict) -> Optional[dict]:
        last = data["last_results"]
        if last:
            # 首页主焦点稳定取最近完成周的首场头条（一级联赛优先），不因
            # 第三方新闻的排列顺序换掉本场焦点。
            return last[0]
        if data["upcoming"]:
            return data["upcoming"][0]
        return None

    def _pending_card(self, pending: List[dict]) -> QFrame:
        frame = card(alt=True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
        layout.setSpacing(t.S12)
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(label("待办事项", "CardTitle"))
        column.addWidget(label(" · ".join(item["label"] for item in pending), "Meta"))
        layout.addLayout(column, 1)
        for item in pending:
            button = QPushButton(item["label"].split(" · ")[0])
            button.clicked.connect(lambda _=False, action=item["id"]: self.app.open_pending(action))
            layout.addWidget(button)
        return frame

    def _story_cards(self, data: dict):
        if not data["stories"]:
            return None
        grid = QWidget()
        grid.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QHBoxLayout(grid)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(t.S12)
        for story in data["stories"][:3]:
            frame = card()
            column = QVBoxLayout(frame)
            column.setContentsMargins(t.S16, t.S14, t.S16, t.S14)
            column.setSpacing(t.S6)
            meta = label(
                f"第 {story.get('week')} 周 · {rules.competition_label(story['competition']) if story.get('competition') in rules.CUP_IDS else '比赛'}",
                "Eyebrow",
            )
            column.addWidget(meta)
            body = label(story.get("text", ""), "Strong", wrap=True)
            # 180 在三列并排 + Segoe UI 时会把双列布局的最小宽度顶出视口；
            # 150 仍能正常换行，正常宽度下卡片实际宽度不受影响。
            body.setMinimumWidth(150)
            column.addWidget(body, 0, Qt.AlignTop)
            match_id = story.get("match_id")
            route = (story.get("payload") or {}).get("route")
            if match_id:
                link = EntityLink("查看战报", "match", match_id)
                link.clicked.connect(lambda _=False, mid=match_id: self.open_route("match", match_id=mid))
                column.addWidget(link)
            elif route:
                link = EntityLink(copy_text.label_for_route(route[0]), route[0], route[1].get("competition_id") or route[0])
                link.clicked.connect(lambda _=False, route=route: self.open_route(route[0], **route[1]))
                column.addWidget(link)
            frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            layout.addWidget(frame, 1)
        return grid

    def _fact_rows(self, facts: List[dict]) -> QWidget:
        """Compact one-line facts; each links to the real destination."""
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
        layout.setSpacing(t.S8)
        for fact in facts:
            row = QHBoxLayout()
            row.setSpacing(t.S10)
            text = label(fact["text"], "FactText", wrap=True)
            text.setMinimumWidth(0)
            row.addWidget(text, 1)
            route, params = fact["route"]
            entity_id = params.get("match_id") or params.get("competition_id") or params.get("team_id") or route
            link = EntityLink(copy_text.label_for_fact(fact.get("kind", ""), route, params), route, entity_id)
            link.clicked.connect(lambda _=False, route=route, params=params: self.open_route(route, **params))
            row.addWidget(link, 0, Qt.AlignTop)
            layout.addLayout(row)
        return frame

    def _week_competitions_card(self, world: dict, data: dict) -> QWidget:
        """Played competitions in the same observed week as the result cards."""
        week = int(data["played_week"])
        entries = queries.week_competitions(world, week, completed_only=True)
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S14, t.S20, t.S14)
        layout.setSpacing(t.S6)
        total = sum(entry["total"] for entry in entries)
        layout.addWidget(SectionTitle("本周赛事", f"第 {week} 周 · {total} 场已赛" if entries else (f"第 {week} 周" if week else "尚未开赛")))
        if not entries:
            if data["season_complete"] or data["finalized"]:
                text = "赛季已结束，开始下一赛季后显示新赛程。"
            elif week in (25, 26, 27):
                text = "冬窗期，本周没有正式比赛。"
            elif week in (50, 51, 52):
                text = "夏窗期，本周没有正式比赛。"
            else:
                text = "本周没有正式比赛。"
            layout.addWidget(label(text, "Meta", wrap=True))
            return frame
        for entry in entries:
            row = QHBoxLayout()
            row.setSpacing(t.S10)
            row.addWidget(TrophyImage(entry["competition_id"], 34), 0, Qt.AlignVCenter)
            link = EntityLink(entry["label"], "competition", entry["competition_id"])
            link.clicked.connect(lambda _=False, cid=entry["competition_id"]: self.open_route("competition", competition_id=cid))
            row.addWidget(link, 0)
            row.addWidget(label(entry.get("stage_text") or "", "Meta", wrap=False), 1)
            match_link = EntityLink(f"{entry['total']} 场", "matches", entry["competition_id"])
            match_link.clicked.connect(
                lambda _=False, cid=entry["competition_id"], week=week: self.open_route("matches", competition=cid, week=week, status="completed")
            )
            row.addWidget(match_link, 0)
            layout.addLayout(row)
        return frame

    def _standings_card(self, world: dict, data: dict, compact: bool = False) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S18, t.S20, t.S18)
        layout.setSpacing(t.S10)
        layout.addWidget(SectionTitle("一级联赛 · 前六", f"截至第 {data['completed_week']} 周" if data.get("completed_week") else "尚未开赛"))
        table = DataTable()
        columns = [
            {"key": "rank", "label": "#", "width": 36, "numeric": True},
            {"key": "team", "label": "球队", "stretch": True, "kind": "link", "icon_key": "club", "icon_size": 24},
            {"key": "played", "label": "赛", "width": 40, "numeric": True},
        ]
        if not compact:
            columns.append({"key": "goal_diff", "label": "净胜", "width": 52, "numeric": True})
        columns.append({"key": "points", "label": "分", "width": 44, "numeric": True})
        if not compact:
            columns.append({"key": "movement", "label": "较上周", "width": 64})
        table.set_columns(columns)
        rows = []
        for row in data["standings"]:
            previous = row.get("previous_rank")
            movement = "—" if previous is None else ("↑" if previous > row["rank"] else "↓" if previous < row["rank"] else "·")
            rows.append({**row, "movement": movement})
        table.set_rows(rows)
        table.set_league_style(rules.PREMIER)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 38 * len(rows) + 4)
        table.horizontalHeader().setStretchLastSection(not macos.enabled())
        table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_id"]))
        table.row_activated.connect(lambda row: self.open_route("team", team_id=row["team_id"]))
        layout.addWidget(table)
        return frame

    def _next_week_card(self, data: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S18, t.S20, t.S18)
        layout.setSpacing(t.S8)
        if macos.enabled():
            week = data["played_week"]
            layout.addWidget(SectionTitle("本周已赛", f"第 {week} 周" if week else "尚未开赛"))
            frame.setAccessibleName("本周已赛")
            for dto in data["played_results"][:4]:
                row = CompactMatchRow(self.app.world(), dto)
                row.clicked_match.connect(lambda match_id: self.open_route("match", match_id=match_id))
                layout.addWidget(row)
            if data["played_count"] > 4:
                more = EntityLink(f"查看本周全部 {data['played_count']} 场 →", "matches", "played-week")
                more.clicked.connect(lambda: self.open_route("matches", week=week, status="completed"))
                layout.addWidget(more)
            if not data["played_results"]:
                layout.addWidget(label("本周暂无已赛比赛。" if week else "推进首周后显示赛果。", "Body", wrap=True))
            return frame
        layout.addWidget(SectionTitle("下一场", f"第 {data['week']} 周"))
        slot = data.get("next_week_slots") or {}
        if slot.get("break_type") == "winter":
            layout.addWidget(label("冬窗休赛周：能力审核、选秀与转会提案。", "Meta", wrap=True))
        elif slot.get("break_type") == "summer":
            layout.addWidget(label("夏窗休赛周：季末选秀与转会窗口。", "Meta", wrap=True))
        if not data["upcoming"]:
            layout.addWidget(label("本周没有比赛安排。" if data["week"] <= 52 else "赛季已经结束。", "Meta", wrap=True))
        for dto in data["upcoming"][:4]:
            if macos.enabled():
                row = CompactMatchRow(self.app.world(), dto)
                row.clicked_match.connect(lambda match_id: self.open_route("match", match_id=match_id))
            else:
                row = MatchRowCard(self.app.world(), dto)
                row.clicked.connect(lambda match_id: self.open_route("match", match_id=match_id))
            layout.addWidget(row)
        if data["upcoming_count"] > 4:
            more = EntityLink(f"另有 {data['upcoming_count'] - 4} 场，查看赛程", "season", "season")
            more.clicked.connect(lambda: self.open_route("season"))
            layout.addWidget(more)
        elif not data["upcoming"]:
            calendar = EntityLink("查看赛季日历", "season", "season")
            calendar.clicked.connect(lambda: self.open_route("season"))
            layout.addWidget(calendar)
        return frame

    def _watch_card(self, data: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S18, t.S20, t.S18)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("我的关注", "按存档保存；关键事项暂停会优先提示"))
        if not data["watchlist"]:
            layout.addWidget(label("还没有关注球队或球员。可在详情页加入关注。", "Meta", wrap=True))
        for row in data["watchlist"][:6]:
            link = EntityLink(row["label"], row["type"], row["id"])
            link.clicked.connect(lambda _=False, row=row: self.open_route(row["type"], **({row["type"] + "_id": row["id"]})))
            layout.addWidget(link)
            layout.addWidget(label(row["detail"], "Meta"))
        return frame

    def _season_end_card(self, data: dict) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S24, t.S20, t.S24, t.S20)
        layout.setSpacing(t.S10)
        champions = data.get("champions", [])
        premier = next((row for row in champions if row.get("competition") == rules.PREMIER), champions[0] if champions else None)
        layout.addWidget(SectionTitle("赛季结束", f"第 {data['season']} 赛季全部冠军已产生"))
        if premier:
            hero = QHBoxLayout()
            hero.setSpacing(t.S14)
            crest = TeamCrest(premier["champion"], 56)
            hero.addWidget(crest, 0, Qt.AlignVCenter)
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(label(premier["champion"], "PageTitle"))
            column.addWidget(label("一级联赛冠军", "Meta"))
            hero.addLayout(column, 1)
            trophy = TrophyImage("premier", 72)
            hero.addWidget(trophy, 0, Qt.AlignVCenter)
            review = QPushButton("回顾本赛季")
            review.setObjectName("Secondary")
            review.clicked.connect(lambda: self._open_last_week(data))
            hero.addWidget(review, 0, Qt.AlignVCenter)
            layout.addLayout(hero)
        rest = [row for row in champions if premier is None or row.get("competition") != premier.get("competition")]
        if rest:
            grid = QGridLayout()
            grid.setHorizontalSpacing(t.S16)
            grid.setVerticalSpacing(t.S6)
            for index, row in enumerate(rest):
                cell = QWidget()
                column = QVBoxLayout(cell)
                column.setContentsMargins(0, 0, 0, 0)
                column.setSpacing(0)
                column.addWidget(label(row["label"], "Meta"))
                column.addWidget(label(row["champion"], "Strong"))
                grid.addWidget(cell, index // 3, index % 3)
            holder = QWidget()
            holder.setLayout(grid)
            layout.addWidget(holder)
        transition = data.get("transition") or {}
        if transition.get("promoted"):
            names = "、".join(self.app.team_name(team_id) for team_id in transition["promoted"])
            layout.addWidget(label(f"升级：{names}", "Meta", wrap=True))
        if transition.get("relegated"):
            names = "、".join(self.app.team_name(team_id) for team_id in transition["relegated"])
            layout.addWidget(label(f"降级：{names}", "Meta", wrap=True))
        actions = QHBoxLayout()
        actions.setSpacing(t.S8)
        honors = EntityLink("查看荣誉馆", "honors", "honors")
        honors.clicked.connect(lambda: self.open_route("honors"))
        actions.addWidget(honors, 0)
        actions.addStretch(1)
        if data.get("season_complete") and not data.get("pending"):
            button = QPushButton("开始下一赛季")
            button.clicked.connect(lambda: self.app.start_next_season())
            actions.addWidget(button, 0)
        elif data.get("pending"):
            actions.addWidget(label("先处理上方季末待办，再开始下一赛季。", "Meta", wrap=True), 1)
        layout.addLayout(actions)
        return frame

    def _open_last_week(self, data: dict) -> None:
        week = int(data.get("last_week") or 0)
        if week < 1:
            week = int(data.get("week") or 1)
        self.open_route("weekly", week=max(1, min(52, week)))


class WeeklyPage(BasePage):
    route_name = "weekly"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)
        self.week: Optional[int] = None
        self.show_review = False
        self.filter = ""
        self.paging = {"page": 1, "page_size": 10}

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        if params.get("week") is not None:
            self.week = int(params["week"])
        if self.week is None:
            self.week = max(1, int(world["season"]["week"]) - 1)
        data = queries.weekly_report(world, self.week)
        self.scroll.reset()
        if macos.enabled():
            self._refresh_compact(world, data)
            self.week = data["week"]
            return
        header_row = QHBoxLayout()
        header_row.addWidget(PageHeader(f"第 {data['season']} 赛季", f"第 {data['week']} 周周报", f"{data['match_count']} 场已完赛 · 先摘要后分赛事"), 1)
        controls = QHBoxLayout()
        previous = QPushButton("上一周")
        previous.setObjectName("Secondary")
        previous.clicked.connect(lambda: self._set_week(data["week"] - 1))
        following = QPushButton("下一周")
        following.setObjectName("Secondary")
        following.clicked.connect(lambda: self._set_week(data["week"] + 1))
        controls.addWidget(previous)
        controls.addWidget(following)
        review_button = QPushButton("收起连续回顾" if self.show_review else "展开连续回顾")
        review_button.setObjectName("Secondary")
        review_button.clicked.connect(self._toggle_review)
        controls.addWidget(review_button)
        container = QWidget()
        container.setLayout(controls)
        header_row.addWidget(container, 0, Qt.AlignTop)
        wrapper = QWidget()
        wrapper.setLayout(header_row)
        self.scroll.layout.addWidget(wrapper)
        if self.show_review:
            self.scroll.layout.addWidget(self._review_card(world, data["week"]))
        if data["empty"]:
            self.scroll.layout.addWidget(
                EmptyState(
                    "本周没有比赛",
                    "可能是冬窗、夏窗或无赛事周。可以查看赛季日历确认下一场比赛。",
                    self._season_button(),
                )
            )
            return
        for group in data["groups"]:
            section = card()
            layout = QVBoxLayout(section)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S8)
            layout.addWidget(SectionTitle(group["label"], f"{len(group['matches'])} 场比赛"))
            for dto in group["matches"]:
                row = MatchRowCard(world, dto)
                row.clicked.connect(lambda match_id: self.open_route("match", match_id=match_id))
                layout.addWidget(row)
            self.scroll.layout.addWidget(section)
        if data["goals"]:
            goals_card = card(alt=True)
            layout = QVBoxLayout(goals_card)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S6)
            layout.addWidget(SectionTitle("进球时间线", "按比赛时间排序"))
            for goal in data["goals"][:8]:
                row = QHBoxLayout()
                row.addWidget(label(f"{goal['minute']}'", "Strong"), 0)
                row.addWidget(label(goal["text"], "Meta", wrap=True), 1)
                layout.addLayout(row)
            self.scroll.layout.addWidget(goals_card)
        self.week = data["week"]

    def _refresh_compact(self, world: dict, data: dict) -> None:
        """Summary + compact one-line results with competition filter and paging."""
        header = PageHeader(f"第 {data['season']} 赛季", f"第 {data['week']} 周周报", f"{data['match_count']} 场已完赛")
        controls = QHBoxLayout()
        controls.setSpacing(t.S8)
        previous = QPushButton("上一周")
        previous.setObjectName("Secondary")
        previous.setEnabled(data["week"] > 1)
        previous.clicked.connect(lambda: self._set_week(data["week"] - 1))
        following = QPushButton("下一周")
        following.setObjectName("Secondary")
        following.setEnabled(data["week"] < 52)
        following.clicked.connect(lambda: self._set_week(data["week"] + 1))
        review_button = QPushButton("收起连续回顾" if self.show_review else "展开连续回顾")
        review_button.setObjectName("Secondary")
        review_button.clicked.connect(self._toggle_review)
        controls.addWidget(previous)
        controls.addWidget(following)
        controls.addWidget(review_button)
        header_row = QHBoxLayout()
        header_row.addWidget(header, 1)
        holder = QWidget()
        holder.setLayout(controls)
        header_row.addWidget(holder, 0, Qt.AlignTop)
        wrapper = QWidget()
        wrapper.setLayout(header_row)
        self.scroll.layout.addWidget(wrapper)
        if self.show_review:
            self.scroll.layout.addWidget(self._review_card(world, data["week"]))
        if data["empty"]:
            empty = copy_text.EMPTY["no_week_matches"]
            self.scroll.layout.addWidget(EmptyState(empty[0], empty[1], self._season_button()))
            return
        facts = focus_view.short_facts(world, {"last_week": data["week"], "season": data["season"]}, limit=2)
        if facts:
            self.scroll.layout.addWidget(self._fact_strip(facts))
        competitions = [(group["competition"], group["label"], group["matches"]) for group in data["groups"]]
        keys = [""] + [comp for comp, _, _ in competitions]
        if self.filter not in keys:
            self.filter = ""
        tabs_items = [("全部赛事", "")] + [(caption, comp) for comp, caption, _ in competitions]
        tabs = macos.section_tabs(tabs_items, self.filter, self._set_filter)
        self.scroll.layout.addWidget(tabs)
        rows = []
        for comp, caption, matches in competitions:
            if self.filter and comp != self.filter:
                continue
            for dto in matches:
                rows.append({**dto, "competition_name": caption})
        rows.sort(key=lambda row: (row.get("week") or 0, row.get("match_id") or ""))
        self.paging.setdefault("page", 1)
        if "competition" not in self.paging or self.paging["competition"] != self.filter:
            self.paging.update(page=1, competition=self.filter)
        table = DataTable()
        columns = [
            {"key": "competition_name", "label": "赛事", "width": 150, "optional": True, "show_at": 1300},
            {"key": "home_name", "label": "主队", "stretch": True, "kind": "link", "icon_key": "home_name", "team_side": "home", "alignment": Qt.AlignRight | Qt.AlignVCenter},
            {"key": "score", "label": "比分", "width": 68, "numeric": True, "kind": "match", "alignment": Qt.AlignCenter},
            {"key": "away_name", "label": "客队", "stretch": True, "kind": "link", "icon_key": "away_name", "team_side": "away", "alignment": Qt.AlignLeft | Qt.AlignVCenter},
            {"key": "stage_text", "label": "阶段", "width": 120, "optional": True, "show_at": 1500},
        ]
        table.set_columns(columns)
        table.horizontalHeader().setStretchLastSection(False)
        table.verticalHeader().setDefaultSectionSize(46)
        table.row_activated.connect(lambda row: self.open_route("match", match_id=row["match_id"]))
        table.cell_activated.connect(lambda row, key: self.open_route("match", match_id=row["match_id"]))
        pager = macos.paginate_table(table, rows, self.scroll.layout, self.paging, default_size=self.paging.get("page_size", 10))
        self.pager = pager
        goals = event_view.goal_timeline(world, [fixture for fixture in world["season"]["fixtures"] if fixture.get("status") == "completed" and int(fixture.get("week") or 0) == data["week"]], limit=6)
        if goals:
            goals_card = card(alt=True)
            layout = QVBoxLayout(goals_card)
            layout.setContentsMargins(t.S20, t.S14, t.S20, t.S14)
            layout.setSpacing(t.S4)
            layout.addWidget(SectionTitle("进球时间线", "本周进球顺序"))
            for goal in goals:
                row = QHBoxLayout()
                row.addWidget(label(goal["label"], "Strong"), 0)
                row.addWidget(label(f"{goal['text']} · {goal['matchup']}", "Meta", wrap=True), 1)
                layout.addLayout(row)
            self.scroll.layout.addWidget(goals_card)

    def _fact_strip(self, facts: List[dict]) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S10, t.S16, t.S10)
        layout.setSpacing(t.S4)
        for fact in facts:
            row = QHBoxLayout()
            text = label(fact["text"], "FactText", wrap=True)
            text.setMinimumWidth(0)
            row.addWidget(text, 1)
            route, params = fact["route"]
            entity_id = params.get("match_id") or params.get("competition_id") or route
            link = EntityLink(copy_text.label_for_fact(fact.get("kind", ""), route, params), route, entity_id)
            link.clicked.connect(lambda _=False, route=route, params=params: self.open_route(route, **params))
            row.addWidget(link, 0)
            layout.addLayout(row)
        return frame

    def _set_filter(self, key: str) -> None:
        self.filter = key or ""
        self.paging.update(page=1)
        self.refresh(self.snapshot, {})

    def _toggle_review(self) -> None:
        self.show_review = not self.show_review
        self.refresh(self.snapshot, {})

    def _review_card(self, world: dict, end_week: int) -> QWidget:
        review = queries.multi_week_review(world, end_week, 4)
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("连续推进回顾", f"第 {review['start_week']}–{review['end_week']} 周；逐周链接到周报"))
        for item in review["weeks"]:
            row = QHBoxLayout()
            row.addWidget(label(f"第 {item['week']} 周", "Strong"), 0)
            row.addWidget(label(f"{item['match_count']} 场", "Meta"), 0)
            row.addWidget(label(item["headline"], "Meta", wrap=True), 1)
            link = EntityLink("展开", "weekly", str(item["week"]))
            link.clicked.connect(lambda _=False, week=item["week"]: self._set_week(week))
            row.addWidget(link, 0)
            layout.addLayout(row)
        return frame

    def _set_week(self, week: int) -> None:
        self.week = max(1, min(52, week))
        self.refresh(self.snapshot, {})

    def _season_button(self) -> QWidget:
        button = QPushButton("查看赛季日历")
        button.clicked.connect(lambda: self.open_route("season"))
        return button


class MatchesPage(BasePage):
    route_name = "matches"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.filters = {"competition": "", "team_id": "", "status": "", "week": "", "search": ""}
        self._week_touched = False
        self._suspend_week_signal = False
        self._last_save = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(t.S32, t.S24, t.S32, t.S24)
        outer.setSpacing(t.S12)
        self.header = PageHeader("观察", "比赛中心", "点击比分查看比赛，点击队名查看球队；Enter 打开选中比赛")
        outer.addWidget(self.header)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(t.S8)
        self.competition = QComboBox()
        self.competition.setMinimumWidth(150)
        self.competition.addItem("全部赛事", "")
        for item in [
            {"label": "一级联赛", "id": rules.PREMIER},
            {"label": "次级联赛", "id": rules.SECOND},
            *[{"label": rules.competition_label(cup_id), "id": cup_id} for cup_id in rules.CUP_IDS],
            {"label": "升级附加赛", "id": rules.PLAYOFF},
        ]:
            self.competition.addItem(item["label"], item["id"])
        self.team = QComboBox()
        self.team.setMinimumWidth(150)
        self.team.addItem("全部球队", "")
        self.status = QComboBox()
        self.status.addItem("全部状态", "")
        self.status.addItem("已完赛", "completed")
        self.status.addItem("未赛", "scheduled")
        self.week = QComboBox()
        self.week.addItem("全部周次", "")
        for week in range(1, 53):
            self.week.addItem(f"第 {week} 周", str(week))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索球队或赛事…")
        self.search.setClearButtonEnabled(True)
        for widget in (self.competition, self.team, self.status, self.week, self.search):
            filter_row.addWidget(widget)
        self.competition.currentIndexChanged.connect(lambda _: self._apply_filters())
        self.team.currentIndexChanged.connect(lambda _: self._apply_filters())
        self.status.currentIndexChanged.connect(lambda _: self._apply_filters())
        self.week.currentIndexChanged.connect(self._week_changed)
        self.search.textChanged.connect(lambda _: self._apply_filters())
        outer.addLayout(filter_row)
        self.table = DataTable()
        columns = [
            {"key": "week", "label": "周", "width": 48, "numeric": True},
            {"key": "competition_label", "label": "赛事", "width": 170},
            {"key": "stage_text", "label": "阶段", "width": 150, "optional": True, "show_at": 1240},
            {"key": "home_name", "label": "主队", "stretch": True, "kind": "link"},
            {"key": "score", "label": "比分", "width": 84, "numeric": True, "kind": "match"},
            {"key": "away_name", "label": "客队", "stretch": True, "kind": "link"},
            {"key": "status_text", "label": "状态", "width": 76},
            {"key": "venue", "label": "场地", "width": 220, "optional": True, "show_at": 1540},
        ]
        if macos.enabled():
            by_key = {column["key"]: column for column in columns}
            by_key["home_name"].update(icon_key="home_name", team_side="home", alignment=Qt.AlignRight | Qt.AlignVCenter)
            by_key["score"].update(alignment=Qt.AlignCenter)
            by_key["away_name"].update(icon_key="away_name", team_side="away", alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.table.set_columns(columns)
        if macos.enabled():
            self.table.horizontalHeader().setStretchLastSection(False)
            self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.row_activated.connect(lambda row: self.open_route("match", match_id=row["match_id"]))
        self.table.cell_activated.connect(self._cell_activated)
        outer.addWidget(self.table, 1)
        self.empty = EmptyState("没有符合条件的比赛", "调整筛选条件，或查看全部比赛。")
        outer.addWidget(self.empty)
        self.empty.hide()

    def _empty_world(self) -> dict:
        return self.app.world() if self.app.has_save else {}

    def _week_changed(self, _index=None) -> None:
        if not self._suspend_week_signal:
            self._week_touched = True
        self._apply_filters()

    def _default_week(self, world: dict) -> int:
        """Current match window: last completed week, else the upcoming week."""
        season = world.get("season") or {}
        week = int(season.get("week") or 1)
        played = [
            int(fixture.get("week") or 0)
            for fixture in season.get("fixtures", [])
            if fixture.get("status") == "completed"
        ]
        if season.get("season_complete") or season.get("finalized"):
            return max(played) if played else week
        return max(played) if played else week

    def _apply_filters(self) -> None:
        self.filters = {
            "competition": self.competition.currentData() or "",
            "team_id": self.team.currentData() or "",
            "status": self.status.currentData() or "",
            "week": self.week.currentData() or "",
            "search": self.search.text().strip(),
        }
        if self.snapshot:
            self.refresh(self.snapshot, {})

    def _apply_route_params(self, params: dict) -> None:
        """First application of competition/week from another page's link."""
        if not macos.enabled() or not params:
            return
        competition = params.get("competition")
        week = params.get("week")
        if competition is not None:
            index = self.competition.findData(competition)
            if index >= 0 and self.competition.currentIndex() != index:
                self.competition.blockSignals(True)
                self.competition.setCurrentIndex(index)
                self.competition.blockSignals(False)
                self.filters["competition"] = competition
        if week is not None:
            index = self.week.findData(str(week))
            if index >= 0 and self.week.currentIndex() != index:
                self.week.blockSignals(True)
                self.week.setCurrentIndex(index)
                self.week.blockSignals(False)
                self.filters["week"] = str(week)
                self._week_touched = True

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        self._apply_route_params(params or {})
        world = self.app.world()
        if self.save_name != self._last_save:
            self._last_save = self.save_name
            self._week_touched = False
        if macos.enabled() and not self._week_touched and not self.filters["week"]:
            default_week = self._default_week(world)
            index = self.week.findData(str(default_week))
            if index >= 0:
                self._suspend_week_signal = True
                self.week.setCurrentIndex(index)
                self._suspend_week_signal = False
                self.filters["week"] = str(default_week)
        if not self.team.count() > 1:
            for item in sorted(
                [{"id": team_id, "label": self.app.team_label(team_id)} for team_id in world["teams"]],
                key=lambda item: item["label"],
            ):
                self.team.addItem(item["label"], item["id"])
        data = queries.matches_page(
            world,
            self.save_name,
            competition=self.filters["competition"] or None,
            team_id=self.filters["team_id"] or None,
            status=self.filters["status"] or None,
            week=int(self.filters["week"]) if self.filters["week"] else None,
            search=self.filters["search"],
        )
        self.table.set_rows(data["rows"])
        self.table.hide_optional_columns(self.window().width())
        self.table.setVisible(bool(data["rows"]))
        self.empty.setVisible(not data["rows"])

    def _cell_activated(self, row: dict, key: str) -> None:
        if key == "home_name":
            self.open_route("team", team_id=row["home_team_id"])
        elif key == "away_name":
            self.open_route("team", team_id=row["away_team_id"])
        else:
            self.open_route("match", match_id=row["match_id"])


class MatchDetailPage(BasePage):
    route_name = "match"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        self.params = params
        match_id = params.get("match_id")
        self.scroll.reset()
        world = self.app.world()
        data = queries.match_detail(world, self.save_name, match_id) if match_id else None
        if data is None:
            self.scroll.layout.addWidget(EmptyState("比赛不存在", "这场比赛可能不在当前存档中。", self._back_button()))
            return
        dto = data["fixture"]
        back = QPushButton("← 返回比赛中心")
        back.setObjectName("Ghost")
        back.clicked.connect(lambda: self.open_route("matches"))
        self.scroll.layout.addWidget(back, 0, Qt.AlignLeft)
        focus = FocusScoreCard()
        event_rows = event_view.display_events(data, world, scope=self.player_scope) if macos.enabled() else None
        focus.set_match(world, dto, tie=data.get("tie"), event_rows=event_rows)
        focus.detail.setVisible(False)
        self.scroll.layout.addWidget(focus)
        if macos.enabled():
            self._refresh_sections(world, dto, data)
            return
        self.scroll.layout.addWidget(self._meta_card(world, dto, data))
        if data["tie"]:
            self.scroll.layout.addWidget(self._aggregate_card(world, data["tie"], dto))
        if data["events"]:
            self.scroll.layout.addWidget(self._timeline_card(world, data["events"]))
        if macos.enabled():
            self.scroll.layout.addWidget(SectionTitle("球员数据", "默认仅查看真实球员；比赛时间线保留完整事件", macos.player_scope_box(self.player_scope, self.set_player_scope)))
        stats = QHBoxLayout()
        stats.setSpacing(t.S16)
        for row in data["roster_rows"]:
            stats.addWidget(self._roster_card(world, row), 1)
        self.scroll.layout.addLayout(stats)
        if data["shootout"]:
            self.scroll.layout.addWidget(self._shootout_card(data["shootout"], dto))
        self.scroll.layout.addWidget(self._links_card(world, dto))

    def _refresh_sections(self, world: dict, dto: dict, data: dict) -> None:
        """First screen: score + meaning; sections for events, players, match info."""
        state = self.__dict__.setdefault("section_state", {"section": "events"})
        if state["section"] not in ("events", "players", "info"):
            state["section"] = "events"
        result_line = self._result_line(dto, data.get("tie"))
        if result_line:
            self.scroll.layout.addWidget(label(result_line, "FactText", wrap=True))
        tabs = macos.section_tabs(
            [("事件", "events"), ("球员表现", "players"), ("对阵信息", "info")],
            state["section"],
            lambda key: self._set_section(key),
        )
        self.scroll.layout.addWidget(tabs)
        if state["section"] == "events":
            # Two-leg context stays on the first screen: this leg, the aggregate
            # and each leg link, so advancement is never hidden behind a tab.
            if data["tie"]:
                self.scroll.layout.addWidget(self._aggregate_card(world, data["tie"], dto))
            self.scroll.layout.addWidget(self._timeline_card(world, data.get("events") or [], data))
            if data["shootout"]:
                self.scroll.layout.addWidget(self._shootout_card(data["shootout"], dto, presentation=True))
        elif state["section"] == "players":
            self.scroll.layout.addWidget(
                SectionTitle("球员表现", "按本场真实数据", macos.player_scope_box(self.player_scope, self.set_player_scope))
            )
            stats = QHBoxLayout()
            stats.setSpacing(t.S16)
            for row in data["roster_rows"]:
                stats.addWidget(self._roster_card(world, row), 1)
            self.scroll.layout.addLayout(stats)
        else:
            if data["tie"]:
                self.scroll.layout.addWidget(self._aggregate_card(world, data["tie"], dto))
            self.scroll.layout.addWidget(self._meta_card(world, dto, data))
            self.scroll.layout.addWidget(self._links_card(world, dto))

    def _set_section(self, key: str) -> None:
        self.section_state = {"section": key}
        self.refresh(self.snapshot, self.params)

    def _result_line(self, dto: dict, tie) -> str:
        if dto.get("status") != "completed":
            return ""
        home, away = dto["home_name"], dto["away_name"]
        score = f"{dto['total_home']}–{dto['total_away']}"
        winner = dto.get("winner_team_id")
        if winner == dto["home_team_id"]:
            winner_name = home
        elif winner == dto["away_team_id"]:
            winner_name = away
        else:
            winner_name = ""
        line = f"{home} {score} {away}"
        if dto.get("home_pk") is not None:
            line += f"（点球 {dto['home_pk']}–{dto['away_pk']}，{winner_name} 晋级）" if winner_name else f"（点球 {dto['home_pk']}–{dto['away_pk']}）"
        elif winner_name:
            line += f" · {winner_name} 胜"
        if tie and tie.get("completed_legs"):
            line += f" · 两回合总分 {tie['aggregate_text']}"
        return line

    def _back_button(self) -> QWidget:
        button = QPushButton("返回比赛中心")
        button.clicked.connect(lambda: self.open_route("matches"))
        return button

    def _meta_card(self, world: dict, dto: dict, data: dict) -> QFrame:
        frame = card()
        grid = QGridLayout(frame)
        grid.setContentsMargins(t.S20, t.S14, t.S20, t.S14)
        grid.setHorizontalSpacing(t.S20)
        grid.setVerticalSpacing(t.S8)
        values = [
            ("赛事", dto["competition_label"]),
            ("阶段", dto["stage_text"] or "联赛"),
            ("场地", data["venue"]["stadium"]),
            (
                "状态",
                dto["status_text"]
                + (f" · 点球 {dto['home_pk']}–{dto['away_pk']}" if dto["home_pk"] is not None else ""),
            ),
        ]
        # 详情元信息按真实正文视口重排；宽屏才四列，窄屏两列并允许场地换行。
        available = self.scroll.viewport().width() if getattr(self, "scroll", None) is not None else self.width()
        columns = 4 if available >= 1380 else 2
        for index, (key, value) in enumerate(values):
            widget = KeyValue(key, value, wrap=True, vertical=columns == 2, key_min_width=64)
            grid.addWidget(widget, index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        return frame

    def _aggregate_card(self, world: dict, tie: dict, dto: dict) -> QFrame:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S14, t.S20, t.S14)
        layout.setSpacing(t.S6)
        completed = int(tie.get("completed_legs") or 0)
        if completed >= 2:
            title = "两回合总比分"
            aggregate_text = tie["aggregate_text"]
        elif completed == 1:
            title = "暂时总分（次回合未赛）"
            aggregate_text = tie["aggregate_text"]
        else:
            title = "两回合赛程（尚未开赛）"
            aggregate_text = "尚无已完成回合"
        layout.addWidget(SectionTitle(title, "取消客场进球；总比分平局才在次回合加时/点球"))
        layout.addWidget(label(aggregate_text, "CardTitle"))
        for leg in tie["legs"]:
            row = QHBoxLayout()
            row.addWidget(label(f"第 {leg['leg']} 回合", "Meta"), 0)
            link = EntityLink(f"{leg['home_name']} {leg['score']} {leg['away_name']}", "match", leg["match_id"])
            link.clicked.connect(lambda _=False, mid=leg["match_id"]: self.open_route("match", match_id=mid))
            row.addWidget(link, 1)
            layout.addLayout(row)
        return frame

    def _timeline_card(self, world: dict, events: List[dict], detail: Optional[dict] = None) -> QFrame:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        if macos.enabled() and detail is not None:
            rows = event_view.display_events(detail, world, scope=self.player_scope, include_minor=True)
            layout.addWidget(SectionTitle("比赛时间线", "进球与关键事件；点球大战单列"))
            if not rows:
                layout.addWidget(label("本场没有记录到关键事件。", "Meta", wrap=True))
            major = [row for row in rows if row["major"]]
            minor = [row for row in rows if not row["major"]]
            for row in major:
                layout.addLayout(self._event_line(row["label"], row["text"], row["score"], "Strong"))
            if minor:
                toggle = QPushButton(f"显示防守与扑救（{len(minor)}）")
                toggle.setObjectName("Ghost")
                holder = QWidget()
                holder_layout = QVBoxLayout(holder)
                holder_layout.setContentsMargins(0, 0, 0, 0)
                holder_layout.setSpacing(t.S4)
                for row in minor:
                    holder_layout.addLayout(self._event_line(row["label"], row["text"], row["score"], "Meta"))
                holder.hide()
                toggle.clicked.connect(lambda: self._toggle_holder(toggle, holder, len(minor)))
                layout.addWidget(toggle, 0, Qt.AlignLeft)
                layout.addWidget(holder)
            return frame
        layout.addWidget(SectionTitle("比赛时间线", "真实事件；加时进球计入，点球大战单列"))
        import re

        for event in events[-24:]:
            row = QHBoxLayout()
            minute = f"{event.get('minute')}'" + (" 加时" if event.get("period") == "et" else "")
            row.addWidget(label(minute, "Meta"), 0)
            text = str(event.get("text", ""))
            text = re.sub(r"^\d+'\s*", "", text)
            weight = "Strong" if event.get("type") == "goal" else ("CardTitle" if event.get("type") in ("save", "penalty_save") else "Meta")
            row.addWidget(label(text, weight, wrap=True), 1)
            layout.addLayout(row)
        return frame

    @staticmethod
    def _event_line(minute: str, text: str, score: Optional[str], weight: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(t.S10)
        stamp = label(minute, "Meta")
        stamp.setFixedWidth(74)
        row.addWidget(stamp, 0, Qt.AlignTop)
        body = label(text, weight, wrap=True)
        body.setMinimumWidth(0)
        row.addWidget(body, 1)
        if score:
            value = label(score, "Meta")
            value.setFixedWidth(46)
            value.setAlignment(Qt.AlignRight | Qt.AlignTop)
            row.addWidget(value, 0, Qt.AlignTop)
        return row

    @staticmethod
    def _toggle_holder(button, holder, count: int) -> None:
        visible = not holder.isVisible()
        holder.setVisible(visible)
        button.setText(f"收起防守与扑救（{count}）" if visible else f"显示防守与扑救（{count}）")

    def _roster_card(self, world: dict, row: dict) -> QFrame:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
        layout.setSpacing(t.S6)
        link = EntityLink(row["team"], "team", row["team_id"])
        link.clicked.connect(lambda _=False, team_id=row["team_id"]: self.open_route("team", team_id=team_id))
        layout.addWidget(link)
        table = DataTable()
        table.set_columns(
            [
                {"key": "name", "label": "球员", "stretch": True, "kind": "link"},
                {"key": "position", "label": "位", "width": 42},
                {"key": "goals", "label": "球", "width": 38, "numeric": True},
                {"key": "assists", "label": "助", "width": 38, "numeric": True},
            ]
        )
        players = macos.visible_players(row["players"], world, self.player_scope)
        table.set_rows(players)
        table.cell_activated.connect(lambda player, key: self.open_route("player", player_id=player["player_id"]))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 34 * len(players) + 4)
        table.verticalHeader().setDefaultSectionSize(32)
        layout.addWidget(table)
        if macos.enabled() and not players:
            layout.addWidget(label("当前范围没有出场球员。", "Meta", wrap=True))
        return frame

    def _shootout_card(self, kicks: List[dict], dto: dict, presentation: bool = False) -> QFrame:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S4)
        layout.addWidget(SectionTitle("点球大战", "每脚独立；不计普通进球、助攻或扑救"))
        layout.addWidget(label(f"{dto['home_name']} {dto['home_pk']}–{dto['away_pk']} {dto['away_name']}", "CardTitle"))
        if presentation and macos.enabled():
            detail = {"fixture": dto, "shootout": kicks}
            world = self.app.world()
            for row in event_view.shootout_rows(detail, world):
                layout.addWidget(label(row["text"], "Meta"))
            return frame
        for kick in kicks:
            layout.addWidget(
                label(
                    f"第 {kick['round']} 轮 · {kick['team']} · {kick['player_name']} · {'命中' if kick['scored'] else '罚失'}",
                    "Meta",
                )
            )
        return frame

    def _links_card(self, world: dict, dto: dict) -> QFrame:
        frame = card()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S12, t.S20, t.S12)
        layout.setSpacing(t.S16)
        for team_id, label_text in ((dto["home_team_id"], dto["home_label"]), (dto["away_team_id"], dto["away_label"])):
            link = EntityLink(label_text, "team", team_id)
            link.clicked.connect(lambda _=False, team_id=team_id: self.open_route("team", team_id=team_id))
            layout.addWidget(link, 1)
        competition = EntityLink(dto["competition_label"], "competition", dto["competition"])
        competition.clicked.connect(lambda _=False, competition_id=dto["competition"]: self.open_route("competition", competition_id=competition_id))
        layout.addWidget(competition, 1)
        return frame
