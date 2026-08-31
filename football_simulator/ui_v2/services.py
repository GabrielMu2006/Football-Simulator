from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from football_simulator.runtime import (
    backup_save,
    create_save_directory,
    delete_save_directory,
    empty_trash,
    export_save,
    import_save_database,
    list_backups,
    list_save_names,
    list_trash_saves,
    load_current_save_name,
    move_save_to_trash,
    normalize_save_name,
    restore_trash_save,
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

    def initialize(self, save_name: str, force: bool = False) -> UIState:
        # 数据安全（用户确认 #2）：赛季进行中重新初始化会丢弃进度，
        # 非强制时拒绝执行（UI 必须先弹强确认再传 force=True）。
        current = self.preview_snapshot(save_name)
        if current is not None and not current.season_complete and not force:
            raise ValueError(
                f"当前存档第 {current.season_number} 赛季尚未结束"
                f"（已进行到第 {current.current_week} 周）。"
                "重新初始化将放弃该赛季（不归档）。如需强制重置，请确认后重试。"
            )
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
        # 改为移入回收站；彻底清空通过 empty_trash。
        move_save_to_trash(save_name)

    def backup_save(self, save_name: str) -> str:
        return str(backup_save(save_name))

    def list_backups(self, save_name: str) -> list[str]:
        return [str(p) for p in list_backups(save_name)]

    def export_save(self, save_name: str, dest_path: str) -> str:
        from pathlib import Path
        return str(export_save(save_name, Path(dest_path)))

    def import_save(self, save_name: str, src_path: str) -> str:
        from pathlib import Path
        return str(import_save_database(save_name, Path(src_path)))

    def list_trash(self) -> list[str]:
        return [str(p) for p in list_trash_saves()]

    def restore_trash(self, trash_path: str, save_name: str) -> str:
        from pathlib import Path
        return str(restore_trash_save(Path(trash_path), save_name))

    def empty_trash(self) -> None:
        empty_trash()

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
