from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QComboBox,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from football_simulator.state import SaveSnapshot, get_competition_archive_rows, _winners_cup_group_standings_from_snapshot
from football_simulator.ui_v2.widgets import CardFrame, ZONE_BACKGROUNDS, build_metric_card, color_position_items, set_table_row, setup_table, shade_row


class CupsPage(QWidget):
    def __init__(
        self,
        open_team_callback: Callable[[str], None],
        open_player_callback: Callable[[str | None, str | None], None],
    ) -> None:
        super().__init__()
        self.open_team_callback = open_team_callback
        self.open_player_callback = open_player_callback
        self.archive_rows: list[dict] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.winners_content, self.winners_layout = _build_scroll_tab()
        self.challenge_content, self.challenge_layout = _build_scroll_tab()
        self.super_content, self.super_layout = _build_scroll_tab()
        self.archive_content, self.archive_layout = _build_scroll_tab()

        self.tabs.addTab(self.winners_content, "优胜者杯")
        self.tabs.addTab(self.challenge_content, "挑战杯")
        self.tabs.addTab(self.super_content, "超级杯")
        self.tabs.addTab(self.archive_content, "赛事档案")

        self._build_winners_tab()
        self._build_challenge_tab()
        self._build_super_tab()
        self._build_archive_tab()

    def _build_winners_tab(self) -> None:
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        self.winners_status_card = build_metric_card("赛事状态", "未启用", "第 2 赛季开始启用")
        self.winners_progress_card = build_metric_card("当前进度", "-", "等待杯赛开始")
        self.winners_champion_card = build_metric_card("当前冠军", "-", "尚未产生")
        cards_row.addWidget(self.winners_status_card)
        cards_row.addWidget(self.winners_progress_card)
        cards_row.addWidget(self.winners_champion_card)
        self.winners_layout.addLayout(cards_row)

        self.winners_layout.addWidget(_section_title("小组阶段"))
        self.winners_layout.addWidget(_group_title("四组双循环，小组前二晋级淘汰赛"))
        group_grid = QGridLayout()
        group_grid.setSpacing(14)
        self.winners_group_tables: dict[str, QTableWidget] = {}
        for index, group_name in enumerate(("A", "B", "C", "D")):
            card = QWidget()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(10)
            card_layout.addWidget(_group_title(f"{group_name} 组"))
            table = self._build_table(["名次", "球队", "赛", "胜", "平", "负", "进", "失", "净", "积"])
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
            table.setMinimumHeight(180)
            table.itemDoubleClicked.connect(self._open_team_from_table_item)
            card_layout.addWidget(table)
            group_grid.addWidget(card, index // 2, index % 2)
            self.winners_group_tables[group_name] = table
        self.winners_layout.addLayout(group_grid)

        self.winners_layout.addWidget(_section_title("淘汰阶段"))
        self.winners_layout.addWidget(_group_title("八强、半决赛、决赛均为主客场两回合"))
        self.winners_bracket_layout = QHBoxLayout()
        self.winners_bracket_layout.setSpacing(14)
        self.winners_qf_column = _build_bracket_stage_column("八强")
        self.winners_sf_column = _build_bracket_stage_column("半决赛")
        self.winners_final_column = _build_bracket_stage_column("决赛")
        self.winners_bracket_layout.addWidget(self.winners_qf_column["widget"])
        self.winners_bracket_layout.addWidget(self.winners_sf_column["widget"])
        self.winners_bracket_layout.addWidget(self.winners_final_column["widget"])
        self.winners_layout.addLayout(self.winners_bracket_layout)

        self.winners_layout.addWidget(_section_title("全部赛果"))
        self.winners_results_table = self._build_table(["阶段", "主队", "比分", "客队"])
        self.winners_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.winners_results_table.horizontalHeader().setStretchLastSection(True)
        self.winners_results_table.setMinimumHeight(320)
        self.winners_results_table.itemDoubleClicked.connect(self._open_team_from_table_item)
        self.winners_layout.addWidget(self.winners_results_table)

    def _build_challenge_tab(self) -> None:
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        self.challenge_status_card = build_metric_card("赛事状态", "未启用", "第 2 赛季开始启用")
        self.challenge_progress_card = build_metric_card("当前进度", "-", "等待杯赛开始")
        self.challenge_champion_card = build_metric_card("当前冠军", "-", "尚未产生")
        cards_row.addWidget(self.challenge_status_card)
        cards_row.addWidget(self.challenge_progress_card)
        cards_row.addWidget(self.challenge_champion_card)
        self.challenge_layout.addLayout(cards_row)

        self.challenge_layout.addWidget(_section_title("挑战杯淘汰赛树"))
        self.challenge_layout.addWidget(_group_title("32 队单场淘汰，排名较高球队主场"))
        self.challenge_bracket_layout = QHBoxLayout()
        self.challenge_bracket_layout.setSpacing(14)
        self.challenge_stage_columns = {
            "challenge_cup_r32": _build_bracket_stage_column("32 强"),
            "challenge_cup_r16": _build_bracket_stage_column("16 强"),
            "challenge_cup_quarterfinal": _build_bracket_stage_column("八强"),
            "challenge_cup_semifinal": _build_bracket_stage_column("四强"),
            "challenge_cup_final": _build_bracket_stage_column("决赛"),
        }
        for stage_key in (
            "challenge_cup_r32",
            "challenge_cup_r16",
            "challenge_cup_quarterfinal",
            "challenge_cup_semifinal",
            "challenge_cup_final",
        ):
            self.challenge_bracket_layout.addWidget(self.challenge_stage_columns[stage_key]["widget"])
        self.challenge_layout.addLayout(self.challenge_bracket_layout)
        self.challenge_layout.addStretch(1)

    def _build_super_tab(self) -> None:
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        self.super_status_card = build_metric_card("赛事状态", "未启用", "第 3 赛季开始启用")
        self.super_progress_card = build_metric_card("当前进度", "-", "等待杯赛开始")
        self.super_champion_card = build_metric_card("当前冠军", "-", "尚未产生")
        cards_row.addWidget(self.super_status_card)
        cards_row.addWidget(self.super_progress_card)
        cards_row.addWidget(self.super_champion_card)
        self.super_layout.addLayout(cards_row)

        self.super_layout.addWidget(_section_title("参赛队"))
        self.super_participants_table = self._build_table(["序号", "球队", "来源"])
        self.super_participants_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.super_participants_table.horizontalHeader().setStretchLastSection(True)
        self.super_participants_table.setMinimumHeight(210)
        self.super_participants_table.itemDoubleClicked.connect(self._open_team_from_table_item)
        self.super_layout.addWidget(self.super_participants_table)

        self.super_layout.addWidget(_section_title("超级杯淘汰赛树"))
        self.super_layout.addWidget(_group_title("4 队单场淘汰，半决赛与决赛跨冬窗进行"))
        stages_row = QHBoxLayout()
        stages_row.setSpacing(14)
        self.super_semifinal_column = _build_bracket_stage_column("半决赛")
        self.super_final_column = _build_bracket_stage_column("决赛")
        stages_row.addWidget(self.super_semifinal_column["widget"])
        stages_row.addWidget(self.super_final_column["widget"])
        self.super_layout.addLayout(stages_row)
        self.super_layout.addStretch(1)

    def _build_archive_tab(self) -> None:
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        self.archive_season_card = build_metric_card("档案赛季", "-", "可查看当前赛季和已归档赛季")
        self.archive_competition_card = build_metric_card("当前赛事", "-", "赛果与个人榜单")
        self.archive_matches_card = build_metric_card("比赛数", "-", "该赛事已记录赛果数量")
        cards_row.addWidget(self.archive_season_card)
        cards_row.addWidget(self.archive_competition_card)
        cards_row.addWidget(self.archive_matches_card)
        self.archive_layout.addLayout(cards_row)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        self.archive_season_combo = QComboBox()
        self.archive_competition_combo = QComboBox()
        self.archive_season_combo.currentIndexChanged.connect(self._refresh_archive_competitions)
        self.archive_competition_combo.currentIndexChanged.connect(self._refresh_archive_tables)
        controls.addWidget(QLabel("赛季"))
        controls.addWidget(self.archive_season_combo)
        controls.addWidget(QLabel("赛事"))
        controls.addWidget(self.archive_competition_combo)
        controls.addStretch(1)
        self.archive_layout.addLayout(controls)

        self.archive_layout.addWidget(_section_title("赛果"))
        self.archive_results_table = self._build_table(["周", "轮次", "主队", "比分", "客队", "事件"])
        self.archive_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.archive_results_table.horizontalHeader().setStretchLastSection(True)
        self.archive_results_table.setMinimumHeight(260)
        self.archive_results_table.itemDoubleClicked.connect(self._open_team_from_table_item)
        self.archive_layout.addWidget(self.archive_results_table)

        leaderboard_grid = QGridLayout()
        leaderboard_grid.setSpacing(14)
        self.archive_goals_table = self._build_leaderboard_table()
        self.archive_assists_table = self._build_leaderboard_table()
        self.archive_ratings_table = self._build_leaderboard_table()
        self.archive_goals_table.itemDoubleClicked.connect(self._open_player_from_archive_item)
        self.archive_assists_table.itemDoubleClicked.connect(self._open_player_from_archive_item)
        self.archive_ratings_table.itemDoubleClicked.connect(self._open_player_from_archive_item)
        leaderboard_grid.addWidget(_table_card("射手榜", self.archive_goals_table), 0, 0)
        leaderboard_grid.addWidget(_table_card("助攻榜", self.archive_assists_table), 0, 1)
        leaderboard_grid.addWidget(_table_card("评分榜", self.archive_ratings_table), 0, 2)
        self.archive_layout.addWidget(_section_title("个人榜单"))
        self.archive_layout.addLayout(leaderboard_grid)
        self.archive_layout.addStretch(1)

    def _build_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget()
        setup_table(table, headers)
        return table

    def _build_leaderboard_table(self) -> QTableWidget:
        table = self._build_table(["排名", "球员", "位置", "球队", "进", "助", "评分"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(320)
        return table

    def set_snapshot(self, snapshot: SaveSnapshot | None) -> None:
        self._populate_winners(snapshot)
        self._populate_challenge(snapshot)
        self._populate_super(snapshot)
        self._populate_archive(snapshot)

    def _populate_winners(self, snapshot: SaveSnapshot | None) -> None:
        _reset_table(self.winners_results_table)
        for table in self.winners_group_tables.values():
            _reset_table(table)
        _clear_bracket_column(self.winners_qf_column["layout"])
        _clear_bracket_column(self.winners_sf_column["layout"])
        _clear_bracket_column(self.winners_final_column["layout"])

        if snapshot is None:
            _set_metric_card(self.winners_status_card, "未载入", "请先选择一个存档")
            _set_metric_card(self.winners_progress_card, "-", "当前没有可展示的数据")
            _set_metric_card(self.winners_champion_card, "-", "尚未产生")
            return

        cup = snapshot.cup_state.get("winners_cup", {})
        if not cup.get("active"):
            _set_metric_card(self.winners_status_card, "未启用", "第 2 赛季开始启用")
            _set_metric_card(self.winners_progress_card, "-", "等待杯赛开始")
            _set_metric_card(self.winners_champion_card, "-", "尚未产生")
            return

        champion = cup.get("champion")
        _set_metric_card(self.winners_status_card, "进行中" if champion is None else "已结束", "16 队参赛 / 4 个小组")
        _set_metric_card(self.winners_progress_card, _latest_winners_stage(cup), f"已完成 {len(cup.get('results', {}))} 个比赛周")
        _set_metric_card(self.winners_champion_card, champion or "-", "冠军已产生" if champion else "尚未产生")

        for group_name, rows in _build_winners_group_rows(snapshot, cup).items():
            table = self.winners_group_tables[group_name]
            for row in rows:
                row_index = table.rowCount()
                set_table_row(
                    table,
                    row_index,
                    [
                        str(row["rank"]),
                        row["team"],
                        str(row["played"]),
                        str(row["wins"]),
                        str(row["draws"]),
                        str(row["losses"]),
                        str(row["goals_for"]),
                        str(row["goals_against"]),
                        str(row["goal_diff"]),
                        str(row["points"]),
                    ],
                )
                table.item(row_index, 1).setData(Qt.UserRole, row["team"])
                if row["rank"] <= 2:
                    shade_row(table, row_index, ZONE_BACKGROUNDS["promotion"])

        knockout_pairs = cup.get("knockout_pairs", {})
        results = cup.get("results", {})
        self._populate_two_leg_stage(
            self.winners_qf_column["layout"],
            knockout_pairs.get("winners_cup_quarterfinal_leg_1", []),
            results.get("winners_cup_quarterfinal_leg_1", []),
            results.get("winners_cup_quarterfinal_leg_2", []),
        )
        self._populate_two_leg_stage(
            self.winners_sf_column["layout"],
            knockout_pairs.get("winners_cup_semifinal_leg_1", []),
            results.get("winners_cup_semifinal_leg_1", []),
            results.get("winners_cup_semifinal_leg_2", []),
        )
        self._populate_two_leg_stage(
            self.winners_final_column["layout"],
            knockout_pairs.get("winners_cup_final_leg_1", []),
            results.get("winners_cup_final_leg_1", []),
            results.get("winners_cup_final_leg_2", []),
            champion=champion,
        )

        for event_key in sorted(results.keys(), key=_event_sort_key):
            for result in results[event_key]:
                row_index = self.winners_results_table.rowCount()
                set_table_row(
                    self.winners_results_table,
                    row_index,
                    [
                        _event_label(event_key),
                        result["home_team"],
                        f"{result['home_goals']}-{result['away_goals']}",
                        result["away_team"],
                    ],
                )
                self.winners_results_table.item(row_index, 1).setData(Qt.UserRole, result["home_team"])
                self.winners_results_table.item(row_index, 3).setData(Qt.UserRole, result["away_team"])

    def _populate_challenge(self, snapshot: SaveSnapshot | None) -> None:
        for stage in self.challenge_stage_columns.values():
            _clear_bracket_column(stage["layout"])

        if snapshot is None:
            _set_metric_card(self.challenge_status_card, "未载入", "请先选择一个存档")
            _set_metric_card(self.challenge_progress_card, "-", "当前没有可展示的数据")
            _set_metric_card(self.challenge_champion_card, "-", "尚未产生")
            return

        cup = snapshot.cup_state.get("challenge_cup", {})
        if not cup.get("active"):
            _set_metric_card(self.challenge_status_card, "未启用", "第 2 赛季开始启用")
            _set_metric_card(self.challenge_progress_card, "-", "等待杯赛开始")
            _set_metric_card(self.challenge_champion_card, "-", "尚未产生")
            return

        champion = cup.get("champion")
        _set_metric_card(self.challenge_status_card, "进行中" if champion is None else "已结束", f"{len(cup.get('participants', []))} 队参赛")
        _set_metric_card(self.challenge_progress_card, _latest_challenge_stage(cup), f"已完成 {len(cup.get('results', {}))} 个阶段")
        _set_metric_card(self.challenge_champion_card, champion or "-", "冠军已产生" if champion else "尚未产生")

        pairings = cup.get("pairings", {})
        results = cup.get("results", {})
        winners = cup.get("winners", {})
        for stage_key, stage in self.challenge_stage_columns.items():
            stage_winners = winners.get(stage_key, [])
            result_list = results.get(stage_key, [])
            for index, pairing in enumerate(pairings.get(stage_key, [])):
                result = result_list[index] if index < len(result_list) else None
                winner = stage_winners[index] if index < len(stage_winners) else "待定"
                stage["layout"].addWidget(
                    _build_match_card(
                        pairing["home"],
                        pairing["away"],
                        _single_leg_score(result),
                        winner,
                        self.open_team_callback,
                    )
                )

    def _populate_super(self, snapshot: SaveSnapshot | None) -> None:
        _reset_table(self.super_participants_table)
        _clear_bracket_column(self.super_semifinal_column["layout"])
        _clear_bracket_column(self.super_final_column["layout"])

        if snapshot is None:
            _set_metric_card(self.super_status_card, "未载入", "请先选择一个存档")
            _set_metric_card(self.super_progress_card, "-", "当前没有可展示的数据")
            _set_metric_card(self.super_champion_card, "-", "尚未产生")
            return

        cup = snapshot.cup_state.get("super_cup", {})
        if not cup.get("active"):
            _set_metric_card(self.super_status_card, "未启用", "第 3 赛季开始启用")
            _set_metric_card(self.super_progress_card, "-", "等待杯赛开始")
            _set_metric_card(self.super_champion_card, "-", "尚未产生")
            return

        champion = cup.get("champion")
        _set_metric_card(self.super_status_card, "进行中" if champion is None else "已结束", "4 队单场淘汰")
        _set_metric_card(self.super_progress_card, _latest_super_stage(cup), f"已完成 {len(cup.get('results', {}))} 个阶段")
        _set_metric_card(self.super_champion_card, champion or "-", "冠军已产生" if champion else "尚未产生")

        for index, team_name in enumerate(cup.get("participants", []), start=1):
            row_index = self.super_participants_table.rowCount()
            set_table_row(
                self.super_participants_table,
                row_index,
                [str(index), team_name, _super_source_label(index)],
            )
            self.super_participants_table.item(row_index, 1).setData(Qt.UserRole, team_name)

        semifinal_results = cup.get("results", {}).get("super_cup_semifinal", [])
        finalists = cup.get("finalists", [])
        for index, pairing in enumerate(cup.get("semifinals", [])):
            result = semifinal_results[index] if index < len(semifinal_results) else None
            winner = finalists[index] if index < len(finalists) else "待定"
            self.super_semifinal_column["layout"].addWidget(
                _build_match_card(
                    pairing[0],
                    pairing[1],
                    _single_leg_score(result),
                    winner,
                    self.open_team_callback,
                )
            )

        final_results = cup.get("results", {}).get("super_cup_final", [])
        if finalists:
            result = final_results[0] if final_results else None
            self.super_final_column["layout"].addWidget(
                _build_match_card(
                    finalists[0],
                    finalists[1],
                    _single_leg_score(result),
                    champion or "待定",
                    self.open_team_callback,
                )
            )

    def _populate_two_leg_stage(
        self,
        layout: QVBoxLayout,
        first_leg_pairs: list[dict],
        first_leg_results: list[dict],
        second_leg_results: list[dict],
        champion: str | None = None,
    ) -> None:
        first_leg_lookup = {(result["home_team"], result["away_team"]): result for result in first_leg_results}
        second_leg_lookup = {(result["home_team"], result["away_team"]): result for result in second_leg_results}
        for pairing in first_leg_pairs:
            first_leg = first_leg_lookup.get((pairing["home"], pairing["away"]))
            second_leg = second_leg_lookup.get((pairing["away"], pairing["home"]))
            aggregate = _two_leg_aggregate(pairing["home"], pairing["away"], first_leg, second_leg)
            winner = _two_leg_winner(pairing["home"], pairing["away"], first_leg, second_leg)
            status = "进行中"
            if second_leg is not None:
                status = f"晋级：{winner}" if winner else "已结束"
            if champion and champion in {pairing["home"], pairing["away"]}:
                status = f"冠军：{champion}"
            layout.addWidget(
                _build_match_card(
                    pairing["home"],
                    pairing["away"],
                    f"首回合 { _single_leg_score(first_leg) } | 次回合 { _single_leg_score(second_leg) }",
                    f"总比分 {aggregate} | {status}",
                    self.open_team_callback,
                )
            )

    def _open_team_from_table_item(self, item) -> None:
        if item is not None and item.data(Qt.UserRole):
            self.open_team_callback(item.data(Qt.UserRole))

    def _open_player_from_archive_item(self, item) -> None:
        if item is None:
            return
        player_item = item.tableWidget().item(item.row(), 1)
        if player_item is None:
            return
        player_id = player_item.data(Qt.UserRole)
        label = player_item.data(Qt.UserRole + 1)
        self.open_player_callback(player_id if player_id and not str(player_id).startswith("real::") else None, label)

    def _populate_archive(self, snapshot: SaveSnapshot | None) -> None:
        self.archive_rows = []
        self.archive_season_combo.blockSignals(True)
        self.archive_competition_combo.blockSignals(True)
        self.archive_season_combo.clear()
        self.archive_competition_combo.clear()
        self.archive_results_table.setRowCount(0)
        for table in (self.archive_goals_table, self.archive_assists_table, self.archive_ratings_table):
            table.setRowCount(0)
        self.archive_season_combo.blockSignals(False)
        self.archive_competition_combo.blockSignals(False)

        if snapshot is None:
            _set_metric_card(self.archive_season_card, "-", "请先选择一个存档")
            _set_metric_card(self.archive_competition_card, "-", "暂无赛事档案")
            _set_metric_card(self.archive_matches_card, "0", "暂无赛果")
            return

        self.archive_rows = sorted(get_competition_archive_rows(snapshot), key=lambda item: item["season_number"], reverse=True)
        self.archive_season_combo.blockSignals(True)
        for row in self.archive_rows:
            self.archive_season_combo.addItem(f"第 {row['season_number']} 赛季", row["season_number"])
        self.archive_season_combo.blockSignals(False)
        self._refresh_archive_competitions()

    def _refresh_archive_competitions(self) -> None:
        season_row = self._selected_archive_season()
        self.archive_competition_combo.blockSignals(True)
        self.archive_competition_combo.clear()
        if season_row is not None:
            for competition in season_row.get("competitions", []):
                self.archive_competition_combo.addItem(competition["name"], competition["name"])
        self.archive_competition_combo.blockSignals(False)
        self._refresh_archive_tables()

    def _refresh_archive_tables(self) -> None:
        season_row = self._selected_archive_season()
        competition_row = self._selected_archive_competition()
        self.archive_results_table.setRowCount(0)
        for table in (self.archive_goals_table, self.archive_assists_table, self.archive_ratings_table):
            table.setRowCount(0)
        if season_row is None or competition_row is None:
            _set_metric_card(self.archive_season_card, "-", "暂无可选赛季")
            _set_metric_card(self.archive_competition_card, "-", "暂无可选赛事")
            _set_metric_card(self.archive_matches_card, "0", "暂无赛果")
            return

        _set_metric_card(self.archive_season_card, f"S{season_row['season_number']}", "赛事历史档案")
        _set_metric_card(self.archive_competition_card, competition_row["name"], _competition_status_note(season_row, competition_row["name"]))
        results = competition_row.get("results", [])
        _set_metric_card(self.archive_matches_card, str(len(results)), "已记录比赛")
        for result in results:
            row_index = self.archive_results_table.rowCount()
            set_table_row(
                self.archive_results_table,
                row_index,
                [
                    str(result.get("week_number", "-")),
                    str(result.get("round_number", "-")),
                    result["home_team"],
                    f"{result['home_goals']}-{result['away_goals']}",
                    result["away_team"],
                    str(result.get("event_count", 0)),
                ],
            )
            self.archive_results_table.item(row_index, 2).setData(Qt.UserRole, result["home_team"])
            self.archive_results_table.item(row_index, 4).setData(Qt.UserRole, result["away_team"])

        leaderboards = competition_row.get("awards", {}).get("leaderboards", {})
        self._populate_archive_leaderboard(self.archive_goals_table, leaderboards.get("goals", []))
        self._populate_archive_leaderboard(self.archive_assists_table, leaderboards.get("assists", []))
        self._populate_archive_leaderboard(self.archive_ratings_table, leaderboards.get("ratings", []))

    def _populate_archive_leaderboard(self, table: QTableWidget, rows: list[dict]) -> None:
        for index, row in enumerate(rows, start=1):
            row_index = table.rowCount()
            set_table_row(
                table,
                row_index,
                [
                    str(index),
                    row["label"],
                    row["position"],
                    row["team_name"],
                    str(row.get("goals", 0)),
                    str(row.get("assists", 0)),
                    f"{float(row.get('rating', 0)):.2f}",
                ],
            )
            table.item(row_index, 1).setData(Qt.UserRole, row.get("player_id"))
            table.item(row_index, 1).setData(Qt.UserRole + 1, row["label"])
        color_position_items(table, 2)

    def _selected_archive_season(self) -> dict | None:
        season_number = self.archive_season_combo.currentData()
        return next((row for row in self.archive_rows if row["season_number"] == season_number), None)

    def _selected_archive_competition(self) -> dict | None:
        season_row = self._selected_archive_season()
        competition_name = self.archive_competition_combo.currentData()
        if season_row is None:
            return None
        return next((row for row in season_row.get("competitions", []) if row["name"] == competition_name), None)


def _build_scroll_tab() -> tuple[QWidget, QVBoxLayout]:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)

    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(6, 6, 6, 6)
    content_layout.setSpacing(18)
    scroll.setWidget(content)

    layout.addWidget(scroll)
    return wrapper, content_layout


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 18px; font-weight: 700; color: #f8fbff; margin-top: 6px;")
    return label


def _group_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 16px; font-weight: 700; color: #f8fbff;")
    return label


def _table_card(title: str, table: QTableWidget) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    layout.addWidget(_group_title(title))
    layout.addWidget(table)
    return wrapper


def _build_bracket_stage_column(title: str) -> dict:
    wrapper = QWidget()
    wrapper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    layout.addWidget(_group_title(title))
    return {"widget": wrapper, "layout": layout}


def _clear_bracket_column(layout: QVBoxLayout) -> None:
    while layout.count() > 1:
        item = layout.takeAt(1)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _build_match_card(
    home: str,
    away: str,
    score_line: str,
    status_line: str,
    open_team_callback: Callable[[str], None],
) -> CardFrame:
    card = CardFrame(f"{home} vs {away}")
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    actions = QHBoxLayout()
    actions.setContentsMargins(0, 0, 0, 0)
    home_button = QPushButton(home)
    away_button = QPushButton(away)
    home_button.clicked.connect(lambda: open_team_callback(home))
    away_button.clicked.connect(lambda: open_team_callback(away))
    actions.addWidget(home_button)
    actions.addWidget(away_button)
    actions.addStretch(1)
    if "冠军" in status_line:
        actions.addWidget(QLabel("冠军战"))
    elif "晋级" in status_line:
        actions.addWidget(QLabel("已决出"))
    score_label = QLabel(score_line)
    score_label.setStyleSheet("font-size: 17px; font-weight: 800; color: #f8fbff;")
    score_label.setWordWrap(True)
    status_label = QLabel(status_line)
    status_label.setObjectName("subtitleLabel")
    status_label.setWordWrap(True)
    card.body_layout.addLayout(actions)
    card.body_layout.addWidget(score_label)
    card.body_layout.addWidget(status_label)
    return card


def _reset_table(table: QTableWidget) -> None:
    table.setRowCount(0)


def _set_metric_card(card, value: str, note: str) -> None:
    card.value_label.setText(value)
    card.note_label.setText(note)


def _event_label(event_key: str) -> str:
    return {
        "winners_cup_group_1": "小组赛第 1 轮",
        "winners_cup_group_2": "小组赛第 2 轮",
        "winners_cup_group_3": "小组赛第 3 轮",
        "winners_cup_group_4": "小组赛第 4 轮",
        "winners_cup_group_5": "小组赛第 5 轮",
        "winners_cup_group_6": "小组赛第 6 轮",
        "winners_cup_quarterfinal_leg_1": "八强首回合",
        "winners_cup_quarterfinal_leg_2": "八强次回合",
        "winners_cup_semifinal_leg_1": "半决赛首回合",
        "winners_cup_semifinal_leg_2": "半决赛次回合",
        "winners_cup_final_leg_1": "决赛首回合",
        "winners_cup_final_leg_2": "决赛次回合",
        "challenge_cup_r32": "32 强",
        "challenge_cup_r16": "16 强",
        "challenge_cup_quarterfinal": "八强",
        "challenge_cup_semifinal": "四强",
        "challenge_cup_final": "决赛",
        "super_cup_semifinal": "半决赛",
        "super_cup_final": "决赛",
    }.get(event_key, event_key)


def _event_sort_key(event_key: str) -> int:
    order = [
        "winners_cup_group_1",
        "winners_cup_group_2",
        "winners_cup_group_3",
        "winners_cup_group_4",
        "winners_cup_group_5",
        "winners_cup_group_6",
        "winners_cup_quarterfinal_leg_1",
        "winners_cup_quarterfinal_leg_2",
        "winners_cup_semifinal_leg_1",
        "winners_cup_semifinal_leg_2",
        "winners_cup_final_leg_1",
        "winners_cup_final_leg_2",
    ]
    try:
        return order.index(event_key)
    except ValueError:
        return len(order)


def _build_winners_group_rows(snapshot: SaveSnapshot, cup: dict) -> dict[str, list[dict]]:
    group_teams = cup.get("groups", {})
    standings = _winners_cup_group_standings_from_snapshot(snapshot)
    group_stats: dict[str, dict[str, dict[str, int]]] = {}
    for group_name, teams in group_teams.items():
        group_stats[group_name] = {
            team_name: {
                "played": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "goals_for": 0,
                "goals_against": 0,
                "points": 0,
            }
            for team_name in teams
        }

    for event_key, result_list in cup.get("results", {}).items():
        if not event_key.startswith("winners_cup_group_"):
            continue
        for result in result_list:
            group_name = next(
                (
                    name
                    for name, teams in group_teams.items()
                    if result["home_team"] in teams and result["away_team"] in teams
                ),
                None,
            )
            if group_name is None:
                continue
            home_stats = group_stats[group_name][result["home_team"]]
            away_stats = group_stats[group_name][result["away_team"]]
            home_goals = result["home_goals"]
            away_goals = result["away_goals"]
            home_stats["played"] += 1
            away_stats["played"] += 1
            home_stats["goals_for"] += home_goals
            home_stats["goals_against"] += away_goals
            away_stats["goals_for"] += away_goals
            away_stats["goals_against"] += home_goals
            if home_goals > away_goals:
                home_stats["wins"] += 1
                away_stats["losses"] += 1
                home_stats["points"] += 3
            elif away_goals > home_goals:
                away_stats["wins"] += 1
                home_stats["losses"] += 1
                away_stats["points"] += 3
            else:
                home_stats["draws"] += 1
                away_stats["draws"] += 1
                home_stats["points"] += 1
                away_stats["points"] += 1

    rows_by_group: dict[str, list[dict]] = {}
    for group_name, teams in group_teams.items():
        ordered_names = standings.get(group_name, teams)
        rows: list[dict] = []
        for rank, team_name in enumerate(ordered_names, start=1):
            stats = group_stats[group_name][team_name]
            rows.append(
                {
                    "rank": rank,
                    "team": team_name,
                    **stats,
                    "goal_diff": stats["goals_for"] - stats["goals_against"],
                }
            )
        rows_by_group[group_name] = rows
    return rows_by_group


def _single_leg_score(result: dict | None) -> str:
    if result is None:
        return "未赛"
    return f"{result['home_goals']}-{result['away_goals']}"


def _two_leg_aggregate(home_team: str, away_team: str, first_leg: dict | None, second_leg: dict | None) -> str:
    if first_leg is None or second_leg is None:
        return "待定"
    home_total = first_leg["home_goals"] + second_leg["away_goals"]
    away_total = first_leg["away_goals"] + second_leg["home_goals"]
    return f"{home_total}-{away_total}"


def _two_leg_winner(home_team: str, away_team: str, first_leg: dict | None, second_leg: dict | None) -> str | None:
    if first_leg is None or second_leg is None:
        return None
    home_total = first_leg["home_goals"] + second_leg["away_goals"]
    away_total = first_leg["away_goals"] + second_leg["home_goals"]
    if home_total > away_total:
        return home_team
    if away_total > home_total:
        return away_team
    home_away_goals = second_leg["away_goals"]
    away_away_goals = first_leg["away_goals"]
    if home_away_goals > away_away_goals:
        return home_team
    if away_away_goals > home_away_goals:
        return away_team
    return None


def _latest_winners_stage(cup: dict) -> str:
    if cup.get("champion"):
        return "决赛结束"
    if "winners_cup_final_leg_1" in cup.get("results", {}):
        return "决赛进行中"
    if "winners_cup_semifinal_leg_2" in cup.get("results", {}):
        return "决赛对阵已出"
    if "winners_cup_semifinal_leg_1" in cup.get("results", {}):
        return "半决赛进行中"
    if "winners_cup_quarterfinal_leg_2" in cup.get("results", {}):
        return "半决赛对阵已出"
    if "winners_cup_quarterfinal_leg_1" in cup.get("results", {}):
        return "八强进行中"
    for round_number in range(6, 0, -1):
        if f"winners_cup_group_{round_number}" in cup.get("results", {}):
            return f"小组赛第 {round_number} 轮"
    return "等待开赛"


def _latest_challenge_stage(cup: dict) -> str:
    if cup.get("champion"):
        return "决赛结束"
    for stage_key in (
        "challenge_cup_final",
        "challenge_cup_semifinal",
        "challenge_cup_quarterfinal",
        "challenge_cup_r16",
        "challenge_cup_r32",
    ):
        if stage_key in cup.get("results", {}):
            return _event_label(stage_key)
    return "等待开赛"


def _latest_super_stage(cup: dict) -> str:
    if cup.get("champion"):
        return "决赛结束"
    if "super_cup_final" in cup.get("results", {}):
        return "决赛进行中"
    if "super_cup_semifinal" in cup.get("results", {}):
        return "决赛对阵已出"
    return "等待开赛"


def _super_source_label(index: int) -> str:
    return {
        1: "联赛前二 / 杯赛冠军资格",
        2: "联赛前二 / 杯赛冠军资格",
        3: "杯赛冠军或顺延资格",
        4: "联赛顺延资格",
    }.get(index, "参赛资格")


def _competition_status_note(season_row: dict, competition: str) -> str:
    if competition == "一级联赛":
        order = season_row.get("premier_order", [])
        return f"冠军：{order[0]}" if order else "当季一级联赛"
    if competition == "次级联赛":
        order = season_row.get("second_order", [])
        return f"冠军：{order[0]}" if order else "当季次级联赛"
    champion = season_row.get("cup_champions", {}).get(competition)
    return f"冠军：{champion}" if champion else "杯赛进行中或未产生冠军"
