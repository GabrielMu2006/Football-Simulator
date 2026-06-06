from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


POSITION_GOALKEEPER = "GK"
POSITION_DEFENDER = "DF"
POSITION_MIDFIELDER = "MF"
POSITION_FORWARD = "FW"

FORMATION_RULES = {
    POSITION_GOALKEEPER: 1,
    POSITION_DEFENDER: 4,
    POSITION_MIDFIELDER: 3,
    POSITION_FORWARD: 3,
}


@dataclass(frozen=True)
class Player:
    player_id: str
    name: Optional[str]
    position: str
    ability: int
    is_real: bool
    slot_number: int
    initial_market_value: Optional[float] = None

    @property
    def label(self) -> str:
        return self.name or f"默认 {self.position} {self.slot_number}"


@dataclass(frozen=True)
class Team:
    name: str
    roster: Tuple[Player, ...]
    division: str = "一级联赛"

    def __post_init__(self) -> None:
        expected_size = sum(FORMATION_RULES.values())
        if len(self.roster) != expected_size:
            raise ValueError(f"{self.name} 必须正好拥有 {expected_size} 名球员。")

        position_counts = {position: 0 for position in FORMATION_RULES}
        for player in self.roster:
            if player.position not in position_counts:
                raise ValueError(f"{self.name} 存在不支持的位置 '{player.position}'。")
            position_counts[player.position] += 1

        for position, expected_count in FORMATION_RULES.items():
            if position_counts[position] != expected_count:
                raise ValueError(
                    f"{self.name} 的 {position} 位置必须有 {expected_count} 名球员。"
                )

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
        return sum(max(0, player.ability - self.baseline_ability) for player in self.roster) / len(
            self.roster
        )

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


@dataclass(frozen=True)
class Fixture:
    round_number: int
    home_team: Team
    away_team: Team
    competition: str = "一级联赛"


@dataclass
class MatchResult:
    fixture: Fixture
    home_goals: int
    away_goals: int
    key_events: List[str]
    player_stats: Dict[str, "PlayerStatDelta"] = field(default_factory=dict)

    @property
    def home_team(self) -> Team:
        return self.fixture.home_team

    @property
    def away_team(self) -> Team:
        return self.fixture.away_team


@dataclass
class TableRow:
    team: Team
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0

    def record_match(self, goals_for: int, goals_against: int) -> None:
        self.played += 1
        self.goals_for += goals_for
        self.goals_against += goals_against
        if goals_for > goals_against:
            self.wins += 1
            self.points += 3
        elif goals_for == goals_against:
            self.draws += 1
            self.points += 1
        else:
            self.losses += 1


@dataclass
class MatchdayReport:
    round_number: int
    competition: str = "一级联赛"
    results: List[MatchResult] = field(default_factory=list)


@dataclass
class PlayerStatDelta:
    goals: int = 0
    assists: int = 0
    chances_created: int = 0
    successful_defenses: int = 0
    successful_saves: int = 0
    clean_sheets: int = 0

    def add(self, stat_name: str, amount: int = 1) -> None:
        setattr(self, stat_name, getattr(self, stat_name) + amount)


@dataclass
class PlayerSeasonStats:
    player: Player
    team_name: str
    season_number: int = 1
    appearances: int = 0
    goals: int = 0
    assists: int = 0
    chances_created: int = 0
    successful_defenses: int = 0
    successful_saves: int = 0
    clean_sheets: int = 0
    season_rating: Optional[float] = None
    market_value: Optional[float] = None

    def apply_delta(self, delta: PlayerStatDelta) -> None:
        self.goals += delta.goals
        self.assists += delta.assists
        self.chances_created += delta.chances_created
        self.successful_defenses += delta.successful_defenses
        self.successful_saves += delta.successful_saves
        self.clean_sheets += delta.clean_sheets


@dataclass
class TeamSeasonStats:
    team_name: str
    division: str = "一级联赛"
    season_number: int = 1
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0
    goals: int = 0
    assists: int = 0
    chances_created: int = 0
    successful_defenses: int = 0
    successful_saves: int = 0
    clean_sheets: int = 0
    total_market_value: Optional[float] = None

    @property
    def goal_diff(self) -> int:
        return self.goals_for - self.goals_against


@dataclass(frozen=True)
class WeekScheduleEntry:
    week_number: int
    label: str
    kind: str
    premier_round_numbers: Tuple[int, ...] = ()
    second_round_numbers: Tuple[int, ...] = ()
    cup_events: Tuple[str, ...] = ()
    promotion_playoff_stage: Optional[str] = None

    @property
    def round_numbers(self) -> Tuple[int, ...]:
        return self.premier_round_numbers


@dataclass
class SeasonReport:
    save_name: str
    matchdays: List[MatchdayReport]
    table: List[TableRow]
    best_attack: TableRow
    best_defense: TableRow
    teams: List[Team]
    player_stats: List[PlayerSeasonStats]
