from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from football_simulator.ui_v2.widgets import CardFrame


class PlaceholderPage(QWidget):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = CardFrame(title, description)
        hint = QLabel("这一页已经在 UI v2 骨架里预留好了，后续可以继续接具体功能。")
        hint.setWordWrap(True)
        card.body_layout.addWidget(hint)
        card.body_layout.addStretch(1)
        layout.addWidget(card)
