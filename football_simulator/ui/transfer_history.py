"""Readable macOS transfer history, based on the recorded sending-side IDs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from football_simulator.app import queries
from football_simulator.ui import macos_presentation as macos, tokens as t
from football_simulator.ui.components import Badge, EntityLink, SectionTitle, card, label
from football_simulator.ui.team_crest import TeamCrest


def ordered_transfers(world: dict, *, team_id=None, player_id=None):
    rows = [row for row in world.get("transfer_history", [])
            if (team_id is not None and team_id in (row["team_a"], row["team_b"]))
            or (player_id is not None and player_id in row.get("players_a", []) + row.get("players_b", []))]
    return sorted(rows, key=lambda row: (int(row["season"]), int(row["week"]), str(row.get("trade_id", ""))), reverse=True)


def team_movement(row: dict, team_id: str) -> dict:
    if team_id not in (row["team_a"], row["team_b"]):
        raise ValueError("球队不在该转会记录中")
    side_a = team_id == row["team_a"]
    return {
        "opponent_id": row["team_b"] if side_a else row["team_a"],
        "outgoing": list(row.get("players_a" if side_a else "players_b", [])),
        "incoming": list(row.get("players_b" if side_a else "players_a", [])),
    }


def player_movement(row: dict, player_id: str) -> tuple:
    if player_id in row.get("players_a", []):
        return row["team_a"], row["team_b"]
    if player_id in row.get("players_b", []):
        return row["team_b"], row["team_a"]
    raise ValueError("球员不在该转会记录中")


def history_card(page, world: dict, rows, state: dict, *, team_id=None, player_id=None) -> QWidget:
    frame = card()
    frame.setProperty("transferHistory", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
    layout.setSpacing(t.S12)
    layout.addWidget(SectionTitle("转会记录", "按实际发生时间倒序；点击球队或球员可查看详情"))
    if not rows:
        layout.addWidget(label("暂无转会记录。", "Meta"))
        return frame

    def entity(kind: str, entity_id: str):
        name = queries.team_short(world, entity_id) if kind == "team" else queries.player_name(world, entity_id)
        link = EntityLink(name, kind, entity_id)
        link.clicked.connect(lambda _=False: page.open_route(kind, **{kind + "_id": entity_id}))
        return link

    def team_link(team_id: str):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(t.S8)
        row.addWidget(TeamCrest(world["teams"][team_id]["club_en"], 36))
        row.addWidget(entity("team", team_id))
        row.addStretch(1)
        return widget

    def build(row):
        event = card(alt=True)
        event.setProperty("tradeId", row.get("trade_id", ""))
        event.setToolTip(str(row.get("trade_id", "")))
        content = QVBoxLayout(event)
        content.setContentsMargins(t.S16, t.S12, t.S16, t.S12)
        content.setSpacing(t.S8)
        window = {"winter": "冬窗", "summer": "夏窗"}.get(row.get("window"), "")
        content.addWidget(label(f"第 {row['season']} 赛季 · 第 {row['week']} 周" + (f" · {window}" if window else ""), "Meta"))
        if player_id is not None:
            source, destination = player_movement(row, player_id)
            flow = QHBoxLayout()
            for index, (caption, tid) in enumerate((("离队", source), ("加盟", destination))):
                if index:
                    flow.addWidget(label("→", "CardTitle"), 0, Qt.AlignVCenter)
                column = QVBoxLayout()
                column.addWidget(label(caption, "Meta"))
                column.addWidget(team_link(tid))
                flow.addLayout(column, 1)
            content.addLayout(flow)
        else:
            movement = team_movement(row, team_id)
            event.setProperty("opponentId", movement["opponent_id"])
            content.addWidget(team_link(movement["opponent_id"]))
            movements = QHBoxLayout()
            movements.setSpacing(t.S24)
            for caption, key, variant in (("转入", "incoming", "success"), ("转出", "outgoing", "danger")):
                line = QHBoxLayout()
                badge = Badge(caption, variant)
                badge.setFixedHeight(30)
                line.addWidget(badge)
                players = QVBoxLayout()
                for pid in movement[key]:
                    players.addWidget(entity("player", pid), 0, Qt.AlignLeft)
                if not movement[key]:
                    players.addWidget(label("无", "Meta"))
                line.addLayout(players, 1)
                movements.addLayout(line, 1)
            content.addLayout(movements)
        return event

    macos.paginate_widgets(rows, layout, state, build)
    return frame
