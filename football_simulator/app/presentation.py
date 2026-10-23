"""集中名称/身份展示：ID 不变，只决定 primary/secondary 文本。"""
from __future__ import annotations
from typing import Optional
from football_simulator.game import rules


def team_display(world: dict, team_id: Optional[str], context: str = "list") -> dict:
    team = (world.get("teams") or {}).get(team_id or "")
    if not team:
        return {"primary": "TBD", "secondary": "", "accessible_text": "TBD"}
    primary = team["club_en"]
    secondary = team.get("name_zh") or ""
    return {
        "primary": primary,
        "secondary": secondary if context in ("detail", "honors") else "",
        "accessible_text": f"{primary} {secondary}".strip(),
    }


def competition_display(competition_id: str, context: str = "list") -> dict:
    primary = rules.display_name(competition_id, "en")
    secondary = rules.display_name(competition_id, "zh")
    return {
        "primary": primary,
        "secondary": secondary if context == "detail" else "",
        "accessible_text": f"{primary} {secondary}".strip(),
    }


def stadium_display(stadium_id: Optional[str], include_club: bool = False) -> dict:
    item = rules.stadium_by_id(stadium_id) if stadium_id else None
    if not item:
        return {"primary": "TBD", "secondary": "", "accessible_text": "TBD"}
    primary = item["name_en"]
    secondary = item["name_zh"]
    suffix = f" ({item['club_en']})" if include_club else ""
    return {
        "primary": primary + suffix,
        "secondary": secondary,
        "accessible_text": f"{primary} {secondary}{suffix}".strip(),
    }
