"""GFE-130 / GFE-030: role-relevant metrics and display values.

Only existing season fields are used (appearances, goals, assists,
chances_created, successful_defenses, successful_saves, clean_sheets, rating,
market_value). No new denominators or estimation models.
"""

from __future__ import annotations

ROLE_NAMES = {"GK": "门将", "DF": "后卫", "MF": "中场", "FW": "前锋"}

ROLE_METRICS = {
    "GK": (("出场", "appearances", "int"), ("成功扑救", "successful_saves", "int"), ("零封", "clean_sheets", "int"), ("评分", "rating", "rating")),
    "DF": (("出场", "appearances", "int"), ("成功防守", "successful_defenses", "int"), ("零封", "clean_sheets", "int"), ("评分", "rating", "rating")),
    "MF": (("出场", "appearances", "int"), ("创造机会", "chances_created", "int"), ("助攻", "assists", "int"), ("评分", "rating", "rating")),
    "FW": (("出场", "appearances", "int"), ("进球", "goals", "int"), ("助攻", "assists", "int"), ("评分", "rating", "rating")),
}


def role_name(position: str) -> str:
    return ROLE_NAMES.get(position, position or "球员")


def _format(value, kind: str) -> str:
    if kind == "rating":
        return "—" if value is None else f"{float(value):.2f}"
    if value is None:
        return "—"
    return str(int(value))


def role_metrics(position: str, stats: dict, rating) -> list:
    entry = ROLE_METRICS.get(position) or ROLE_METRICS["MF"]
    rows = []
    for label, key, kind in entry:
        raw = rating if key == "rating" else (stats or {}).get(key)
        rows.append({"label": label, "value": _format(raw, kind), "key": key})
    return rows


def valuation_text(value) -> str:
    """Neutral scale: the model has no currency unit, so none is invented."""
    if value in (None, ""):
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def signed_gap(value) -> str:
    if value is None:
        return "—"
    number = int(value)
    return f"+{number}" if number > 0 else str(number)


METRIC_COLUMNS = (
    {"key": "appearances", "label": "出场", "width": 48, "numeric": True},
    {"key": "goals", "label": "进球", "width": 48, "numeric": True},
    {"key": "assists", "label": "助攻", "width": 48, "numeric": True},
    {"key": "rating", "label": "评分", "width": 58, "numeric": True, "decimals": 2},
    {"key": "market_value", "label": "估值", "width": 74, "numeric": True, "decimals": 2, "optional": True, "show_at": 1280},
)


def missing_note(position: str) -> str:
    return f"本赛季尚无{role_name(position)}出场记录。"
