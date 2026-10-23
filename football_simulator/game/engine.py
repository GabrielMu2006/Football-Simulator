"""比赛引擎：保留现有 90 分钟公式，新增加时、点球与中立场。

正常 90 分钟沿用 ``football_simulator.match_engine`` 的压力/转化/扑救系数与
13 个事件窗口，不修改任何系数。新增部分全部是显式实现参数：

- 加时 2×15 分钟：沿用同场压力与节奏，事件窗口数 4 或 5（取 5 概率 1/3），
  分钟从 91–120 抽取；加时产出计入正式统计。
- 点球：双方每脚成功概率 0.75、独立随机、无主场/能力加成；先罚方独立抽签；
  五轮后突然死亡；点球不计进球、助攻、普通扑救，整场仍只记一次出场。
- 中立场：名义主客仍用于比分展示，但双方都没有主场加成。

所有随机调用都来自传入的 ``random.Random``，读取页面永远不会调用本模块。
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from football_simulator.match_engine import (
    EVENT_MINUTES,
    _is_goal,
    _is_saved,
    _pick_assist,
    _pick_players,
    _shared_event_count,
    _team_pressure_score,
)
from football_simulator.game.models import (
    MatchFacts,
    Player,
    Team,
    empty_player_fact,
)

EXTRA_TIME_WINDOWS = (4, 5)
EXTRA_TIME_FIVE_WINDOW_PROBABILITY = 1.0 / 3.0
PENALTY_SUCCESS_PROBABILITY = 0.75
PENALTY_ROUNDS = 5


class MatchEngine:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    # -- 对外入口 -------------------------------------------------------

    def play(
        self,
        spec: dict,
        home_team: Team,
        away_team: Team,
        *,
        need_winner: bool = False,
        aggregate: Optional[Tuple[int, int]] = None,
    ) -> MatchFacts:
        """模拟一场比赛。

        ``aggregate`` 仅用于两回合次回合：传入首回合的（主队总比分, 客队
        总比分），此时只有总比分在 90 分钟后仍持平才进入加时；没有客场
        进球规则。
        """
        match = MatchFacts(
            match_id=spec["match_id"],
            season_number=int(spec["season_number"]),
            competition=spec["competition"],
            stage=spec["stage"],
            week=int(spec["week"]),
            round_number=int(spec.get("round", 1)),
            ordinal=int(spec.get("ordinal", 0)),
            home_team_id=home_team.team_id,
            away_team_id=away_team.team_id,
            stadium_id=spec["stadium_id"],
            venue_mode=spec.get("venue_mode", "home"),
            neutral_reason=spec.get("neutral_reason"),
            leg=int(spec.get("leg", 1)),
            tie_id=spec.get("tie_id"),
        )
        home_has_advantage = (
            match.venue_mode == "home"
            and spec.get("home_advantage_team_id", home_team.team_id) == home_team.team_id
        )
        away_has_advantage = (
            match.venue_mode == "home"
            and spec.get("home_advantage_team_id") == away_team.team_id
        )
        match.home_advantage_team_id = (
            home_team.team_id if home_has_advantage else away_team.team_id if away_has_advantage else None
        )

        stats: Dict[str, dict] = {}
        for player in home_team.roster:
            stats[player.player_id] = empty_player_fact(home_team.team_id)
        for player in away_team.roster:
            stats[player.player_id] = empty_player_fact(away_team.team_id)

        self._play_period(
            match=match,
            home_team=home_team,
            away_team=away_team,
            minutes=list(EVENT_MINUTES),
            home_pressure=_team_pressure_score(home_team, away_team, is_home=home_has_advantage),
            away_pressure=_team_pressure_score(away_team, home_team, is_home=away_has_advantage),
            home_advantage=home_has_advantage,
            away_advantage=away_has_advantage,
            stats=stats,
            period="90",
        )

        aggregate_tied = True
        if aggregate is not None:
            aggregate_tied = (match.home_goals + aggregate[0]) == (match.away_goals + aggregate[1])
        level_after_90 = match.home_goals == match.away_goals

        should_play_extra = need_winner and (aggregate_tied if aggregate is not None else level_after_90)
        if should_play_extra:
            match.extra_time_played = True
            extra_minutes = self._extra_time_minutes()
            self._play_period(
                match=match,
                home_team=home_team,
                away_team=away_team,
                minutes=extra_minutes,
                home_pressure=_team_pressure_score(home_team, away_team, is_home=home_has_advantage),
                away_pressure=_team_pressure_score(away_team, home_team, is_home=away_has_advantage),
                home_advantage=home_has_advantage,
                away_advantage=away_has_advantage,
                stats=stats,
                period="et",
            )

        home_total = match.home_goals + match.home_goals_et + (aggregate[0] if aggregate else 0)
        away_total = match.away_goals + match.away_goals_et + (aggregate[1] if aggregate else 0)
        still_level = home_total == away_total
        if need_winner and still_level:
            self._shootout(match, home_team, away_team, stats)
        else:
            if home_total > away_total:
                match.winner_team_id = home_team.team_id
            elif home_total < away_total:
                match.winner_team_id = away_team.team_id
            if match.extra_time_played:
                match.decided_by = "et"

        self._record_clean_sheets(match, home_team, away_team, stats)
        match.player_facts = stats
        match.status = "completed"
        if not match.events:
            match.events.append(
                {
                    "minute": 0,
                    "type": "note",
                    "text": "比赛比较胶着，双方都没创造出太多绝对机会。",
                }
            )
        return match

    # -- 90 分钟与加时 ---------------------------------------------------

    def _play_period(
        self,
        *,
        match: MatchFacts,
        home_team: Team,
        away_team: Team,
        minutes: List[int],
        home_pressure: float,
        away_pressure: float,
        home_advantage: bool,
        away_advantage: bool,
        stats: Dict[str, dict],
        period: str,
    ) -> None:
        rng = self.rng
        match_tempo = rng.uniform(0.94, 1.18)
        match_chaos = rng.uniform(-0.04, 0.08)
        for minute in minutes:
            swing = rng.uniform(-0.22, 0.22)
            home_window = home_pressure * match_tempo + swing + match_chaos
            away_window = away_pressure * match_tempo - swing + match_chaos
            if rng.random() < max(0.06, min(0.62, home_window)):
                self._attack(
                    match=match,
                    attacking_team=home_team,
                    defending_team=away_team,
                    minute=minute,
                    is_home=home_advantage,
                    stats=stats,
                    period=period,
                )
            if rng.random() < max(0.06, min(0.62, away_window)):
                self._attack(
                    match=match,
                    attacking_team=away_team,
                    defending_team=home_team,
                    minute=minute,
                    is_home=away_advantage,
                    stats=stats,
                    period=period,
                )

    def _extra_time_minutes(self) -> List[int]:
        count = EXTRA_TIME_WINDOWS[1] if self.rng.random() < EXTRA_TIME_FIVE_WINDOW_PROBABILITY else EXTRA_TIME_WINDOWS[0]
        return sorted(self.rng.sample(range(91, 121), count))

    def _attack(
        self,
        *,
        match: MatchFacts,
        attacking_team: Team,
        defending_team: Team,
        minute: int,
        is_home: bool,
        stats: Dict[str, dict],
        period: str,
    ) -> None:
        rng = self.rng
        creators = _pick_players(
            team=attacking_team,
            rng=rng,
            role="creator",
            count=_shared_event_count(rng, second_probability=0.48, third_probability=0.16),
        )
        for creator in creators:
            self._record(stats, creator, "chances_created")
        shooter = _pick_players(team=attacking_team, rng=rng, role="scorer", count=1)[0]

        if _is_goal(attacking_team, defending_team, shooter, rng, is_home=is_home):
            self._record(stats, shooter, "goals")
            assist = _pick_assist(attacking_team, creators, shooter, rng)
            if assist is not None:
                self._record(stats, assist, "assists")
            if attacking_team.team_id == match.home_team_id:
                if period == "et":
                    match.home_goals_et += 1
                else:
                    match.home_goals += 1
            else:
                if period == "et":
                    match.away_goals_et += 1
                else:
                    match.away_goals += 1
            score_home = match.home_goals + match.home_goals_et
            score_away = match.away_goals + match.away_goals_et
            assist_text = f"，助攻 {assist.label}" if assist is not None else ""
            match.events.append(
                {
                    "minute": minute,
                    "type": "goal",
                    "period": period,
                    "team_id": attacking_team.team_id,
                    "player_id": shooter.player_id,
                    "assist_id": assist.player_id if assist else None,
                    "score": [score_home, score_away],
                    "text": "",
                }
            )
            # 事件文本使用真实队名，避免把稳定 ID 暴露给正文。
            event = match.events[-1]
            home_name = attacking_team.name if attacking_team.team_id == match.home_team_id else defending_team.name
            away_name = defending_team.name if attacking_team.team_id == match.home_team_id else attacking_team.name
            event["text"] = (
                f"{minute}' {attacking_team.name} 进球，进球者 {shooter.label}{assist_text}。"
                f"当前比分 {home_name} {score_home}-{score_away} {away_name}。"
            )
            return

        if _is_saved(attacking_team, defending_team, shooter, rng):
            goalkeeper = defending_team.goalkeeper
            self._record(stats, goalkeeper, "successful_saves")
            match.events.append(
                {
                    "minute": minute,
                    "type": "save",
                    "period": period,
                    "team_id": defending_team.team_id,
                    "player_id": goalkeeper.player_id,
                    "text": f"{minute}' {goalkeeper.label} 为 {defending_team.name} 扑出了 {shooter.label} 的射门。",
                }
            )
            return

        defenders = _pick_players(
            team=defending_team,
            rng=rng,
            role="defense",
            count=_shared_event_count(rng, second_probability=0.52, third_probability=0.18),
        )
        for defender in defenders:
            self._record(stats, defender, "successful_defenses")
        labels = "、".join(player.label for player in defenders) or "防线"
        match.events.append(
            {
                "minute": minute,
                "type": "defense",
                "period": period,
                "team_id": defending_team.team_id,
                "player_id": defenders[0].player_id if defenders else None,
                "text": f"{minute}' {labels} 成功化解了 {attacking_team.name} 的这次进攻。",
            }
        )

    # -- 点球 -------------------------------------------------------------

    def _shootout(self, match: MatchFacts, home_team: Team, away_team: Team, stats: Dict[str, dict]) -> None:
        rng = self.rng
        first_team = home_team if rng.random() < 0.5 else away_team
        second_team = away_team if first_team is home_team else home_team
        match.pk_first = first_team.team_id
        takers = {
            home_team.team_id: self._penalty_takers(home_team),
            away_team.team_id: self._penalty_takers(away_team),
        }
        index = {home_team.team_id: 0, away_team.team_id: 0}
        scores = {home_team.team_id: 0, away_team.team_id: 0}
        kicks: List[dict] = []
        round_number = 0
        while True:
            round_number += 1
            for team in (first_team, second_team):
                taker = takers[team.team_id][index[team.team_id] % len(takers[team.team_id])]
                index[team.team_id] += 1
                scored = rng.random() < PENALTY_SUCCESS_PROBABILITY
                if scored:
                    scores[team.team_id] += 1
                kicks.append(
                    {
                        "round": round_number,
                        "team_id": team.team_id,
                        "player_id": taker.player_id,
                        "scored": scored,
                    }
                )
            if round_number >= PENALTY_ROUNDS:
                remaining_first = max(0, PENALTY_ROUNDS - min(index[first_team.team_id], PENALTY_ROUNDS))
                remaining_second = max(0, PENALTY_ROUNDS - min(index[second_team.team_id], PENALTY_ROUNDS))
                if index[first_team.team_id] >= PENALTY_ROUNDS and index[second_team.team_id] >= PENALTY_ROUNDS:
                    if scores[first_team.team_id] != scores[second_team.team_id]:
                        break
                    if round_number > PENALTY_ROUNDS + 20:
                        break
                elif (
                    abs(scores[first_team.team_id] - scores[second_team.team_id])
                    > remaining_first + remaining_second
                ):
                    break
            else:
                remaining_first = PENALTY_ROUNDS - index[first_team.team_id]
                remaining_second = PENALTY_ROUNDS - index[second_team.team_id]
                if abs(scores[first_team.team_id] - scores[second_team.team_id]) > remaining_first + remaining_second:
                    break
        match.home_pk = scores[home_team.team_id]
        match.away_pk = scores[away_team.team_id]
        match.shootout = kicks
        match.winner_team_id = home_team.team_id if match.home_pk > match.away_pk else away_team.team_id
        match.decided_by = "pk"

    def _penalty_takers(self, team: Team) -> List[Player]:
        candidates = [player for player in team.roster if player.position != "GK"]
        candidates.sort(key=lambda player: (-player.ability, player.slot_number))
        ordered = candidates + [team.goalkeeper]
        return ordered or list(team.roster)

    # -- 统计 -------------------------------------------------------------

    @staticmethod
    def _record(stats: Dict[str, dict], player: Player, field: str, amount: int = 1) -> None:
        stats[player.player_id][field] += amount

    @staticmethod
    def _record_clean_sheets(match: MatchFacts, home_team: Team, away_team: Team, stats: Dict[str, dict]) -> None:
        home_total = match.home_goals + match.home_goals_et
        away_total = match.away_goals + match.away_goals_et
        if away_total == 0:
            stats[home_team.goalkeeper.player_id]["clean_sheets"] += 1
        if home_total == 0:
            stats[away_team.goalkeeper.player_id]["clean_sheets"] += 1
