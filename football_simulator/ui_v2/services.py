from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from football_simulator.runtime import (
    create_save_directory,
    delete_save_directory,
    list_save_names,
    load_current_save_name,
    normalize_save_name,
    save_exists,
    save_root,
    store_current_save_name,
)
from football_simulator.state import (
    SaveSnapshot,
    WeekSimulationResult,
    apply_ability_review_decisions,
    apply_draft_prospects,
    apply_transfer_review_decisions,
    initialize_save_state,
    load_last_draft_log,
    load_save_snapshot,
    simulate_next_week,
)
from football_simulator.data import ensure_save_config, load_team_chinese_names


@dataclass
class UIState:
    save_name: str
    snapshot: Optional[SaveSnapshot]


class SimulatorUIService:
    def current_save_name(self) -> str:
        return load_current_save_name()

    def available_saves(self) -> list[str]:
        return list_save_names()

    def save_directory(self) -> str:
        return str(save_root())

    def load_state(self, save_name: str) -> UIState:
        store_current_save_name(save_name)
        if not save_exists(save_name):
            return UIState(save_name=save_name, snapshot=None)
        try:
            snapshot = load_save_snapshot(save_name)
        except (FileNotFoundError, ValueError):
            snapshot = None
        return UIState(save_name=save_name, snapshot=snapshot)

    def preview_snapshot(self, save_name: str) -> Optional[SaveSnapshot]:
        if not save_exists(save_name):
            return None
        try:
            return load_save_snapshot(save_name)
        except (FileNotFoundError, ValueError):
            return None

    def initialize(self, save_name: str) -> UIState:
        snapshot = initialize_save_state(save_name)
        store_current_save_name(save_name)
        return UIState(save_name=save_name, snapshot=snapshot)

    def create_save(self, save_name: str) -> UIState:
        normalized = normalize_save_name(save_name)
        if not save_exists(normalized):
            create_save_directory(normalized)
            ensure_save_config(normalized)
        store_current_save_name(normalized)
        return UIState(save_name=normalized, snapshot=None)

    def delete_save(self, save_name: str) -> None:
        delete_save_directory(save_name)

    def simulate_week(self, save_name: str) -> WeekSimulationResult:
        return simulate_next_week(save_name)

    def apply_ability_review(self, save_name: str, decisions: dict[str, bool]) -> UIState:
        snapshot = apply_ability_review_decisions(save_name, decisions)
        return UIState(save_name=save_name, snapshot=snapshot)

    def apply_transfer_review(self, save_name: str, decisions: dict[str, bool]) -> UIState:
        snapshot = apply_transfer_review_decisions(save_name, decisions)
        return UIState(save_name=save_name, snapshot=snapshot)

    def apply_draft(self, save_name: str, prospects: list[dict]) -> UIState:
        snapshot = apply_draft_prospects(save_name, prospects)
        return UIState(save_name=save_name, snapshot=snapshot)

    def load_last_draft(self, save_name: str) -> dict:
        return load_last_draft_log(save_name)

    def team_chinese_names(self, save_name: str | None = None) -> dict[str, str]:
        return load_team_chinese_names(save_name)
