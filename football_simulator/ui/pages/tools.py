"""转会、选秀与存档管理页面。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from football_simulator.app import queries
from football_simulator.ui import tokens as t
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.team_crest import TeamCrest
from football_simulator.ui.components import (
    Badge,
    DataTable,
    EmptyState,
    EntityLink,
    PageHeader,
    PageScroll,
    SectionTitle,
    card,
    hline,
    label,
)
from football_simulator.ui.pages.base import BasePage


class TransfersPage(BasePage):
    route_name = "transfers"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)
        self.decisions: Dict[str, bool] = {}
        self._transfer_draft_key = ""
        self._ability_draft_key = ""
        self._summary_label: Optional[QLabel] = None
        self._history_paging: dict = {}

    def _batch_key(self, world: dict, season: int, kind: str, ids) -> str:
        joined = ",".join(sorted(str(item) for item in ids))
        world_id = world.get("world_id", "world")
        return f"{self.app.current_save_name()}|{world_id}|{season}|{kind}|{joined}"

    def _set_transfer_decision(self, trade_id: str, approved: bool) -> None:
        self.decisions[trade_id] = bool(approved)
        if self._transfer_draft_key:
            self.app.set_ui_draft(self._transfer_draft_key, self.decisions)
        self._render_summary()
        self._refresh_position_previews()

    def _refresh_position_previews(self) -> None:
        if not macos.enabled() or not getattr(self, "_proposal_widgets", []):
            return
        from football_simulator.ui.transfer_cards import projected_position_counts
        counts = projected_position_counts(self._proposal_world, self._proposal_items, self.decisions)
        for widget in self._proposal_widgets:
            widget.set_projected(counts)

    def _render_summary(self) -> None:
        if self._summary_label is None:
            return
        approved = sum(1 for value in self.decisions.values() if value)
        rejected = len(self.decisions) - approved
        self._summary_label.setText(f"本批决定：接受 {approved} · 拒绝 {rejected}（提交前可继续修改）")

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        data = queries.transfers_page(world)
        self._proposal_world, self._proposal_items, self._proposal_widgets = world, data["pending"], []
        if macos.enabled():
            data["history"] = [{**row,
                "team_a_name": queries.team_short(world, row["team_a"]),
                "team_b_name": queries.team_short(world, row["team_b"]),
                "players_a_names": [queries.player_name(world, pid) for pid in row.get("players_a", [])],
                "players_b_names": [queries.player_name(world, pid) for pid in row.get("players_b", [])],
            } for row in sorted(world.get("transfer_history", []), key=lambda row: (int(row["season"]), int(row["week"]), str(row.get("trade_id", ""))), reverse=True)]
        self.scroll.reset()
        action_row = QHBoxLayout()
        ability_active = bool(snapshot["pending"]["ability_review"])
        window_name = "冬窗" if "冬" in str(data.get("window", "")) else ("夏窗" if "夏" in str(data.get("window", "")) else "转会窗口")
        pending_count = len(data["pending"])
        subtitle = f"{pending_count} 笔交换提案待决定" if pending_count else "本窗口没有待处理提案"
        if macos.enabled():
            action_row.addWidget(PageHeader(window_name, "转会与能力变化", subtitle), 1)
        else:
            if ability_active and data["pending"]:
                page_title = "能力审核与转会"
            elif ability_active:
                page_title = "能力审核"
            else:
                page_title = "转会审核"
            action_row.addWidget(
                PageHeader(f"第 {data['season']} 赛季 · {data['window']}", page_title, "勾选代表本批决定，可随时修改"),
                1,
            )
        if macos.enabled() and (snapshot["pending"]["ability_review"] or snapshot["pending"]["draft"]):
            self.scroll.layout.addWidget(self._step_strip(snapshot, data))
        submit = QPushButton(f"确认本批决定（{pending_count} 笔）" if macos.enabled() and pending_count else "提交审核结果")
        submit.setObjectName("SubmitTransfers")
        submit.setAccessibleName("确认本批转会决定")
        submit.setEnabled(bool(data["pending"]))
        submit.clicked.connect(lambda: self._submit(data["pending"]))
        if snapshot["pending"]["ability_review"]:
            ability_submit = QPushButton("提交能力审核")
            ability_submit.clicked.connect(self._submit_ability)
            action_row.addWidget(ability_submit, 0, Qt.AlignTop)
        action_row.addWidget(submit, 0, Qt.AlignTop)
        wrapper = QWidget()
        wrapper.setLayout(action_row)
        self.scroll.layout.addWidget(wrapper)
        pending_ids = [item["trade_id"] for item in data["pending"]]
        self._transfer_draft_key = self._batch_key(world, data["season"], "transfer", pending_ids)
        self.decisions = self.app.get_ui_draft(self._transfer_draft_key, {tid: True for tid in pending_ids})
        self._summary_label = label("", "Meta", wrap=True)
        if data["pending"]:
            self._render_summary()
            self.scroll.layout.addWidget(self._summary_label)
        if snapshot["pending"]["ability_review"] or snapshot["pending"]["draft"]:
            note = card(alt=True)
            layout = QVBoxLayout(note)
            layout.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
            layout.addWidget(label("前置待办尚未完成", "CardTitle"))
            layout.addWidget(label("必须先完成能力审核与选秀，转会审核才会生效。", "Meta", wrap=True))
            self.scroll.layout.addWidget(note)
        if snapshot["pending"]["ability_review"]:
            self.scroll.layout.addWidget(self._ability_review_card(world))
        if not data["pending"]:
            if macos.enabled():
                empty = card(alt=True)
                layout = QVBoxLayout(empty)
                layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
                layout.setSpacing(t.S6)
                layout.addWidget(SectionTitle("当前没有待处理事项", "本批提案会出现在这里"))
                links = QHBoxLayout()
                calendar = EntityLink("查看赛历", "season", "season")
                calendar.clicked.connect(lambda: self.open_route("season"))
                back = EntityLink("返回总览", "dashboard", "dashboard")
                back.clicked.connect(lambda: self.open_route("dashboard"))
                links.addWidget(calendar)
                links.addWidget(back)
                links.addStretch(1)
                layout.addLayout(links)
                self.scroll.layout.addWidget(empty)
            else:
                self.scroll.layout.addWidget(
                    EmptyState("当前没有待审核转会", "冬窗 25–27 周、夏窗 50–52 周会生成提案；已完成的交易见下方历史。")
                )
        for item in data["pending"]:
            self.scroll.layout.addWidget(self._proposal_card(world, item))
        self._refresh_position_previews()
        if data["history"]:
            frame = card()
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S8)
            layout.addWidget(SectionTitle("转会历史", "逐场归属保留；同一球员代表两队时个人榜合并"))
            table = DataTable()
            table.set_columns(
                [
                    {"key": "season", "label": "赛季", "width": 56, "numeric": True},
                    {"key": "week", "label": "周", "width": 40, "numeric": True},
                    {"key": "team_a_name", "label": "球队甲", "stretch": True, "kind": "link"},
                    {"key": "players_a_text", "label": "甲队转出 → 乙队" if macos.enabled() else "转出", "stretch": True},
                    {"key": "team_b_name", "label": "球队乙", "stretch": True, "kind": "link"},
                    {"key": "players_b_text", "label": "乙队转出 → 甲队" if macos.enabled() else "转入", "stretch": True},
                ]
            )
            rows = [
                {
                    **row,
                    "players_a_text": "、".join(row["players_a_names"]),
                    "players_b_text": "、".join(row["players_b_names"]),
                }
                for row in data["history"]
            ]
            table.set_rows(rows)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setFixedHeight(36 + 38 * len(rows) + 4)
            if macos.enabled():
                table.cell_activated.connect(lambda row, key: self.open_route("team", team_id=row["team_a" if key == "team_a_name" else "team_b"]))
                macos.paginate_table(table, rows, layout, self._history_paging)
            else:
                layout.addWidget(table)
            self.scroll.layout.addWidget(frame)
        self.scroll.layout.addStretch(1)

    def _ability_review_card(self, world: dict) -> QWidget:
        frame = card(alt=True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S6)
        layout.addWidget(SectionTitle("能力审核", "按存档保存的真实球员能力建议；接受后才写入当前能力，历史结算不变"))
        proposals = world["season"]["pending"].get("ability_review") or []
        ids = [item["player_id"] for item in proposals]
        season = int(world["season_number"])
        self._ability_draft_key = self._batch_key(world, season, "ability", ids)
        decisions = self.app.get_ui_draft(self._ability_draft_key, {pid: True for pid in ids})
        self._ability_summary = label(
            f"本批决定：接受 {sum(1 for value in decisions.values() if value)} · 拒绝 {sum(1 for value in decisions.values() if not value)}",
            "Meta",
            wrap=True,
        )
        layout.addWidget(self._ability_summary)
        self._ability_checks = {}
        for item in proposals:
            row = QHBoxLayout()
            check = QCheckBox(f"{item['label']} · {item['position']} · {item['old_ability']} → {item['new_ability']}（{item['delta']:+d}）")
            check.setChecked(bool(decisions.get(item["player_id"], True)))
            check.stateChanged.connect(
                lambda _state, pid=item["player_id"], cb=check: self._set_ability_decision(pid, cb.isChecked())
            )
            self._ability_checks[item["player_id"]] = check
            row.addWidget(check, 1)
            team_id = item.get("team_id")
            if team_id:
                link = EntityLink(self.app.team_name(team_id), "team", team_id)
                link.clicked.connect(lambda _=False, tid=team_id: self.open_route("team", team_id=tid))
                row.addWidget(link, 0)
            player_link = EntityLink("球员", "player", item["player_id"])
            player_link.clicked.connect(lambda _=False, pid=item["player_id"]: self.open_route("player", player_id=pid))
            row.addWidget(player_link, 0)
            layout.addLayout(row)
        submit = QPushButton("提交本批能力审核")
        submit.clicked.connect(self._submit_ability)
        layout.addWidget(submit, 0, Qt.AlignLeft)
        return frame

    def _set_ability_decision(self, player_id: str, approved: bool) -> None:
        if self._ability_draft_key:
            draft = self.app.get_ui_draft(
                self._ability_draft_key,
                {pid: True for pid in getattr(self, "_ability_checks", {})},
            )
            draft[player_id] = bool(approved)
            self.app.set_ui_draft(self._ability_draft_key, draft)
            if hasattr(self, "_ability_summary"):
                self._ability_summary.setText(
                    f"本批决定：接受 {sum(1 for value in draft.values() if value)} · 拒绝 {sum(1 for value in draft.values() if not value)}"
                )

    def _submit_ability(self) -> None:
        decisions = {player_id: check.isChecked() for player_id, check in getattr(self, "_ability_checks", {}).items()}
        draft_key = self._ability_draft_key
        try:
            self.app.apply_ability_review(decisions)
        except Exception as exc:
            QMessageBox.warning(self, "能力审核失败", str(exc))
            return
        if draft_key:
            self.app.clear_ui_draft(draft_key)

    def _proposal_card(self, world: dict, item: dict) -> QWidget:
        if macos.enabled():
            from football_simulator.ui.transfer_cards import ExchangeProposalCard
            frame = ExchangeProposalCard(world, item, bool(self.decisions.get(item["trade_id"], True)), self.open_route)
            frame.decision_changed.connect(lambda approved, tid=item["trade_id"]: self._set_transfer_decision(tid, approved))
            self._proposal_widgets.append(frame)
            return frame
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        top = QHBoxLayout()
        title = f"交换提案 · {item['position']}"
        top.addWidget(label(title, "CardTitle"), 1)
        top.addWidget(Badge("待决定", "warning"))
        layout.addLayout(top)
        columns = QHBoxLayout()
        for side_key, team_key, players_key in (("甲队", "team_a_name", "players_a"), ("乙队", "team_b_name", "players_b")):
            column = QVBoxLayout()
            link = EntityLink(item[team_key], "team", item[team_key == "team_a_name" and "team_a" or "team_b"])
            team_id = item["team_a"] if team_key == "team_a_name" else item["team_b"]
            link.clicked.connect(lambda _=False, team_id=team_id: self.open_route("team", team_id=team_id))
            column.addWidget(link)
            for player in item[players_key]:
                player_link = EntityLink(f"{player['name']} · 能力 {player['ability']} · 身价 {player['value']:.1f}", "player", player["player_id"])
                player_link.clicked.connect(lambda _=False, pid=player["player_id"]: self.open_route("player", player_id=pid))
                column.addWidget(player_link)
            columns.addLayout(column, 1)
        layout.addLayout(columns)
        gap = f"{item['value_gap']:+.2f}"
        layout.addWidget(label(f"估值差 {gap} · {item['reason']}", "Meta", wrap=True))
        check = QCheckBox("批准这笔转会（取消勾选＝拒绝）")
        check.setChecked(bool(self.decisions.get(item["trade_id"], True)))
        check.stateChanged.connect(
            lambda _state, trade_id=item["trade_id"], cb=check: self._set_transfer_decision(trade_id, cb.isChecked())
        )
        layout.addWidget(check)
        return frame

    def _step_strip(self, snapshot: dict, data: dict) -> QWidget:
        """Real pending state as steps; no step is invented when nothing is pending."""
        frame = card(alt=True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(t.S16, t.S10, t.S16, t.S10)
        layout.setSpacing(t.S10)
        pending = snapshot.get("pending") or {}
        ability = bool(pending.get("ability_review"))
        draft = bool(pending.get("draft"))
        transfer = bool(data.get("pending"))
        steps = []
        if ability:
            steps.append(("能力变化", "待确认"))
        if draft:
            steps.append(("选秀", "待确认"))
        if transfer:
            steps.append(("交换提案", "待确认"))
        if not steps:
            steps = [("本窗口", "没有待处理事项")]
        layout.addWidget(label(" → ".join(name for name, _ in steps), "Strong"))
        layout.addWidget(label(" · ".join(state for _, state in steps), "Meta"), 1)
        if data.get("window"):
            layout.addWidget(label(str(data["window"]), "Meta"))
        return frame

    def _submit(self, pending: List[dict]) -> None:
        decisions = {item["trade_id"]: self.decisions.get(item["trade_id"], True) for item in pending}
        draft_key = self._transfer_draft_key
        try:
            self.app.apply_transfers(decisions)
        except Exception as exc:
            QMessageBox.warning(self, "转会审核失败", str(exc))
            self._render_summary()
            return
        if draft_key:
            self.app.clear_ui_draft(draft_key)


class DraftPage(BasePage):
    route_name = "draft"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)
        self.selected: Dict[str, bool] = {}
        self._result_paging: dict = {}

    def refresh(self, snapshot: dict, params: dict) -> None:
        self.snapshot = snapshot
        world = self.app.world()
        data = queries.draft_page(world, history=macos.enabled(), selected_season=params.get("season"))
        self.scroll.reset()
        if macos.enabled():
            selector = QComboBox()
            selector.setObjectName("DraftHistorySeason")
            selector.setAccessibleName("选秀赛季")
            for number in data["options"]:
                selector.addItem(f"第 {number} 赛季" + (" · 当季" if number == int(world["season_number"]) else ""), number)
            selector.setCurrentIndex(max(0, selector.findData(data["selected_season"])))
            selector.currentIndexChanged.connect(lambda _: self.open_route("draft", season=selector.currentData()))
            self.scroll.layout.addWidget(selector, 0, Qt.AlignRight)
        action_row = QHBoxLayout()
        if macos.enabled():
            subtitle = f"{len(data['candidates'])} 名候选" if data["status"] == "awaiting_input" else f"选秀已完成 · {len(data['logs'][-1]['results'])} 人入选" if data["logs"] else "当前没有选秀"
            action_row.addWidget(PageHeader(f"第 {data['selected_season']} 赛季", "选秀", subtitle), 1)
        else:
            action_row.addWidget(PageHeader(f"第 {data['season']} 赛季", "选秀中心", "按次级倒序 + 一级倒序分配；能力优先，位置空缺决定可否被选中"), 1)
        confirm = QPushButton("确认选秀")
        confirm.setObjectName("SubmitDraft")
        confirm.setAccessibleName("确认选秀")
        confirm.setEnabled(data["status"] == "awaiting_input" and data.get("selected_season", data["season"]) == int(world["season_number"]) and not snapshot["pending"]["ability_review"])
        confirm.clicked.connect(lambda: self._confirm(data["candidates"]))
        action_row.addWidget(confirm, 0, Qt.AlignTop)
        wrapper = QWidget()
        wrapper.setLayout(action_row)
        self.scroll.layout.addWidget(wrapper)
        if snapshot["pending"]["ability_review"]:
            self.scroll.layout.addWidget(label("能力审核尚未完成，选秀不可提交。", "ErrorText", wrap=True))
        if data["status"] == "awaiting_input":
            ids = [item["name"] for item in data["candidates"]]
            world_id = world.get("world_id", "world")
            draft_key = f"{self.app.current_save_name()}|{world_id}|{data['season']}|draft|{','.join(sorted(ids))}"
            self._draft_key = draft_key
            self.selected = self.app.get_ui_draft(draft_key, {name: True for name in ids})
            self.scroll.layout.addWidget(
                label(
                    f"候选 {len(data['candidates'])} 名；选秀顺序 {len(data['order'])} 队。取消勾选可跳过某名候选。",
                    "Meta",
                    wrap=True,
                )
            )
            candidates = card()
            layout = QVBoxLayout(candidates)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S6)
            layout.addWidget(SectionTitle("候选球员", "勾选即参与本次分配"))
            for item in data["candidates"]:
                check = QCheckBox(item["label"])
                check.setChecked(bool(self.selected.get(item["name"], True)))
                check.stateChanged.connect(
                    lambda _state, name=item["name"], cb=check: self._set_draft_choice(name, cb.isChecked())
                )
                layout.addWidget(check)
            self.scroll.layout.addWidget(candidates)
            order = card(alt=True)
            layout = QVBoxLayout(order)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S4)
            layout.addWidget(SectionTitle("选秀顺序", "次级倒数第一 → 次级第一 → 一级倒数第一 → 一级第一"))
            for index, row in enumerate(data["order"], start=1):
                line = QHBoxLayout()
                line.addWidget(label(f"{index:02d}", "Meta"), 0)
                link = EntityLink(row["team"], "team", row["team_id"])
                link.clicked.connect(lambda _=False, team_id=row["team_id"]: self.open_route("team", team_id=team_id))
                line.addWidget(link, 1)
                layout.addLayout(line)
            self.scroll.layout.addWidget(order)
        elif not data["logs"]:
            if macos.enabled() and data["selected_season"] != int(world["season_number"]):
                self.scroll.layout.addWidget(EmptyState("本赛季没有选秀记录", "可切换其他赛季查看。"))
            else:
                self.scroll.layout.addWidget(EmptyState("当前没有选秀", "选秀在赛季末结算后开放；完成能力审核后即可提交。"))
        for log in reversed(data["logs"]):
            if macos.enabled():
                self.scroll.layout.addWidget(self._result_card(world, log))
                continue
            frame = card()
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
            layout.setSpacing(t.S6)
            layout.addWidget(SectionTitle(f"第 {log['season']} 赛季选秀结果", "已完成选秀"))
            for row in log["results"]:
                layout.addWidget(label(f"{row['team']} ← {row['player']} · {row['position']} · 能力 {row['ability']} · 身价 {row['market_value']}", "Meta", wrap=True))
            if log.get("skipped"):
                layout.addWidget(label("用户跳过：" + "、".join(item["name"] for item in log["skipped"]), "Meta", wrap=True))
            if log.get("no_slot"):
                layout.addWidget(label("选中但无位置：" + "、".join(item["name"] for item in log["no_slot"]), "Meta", wrap=True))
            if log["undrafted"]:
                layout.addWidget(label("本批未分配：" + "、".join(item["name"] for item in log["undrafted"]), "Meta", wrap=True))
            self.scroll.layout.addWidget(frame)
        self.scroll.layout.addStretch(1)

    def _result_card(self, world: dict, log: dict) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S16)
        # skipped/no_slot also appear in undrafted; present each candidate once.
        unassigned = {row["name"]: dict(row) for row in log.get("undrafted", [])}
        for key, reason in (("skipped", "user_skipped"), ("no_slot", "no_open_slot")):
            for row in log.get(key, []):
                unassigned[row["name"]] = {**row, "reason": reason}
        summary = Badge(f"入选 {len(log['results'])} 人 · 未入选 {len(unassigned)} 人", "neutral")
        layout.addWidget(SectionTitle(f"第 {log['season']} 赛季选秀结果", "", summary))
        state_key = f"{self.save_name}|{world.get('world_id', '')}|{log['season']}"
        state = self._result_paging.setdefault(state_key, {})
        if log["results"]:
            table = DataTable()
            table.setObjectName("DraftResults")
            table.set_columns([
                {"key": "pick", "label": "顺位", "width": 60, "numeric": True},
                {"key": "player", "label": "球员", "stretch": True, "kind": "link"},
                {"key": "position", "label": "位置", "width": 58},
                {"key": "ability", "label": "能力", "width": 64, "numeric": True},
                {"key": "market_value", "label": "身价", "width": 80, "numeric": True, "decimals": 2},
                macos.team_column("team", "加盟球队"),
            ])
            rows = [{**row, "pick": row.get("pick") or index, "club": world["teams"].get(row["team_id"], {}).get("club_en", "")} for index, row in enumerate(log["results"], 1)]
            def activated(row, key):
                if key == "team":
                    self.open_route("team", team_id=row["team_id"])
                elif key == "player":
                    self.open_route("player", player_id=row["player_id"])
            table.cell_activated.connect(activated)
            table.row_activated.connect(lambda row: self.open_route("player", player_id=row["player_id"]))
            macos.paginate_table(table, rows, layout, state.setdefault("selected", {}))
        else:
            layout.addWidget(label("本次没有球员入选", "CardTitle"))
        if unassigned:
            layout.addWidget(label("未入选球员", "CardTitle"))
            table = DataTable()
            table.setObjectName("DraftUnassigned")
            table.set_columns([
                {"key": "name", "label": "球员", "stretch": True},
                {"key": "position", "label": "位置", "width": 58},
                {"key": "ability", "label": "能力", "width": 64, "numeric": True},
                {"key": "result", "label": "结果", "width": 156},
            ])
            rows = [{**row, "result": {"user_skipped": "已跳过", "no_open_slot": "无对应位置"}.get(row.get("reason"), "未分配")} for row in unassigned.values()]
            macos.paginate_table(table, rows, layout, state.setdefault("unassigned", {}))
        return frame

    def _set_draft_choice(self, name: str, selected: bool) -> None:
        self.selected[name] = bool(selected)
        if getattr(self, "_draft_key", ""):
            self.app.set_ui_draft(self._draft_key, self.selected)

    def _confirm(self, candidates: List[dict]) -> None:
        selected = [item["name"] for item in candidates if self.selected.get(item["name"], True)]
        draft_key = getattr(self, "_draft_key", "")
        try:
            self.app.apply_draft(selected)
        except Exception as exc:
            QMessageBox.warning(self, "选秀失败", str(exc))
            return
        if draft_key:
            self.app.clear_ui_draft(draft_key)


class SavesPage(BasePage):
    route_name = "saves"

    def __init__(self, app) -> None:
        super().__init__(app)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll()
        outer.addWidget(self.scroll)

    def refresh(self, snapshot: Optional[dict], params: dict) -> None:
        self.snapshot = snapshot
        self.scroll.reset()
        self.scroll.layout.addWidget(PageHeader("工具", "存档管理", "继续当前世界，或备份与导入导出"))
        self.scroll.layout.addWidget(self._current_card())
        self.scroll.layout.addWidget(self._new_save_card())
        self.scroll.layout.addWidget(self._save_list_card())
        self.scroll.layout.addWidget(self._trash_card())
        self.scroll.layout.addWidget(self._io_card())
        self.scroll.layout.addStretch(1)

    def _current_card(self) -> QWidget:
        frame = card(alt=True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S14, t.S20, t.S14)
        current = self.app.current_save_name()
        layout.addWidget(label("当前存档", "Meta"), 0)
        layout.addWidget(label(current or "未选择", "CardTitle"), 1)
        if current:
            suffix = "已初始化" if self.app.has_save else "仅建档，尚未初始化"
            layout.addWidget(Badge(suffix, "success" if self.app.has_save else "warning"))
            if not self.app.has_save:
                start = QPushButton("初始化并开始观察")
                start.clicked.connect(lambda: self._initialize(current))
                layout.addWidget(start, 0)
        return frame

    def _new_save_card(self) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        if macos.enabled():
            layout.addWidget(SectionTitle("开始新世界", "为这个世界起一个名字"))
        else:
            layout.addWidget(SectionTitle("新建存档", "中文/字母/数字开头，1–64 字符；重名会被拒绝"))
        row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如：我的联赛")
        if macos.enabled():
            self.name_input.setToolTip("1–64 个字符；中文、字母、数字、空格、下划线与连字符；重名会被拒绝")
        create = QPushButton("开始" if macos.enabled() else "新建")
        create.clicked.connect(self._create)
        row.addWidget(self.name_input, 1)
        row.addWidget(create, 0)
        layout.addLayout(row)
        return frame

    def _save_list_card(self) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("存档列表", "当前世界与进度"))
        table = DataTable()
        if macos.enabled():
            columns = [
                {"key": "save_name", "label": "世界", "stretch": True},
                {"key": "status_text", "label": "状态", "width": 96},
                {"key": "season_text", "label": "赛季", "width": 76, "numeric": True},
                {"key": "week_text", "label": "进度", "width": 92, "numeric": True},
                {"key": "action_text", "label": "操作", "width": 76},
            ]
        else:
            columns = [
                {"key": "save_name", "label": "存档", "stretch": True},
                {"key": "status_text", "label": "状态", "width": 100},
                {"key": "progress", "label": "进度", "width": 130},
                {"key": "action_text", "label": "操作", "width": 90},
            ]
        table.set_columns(columns)
        rows = []
        for item in self.app.service.list_saves():
            if item.get("has_database") and not item.get("compatible", True):
                status, action, progress = "旧格式", "仅提示", "不读取、不迁移"
                season_text, week_text = "—", "—"
            elif item["initialized"]:
                status, action, progress = "已初始化", "继续", f"第 {item['season']} 季 · 第 {item['week']} 周"
                season_text = f"第 {item['season']} 季"
                week_text = f"第 {item['week']} 周"
            else:
                status, action, progress = "未初始化", "初始化", "—"
                season_text, week_text = "—", "—"
            rows.append({**item, "status_text": status, "progress": progress, "action_text": action,
                         "season_text": season_text, "week_text": week_text})
        table.set_rows(rows)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(36 + 38 * len(rows) + 4)
        table.row_activated.connect(self._open_row)
        layout.addWidget(table)
        buttons = QHBoxLayout()
        open_button = QPushButton("打开选中存档")
        open_button.setObjectName("Secondary")
        open_button.clicked.connect(lambda: self._open_selected(table))
        init_button = QPushButton("初始化选中存档")
        init_button.setObjectName("Secondary")
        init_button.clicked.connect(lambda: self._initialize_selected(table))
        delete_button = QPushButton("移入回收站")
        delete_button.setObjectName("Danger")
        delete_button.clicked.connect(lambda: self._delete_selected(table))
        self._save_table = table
        self._open_button = open_button
        self._init_button = init_button
        self._delete_button = delete_button
        table.itemSelectionChanged.connect(self._update_save_actions)
        buttons.addWidget(open_button)
        buttons.addWidget(init_button)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        current = self.app.current_save_name()
        for index, row in enumerate(rows):
            if row.get("save_name") == current:
                table.selectRow(index)
                break
        self._update_save_actions()
        return frame

    def _update_save_actions(self) -> None:
        table = getattr(self, "_save_table", None)
        if table is None:
            return
        row = table.currentRow()
        item = table._rows[row] if 0 <= row < len(table._rows) else None
        compatible = bool(item and item.get("compatible", True))
        initialized = bool(item and item.get("initialized"))
        has_database = bool(item and item.get("has_database"))
        self._open_button.setEnabled(bool(item and compatible and initialized and has_database))
        self._init_button.setEnabled(bool(item and compatible and not initialized))
        self._delete_button.setEnabled(bool(item and compatible))

    def _trash_card(self) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("回收站", "删除只会移入回收站，可恢复"))
        entries = self.app.service.list_trash()
        if not entries:
            layout.addWidget(label("回收站为空。", "Meta"))
        for entry in entries:
            row = QHBoxLayout()
            row.addWidget(label(entry["name"], "Strong"), 1)
            restore = QPushButton("恢复")
            restore.setObjectName("Secondary")
            restore.clicked.connect(lambda _=False, path=entry["path"], name=entry["name"]: self._restore(path, name))
            row.addWidget(restore)
            layout.addLayout(row)
        if entries:
            empty = QPushButton("清空回收站")
            empty.setObjectName("Danger")
            empty.clicked.connect(self._empty_trash)
            layout.addWidget(empty, 0, Qt.AlignLeft)
        return frame

    def _io_card(self) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S8)
        layout.addWidget(SectionTitle("导入 / 导出存档", "导出为单个存档包；导入会先校验再安装"))
        row = QHBoxLayout()
        export = QPushButton("导出当前存档…")
        export.setObjectName("Secondary")
        export.clicked.connect(self._export)
        import_button = QPushButton("导入存档包…")
        import_button.setObjectName("Secondary")
        import_button.clicked.connect(self._import)
        row.addWidget(export)
        row.addWidget(import_button)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _selected_name(self, table: DataTable) -> Optional[str]:
        row = table.currentRow()
        if row < 0 or row >= len(table._rows):
            return None
        return table._rows[row]["save_name"]

    def _create(self) -> None:
        name = self.name_input.text().strip()
        try:
            self.app.create_save(name)
        except Exception as exc:
            QMessageBox.warning(self, "新建存档失败", str(exc))

    def _open_row(self, row: dict) -> None:
        if row.get("has_database") and not row.get("compatible", True):
            QMessageBox.warning(self, "旧格式存档", "该存档不是当前新格式，本版本不迁移、不读取。原文件保持原样。")
            return
        if row["initialized"]:
            self.app.open_save(row["save_name"])
        else:
            self._initialize(row["save_name"])

    def _open_selected(self, table: DataTable) -> None:
        name = self._selected_name(table)
        if name:
            self.app.open_save(name)

    def _initialize_selected(self, table: DataTable) -> None:
        name = self._selected_name(table)
        if name:
            self._initialize(name)

    def _initialize(self, name: str) -> None:
        try:
            self.app.initialize_save(name)
        except Exception as exc:
            QMessageBox.warning(self, "初始化失败", str(exc))

    def _delete_selected(self, table: DataTable) -> None:
        name = self._selected_name(table)
        if not name:
            return
        answer = QMessageBox.question(self, "移入回收站", f"把存档「{name}」移入回收站？可在本页恢复。")
        if answer == QMessageBox.Yes:
            try:
                self.app.delete_save(name)
            except Exception as exc:
                QMessageBox.warning(self, "删除失败", str(exc))

    def _restore(self, path: str, name: str) -> None:
        try:
            self.app.restore_trash(Path(path), name)
        except Exception as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _empty_trash(self) -> None:
        answer = QMessageBox.question(self, "清空回收站", "永久删除回收站中的全部存档？此操作不可撤销。")
        if answer == QMessageBox.Yes:
            try:
                self.app.empty_trash()
            except Exception as exc:
                QMessageBox.warning(self, "清空失败", str(exc))

    def _export(self) -> None:
        name = self.app.current_save_name()
        if not name:
            return
        default = str(Path.home() / f"{name}.fsarchive")
        path, _ = QFileDialog.getSaveFileName(self, "导出完整存档包", default, "Football Simulator 存档包 (*.fsarchive)")
        if not path:
            return
        try:
            self.app.export_save(name, Path(path))
            QMessageBox.information(self, "导出完成", f"已导出到：\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入完整存档包", str(Path.home()), "Football Simulator 存档包 (*.fsarchive)")
        if not path:
            return
        suggested = Path(path).stem
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "导入存档", "新存档名：", text=suggested)
        if not ok or not name.strip():
            return
        try:
            self.app.import_save(Path(path), name.strip())
            QMessageBox.information(self, "导入完成", "导入包已校验并安装。")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"{exc}\n\n现有存档未被改动。")
