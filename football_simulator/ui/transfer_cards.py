"""macOS exchange cards: two clubs, readable packages, explicit decisions."""
from collections import Counter

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from football_simulator.game.models import FORMATION_RULES
from football_simulator.ui import tokens as t
from football_simulator.ui.components import Badge, EntityLink, hline, label
from football_simulator.ui.team_crest import TeamCrest


def real_position_counts(world, team_id):
    return Counter(world["players"][entry["player_id"]]["position"] for entry in world["teams"][team_id]["roster"] if world["players"][entry["player_id"]].get("is_real"))


def projected_position_counts(world, proposals, decisions):
    counts = {team_id: real_position_counts(world, team_id) for team_id in world["teams"]}
    for item in proposals:
        if not decisions.get(item["trade_id"], True):
            continue
        for source, target, key in (("team_a", "team_b", "players_a"), ("team_b", "team_a", "players_b")):
            for player in item[key]:
                position = player["position"]
                counts[item[source]][position] -= 1
                counts[item[target]][position] += 1
    return counts


class ExchangeTeamCard(QFrame):
    def __init__(self, world, team_id, players, open_route):
        super().__init__()
        self.setObjectName("ExchangeTeam")
        self.team_id = team_id
        self.current_counts = real_position_counts(world, team_id)
        self.projected_counts = self.current_counts.copy()
        self.count_labels = {}
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setStyleSheet(f"QFrame#ExchangeTeam {{ background: {t.CARD_ALT}; border: 1px solid {t.BORDER}; border-radius: {t.RADIUS_MD}px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.S16, t.S16, t.S16, t.S16)
        layout.setSpacing(t.S12)
        header = QHBoxLayout()
        header.setSpacing(t.S10)
        club = world["teams"][team_id]["club_en"]
        header.addWidget(TeamCrest(club, 40), 0)
        name = EntityLink(club, "team", team_id)
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        name.setMinimumHeight(40)
        name.setToolTip(club)
        name.clicked.connect(lambda: open_route("team", team_id=team_id))
        header.addWidget(name, 1)
        layout.addLayout(header)
        summary = QHBoxLayout()
        summary.addWidget(label(f"送出 {len(players)} 人", "Strong"), 1)
        summary.addWidget(label(f"总身价 {sum(player['value'] for player in players):.2f}", "Strong"), 0)
        layout.addLayout(summary)
        layout.addWidget(hline())
        grid = QGridLayout()
        grid.setHorizontalSpacing(t.S10)
        grid.setVerticalSpacing(t.S6)
        grid.setColumnStretch(0, 1)
        grid.addWidget(label("球员", "Meta"), 0, 0)
        grid.addWidget(label("位置", "Meta"), 0, 1)
        grid.addWidget(label("身价", "Meta"), 0, 2, Qt.AlignRight)
        for row, player in enumerate(players, 1):
            link = EntityLink(player["name"], "player", player["player_id"])
            link.setMinimumHeight(36)
            link.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            link.setToolTip(player["name"])
            link.clicked.connect(lambda _=False, pid=player["player_id"]: open_route("player", player_id=pid))
            grid.addWidget(link, row, 0)
            position = Badge(player["position"], "neutral")
            position.setMinimumWidth(38)
            position.setAlignment(Qt.AlignCenter)
            grid.addWidget(position, row, 1)
            value = label(f"{player['value']:.2f}", "Strong")
            value.setMinimumWidth(68)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(value, row, 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        layout.addWidget(hline())
        layout.addWidget(label("位置人数 · 真实球员", "Strong"))
        self.count_caption = label("当前 → 本批接受后 / 上限", "Meta")
        layout.addWidget(self.count_caption)
        counts = QHBoxLayout()
        counts.setSpacing(t.S6)
        for position in FORMATION_RULES:
            cell = QWidget()
            column = QVBoxLayout(cell)
            column.setContentsMargins(0, t.S4, 0, t.S4)
            column.setSpacing(t.S4)
            title = label(position, "Meta")
            title.setAlignment(Qt.AlignCenter)
            number = label("", "Strong")
            number.setAlignment(Qt.AlignCenter)
            column.addWidget(title)
            column.addWidget(number)
            self.count_labels[position] = number
            counts.addWidget(cell, 1)
        layout.addLayout(counts)
        self.set_projected(self.current_counts)

    def set_projected(self, counts):
        self.projected_counts = dict(counts)
        for position, limit in FORMATION_RULES.items():
            now, after = self.current_counts[position], counts.get(position, 0)
            text = f"{now} → {after}/{limit}" if now != after else f"{now}/{limit}"
            number = self.count_labels[position]
            number.setText(text)
            number.setAccessibleName(f"{position} 当前{now}人，本批接受后{after}人，上限{limit}人")
            color = t.DANGER if after > limit or after < 0 else t.PRIMARY if now != after else t.TEXT_STRONG
            number.setStyleSheet(f"color: {color}; font-weight: 700;")


class ExchangePanels(QWidget):
    def __init__(self, left, right):
        super().__init__()
        self.panels = (left, right)
        self._wide = None
        self.grid = QGridLayout(self)
        self.grid.setSizeConstraint(QLayout.SetNoConstraint)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(t.S16)
        self._reflow(True)

    def _reflow(self, wide):
        if wide == self._wide:
            return
        self._wide = wide
        for panel in self.panels:
            self.grid.removeWidget(panel)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if wide else 0)
        self.grid.addWidget(self.panels[0], 0, 0)
        self.grid.addWidget(self.panels[1], 0 if wide else 1, 1 if wide else 0)
        self.updateGeometry()

    def minimumSizeHint(self):
        return QSize(360, self.grid.minimumSize().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(self.width() >= 760)


class ExchangeProposalCard(QFrame):
    decision_changed = Signal(bool)

    def __init__(self, world, item, approved, open_route):
        super().__init__()
        self.setObjectName("Card")
        self.trade_id = item["trade_id"]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.S20, t.S16, t.S20, t.S16)
        layout.setSpacing(t.S16)
        top = QHBoxLayout()
        top.addWidget(label(f"交换提案 · {len(item['players_a'])}换{len(item['players_b'])}", "CardTitle"), 1)
        gap = abs(sum(p["value"] for p in item["players_a"]) - sum(p["value"] for p in item["players_b"]))
        top.addWidget(Badge(f"身价差 {gap:.2f} / 20", "danger" if round(gap, 2) > 20 else "neutral"))
        self.status = Badge("", "success")
        top.addWidget(self.status)
        layout.addLayout(top)
        self.teams = [ExchangeTeamCard(world, item[key], item[players], open_route) for key, players in (("team_a", "players_a"), ("team_b", "players_b"))]
        layout.addWidget(ExchangePanels(*self.teams))
        actions = QHBoxLayout()
        actions.setSpacing(t.S12)
        self.group = QButtonGroup(self)
        self.accept = QPushButton("接受这笔交换")
        self.reject = QPushButton("拒绝这笔交换")
        for button, choice in ((self.accept, "accept"), (self.reject, "reject")):
            button.setObjectName("TransferAccept" if choice == "accept" else "TransferReject")
            button.setProperty("choice", choice)
            button.setProperty("trade_id", self.trade_id)
            button.setCheckable(True)
            button.setMinimumHeight(46)
            button.setMinimumWidth(160)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(f"{'接受' if choice == 'accept' else '拒绝'}交换 · {world['teams'][item['team_a']]['club_en']} 与 {world['teams'][item['team_b']]['club_en']}")
            self.group.addButton(button)
            actions.addWidget(button, 1)
        self.setStyleSheet(f"""
            QPushButton#TransferAccept, QPushButton#TransferReject {{ background: {t.CARD}; color: {t.TEXT}; border: 1px solid {t.BORDER_STRONG}; }}
            QPushButton#TransferAccept:hover, QPushButton#TransferReject:hover {{ background: {t.CARD_ALT}; }}
            QPushButton#TransferAccept:checked {{ background: {t.PRIMARY}; color: {t.FOCUS_TEXT}; border: 1px solid {t.PRIMARY}; }}
            QPushButton#TransferReject:checked {{ background: {t.DANGER_BG}; color: {t.DANGER}; border: 1px solid {t.DANGER}; }}
            QPushButton#TransferAccept:focus, QPushButton#TransferReject:focus {{ border: 2px solid {t.ACCENT}; }}
        """)
        (self.accept if approved else self.reject).setChecked(True)
        self._sync_status()
        # Native accessibility activation may toggle a checkable QPushButton
        # without emitting QButtonGroup.buttonClicked. Persist the state itself.
        self.accept.toggled.connect(self._changed)
        layout.addLayout(actions)

    def _sync_status(self):
        approved = self.accept.isChecked()
        self.status.setText("接受 · 待提交" if approved else "拒绝 · 待提交")
        self.status.set_variant("success" if approved else "danger")

    def _changed(self, _button):
        self._sync_status()
        self.decision_changed.emit(self.accept.isChecked())

    def set_projected(self, counts):
        for team in self.teams:
            team.set_projected(counts[team.team_id])
