"""页面基类与共享上下文。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget
from football_simulator.ui import macos_presentation as macos


class BasePage(QWidget):
    route_name = ""

    def __init__(self, app) -> None:
        super().__init__()
        self.setObjectName("PageRoot")
        self.app = app
        self.params: dict = {}
        self.snapshot: Optional[dict] = None
        self.player_scope = macos.default_player_scope()
        self._detail_states: dict = {}

    def detail_state(self, entity_id: str) -> dict:
        return self._detail_states.setdefault(str(entity_id), {"section": "overview"})

    def set_detail_section(self, entity_id: str, section: str) -> None:
        self.detail_state(entity_id)["section"] = section
        self.app._refresh_current_page(restore_scroll=False)
        self.scroll.verticalScrollBar().setValue(0)

    def set_player_scope(self, scope: str) -> None:
        self.player_scope = scope
        self.app._refresh_current_page(restore_scroll=True)

    @property
    def save_name(self) -> Optional[str]:
        return self.app.current_save_name()

    def open_route(self, name: str, **params) -> None:
        self.app.navigate(name, **params)

    def refresh(self, snapshot: dict, params: dict) -> None:  # pragma: no cover - interface
        raise NotImplementedError
