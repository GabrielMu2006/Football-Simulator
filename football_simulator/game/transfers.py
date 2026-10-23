"""Transfer packages, roster capacity and one move per player/window.

Generation and approval share the same checks. Only real players are exchanged;
vacant formation slots retain/recover their own stable default-player identity.
"""

from __future__ import annotations

import math
from itertools import combinations

from football_simulator.game.config import DEFAULT_ABILITY
from football_simulator.game.models import FORMATION_RULES, default_player_id

MAX_VALUE_GAP = 20.0
ALLOWED_PACKAGE_SIZES = {(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (1, 3), (3, 2), (2, 3)}


class TradeError(ValueError):
    pass


def window_key(row: dict) -> str:
    name = row.get("window")
    if name in ("winter", "冬窗"):
        return "winter"
    if name in ("summer", "夏窗"):
        return "summer"
    if not name:
        week = int(row.get("week", row.get("week_number", 0)) or 0)
        return "winter" if 24 <= week <= 27 else "summer" if 50 <= week <= 52 else ""
    return ""


def moved_in_window(world: dict, window: str) -> set:
    season = world["season"]
    number = int(season["number"])
    moved = set()
    for row in [*world.get("transfer_history", []), *season.get("transfers", [])]:
        if int(row.get("season", row.get("season_number", number))) != number:
            continue
        if row.get("status") not in (None, "approved", "已批准") or window_key(row) != window:
            continue
        moved.update(row.get("players_a") or [])
        moved.update(row.get("players_b") or [])
    return moved


def real_rosters(world: dict) -> dict:
    return {
        team_id: [entry["player_id"] for entry in team["roster"] if world["players"][entry["player_id"]]["is_real"]]
        for team_id, team in world["teams"].items()
    }


def validate_trade(world: dict, item: dict, player_value, *, rosters=None, used=None) -> tuple:
    a, b = item.get("team_a"), item.get("team_b")
    if a == b or a not in world["teams"] or b not in world["teams"]:
        raise TradeError("交易必须发生在两支不同的现有球队之间。")
    outgoing_a, outgoing_b = list(item.get("players_a") or []), list(item.get("players_b") or [])
    if (len(outgoing_a), len(outgoing_b)) not in ALLOWED_PACKAGE_SIZES:
        raise TradeError("支持1换1、1换2、2换2、3换1、3换2及反向组合。")
    all_ids = outgoing_a + outgoing_b
    if len(set(all_ids)) != len(all_ids):
        raise TradeError("同一名球员不能在交易中重复出现。")
    window = window_key(item)
    if not window:
        raise TradeError("交易缺少有效的转会窗口。")
    week = int(world["season"]["week"])
    current_window = "winter" if 24 <= week <= 28 else "summer" if 50 <= week <= 52 else ""
    proposal_week = int(item.get("week", week))
    if window != current_window or window_key({"week": proposal_week}) != window:
        raise TradeError("交易不属于当前转会窗口。")
    blocked = moved_in_window(world, window) if used is None else used
    if set(all_ids) & blocked:
        raise TradeError("有球员已在本转会窗完成转会，不能再次转会；请取消该提案。")
    live = real_rosters(world) if rosters is None else rosters
    for team_id, ids in ((a, outgoing_a), (b, outgoing_b)):
        for pid in ids:
            data = world["players"].get(pid) or {}
            if not data.get("is_real") or pid not in live[team_id]:
                raise TradeError("交易球员已不在原球队，或不是可交换的真实球员。")
            if rosters is None and data.get("team_id") != team_id:
                raise TradeError("球员所属球队与阵容记录不一致，不能完成交易。")
    values = [round(sum(player_value(world, pid) for pid in ids), 2) for ids in (outgoing_a, outgoing_b)]
    if not all(math.isfinite(value) and value >= 0 for value in values):
        raise TradeError("交易身价无效。")
    if round(abs(values[0] - values[1]), 2) > MAX_VALUE_GAP:
        raise TradeError("双方交易总身价差超过20，不能完成交易；请取消该提案。")
    for team_id, outgoing, incoming in ((a, outgoing_a, outgoing_b), (b, outgoing_b, outgoing_a)):
        after = [pid for pid in live[team_id] if pid not in outgoing] + incoming
        counts = {position: 0 for position in FORMATION_RULES}
        for pid in after:
            position = world["players"][pid]["position"]
            if position not in counts:
                raise TradeError("交易球员位置无效。")
            counts[position] += 1
        if any(counts[position] > limit for position, limit in FORMATION_RULES.items()):
            raise TradeError("交易后的位置人数超出限制（GK 1、DF 4、MF 3、FW 3）；请取消该提案。")
    return values[0], values[1]


def _restore_default(world: dict, team_id: str, entry: dict) -> str:
    pid = default_player_id(team_id, entry["position"], entry["slot"])
    if pid not in world["players"]:
        world["players"][pid] = {
            "player_id": pid, "name": None, "position": entry["position"],
            "ability": DEFAULT_ABILITY, "is_real": False,
            "slot_number": entry["slot"], "initial_market_value": None, "market_value": None,
        }
    world["players"][pid].update(team_id=team_id, retired_from_roster=False)
    return pid


def _install_roster(world: dict, team_id: str, outgoing: list, incoming: list) -> None:
    roster = world["teams"][team_id]["roster"]
    for entry in roster:
        if entry["player_id"] in outgoing:
            entry["player_id"] = None
    for pid in incoming:
        data = world["players"][pid]
        vacancies = [entry for entry in roster if entry["position"] == data["position"] and (
            entry["player_id"] is None or not world["players"][entry["player_id"]]["is_real"]
        )]
        vacancies.sort(key=lambda entry: entry["player_id"] is not None)
        if not vacancies:
            raise TradeError("目标球队没有对应位置的空缺。")
        entry = vacancies[0]
        old_id = entry["player_id"]
        if old_id:
            world["players"][old_id].update(team_id=None, retired_from_roster=True)
        entry["player_id"] = pid
        data.update(team_id=team_id, slot_number=entry["slot"])
    for entry in roster:
        if entry["player_id"] is None:
            entry["player_id"] = _restore_default(world, team_id, entry)


def apply_trade(world: dict, item: dict, player_value) -> None:
    validate_trade(world, item, player_value)
    _install_roster(world, item["team_a"], item["players_a"], item["players_b"])
    _install_roster(world, item["team_b"], item["players_b"], item["players_a"])


def generate_proposals(world: dict, rng, window: str, player_value) -> list:
    season = world["season"]
    target = rng.randint(1, 3) if window == "winter" else rng.randint(3, 5)
    live = real_rosters(world)
    used = moved_in_window(world, window)
    proposals = []
    offset = sum(int(row.get("week", 0)) == int(season["week"]) for row in season.get("transfers", []))
    for _attempt in range(target * 20):
        if len(proposals) >= target:
            break
        eligible = {team: [pid for pid in ids if pid not in used] for team, ids in live.items()}
        teams = [team for team, ids in eligible.items() if ids]
        if len(teams) < 2:
            break
        a, b = rng.sample(teams, 2)
        packages = []
        for team in (a, b):
            choices = [list(combo) for size in (1, 2, 3) for combo in combinations(eligible[team], size)]
            rng.shuffle(choices)
            packages.append(choices)
        chosen = None
        package_values = [{tuple(ids): round(sum(player_value(world, pid) for pid in ids), 2) for ids in choices} for choices in packages]
        for outgoing_a in packages[0]:
            for outgoing_b in packages[1]:
                if (len(outgoing_a), len(outgoing_b)) not in ALLOWED_PACKAGE_SIZES:
                    continue
                if round(abs(package_values[0][tuple(outgoing_a)] - package_values[1][tuple(outgoing_b)]), 2) > MAX_VALUE_GAP:
                    continue
                item = {"team_a": a, "team_b": b, "players_a": outgoing_a, "players_b": outgoing_b, "window": window}
                try:
                    value_a, value_b = validate_trade(world, item, player_value, rosters=live, used=used)
                except TradeError:
                    continue
                chosen = {**item, "value_a": value_a, "value_b": value_b}
                break
            if chosen:
                break
        if not chosen:
            continue
        chosen.update(
            trade_id=f"trade-{season['number']}-{int(season['week'])}-{offset + len(proposals) + 1}",
            value_gap=round(abs(chosen["value_a"] - chosen["value_b"]), 2),
            position=" ↔ ".join(" + ".join(world["players"][pid]["position"] for pid in chosen[key]) for key in ("players_a", "players_b")),
            week=int(season["week"]), status="pending", reason="身价接近 · 双方位置名额充足",
        )
        proposals.append(chosen)
        used.update(chosen["players_a"] + chosen["players_b"])
        live[a] = [pid for pid in live[a] if pid not in chosen["players_a"]] + chosen["players_b"]
        live[b] = [pid for pid in live[b] if pid not in chosen["players_b"]] + chosen["players_a"]
    return proposals
