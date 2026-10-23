"""领域模型：稳定身份的球队/球员与比赛事实合同。

新存档只使用本模块的稳定 ID 合同：
- 球队 ``team-<slug>``；真实球员 ``real::<slug>``；默认球员
  ``default::<team_id>:<position>:<slot>``。
- 球员姓名只是可显示标签，任何业务键、路由、奖项候选都不依赖姓名。
- 每场比赛保存名义主客、实际球场、场地模式（home/neutral）、90 分钟、
  加时、点球与逐场球员事实。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

POSITION_GOALKEEPER = "GK"
POSITION_DEFENDER = "DF"
POSITION_MIDFIELDER = "MF"
POSITION_FORWARD = "FW"
POSITIONS = (POSITION_GOALKEEPER, POSITION_DEFENDER, POSITION_MIDFIELDER, POSITION_FORWARD)
FORMATION_RULES = {
    POSITION_GOALKEEPER: 1,
    POSITION_DEFENDER: 4,
    POSITION_MIDFIELDER: 3,
    POSITION_FORWARD: 3,
}

_ASCII_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _ASCII_SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "unnamed"


def team_id_for(club_en: str) -> str:
    return f"team-{slugify(club_en)}"


def real_player_id_for(name: str) -> str:
    return f"real::{slugify(name)}"


def default_player_id(team_id: str, position: str, slot_number: int) -> str:
    return f"default::{team_id}:{position}:{slot_number}"


@dataclass(frozen=True)
class Player:
    player_id: str
    name: Optional[str]
    position: str
    ability: int
    is_real: bool
    slot_number: int
    initial_market_value: Optional[float] = None
    team_id: Optional[str] = None

    @property
    def label(self) -> str:
        return self.name or f"默认 {self.position} {self.slot_number}"

    @property
    def display_name(self) -> str:
        return self.name or f"默认球员 {self.position}{self.slot_number}"


@dataclass(frozen=True)
class Team:
    team_id: str
    name: str
    name_zh: str
    division: str
    stadium_id: str
    roster: Tuple[Player, ...]

    def __post_init__(self) -> None:
        if len(self.roster) != sum(FORMATION_RULES.values()):
            raise ValueError(f"{self.name} 必须正好拥有 {sum(FORMATION_RULES.values())} 名球员。")
        counts = {position: 0 for position in FORMATION_RULES}
        for player in self.roster:
            counts[player.position] = counts.get(player.position, 0) + 1
        if counts != FORMATION_RULES:
            raise ValueError(f"{self.name} 阵容位置不符：{counts}")

    @property
    def goalkeeper(self) -> Player:
        return self.players_for_position(POSITION_GOALKEEPER)[0]

    @property
    def defenders(self) -> Tuple[Player, ...]:
        return self.players_for_position(POSITION_DEFENDER)

    @property
    def midfielders(self) -> Tuple[Player, ...]:
        return self.players_for_position(POSITION_MIDFIELDER)

    @property
    def forwards(self) -> Tuple[Player, ...]:
        return self.players_for_position(POSITION_FORWARD)

    @property
    def real_player_count(self) -> int:
        return sum(1 for player in self.roster if player.is_real)

    @property
    def baseline_ability(self) -> float:
        return min(player.ability for player in self.roster)

    @property
    def star_power(self) -> float:
        return sum(max(0, player.ability - self.baseline_ability) for player in self.roster) / len(self.roster)

    def position_star_power(self, position: str) -> float:
        players = self.players_for_position(position)
        return sum(max(0, player.ability - self.baseline_ability) for player in players) / len(players)

    @property
    def attack(self) -> float:
        return (
            self._average(self.forwards) * 0.62
            + self._average(self.midfielders) * 0.20
            + self.position_star_power(POSITION_FORWARD) * 1.05
            + self.position_star_power(POSITION_MIDFIELDER) * 0.40
        )

    @property
    def midfield(self) -> float:
        return (
            self._average(self.midfielders) * 0.50
            + self._average(self.defenders) * 0.15
            + self._average(self.forwards) * 0.15
            + self.position_star_power(POSITION_MIDFIELDER) * 1.00
            + self.position_star_power(POSITION_FORWARD) * 0.25
        )

    @property
    def defense(self) -> float:
        return (
            self._average(self.defenders) * 0.50
            + self.goalkeeper.ability * 0.20
            + self._average(self.midfielders) * 0.08
            + self.position_star_power(POSITION_DEFENDER) * 0.90
            + max(0, self.goalkeeper.ability - self.baseline_ability) * 0.80
        )

    @property
    def mentality(self) -> float:
        return min(99.0, self.rating + self.real_player_count * 1.30 + self.star_power * 0.55)

    @property
    def rating(self) -> float:
        return sum(player.ability for player in self.roster) / len(self.roster)

    def players_for_position(self, position: str) -> Tuple[Player, ...]:
        return tuple(player for player in self.roster if player.position == position)

    @staticmethod
    def _average(players: Tuple[Player, ...]) -> float:
        return sum(player.ability for player in players) / len(players)


STAT_FIELDS = (
    "goals",
    "assists",
    "chances_created",
    "successful_defenses",
    "successful_saves",
    "clean_sheets",
)


def empty_player_fact(team_id: str) -> dict:
    fact = {name: 0 for name in STAT_FIELDS}
    fact["team_id"] = team_id
    fact["appeared"] = 1
    return fact


@dataclass
class MatchFacts:
    match_id: str
    season_number: int
    competition: str
    stage: str
    week: int
    round_number: int
    ordinal: int
    home_team_id: str
    away_team_id: str
    stadium_id: str
    venue_mode: str = "home"
    neutral_reason: Optional[str] = None
    leg: int = 1
    tie_id: Optional[str] = None
    status: str = "scheduled"
    home_goals: int = 0
    away_goals: int = 0
    home_goals_et: int = 0
    away_goals_et: int = 0
    home_pk: Optional[int] = None
    away_pk: Optional[int] = None
    pk_first: Optional[str] = None
    shootout: List[dict] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    player_facts: Dict[str, dict] = field(default_factory=dict)
    winner_team_id: Optional[str] = None
    decided_by: str = "90"
    home_advantage_team_id: Optional[str] = None
    extra_time_played: bool = False

    @property
    def total_home_goals(self) -> int:
        return self.home_goals + self.home_goals_et

    @property
    def total_away_goals(self) -> int:
        return self.away_goals + self.away_goals_et

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict:
        data = {
            "match_id": self.match_id,
            "season_number": self.season_number,
            "competition": self.competition,
            "stage": self.stage,
            "week": self.week,
            "round_number": self.round_number,
            "ordinal": self.ordinal,
            "home_team_id": self.home_team_id,
            "away_team_id": self.away_team_id,
            "stadium_id": self.stadium_id,
            "venue_mode": self.venue_mode,
            "neutral_reason": self.neutral_reason,
            "leg": self.leg,
            "tie_id": self.tie_id,
            "status": self.status,
            "home_goals": self.home_goals,
            "away_goals": self.away_goals,
            "home_goals_et": self.home_goals_et,
            "away_goals_et": self.away_goals_et,
            "home_pk": self.home_pk,
            "away_pk": self.away_pk,
            "pk_first": self.pk_first,
            "shootout": self.shootout,
            "events": self.events,
            "player_facts": self.player_facts,
            "winner_team_id": self.winner_team_id,
            "decided_by": self.decided_by,
            "home_advantage_team_id": self.home_advantage_team_id,
            "extra_time_played": self.extra_time_played,
            "total_home_goals": self.total_home_goals,
            "total_away_goals": self.total_away_goals,
        }
        return data


def player_from_dict(player_id: str, data: dict) -> Player:
    return Player(
        player_id=player_id,
        name=data.get("name"),
        position=data["position"],
        ability=int(data["ability"]),
        is_real=bool(data.get("is_real")),
        slot_number=int(data.get("slot_number", 0)),
        initial_market_value=data.get("initial_market_value"),
        team_id=data.get("team_id"),
    )
