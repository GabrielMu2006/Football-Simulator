"""CUI-050/070/080: native Qt competition views.

A small painted graph canvas (no WebView, no static images) shared by the Swiss
bracket and the double-elimination panorama, plus the shared top-10 boards.
Only display data goes in: the topology comes from presentation.swiss /
presentation.bracket, so both views share one source of truth.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from football_simulator.ui import components as ui
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui import tokens as t
from football_simulator.ui.components import DataTable, TeamCrest, label
from football_simulator.ui.team_crest import TeamCrest as CrestWidget, crest_pixmap


class GraphView(QWidget):
    """Painted node/edge canvas with hit-testing; one vertical page scroll only."""

    match_requested = Signal(str)
    team_requested = Signal(str)
    stage_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("GraphCanvas")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._nodes: List[dict] = []
        self._edges: List[dict] = []
        self._headers: List[dict] = []
        self._size = QSize(640, 320)
        self._scale = 1.0
        self._polished = False
        self._bucket_graph = False
        self._full_height = False
        self._auto_fit = False
        self._pool_blocks: List[dict] = []
        self._outcome_panels: List[dict] = []
        self._bands: List[dict] = []
        self._active_team = ""
        self._active_node = ""
        self._keyboard_row = 0
        #: Polylines actually handed to QPainter in the last paintEvent (model
        #: coordinates).  The P2 geometry test renders the real widget and reads
        #: this list, so it can never check a path different from the drawing.
        self._painted_polylines: List[dict] = []
        title_font = QFont(self.font())
        title_font.setPointSizeF(max(9.0, title_font.pointSizeF()))
        title_font.setWeight(QFont.DemiBold)
        self._title_font = title_font
        self._name_font = QFont(self.font())
        self._name_font.setPointSizeF(max(9.0, self.font().pointSizeF()))
        self._name_metrics = QFontMetrics(self._name_font)
        self._title_metrics = QFontMetrics(self._title_font)

    # -- data -----------------------------------------------------------
    def set_graph(self, nodes: List[dict], edges: List[dict], headers: List[dict], size: QSize) -> None:
        self._nodes = nodes
        self._edges = edges
        self._headers = headers
        self._size = size
        self._apply_scale(self._scale)

    def content_width(self) -> int:
        return self._size.width()

    def scale(self) -> float:
        return self._scale

    def _apply_scale(self, scale: float) -> None:
        self._scale = max(0.45, min(1.8, scale))
        self.setMinimumSize(QSize(int(self._size.width() * self._scale), int(self._size.height() * self._scale)))
        self.resize(self.minimumSize())
        self.update()

    def apply_scale(self, scale: float) -> None:
        self._apply_scale(scale)

    def fit_width(self, width: int) -> None:
        if self._size.width() > 0:
            self._apply_scale(width / self._size.width())

    def node_rect(self, node_id: str) -> Optional[QRect]:
        for node in self._nodes:
            if node["id"] == node_id:
                return QRect(
                    int(node["x"] * self._scale), int(node["y"] * self._scale),
                    int(node["w"] * self._scale), int(node["h"] * self._scale),
                )
        return None

    def node_ids(self) -> List[str]:
        return [node["id"] for node in self._nodes]

    # -- painting -------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(t.BG))
        painter.save()
        painter.scale(self._scale, self._scale)
        for band in self._bands:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(band["color"]))
            painter.drawRoundedRect(QRect(*band["rect"]), t.RADIUS_MD, t.RADIUS_MD)
        for block in self._pool_blocks + self._outcome_panels:
            rect = QRect(block["x"], block["y"], block["w"], block["h"])
            painter.setPen(QPen(QColor(t.BORDER), 1))
            painter.setBrush(QColor(block.get("bg") or t.CARD_ALT))
            painter.drawRoundedRect(rect, t.RADIUS_SM, t.RADIUS_SM)
            painter.setFont(self._title_font)
            painter.setPen(QColor(block.get("color") or t.TEXT_STRONG))
            painter.drawText(QRect(rect.left() + 12, rect.top() + 4, rect.width() - 24, 24), Qt.AlignLeft | Qt.AlignVCenter, block["label"])
            if block.get("empty"):
                painter.setFont(self._name_font)
                painter.setPen(QColor(t.MUTED))
                painter.drawText(rect.adjusted(12, 32, -12, -8), Qt.AlignLeft | Qt.AlignVCenter, block["empty"])
        for header in self._headers:
            rect = QRect(header["x"], header["y"], header["w"], header["h"])
            painter.setPen(QColor(t.TEXT_STRONG))
            painter.setFont(self._title_font)
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, header["label"])
            painter.setFont(self._name_font)
            painter.setPen(QColor(t.MUTED))
            painter.drawText(rect.adjusted(0, 0, 0, 0), Qt.AlignRight | Qt.AlignVCenter, header.get("hint", ""))
            if header.get("stage"):
                painter.setPen(QPen(QColor(t.BORDER), 1))
                painter.drawLine(rect.left(), rect.bottom() + 4, rect.right(), rect.bottom() + 4)
        positions = {node["id"]: QRect(node["x"], node["y"], node["w"], node["h"]) for node in self._nodes}
        nodes_by_id = {node["id"]: node for node in self._nodes}
        positions.update({block["id"]: QRect(block["x"], block["y"], block["w"], block["h"]) for block in self._pool_blocks})
        self._painted_polylines = []
        for edge in self._edges:
            source = positions.get(edge.get("from"))
            target = positions.get(edge.get("to"))
            if source is None or target is None:
                continue
            winner = edge.get("style") == "winner"
            color = QColor(t.SUCCESS if winner else t.ACCENT)
            weight = 1.6
            if self._bucket_graph:
                color = QColor(t.SUCCESS if winner else t.NEUTRAL)
                weight = 1.5
            if self._polished:
                color = QColor(t.SUCCESS if winner else t.BRACKET_TRANSFER)
                weight = 1.7 if winner else 1.1
                if self._active_team:
                    selected = edge.get("team_id") == self._active_team
                    color = QColor(t.PRIMARY if winner else t.ACCENT) if selected else color
                    color.setAlpha(255 if selected else 45)
                    weight = 2.6 if selected else 1.0
            pen = QPen(color, weight, Qt.SolidLine if winner else Qt.DashLine)
            if self._polished:
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                if not winner:
                    pen.setDashPattern([4, 4])
            painter.setPen(pen)
            spec = ui.BRACKET_NODE_SPEC
            from_row = int(edge.get("from_row") or 0)
            to_row = int(edge.get("to_row") or 0)
            row_h = spec["row_height"]
            source_node = nodes_by_id.get(edge.get("from"), {})
            target_node = nodes_by_id.get(edge.get("to"), {})
            start = QPoint(source.right(), source.top() + spec["pad_y"] + source_node.get("row_offset", 0) + from_row * row_h + row_h // 2)
            end = QPoint(target.left(), target.top() + spec["pad_y"] + target_node.get("row_offset", 0) + to_row * row_h + row_h // 2)
            points = [QPoint(int(x), int(y)) for x, y in (edge.get("polyline") or [])]
            if len(points) >= 2:
                # Layouts own their explicit routes; this canvas transforms
                # and draws them. Unrouted generic graphs use the fallback.
                painter.drawPolyline(QPolygon(points))
            else:
                mid = (start.x() + end.x()) // 2
                points = [QPoint(start.x(), start.y()), QPoint(mid, start.y()), QPoint(mid, end.y()), QPoint(end.x(), end.y())]
                painter.drawLine(start.x(), start.y(), mid, start.y())
                painter.drawLine(mid, start.y(), mid, end.y())
                painter.drawLine(mid, end.y(), end.x(), end.y())
            if edge.get("arrow"):
                end_point = points[-1]
                painter.setBrush(color)
                painter.setPen(Qt.NoPen)
                painter.drawPolygon(QPolygon([end_point, end_point + QPoint(-7, -4), end_point + QPoint(-7, 4)]))
            self._painted_polylines.append(
                {
                    "from": edge.get("from"),
                    "to": edge.get("to"),
                    "style": edge.get("style"),
                    "points": [(point.x(), point.y()) for point in points],
                }
            )
        for node in self._nodes:
            self._paint_node(painter, node)
        painter.restore()
        painter.end()

    def _paint_node(self, painter: QPainter, node: dict) -> None:
        spec = ui.BRACKET_NODE_SPEC
        rect = QRect(node["x"], node["y"], node["w"], node["h"])
        active = self._polished and self._active_team and any(team.get("team_id") == self._active_team for team in node.get("team_rows") or [])
        border = t.PRIMARY if active else (node.get("border") or t.BORDER)
        painter.setPen(QPen(QColor(border), 1.5 if active else 1))
        painter.setBrush(QColor(node.get("bg") or t.CARD))
        painter.drawRoundedRect(rect, t.RADIUS_SM, t.RADIUS_SM)
        inner = rect.adjusted(spec["pad_x"], spec["pad_y"], -spec["pad_x"], -spec["pad_y"])
        two_leg = bool(node.get("legs"))
        score_width = 46 if two_leg else spec["score_width"]
        score_left = inner.right() - (154 if two_leg else score_width)
        text_width = score_left - inner.left() - spec["crest"] - spec["gap"] - spec["gap"]
        score_font = QFont(self._name_font)
        score_font.setPixelSize(17)
        score_font.setWeight(QFont.DemiBold)
        if two_leg:
            header_font = QFont(self._name_font)
            header_font.setPixelSize(12)
            painter.setFont(header_font)
            painter.setPen(QColor(t.PRIMARY))
            complete = all(leg.get("completed") for leg in node["legs"]) and len(node["legs"]) == 2
            for index, title in enumerate(("首回合", "次回合", "总比分" if complete else "暂合计")):
                painter.drawText(QRect(score_left + index * 54, inner.top(), 46, 22), Qt.AlignCenter, title)
        for row in node.get("rows") or []:
            top = inner.top() + row["y"]
            decorated = (self._polished or self._bucket_graph) and not node.get("terminal")
            if decorated:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(t.SUCCESS_BG if row.get("bold") else t.CARD_ALT))
                painter.drawRoundedRect(QRect(score_left + (108 if two_leg else 0) - 3, top + 1, score_width + 7, row["h"] - 2), 4, 4)
            crest = crest_pixmap(row.get("crest") or "", spec["crest"])
            if not crest.isNull():
                painter.drawPixmap(QRect(inner.left(), top + (row["h"] - spec["crest"]) // 2, spec["crest"], spec["crest"]), crest)
            font = QFont(ui.bracket_name_font())
            font.setWeight(QFont.Bold if row.get("bold") else QFont.Normal)
            painter.setFont(font)
            painter.setPen(QColor(row.get("color") or (t.TEXT_STRONG if row.get("bold") else t.TEXT)))
            painter.drawText(
                QRect(inner.left() + spec["crest"] + spec["gap"] * (1 if self._polished or self._bucket_graph else 2), top, max(10, text_width), row["h"]),
                Qt.AlignLeft | Qt.AlignVCenter,
                row["text"],
            )
            painter.setFont(score_font)
            painter.setPen(QColor(row.get("color") or (t.PRIMARY if decorated and row.get("bold") else t.TEXT_STRONG)))
            values = [row.get("value", "")]
            if two_leg:
                values = [str((leg.get("scores") or {}).get(row.get("team_id"), "—")) for leg in node["legs"][:2]] + values
            for index, value in enumerate(values):
                painter.drawText(QRect(score_left + index * 54, top, score_width, row["h"]), (Qt.AlignCenter if self._polished or self._bucket_graph else Qt.AlignRight) | Qt.AlignVCenter, value)
        footer_y = inner.top() + max([row["y"] + row["h"] for row in node.get("rows") or []] + [0]) + 2
        if node.get("badge"):
            badge_font = QFont(self._name_font)
            badge_font.setPixelSize(11)
            painter.setFont(badge_font)
            painter.setPen(QColor(t.MUTED))
            painter.drawText(QRect(inner.left(), footer_y, inner.width(), 16), Qt.AlignLeft | Qt.AlignVCenter, node["badge"])
            footer_y += 18
        painter.setFont(ui.venue_font())
        painter.setPen(QColor(t.MUTED))
        for text in ui.node_venue_lines(node):
            height = ui.venue_text_height(text, inner.width())
            painter.drawText(QRect(inner.left(), footer_y, inner.width(), height), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text)
            footer_y += height

    # -- interaction ----------------------------------------------------
    def _inspect_node(self, node: dict, row: int = 0) -> None:
        teams = node.get("team_rows") or []
        team = teams[min(row, len(teams) - 1)] if teams else {}
        active_team = team.get("team_id") or ""
        if (self._active_node, self._active_team) != (node["id"], active_team):
            self._active_node, self._active_team = node["id"], active_team
            self._keyboard_row = row
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._polished:
            return super().mouseMoveEvent(event)
        pos = QPoint(int(event.position().x() / self._scale), int(event.position().y() / self._scale))
        for node in self._nodes:
            if QRect(node["x"], node["y"], node["w"], node["h"]).contains(pos):
                row = max(0, min(1, (pos.y() - node["y"] - ui.BRACKET_NODE_SPEC["pad_y"] - node.get("row_offset", 0)) // ui.BRACKET_NODE_SPEC["row_height"]))
                self._inspect_node(node, row)
                self.setCursor(Qt.PointingHandCursor)
                self.setToolTip(" · ".join(item["text"] for item in node.get("rows") or []))
                return
        header_hit = any(header.get("stage") and QRect(header["x"], header["y"], header["w"], header["h"]).contains(pos) for header in self._headers)
        self.setCursor(Qt.PointingHandCursor if header_hit else Qt.ArrowCursor)
        self.setToolTip("查看该轮对阵" if header_hit else "")
        if self._active_node or self._active_team:
            self._active_node = self._active_team = ""
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._polished:
            self._active_node = self._active_team = ""
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._polished and self._nodes:
            ids = self.node_ids()
            index = ids.index(self._active_node) if self._active_node in ids else 0
            if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                if event.key() in (Qt.Key_Left, Qt.Key_Right):
                    index = (index + (1 if event.key() == Qt.Key_Right else -1)) % len(ids)
                row = (1 if event.key() == Qt.Key_Down else 0) if event.key() in (Qt.Key_Up, Qt.Key_Down) else self._keyboard_row
                node = self._nodes[index]
                self._inspect_node(node, row)
                self.setAccessibleDescription(" · ".join(item["text"] for item in node.get("rows") or []) + "；回车查看比赛")
                parent = self.parentWidget()
                while parent is not None:
                    if isinstance(parent, QScrollArea):
                        rect = self.node_rect(node["id"])
                        parent.ensureVisible(rect.center().x(), rect.center().y(), rect.width() // 2 + 8, 0)
                        break
                    parent = parent.parentWidget()
                event.accept()
                return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                match_id = self._nodes[index].get("match_id")
                if match_id:
                    self.match_requested.emit(match_id)
                event.accept()
                return
            if event.key() == Qt.Key_Escape:
                self._active_node = self._active_team = ""
                self.update()
                event.accept()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        pos = QPoint(int(event.position().x() / self._scale), int(event.position().y() / self._scale))
        for header in self._headers:
            if header.get("stage") and QRect(header["x"], header["y"], header["w"], header["h"]).contains(pos):
                self.stage_requested.emit(header["stage"])
                return
        for node in self._nodes:
            rect = QRect(node["x"], node["y"], node["w"], node["h"])
            if not rect.contains(pos):
                continue
            if node.get("legs"):
                score_left = rect.right() - ui.BRACKET_NODE_SPEC["pad_x"] - 154
                for index, leg in enumerate(node["legs"][:2]):
                    zone = QRect(score_left + index * 54, rect.top(), 46, rect.height())
                    if zone.contains(pos) and leg.get("match_id"):
                        self.match_requested.emit(leg["match_id"])
                        return
            for team in node.get("team_rows") or []:
                zone = QRect(rect.left() + 6, rect.top() + team["y"] + 4, rect.width() - (180 if node.get("legs") else 60), team["h"] - 6)
                if zone.contains(pos) and team.get("team_id"):
                    self.team_requested.emit(team["team_id"])
                    return
            if node.get("match_id"):
                self.match_requested.emit(node["match_id"])
            return
        super().mousePressEvent(event)


class GraphPanel(QWidget):
    """One page-scroll graph with fit / zoom controls and horizontal panning."""

    def __init__(self, view: GraphView, *, height: int = 420) -> None:
        super().__init__()
        self.view = view
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(t.S6)
        bar = QHBoxLayout()
        bar.setSpacing(t.S6)
        self.hint = label("", "Meta")
        self.hint.setMinimumWidth(0)
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bar.addWidget(self.hint, 1)
        polished = view._polished or view._bucket_graph
        if polished:
            self.hint.setText(
                f'<span style="color:{t.SUCCESS}">→ 胜</span>　<span style="color:{t.NEUTRAL}">⇢ 负</span>' if view._bucket_graph else
                f'<span style="color:{t.SUCCESS}">━ 晋级</span>　<span style="color:{t.BRACKET_TRANSFER}">┄ 落位</span>'
            )
            if getattr(view, "_redraw", False):
                self.hint.setText("每轮重新抽签")
            elif getattr(view, "_knockout", False):
                self.hint.setText(f'<span style="color:{t.SUCCESS}">━ 晋级</span>')
        self.zoom_label = label("100%", "Meta")
        self.zoom_label.setFixedWidth(44)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        if polished:
            bar.addWidget(self.zoom_label)
        controls = (("适应宽度", self.fit), ("−", self.zoom_out), ("+", self.zoom_in)) if polished else (("适应宽度", self.fit), ("放大", self.zoom_in), ("缩小", self.zoom_out))
        for text, slot in controls:
            button = QPushButton(text)
            button.setObjectName("Ghost")
            if polished:
                button.setCursor(Qt.PointingHandCursor)
                button.setAccessibleName({"−": "缩小签表", "+": "放大签表"}.get(text, text))
                if text in ("−", "+"):
                    button.setFixedSize(36, 34)
            button.clicked.connect(lambda _checked=False, slot=slot: slot())
            bar.addWidget(button, 0)
        outer.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("GraphScroll")
        if polished:
            self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(view)
        self.scroll.setWidgetResizable(False)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content_height = view.minimumSize().height()
        self.scroll.setFixedHeight(max(320, content_height + 16) if view._bucket_graph or view._full_height else max(320, min(720, content_height + 16)))
        outer.addWidget(self.scroll)
        view.stage_requested.connect(lambda stage: self.hint.setText(f"已选择阶段：{stage}；可在轮次视图查看"))
        if not polished:
            self.hint.setText("")

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self.view._auto_fit or getattr(self.view, "_redraw", False):
            QTimer.singleShot(0, self.fit)
        # Keep 100% by default so names stay readable; the reader can pan or use
        # 「适应宽度」. Never shrink the font to fit five rounds into 1280.

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if (self.view._auto_fit or getattr(self.view, "_redraw", False)) and event.size().width() != event.oldSize().width():
            QTimer.singleShot(0, self.fit)

    def fit(self) -> None:
        self.view.fit_width(max(320, self.scroll.viewport().width() - 4))
        self.scroll.horizontalScrollBar().setValue(0)
        if self.view._polished or self.view._bucket_graph:
            self._sync_height()

    def _sync_height(self) -> None:
        content = self.view.minimumSize().height()
        self.scroll.setFixedHeight(max(320, content + 16) if self.view._bucket_graph or self.view._full_height else max(320, min(1400, content + 16)))
        self.zoom_label.setText(f"{self.view.scale():.0%}")

    def zoom_in(self) -> None:
        self.view.apply_scale(self.view.scale() + 0.12)
        self._sync_height()

    def zoom_out(self) -> None:
        self.view.apply_scale(self.view.scale() - 0.12)
        self._sync_height()


class KnockoutPanorama(GraphView):
    """All saved knockout rounds, with the same leg scores as the round cards."""

    def __init__(self, world: dict, model: dict) -> None:
        super().__init__()
        self.setObjectName("KnockoutFullBracket")
        self.setAccessibleName("完整淘汰赛签表")
        self._polished = self._full_height = self._auto_fit = True
        self._knockout = True
        self.model = model
        columns = [{**column, "nodes": list(column.get("nodes") or [])} for column in model.get("columns") or []]
        links = model.get("links") or []
        for index in range(len(columns) - 2, -1, -1):
            left = columns[index]["nodes"]
            ordered = []
            for target in columns[index + 1]["nodes"]:
                parent_ids = {edge["from"] for edge in links if edge["to"] == target["tie_id"]}
                ordered.extend(node for node in left if node["tie_id"] in parent_ids)
            columns[index]["nodes"] = ordered + [node for node in left if node not in ordered]
        all_nodes = [node for column in columns for node in column["nodes"]]
        width = ui.bracket_node_width(all_nodes)
        count = max([len(column["nodes"]) for column in columns] + [1])
        max_height = max([86 + (24 if node.get("two_leg") else 0) + ui.node_venue_height(node, width) for node in all_nodes] + [86])
        step, gap = max_height + 26, 48
        nodes, edges, headers, lookup = [], [], [], {}
        for index, column in enumerate(columns):
            x = 12 + index * (width + gap)
            headers.append({"x": x, "y": 4, "w": width, "h": 28, "label": column["label"], "stage": column["key"]})
            for slot, node in enumerate(column["nodes"]):
                two_leg = bool(node.get("two_leg"))
                offset = 24 if two_leg else 0
                height = 86 + offset + ui.node_venue_height(node, width)
                y = int(44 + (slot + .5) * step * count / len(column["nodes"]) - height / 2)
                teams = node.get("teams") or []
                item = {"id": node["tie_id"], "x": x, "y": y, "w": width, "h": height,
                        "match_id": (node.get("match_ids") or [None])[-1], "row_offset": offset,
                        "legs": node.get("legs") if two_leg else [], "venues": ui.node_venue_lines(node),
                        "team_rows": [{"team_id": team.get("team_id"), "y": offset + i * 26, "h": 26} for i, team in enumerate(teams[:2])],
                        "rows": [{"y": offset + i * 26, "h": 26, "text": team.get("name") or "待定",
                                  "team_id": team.get("team_id"), "crest": team.get("name") or "",
                                  "value": "—" if team.get("score") is None else str(team["score"]), "bold": bool(team.get("winner"))}
                                 for i, team in enumerate(teams[:2])],
                        "badge": ui.node_special_badge(node)}
                nodes.append(item)
                lookup[node["tie_id"]] = node
        for edge in links:
            if edge["from"] not in lookup or edge["to"] not in lookup:
                continue
            row_of = lambda tie_id: next((i for i, team in enumerate(lookup[tie_id].get("teams") or []) if team.get("team_id") == edge.get("team_id")), 0)
            edges.append({**edge, "style": "winner", "from_row": row_of(edge["from"]), "to_row": row_of(edge["to"])})
        self.set_graph(nodes, edges, headers, QSize(24 + len(columns) * width + max(0, len(columns) - 1) * gap, 44 + count * step))


class RedrawCupPanorama(GraphView):
    """All five Destiny rounds, including undrawn slots without invented paths."""

    def __init__(self, world: dict, model: dict) -> None:
        super().__init__()
        from football_simulator.ui.presentation.bracket import STAGE_LABELS, STAGE_SEQUENCE

        self.setObjectName("DestinyFullBracket")
        self.setAccessibleName("Destiny Cup 完整淘汰赛签表")
        self._polished = True
        self._full_height = True
        self._redraw = True
        existing = {column["key"]: column for column in model.get("columns") or []}
        columns = []
        for index, stage in enumerate(STAGE_SEQUENCE["confluence"]):
            actual = list((existing.get(stage) or {}).get("nodes") or [])
            for slot in range(len(actual), 16 >> index):
                actual.append({"tie_id": f"undrawn-{stage}-{slot}", "pending": True,
                               "teams": [{"name": "待抽签"}, {"name": "待抽签"}]})
            columns.append({"key": stage, "label": STAGE_LABELS[stage], "nodes": actual})
        links = model.get("links") or []
        # A later saved draw determines where its real predecessors are placed.
        # Future placeholders never imply fixed pairings in a redraw cup.
        for index in range(len(columns) - 2, -1, -1):
            left = columns[index]["nodes"]
            ordered = []
            for target in columns[index + 1]["nodes"]:
                parent_ids = {edge["from"] for edge in links if edge["to"] == target["tie_id"]}
                ordered.extend(node for node in left if node["tie_id"] in parent_ids)
            columns[index]["nodes"] = ordered + [node for node in left if node not in ordered]
        width = ui.bracket_node_width(node for column in columns for node in column["nodes"])
        height = max([86 + ui.node_venue_height(node, width) for col in columns for node in col["nodes"]] + [86])
        step, gap = height + 24, 48
        nodes, headers, edges = [], [], []
        lookup = {}
        for index, column in enumerate(columns):
            x = 12 + index * (width + gap)
            headers.append({"x": x, "y": 4, "w": width, "h": 28, "label": column["label"]})
            for slot, node in enumerate(column["nodes"]):
                y = int(44 + (slot + .5) * step * (2 ** index) - height / 2)
                teams = node.get("teams") or []
                item = {
                    "id": node["tie_id"], "x": x, "y": y, "w": width, "h": height,
                    "match_id": (node.get("match_ids") or [None])[0],
                    "pending": bool(node.get("pending")),
                    "team_rows": [{"team_id": team.get("team_id"), "y": i * 26, "h": 26} for i, team in enumerate(teams[:2])],
                    "rows": [{"y": i * 26, "h": 26, "text": team.get("name") or "待抽签",
                              "crest": team.get("name") if team.get("team_id") else "",
                              "value": "—" if team.get("score") is None else str(team["score"]),
                              "bold": bool(team.get("winner")),
                              "color": t.SOFT if not team.get("team_id") else None}
                             for i, team in enumerate(teams[:2])],
                    "badge": ui.node_special_badge(node), "venues": ui.node_venue_lines(node),
                }
                nodes.append(item)
                lookup[node["tie_id"]] = (item, node)
        for edge in links:
            if edge["from"] not in lookup or edge["to"] not in lookup:
                continue
            source, source_model = lookup[edge["from"]]
            target, target_model = lookup[edge["to"]]
            team_id = edge["team_id"]
            row_of = lambda node: next((i for i, team in enumerate(node.get("teams") or []) if team.get("team_id") == team_id), 0)
            edges.append({**edge, "style": "winner", "from_row": row_of(source_model), "to_row": row_of(target_model)})
        self.set_graph(nodes, edges, headers, QSize(24 + 5 * width + 4 * gap, 44 + 16 * step))


class SwissBoard(GraphView):
    """Rounds 1–5 from left to right, each split into pre-round record pools."""

    NODE_W = 272
    GAP_X = 60
    GAP_Y = 10

    def __init__(self, world, model: dict) -> None:
        super().__init__()
        self._bucket_graph = True
        self.setAccessibleName("瑞士轮战绩分区图")
        spec = ui.BRACKET_NODE_SPEC
        samples = [{"teams": [{"team_id": pairing[side + "_id"], "name": pairing[side]} for side in ("home", "away")]} for rnd in model["rounds"] for bucket in rnd["buckets"] for pairing in bucket["nodes"]]
        card_width = max(self.NODE_W, ui.bracket_node_width(samples))
        block_width = card_width + 20
        nodes, edges, headers = [], [], []
        pools = {}
        for column, rnd in enumerate(model["rounds"]):
            x, y = column * (block_width + self.GAP_X), 44
            headers.append({"x": x, "y": 6, "w": block_width, "h": 26,
                            "label": f"第 {rnd['round']} 轮 · 第 {rnd['week']} 周",
                            "hint": f"{rnd['pairings']} 场", "stage": rnd["stage"]})
            # Display order is a property of the record, never of match IDs.
            for bucket in sorted(rnd["buckets"], key=lambda item: (-item["record"][0], item["record"][1])):
                wins, losses = bucket["record"]
                block_id = f"pool-{rnd['round']}-{wins}-{losses}"
                top = y
                y += 34
                for pairing in bucket["nodes"]:
                    height = 2 * spec["row_height"] + 2 * spec["pad_y"] + (18 if pairing.get("pk_text") else 0) + ui.node_venue_height(pairing, card_width)
                    nodes.append({
                        "id": pairing["match_id"], "x": x + 10, "y": y,
                        "w": card_width, "h": height, "match_id": pairing["match_id"],
                        "team_rows": [{"team_id": pairing[side + "_id"], "y": i * spec["row_height"], "h": spec["row_height"]} for i, side in enumerate(("home", "away"))],
                        "rows": [{"y": i * spec["row_height"], "h": spec["row_height"], "text": pairing[side], "crest": pairing[side],
                                  "value": str(pairing[side + "_goals"]) if pairing["completed"] else "—",
                                  "bold": pairing.get("winner_id") == pairing[side + "_id"]} for i, side in enumerate(("home", "away"))],
                        "badge": pairing.get("pk_text") or "", "pool_id": block_id, "venues": ui.node_venue_lines(pairing),
                    })
                    y += height + self.GAP_Y
                block = {"id": block_id, "x": x, "y": top, "w": block_width, "h": y - top,
                         "round": rnd["round"], "record": (wins, losses),
                         "label": f"{wins}–{losses} · {len(bucket['nodes'])} 场",
                         "color": t.PRIMARY if wins > losses else t.TEXT_STRONG,
                         "bg": t.CARD_ALT}
                self._pool_blocks.append(block)
                pools[(rnd["round"], wins, losses)] = block
                y += 24
        # A single arrow per possible record transition, independent of which
        # clubs won a particular match. Unknown future pools are not invented.
        links = []
        for (rnd, wins, losses), block in pools.items():
            for outcome, target_key in (("winner", (rnd + 1, wins + 1, losses)), ("loser", (rnd + 1, wins, losses + 1))):
                target = pools.get(target_key)
                if target:
                    links.append((block, target, outcome))
        for source, target, outcome in links:
            incoming = sorted([link for link in links if link[1]["id"] == target["id"]], key=lambda link: link[0]["y"])
            slot = next(i for i, link in enumerate(incoming) if link[0]["id"] == source["id"])
            start = (source["x"] + source["w"] - 1, source["y"] + int(source["h"] * (0.35 if outcome == "winner" else 0.65)))
            end = (target["x"], target["y"] + int(target["h"] * (slot + 1) / (len(incoming) + 1)))
            mid = (start[0] + end[0]) // 2 + (-8 if outcome == "winner" else 8)
            edges.append({"from": source["id"], "to": target["id"], "style": outcome, "arrow": True,
                          "polyline": [start, (mid, start[1]), (mid, end[1]), end]})
        main_width = max(640, len(model["rounds"]) * (block_width + self.GAP_X) - self.GAP_X)
        diagram_bottom = max([block["y"] + block["h"] for block in self._pool_blocks] + [320])
        summary_top = diagram_bottom + 36
        panel_width = (main_width - 24) // 2
        bottom = summary_top
        for index, (kind, rows, title, color, bg) in enumerate((
            ("qualified", model["qualified"], "晋级八强", t.SUCCESS, t.SUCCESS_BG),
            ("eliminated", model["eliminated"], "已淘汰", t.DANGER, t.DANGER_BG),
        )):
            x = index * (panel_width + 24)
            columns = 2 if panel_width >= card_width * 2 + 36 else 1
            terminal_width = (panel_width - (columns + 1) * 12) // columns
            for i, row in enumerate(rows):
                nodes.append({
                    "id": f"{kind}-{row['team_id']}", "x": x + 12 + (i % columns) * (terminal_width + 12),
                    "y": summary_top + 36 + (i // columns) * 54, "w": terminal_width, "h": 46,
                    "match_id": None, "terminal": True,
                    "team_rows": [{"team_id": row["team_id"], "y": 4, "h": 26}],
                    "rows": [{"y": 0, "h": 26, "text": row["name"], "crest": row["name"], "value": row["record"], "bold": True, "color": color}],
                    "border": color, "bg": t.CARD,
                })
            height = 42 + max(1, (len(rows) + columns - 1) // columns) * 54
            self._outcome_panels.append({"id": kind, "x": x, "y": summary_top, "w": panel_width, "h": height,
                                         "label": f"{title} · {len(rows)} 队", "color": color, "bg": bg,
                                         "empty": "暂无球队" if not rows else ""})
            bottom = max(bottom, summary_top + height)
        self.set_graph(nodes, edges, headers, QSize(main_width + 2, bottom + 18))


class DoubleElimPanorama(GraphView):
    """WB on top, LB below, GF right; nodes share the round-view card geometry.

    The layout layer owns the routed polyline for every double-elimination edge.
    Same-column, cross-group and multi-column edges leave the card through the
    column gap, travel in the WB/LB band gap, and enter the real target slot from
    the target column's outside channel.  GraphView only scales and draws these
    points; the Swiss layout supplies its own record-pool routes.
    """

    GAP_X = 46
    GAP_Y = 14
    MARGIN_LEFT = 44
    CHANNEL_MIN = 8
    CHANNEL_STEP = 5

    def __init__(self, world, model: dict) -> None:
        super().__init__()
        self._polished = macos.enabled()
        self._full_height = macos.enabled()
        self.setAccessibleName("双败全景签表")
        groups = {group["key"]: group for group in model.get("groups") or []}
        wb_rounds = (groups.get("wb") or {}).get("rounds") or []
        lb_rounds = (groups.get("lb") or {}).get("rounds") or []
        gf_rounds = (groups.get("gf") or {}).get("rounds") or []
        all_model_nodes = [
            node
            for group in (wb_rounds, lb_rounds, gf_rounds)
            for round_data in group
            for node in round_data.get("nodes") or []
        ]
        width = ui.bracket_node_width(all_model_nodes)
        nodes: List[dict] = []
        edges: List[dict] = []
        headers: List[dict] = []
        positions: Dict[str, QRect] = {}
        node_of: Dict[str, dict] = {}
        group_of: Dict[str, str] = {}
        column_of: Dict[str, int] = {}

        def node_height(node: dict) -> int:
            return 2 * ui.BRACKET_NODE_SPEC["row_height"] + 2 * ui.BRACKET_NODE_SPEC["pad_y"] + (18 if ui.node_special_badge(node) else 0) + ui.node_venue_height(node, width)

        def place(rounds, start_column: int, top: int, group_key: str) -> int:
            column = start_column
            bottom = top
            for round_data in rounds:
                x = self.MARGIN_LEFT + column * (width + self.GAP_X)
                headers.append({
                    "x": x, "y": top - 32, "w": width, "h": 24,
                    "label": round_data.get("label", ""), "hint": f"{len(round_data.get('nodes') or [])} 场",
                    "stage": round_data.get("key"),
                })
                previous_bottom = top - self.GAP_Y
                for node in round_data.get("nodes") or []:
                    incoming = [
                        positions[edge["source_tie_id"]]
                        for edge in model.get("edges") or []
                        if edge.get("target_tie_id") == node["tie_id"] and edge.get("source_tie_id") in positions
                    ]
                    height = node_height(node)
                    if incoming:
                        centre = sum(rect.center().y() for rect in incoming) / len(incoming)
                        y = int(centre - height / 2)
                    else:
                        y = previous_bottom + self.GAP_Y
                    y = max(top, y, previous_bottom + self.GAP_Y)
                    rect = QRect(x, y, width, height)
                    positions[node["tie_id"]] = rect
                    node_of[node["tie_id"]] = node
                    group_of[node["tie_id"]] = group_key
                    column_of[node["tie_id"]] = column
                    previous_bottom = y + height
                    bottom = max(bottom, y + height)
                    teams = node.get("teams") or []
                    nodes.append({
                        "id": node["tie_id"], "x": x, "y": y, "w": width, "h": height,
                        "match_id": (node.get("match_ids") or [None])[0],
                        "team_rows": [{"team_id": team.get("team_id"), "y": index * ui.BRACKET_NODE_SPEC["row_height"], "h": ui.BRACKET_NODE_SPEC["row_height"]} for index, team in enumerate(teams[:2])],
                        "rows": [
                            {
                                "y": index * ui.BRACKET_NODE_SPEC["row_height"], "h": ui.BRACKET_NODE_SPEC["row_height"],
                                "text": team.get("name") or "待定",
                                "crest": team.get("name") or "",
                                "value": "—" if team.get("score") is None else str(team.get("score")),
                                "bold": bool(team.get("winner")),
                            }
                            for index, team in enumerate(teams[:2])
                        ],
                        "badge": ui.node_special_badge(node), "venues": ui.node_venue_lines(node),
                    })
                column += 1
            return bottom - top

        wb_height = place(wb_rounds, 0, 40, "wb")
        lb_top = 40 + wb_height + 76
        lb_height = place(lb_rounds, 0, lb_top, "lb")
        gf_column = max(len(wb_rounds), len(lb_rounds))
        gf_top = lb_top
        place(gf_rounds, gf_column, gf_top, "gf")
        if self._polished:
            for group_key, color in (("wb", t.BRACKET_WINNER_BAND), ("lb", t.BRACKET_LOWER_BAND), ("gf", t.BRACKET_FINAL_BAND)):
                group_rects = [rect for tie_id, rect in positions.items() if group_of.get(tie_id) == group_key]
                if group_rects:
                    left = min(rect.left() for rect in group_rects) - 12
                    top = min(rect.top() for rect in group_rects) - 36
                    right = max(rect.right() for rect in group_rects) + 12
                    bottom = max(rect.bottom() for rect in group_rects) + 12
                    self._bands.append({"rect": (left, top, right - left + 1, bottom - top + 1), "color": color})

        wb_rects = [rect for tie_id, rect in positions.items() if group_of.get(tie_id) == "wb"]
        lb_rects = [rect for tie_id, rect in positions.items() if group_of.get(tie_id) == "lb"]
        # A horizontal transfer lane above the loser-bracket headers (and at
        # least 6 px clear of their title text).
        if lb_rects:
            band_lane = min(rect.top() for rect in lb_rects) - 38
        elif wb_rects:
            band_lane = max(rect.bottom() for rect in wb_rects) + 38
        else:
            band_lane = 40

        def channel(index: int) -> int:
            return self.CHANNEL_MIN + (index % 4) * self.CHANNEL_STEP

        def route(index: int, source_rect: QRect, target_rect: QRect, source_id: str, target_id: str, start, end):
            sc, tc = column_of[source_id], column_of[target_id]
            same_group = group_of[source_id] == group_of[target_id]
            if same_group and tc == sc + 1:
                # Ordinary forward edge: turn in the empty inter-column gap.
                mid = (source_rect.right() + target_rect.left()) // 2 + ((index % 5) - 2) * 4
                return [(start.x(), start.y()), (mid, start.y()), (mid, end.y()), (end.x(), end.y())]
            # Same column, backward, multi-column and cross-group edges use the
            # outer channels and the WB/LB band lane, then enter at the true slot.
            x1 = source_rect.right() + channel(index)
            x2 = target_rect.left() - channel(index + 1)
            lane = band_lane - (index % 5) * 6
            return [
                (start.x(), start.y()),
                (x1, start.y()),
                (x1, lane),
                (x2, lane),
                (x2, end.y()),
                (end.x(), end.y()),
            ]

        spec = ui.BRACKET_NODE_SPEC
        for index, edge in enumerate(model.get("edges") or []):
            source_id = edge.get("source_tie_id")
            target_id = edge.get("target_tie_id")
            if source_id not in positions or target_id not in positions:
                continue
            source_node = node_of[source_id]
            team_id = edge.get("team_id")
            source_team_ids = [team.get("team_id") for team in source_node.get("teams") or []]
            from_row = source_team_ids.index(team_id) if team_id in source_team_ids else 0
            to_row = int(edge.get("target_slot") or 0)
            source_rect = positions[source_id]
            target_rect = positions[target_id]
            row_h = spec["row_height"]
            start = QPoint(source_rect.right(), source_rect.top() + spec["pad_y"] + from_row * row_h + row_h // 2)
            end = QPoint(target_rect.left(), target_rect.top() + spec["pad_y"] + to_row * row_h + row_h // 2)
            edges.append({
                "from": source_id, "to": target_id,
                "team_id": team_id,
                "style": "winner" if edge.get("outcome") == "winner" else "loser",
                "from_row": from_row, "to_row": to_row,
                "polyline": route(index, source_rect, target_rect, source_id, target_id, start, end),
            })
        width_total = self.MARGIN_LEFT + gf_column * (width + self.GAP_X) + width + (16 if self._polished else 0)
        height = max([rect.bottom() for rect in positions.values()] + [lb_top + lb_height, gf_top + 120]) + 34
        self.set_graph(nodes, edges, headers, QSize(width_total, height))


class DualLeaderboard(QWidget):
    """Goals and assists top-10 side by side; one table per board."""

    def __init__(self, world, leaders: dict, *, competition: str = "") -> None:
        super().__init__()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(t.S16)
        self.tables: Dict[str, DataTable] = {}
        for key, title, value_label, empty_text in (
            ("goals", "进球榜 Top 10", "进球", "暂无进球"),
            ("assists", "助攻榜 Top 10", "助攻", "暂无助攻"),
        ):
            frame = ui.card()
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(t.S18, t.S14, t.S18, t.S14)
            layout.setSpacing(t.S8)
            layout.addWidget(ui.SectionTitle(title, f"共 {leaders.get(key + '_total', 0)} 人"))
            table = DataTable()
            table.set_columns([
                {"key": "rank", "label": "#", "width": 34, "numeric": True},
                {"key": "player", "label": "球员", "stretch": True, "kind": "link"},
                {"key": "team_label", "label": "当前球队", "stretch": True, "kind": "link", "icon_key": "club", "icon_size": 22},
                {"key": key, "label": value_label, "width": 52, "numeric": True},
            ])
            rows = leaders.get(key) or []
            table.set_rows(rows)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setFixedHeight(38 + 38 * max(1, len(rows)) + 6)
            table.cell_activated.connect(
                lambda row, column, key=key: self._activated(row, column)
            )
            layout.addWidget(table)
            if not rows:
                layout.addWidget(label(empty_text, "Meta"))
            layout.addStretch(1)
            self.tables[key] = table
            outer.addWidget(frame, 1)
        for table in self.tables.values():
            table.setObjectName("LeaderTable")

    def _activated(self, row: dict, column: str) -> None:
        window = self.window()
        if hasattr(window, "open_route"):
            if column == "player":
                window.open_route("player", player_id=row["player_id"])
            elif column == "team_label" and row.get("has_team"):
                window.open_route("team", team_id=row["team_id"])
