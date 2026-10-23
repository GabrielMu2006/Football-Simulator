"""应用服务：存档生命周期、允许动作、观察模式与待办命令。

UI 只调用本服务，不直接读 SQL、不判断另一套流程。所有写命令在这里完成
事务边界；读取快照走只读查询（world 由持久化层只读加载）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Callable, Dict, List, Optional

from football_simulator import runtime
from football_simulator.game import rules, world as game_world
from football_simulator.persistence_v2 import store

MODES = ("auto", "key_pause", "manual")
MODE_LABELS = {
    "auto": "自动处理",
    "key_pause": "关键事项暂停",
    "manual": "全部手动",
}


class ServiceError(ValueError):
    pass


class GameService:
    def __init__(self, rng_factory: Optional[Callable[[], random.Random]] = None, *, honor_profile: Optional[str] = None, edition_archives: bool = False) -> None:
        self._rng_factory = rng_factory or random.SystemRandom
        self.honor_profile = honor_profile
        self.edition_archives = edition_archives

    def prepare_save(self, save_name: str) -> Optional[dict]:
        """Explicit macOS open command; ordinary world reads remain read-only."""
        if not self.honor_profile and not self.edition_archives:
            return None
        if self.honor_profile and self.honor_profile != rules.HONOR_PROFILE:
            raise ServiceError("不支持的荣誉分规则。")
        current = store.load_world(save_name, readonly=True)
        honor_needed = bool(self.honor_profile and current.get("honor_scoring_profile") != self.honor_profile)
        history_needed = self.edition_archives and current.get("edition_archive_version") != 1
        if not honor_needed and not history_needed:
            return None
        from football_simulator.game import honor_scoring
        upgraded = honor_scoring.reweight(current) if honor_needed else current
        if history_needed:
            from football_simulator.app import edition_history
            upgraded = edition_history.upgrade(upgraded, save_name)
        from uuid import uuid4
        profile = "edition-history-v1" if history_needed else rules.HONOR_PROFILE
        backup = runtime.backups_root() / runtime.normalize_save_name(save_name) / f"before-{profile}-v{current['version']}-{uuid4().hex[:8]}.fsarchive"
        store.export_package(save_name, backup)
        store.write_world(save_name, upgraded)
        return {"backup": str(backup), "profile": self.honor_profile, "edition_archive_version": upgraded.get("edition_archive_version")}

    # -- 存档管理 --------------------------------------------------------

    def list_saves(self) -> List[dict]:
        current = None
        try:
            current = self.current_save_name()
        except Exception:
            current = None
        result = []
        for name in runtime.list_save_names():
            path = store.database_path(name)
            initialized = path.exists()
            compatible = initialized
            season = None
            week = None
            if initialized:
                try:
                    world = store.load_world(name, readonly=True)
                    season = world["season_number"]
                    week = world["season"]["week"]
                except Exception:
                    compatible = False
            result.append(
                {
                    "save_name": name,
                    "initialized": initialized and compatible,
                    "has_database": initialized,
                    "compatible": compatible,
                    "season": season,
                    "week": week,
                    "is_current": name == current,
                }
            )
        return result

    def current_save_name(self) -> Optional[str]:
        path = runtime.current_save_path()
        if path.exists():
            try:
                name = path.read_text(encoding="utf-8").strip()
            except OSError:
                name = ""
            if name and runtime.save_exists(name):
                return name
        names = runtime.list_save_names()
        return names[0] if names else None

    def set_current_save(self, save_name: str) -> None:
        save_name = runtime.normalize_save_name(save_name)
        if not runtime.save_exists(save_name):
            raise ServiceError(f"存档 '{save_name}' 不存在。")
        runtime.store_current_save_name(save_name)

    def create_save(self, save_name: str) -> Path:
        try:
            save_name = runtime.normalize_save_name(save_name)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        return runtime.create_save_directory(save_name)

    def initialize_save(self, save_name: str) -> dict:
        save_name = runtime.normalize_save_name(save_name)
        if store.database_path(save_name).exists():
            raise ServiceError(f"存档 '{save_name}' 已经初始化。")
        world = game_world.create_new_world(save_name, rng=self._rng_factory())
        if self.honor_profile or self.edition_archives:
            self.prepare_save(save_name)
            world = store.load_world(save_name, readonly=True)
        runtime.store_current_save_name(save_name)
        return world

    def delete_save(self, save_name: str) -> Path:
        was_current = self.current_save_name() == save_name
        path = runtime.move_save_to_trash(save_name)
        if was_current:
            remaining = runtime.list_save_names()
            if remaining:
                runtime.store_current_save_name(remaining[0])
            else:
                try:
                    runtime.current_save_path().unlink(missing_ok=True)
                except OSError:
                    pass
        return path

    def list_trash(self) -> List[dict]:
        return [
            {"name": entry.name, "path": str(entry)}
            for entry in runtime.list_trash_saves()
            if entry.is_dir()
        ]

    def restore_trash(self, trash_path: Path, save_name: Optional[str] = None) -> Path:
        return runtime.restore_trash_save(Path(trash_path), save_name)

    def empty_trash(self) -> List[Path]:
        return runtime.empty_trash()

    def export_save(self, save_name: str, dest_path: Path) -> Path:
        return store.export_package(save_name, Path(dest_path))

    def import_save(self, package_path: Path, target_name: str) -> Path:
        path = store.import_package(Path(package_path), target_name)
        if self.honor_profile or self.edition_archives:
            self.prepare_save(target_name)
        runtime.store_current_save_name(runtime.normalize_save_name(target_name))
        return path

    def backup_save(self, save_name: str) -> Path:
        return runtime.backup_save(save_name)

    # -- 读取 ------------------------------------------------------------

    def load_world(self, save_name: str) -> dict:
        return store.load_world(save_name, readonly=True)

    def snapshot(self, save_name: str) -> dict:
        world = self.load_world(save_name)
        season = world["season"]
        pending = season["pending"]
        actions = self.allowed_actions(world)
        return {
            "save_name": save_name,
            "world_id": world["world_id"],
            "season": world["season_number"],
            "week": season["week"],
            "season_complete": bool(season.get("season_complete")),
            "finalized": bool(season.get("finalized")),
            "pending": {
                "ability_review": len(pending.get("ability_review") or []),
                "transfer_review": len(pending.get("transfer_review") or []),
                "draft": "awaiting_input" if pending.get("draft", {}).get("status") == "awaiting_input" else None,
            },
            "mode": self.get_mode(world),
            "watchlist": list(world.get("settings", {}).get("watchlist", [])),
            "primary_action": actions["primary"],
            "actions": actions,
        }

    def allowed_actions(self, world: dict) -> dict:
        season = world["season"]
        pending = season["pending"]
        actions = {
            "advance": False,
            "resolve_ability": False,
            "resolve_transfers": False,
            "run_draft": False,
            "start_next_season": False,
            "export": True,
        }
        reasons = {}
        if pending.get("ability_review"):
            actions["resolve_ability"] = True
            reasons["advance"] = "请先完成能力审核。"
        elif pending.get("draft", {}).get("status") == "awaiting_input":
            actions["run_draft"] = True
            reasons["advance"] = "请先完成选秀。"
        elif pending.get("transfer_review"):
            actions["resolve_transfers"] = True
            reasons["advance"] = "请先完成转会审核。"
        elif season.get("season_complete"):
            actions["start_next_season"] = True
            reasons["advance"] = "赛季已结束，请开始下一赛季。"
        else:
            actions["advance"] = True
        if actions["resolve_ability"]:
            primary = {"id": "resolve_ability", "label": "处理能力审核", "enabled": True, "reason": "季末能力变化等待确认"}
        elif actions["run_draft"]:
            primary = {"id": "run_draft", "label": "进行选秀", "enabled": True, "reason": "新秀候选等待选择"}
        elif actions["resolve_transfers"]:
            primary = {"id": "resolve_transfers", "label": "处理转会审核", "enabled": True, "reason": "转会提案等待批准"}
        elif actions["start_next_season"]:
            primary = {"id": "start_next_season", "label": "开始下一赛季", "enabled": True, "reason": "赛季结束且待办已清"}
        elif actions["advance"]:
            mode = self.get_mode(world)
            progress = int(season["week"])
            label = f"连续观察 · 第 {progress} 周起" if mode == "auto" else f"推进第 {progress} 周"
            primary = {"id": "advance", "label": label, "enabled": True, "reason": f"已完成第 {max(0, progress - 1)} 周；下一步第 {progress} 周"}
        else:
            primary = {"id": "none", "label": "暂不可推进", "enabled": False, "reason": reasons.get("advance", "")}
        return {"primary": primary, "actions": actions, "reasons": reasons}

    # -- 模式与关注 -------------------------------------------------------

    def get_mode(self, world: dict) -> str:
        return world.get("settings", {}).get("mode", game_world.DEFAULT_MODE)

    def set_mode(self, save_name: str, mode: str) -> dict:
        if mode not in MODES:
            raise ServiceError(f"未知观察模式：{mode}")
        world = store.load_world(save_name)
        world.setdefault("settings", {})["mode"] = mode
        world["season"]["mode"] = mode
        store.write_world(save_name, world)
        return world

    def set_watchlist(self, save_name: str, watchlist: List[str]) -> dict:
        world = store.load_world(save_name)
        world.setdefault("settings", {})["watchlist"] = list(dict.fromkeys(watchlist))
        store.write_world(save_name, world)
        return world

    def toggle_watch(self, save_name: str, entity_id: str) -> dict:
        world = store.load_world(save_name)
        watchlist = list(world.setdefault("settings", {}).get("watchlist", []))
        if entity_id in watchlist:
            watchlist.remove(entity_id)
        else:
            if entity_id not in world["teams"] and entity_id not in world["players"]:
                raise ServiceError(f"未知关注对象：{entity_id}")
            watchlist.append(entity_id)
        world["settings"]["watchlist"] = watchlist
        store.write_world(save_name, world)
        return world

    # -- 推进 ------------------------------------------------------------

    def advance_week(self, save_name: str) -> dict:
        world = store.load_world(save_name)
        mode = self.get_mode(world)
        before_champions = _champions(world)
        result = game_world.advance_week(save_name, rng=self._rng_factory())
        world = result["world"]
        auto_resolved: List[str] = []
        if mode == "auto":
            auto_resolved = self._auto_resolve_pending(save_name)
            world = store.load_world(save_name)
        after_champions = _champions(world)
        new_champions = [
            cup_id for cup_id, team_id in after_champions.items() if team_id and before_champions.get(cup_id) != team_id
        ]
        pause = None
        if world["season"]["pending"].get("ability_review"):
            pause = {"type": "decision", "message": "季末能力变化等待确认。"}
        elif world["season"]["pending"].get("transfer_review"):
            pause = {"type": "decision", "message": "有新的转会提案等待批准。"}
        elif world["season"]["pending"].get("draft", {}).get("status") == "awaiting_input":
            pause = {"type": "decision", "message": "选秀候选等待选择。"}
        elif world["season"].get("season_complete"):
            pause = {"type": "season_end", "message": "赛季结束，等待明确开始下一赛季。"}
        elif new_champions:
            labels = "、".join(rules.display_name(cup_id, "zh") for cup_id in new_champions)
            pause = {"type": "reveal", "message": f"冠军揭晓：{labels}。"}
        elif mode == "manual":
            pause = {"type": "week", "message": f"第 {result['week']} 周结果已产生。"}
        return {
            "week": result["week"],
            "matches": result["matches"],
            "auto_resolved": auto_resolved,
            "pause": pause,
            "world": world,
        }

    def _auto_resolve_pending(self, save_name: str) -> List[str]:
        resolved: List[str] = []
        for _ in range(10):
            world = store.load_world(save_name)
            pending = world["season"]["pending"]
            if pending.get("ability_review"):
                decisions = {item["player_id"]: True for item in pending["ability_review"]}
                self.apply_ability_review(save_name, decisions)
                resolved.append("ability_review")
            elif pending.get("draft", {}).get("status") == "awaiting_input":
                self.apply_draft(save_name, [])
                resolved.append("draft")
            elif pending.get("transfer_review"):
                decisions = {item["trade_id"]: True for item in pending["transfer_review"]}
                self.apply_transfer_reviews(save_name, decisions)
                resolved.append("transfer_review")
            else:
                break
        return resolved

    def run_weeks(self, save_name: str, max_weeks: int = 60, should_stop: Optional[Callable[[], bool]] = None) -> dict:
        """连续推进直到季末/待办/停止条件；每步仍是独立事务。"""
        weeks = []
        for _ in range(max_weeks):
            if should_stop is not None and should_stop():
                break
            snapshot = self.snapshot(save_name)
            if snapshot["season_complete"] or not snapshot["actions"]["actions"]["advance"]:
                break
            result = self.advance_week(save_name)
            weeks.append(result["week"])
            if result["pause"] is not None:
                break
        return {"weeks": weeks, "snapshot": self.snapshot(save_name)}

    # -- 待办命令 --------------------------------------------------------

    def apply_ability_review(self, save_name: str, decisions: Dict[str, bool]) -> dict:
        return game_world.apply_ability_review(save_name, decisions)

    def apply_transfer_reviews(self, save_name: str, decisions: Dict[str, bool]) -> dict:
        return game_world.apply_transfer_reviews(save_name, decisions)

    def apply_draft(self, save_name: str, selected_names: Optional[List[str]] = None) -> dict:
        return game_world.apply_draft(save_name, selected_names=selected_names)

    def start_next_season(self, save_name: str) -> dict:
        return game_world.start_next_season(save_name, rng=self._rng_factory())

    # -- 事件 ------------------------------------------------------------

    def story_events(self, save_name: str, season: Optional[int] = None, limit: int = 50) -> List[dict]:
        world = self.load_world(save_name)
        events = world.get("story_events", [])
        if season is not None:
            events = [event for event in events if int(event.get("season", 0)) == season]
        return events[-limit:][::-1]


def _champions(world: dict) -> Dict[str, Optional[str]]:
    return {
        cup_id: state.get("champion")
        for cup_id, state in world["season"].get("cups", {}).items()
    }
