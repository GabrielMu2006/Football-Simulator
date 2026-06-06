from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QWidget, QHeaderView

from football_simulator.state import SaveSnapshot
from football_simulator.ui_v2.widgets import CardFrame, build_metric_card, color_position_items, money_text, set_table_row, setup_table


class DashboardPage(QWidget):
    def __init__(self, open_matches_callback: Callable[[], None]) -> None:
        super().__init__()
        self.open_matches_callback = open_matches_callback
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.season_card = build_metric_card("当前赛季", "-", "还没有载入赛季数据")
        self.week_card = build_metric_card("当前周次", "-", "初始化后就会显示 52 周进度")
        self.pending_card = build_metric_card("待处理事项", "-", "能力审核、转会审核、选秀都会汇总在这里")
        self.phase_card = build_metric_card("当前阶段", "-", "联赛周、冬窗、夏窗、休赛期")
        layout.addWidget(self.season_card, 0, 0)
        layout.addWidget(self.week_card, 0, 1)
        layout.addWidget(self.pending_card, 0, 2)
        layout.addWidget(self.phase_card, 0, 3)

        self.recent_panel = CardFrame("最近一周赛果", "这里会显示最近一周的比分摘要。")
        recent_actions = QHBoxLayout()
        recent_actions.setContentsMargins(0, 0, 0, 0)
        recent_actions.addStretch(1)
        self.open_recent_button = QPushButton("查看最近一周详细赛况")
        self.open_recent_button.clicked.connect(self.open_matches_callback)
        recent_actions.addWidget(self.open_recent_button, 0, Qt.AlignRight)
        self.recent_panel.body_layout.addLayout(recent_actions)
        self.recent_table = QTableWidget()
        setup_table(self.recent_table, ["赛事", "轮次", "主队", "比分", "客队"])
        self.recent_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.recent_table.horizontalHeader().setStretchLastSection(True)
        self.recent_panel.body_layout.addWidget(self.recent_table)
        layout.addWidget(self.recent_panel, 1, 0, 1, 2)

        self.overview_panel = CardFrame("赛季概览", "快速查看联赛和杯赛当前状态。")
        self.overview_label = QLabel("还没有可显示的数据。")
        self.overview_label.setWordWrap(True)
        self.overview_panel.body_layout.addWidget(self.overview_label)
        self.overview_panel.body_layout.addStretch(1)
        layout.addWidget(self.overview_panel, 1, 2, 1, 2)

        self.race_panel = CardFrame("赛季指挥中心", "争冠、升级、保级风险一屏扫过。")
        self.race_table = QTableWidget()
        setup_table(self.race_table, ["区域", "球队", "说明"])
        self.race_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.race_table.horizontalHeader().setStretchLastSection(True)
        self.race_panel.body_layout.addWidget(self.race_table)
        layout.addWidget(self.race_panel, 2, 0, 1, 2)

        self.spotlight_panel = CardFrame("本季明星", "只读取当前赛季已有数据，不改变结算。")
        self.spotlight_table = QTableWidget()
        setup_table(self.spotlight_table, ["类型", "球员", "位置", "球队", "数据"])
        self.spotlight_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.spotlight_table.horizontalHeader().setStretchLastSection(True)
        self.spotlight_panel.body_layout.addWidget(self.spotlight_table)
        layout.addWidget(self.spotlight_panel, 2, 2, 1, 2)

        self.todo_panel = CardFrame("下一步操作", "把需要玩家处理的事情放到首页。")
        self.todo_table = QTableWidget()
        setup_table(self.todo_table, ["事项", "数量", "建议"])
        self.todo_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.todo_table.horizontalHeader().setStretchLastSection(True)
        self.todo_panel.body_layout.addWidget(self.todo_table)
        layout.addWidget(self.todo_panel, 3, 0, 1, 4)

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        if snapshot is None or not snapshot.premier_table or not snapshot.second_table:
            self._set_card_values("-", "-", "-", "-")
            self.recent_table.setRowCount(0)
            self.race_table.setRowCount(0)
            self.spotlight_table.setRowCount(0)
            self.todo_table.setRowCount(0)
            self.open_recent_button.setEnabled(False)
            self.overview_label.setText("增强版首页已经就绪。下一步可以在这里放赛历时间轴、联赛形势和杯赛摘要。")
            return

        phase = snapshot.weeks[snapshot.current_week].label if snapshot.current_week < len(snapshot.weeks) else "赛季已结束"
        pending_count = (
            len(snapshot.pending_ability_review)
            + len(snapshot.pending_transfer_review)
            + (1 if snapshot.pending_draft.get("status") == "awaiting_input" else 0)
        )
        self._set_card_values(
            f"第 {snapshot.season_number} 赛季",
            f"{snapshot.current_week}/{len(snapshot.weeks)}",
            str(pending_count),
            phase,
        )

        self.recent_table.setRowCount(0)
        if snapshot.simulated_weeks:
            week = snapshot.simulated_weeks[-1]
            self.open_recent_button.setEnabled(True)
            for key in ("premier_matchdays", "second_matchdays", "cup_matchdays", "playoff_matchdays"):
                for matchday in week.get(key, []):
                    competition = matchday.get("competition", "赛事")
                    round_number = matchday.get("round_number", "-")
                    for result in matchday.get("results", []):
                        set_table_row(
                            self.recent_table,
                            self.recent_table.rowCount(),
                            [
                                competition,
                                str(round_number),
                                result["home_team"],
                                f"{result['home_goals']}-{result['away_goals']}",
                                result["away_team"],
                            ],
                        )
        else:
            self.open_recent_button.setEnabled(False)

        overview_lines = [
            f"一级联赛榜首：{snapshot.premier_table[0].team.name}（{snapshot.premier_table[0].points} 分）",
            f"次级联赛榜首：{snapshot.second_table[0].team.name}（{snapshot.second_table[0].points} 分）",
            f"历史赛季数：{len(snapshot.history)}",
        ]
        relegation_names = "、".join(row.team.name for row in snapshot.premier_table[-3:])
        promotion_names = "、".join(row.team.name for row in snapshot.second_table[:2])
        active_cups = [
            label
            for label, key in (("优胜者杯", "winners_cup"), ("挑战杯", "challenge_cup"), ("超级杯", "super_cup"))
            if snapshot.cup_state.get(key, {}).get("active")
        ]
        value_leader = max(
            (row for row in snapshot.team_stats if row.total_market_value is not None),
            key=lambda row: row.total_market_value or 0,
            default=None,
        )
        overview_lines.extend(
            [
                f"当前降级区：{relegation_names}",
                f"当前直升区：{promotion_names}",
                f"激活杯赛：{'、'.join(active_cups) if active_cups else '暂无'}",
            ]
        )
        if value_leader is not None:
            overview_lines.append(f"总身价最高：{value_leader.team_name}（{value_leader.total_market_value:.2f}M）")
        if snapshot.pending_draft.get("status") == "awaiting_input":
            overview_lines.append("当前赛季已结束，等待选秀录入。")
        self.overview_label.setText("\n".join(overview_lines))
        self._populate_race_table(snapshot)
        self._populate_spotlight_table(snapshot)
        self._populate_todo_table(snapshot)

    def _set_card_values(self, season: str, week: str, pending: str, phase: str) -> None:
        self.season_card.value_label.setText(season)  # type: ignore[attr-defined]
        self.week_card.value_label.setText(week)  # type: ignore[attr-defined]
        self.pending_card.value_label.setText(pending)  # type: ignore[attr-defined]
        self.phase_card.value_label.setText(phase)  # type: ignore[attr-defined]

    def _populate_race_table(self, snapshot: SaveSnapshot) -> None:
        self.race_table.setRowCount(0)
        for index, row in enumerate(snapshot.premier_table[:4], start=1):
            set_table_row(
                self.race_table,
                self.race_table.rowCount(),
                ["争冠区", row.team.name, f"第 {index} 名 | {row.points} 分 | 净胜球 {row.goal_diff}"],
            )
        for index, row in enumerate(snapshot.premier_table[-3:], start=len(snapshot.premier_table) - 2):
            set_table_row(
                self.race_table,
                self.race_table.rowCount(),
                ["降级区", row.team.name, f"第 {index} 名 | {row.points} 分 | 净胜球 {row.goal_diff}"],
            )
        for index, row in enumerate(snapshot.second_table[:6], start=1):
            zone = "直升区" if index <= 2 else "附加赛区"
            set_table_row(
                self.race_table,
                self.race_table.rowCount(),
                [zone, row.team.name, f"第 {index} 名 | {row.points} 分 | 净胜球 {row.goal_diff}"],
            )

    def _populate_spotlight_table(self, snapshot: SaveSnapshot) -> None:
        self.spotlight_table.setRowCount(0)
        real_rows = [row for row in snapshot.player_stats if row.player.is_real]
        if not real_rows:
            set_table_row(self.spotlight_table, 0, ["暂无", "-", "-", "-", "还没有真实球员数据"])
            return
        entries = [
            ("射手", max(real_rows, key=lambda row: (row.goals, row.assists, row.player.ability)), lambda row: f"{row.goals} 球"),
            ("助攻", max(real_rows, key=lambda row: (row.assists, row.goals, row.player.ability)), lambda row: f"{row.assists} 助"),
            (
                "评分",
                max(real_rows, key=lambda row: (row.season_rating or 0, row.goals + row.assists, row.player.ability)),
                lambda row: "待结算" if row.season_rating is None else f"{row.season_rating:.2f}",
            ),
            (
                "身价",
                max(real_rows, key=lambda row: row.market_value or 0),
                lambda row: money_text(row.market_value),
            ),
        ]
        for label, row, formatter in entries:
            set_table_row(
                self.spotlight_table,
                self.spotlight_table.rowCount(),
                [label, row.player.label, row.player.position, row.team_name, formatter(row)],
            )
        color_position_items(self.spotlight_table, 2)

    def _populate_todo_table(self, snapshot: SaveSnapshot) -> None:
        self.todo_table.setRowCount(0)
        rows: list[list[str]] = []
        if snapshot.pending_transfer_review:
            rows.append(["转会审核", str(len(snapshot.pending_transfer_review)), "进入转会中心，逐笔批准或拒绝"])
        if snapshot.pending_ability_review:
            rows.append(["能力变动", str(len(snapshot.pending_ability_review)), "进入待办流程，确认赛季末能力变化"])
        if snapshot.pending_draft.get("status") == "awaiting_input":
            rows.append(["选秀", "1", "进入选秀中心录入或使用配置候选名单"])
        if not rows:
            rows.append(["暂无待办", "0", "可以继续模拟下一周"])
        for row in rows:
            set_table_row(self.todo_table, self.todo_table.rowCount(), row)
