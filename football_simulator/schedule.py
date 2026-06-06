from football_simulator.models import Fixture, Team, WeekScheduleEntry


TOTAL_WEEKS = 52
WINTER_BREAK_WEEKS = (25, 26, 27)
SUMMER_BREAK_WEEKS = (50, 51, 52)

CUP_EVENT_LABELS = {
    "winners_cup_group_1": "优胜者杯小组赛第1轮",
    "winners_cup_group_2": "优胜者杯小组赛第2轮",
    "winners_cup_group_3": "优胜者杯小组赛第3轮",
    "winners_cup_group_4": "优胜者杯小组赛第4轮",
    "winners_cup_group_5": "优胜者杯小组赛第5轮",
    "winners_cup_group_6": "优胜者杯小组赛第6轮",
    "winners_cup_quarterfinal_leg_1": "优胜者杯四分之一决赛首回合",
    "winners_cup_quarterfinal_leg_2": "优胜者杯四分之一决赛次回合",
    "winners_cup_semifinal_leg_1": "优胜者杯半决赛首回合",
    "winners_cup_semifinal_leg_2": "优胜者杯半决赛次回合",
    "winners_cup_final_leg_1": "优胜者杯决赛首回合",
    "winners_cup_final_leg_2": "优胜者杯决赛次回合",
    "challenge_cup_r32": "挑战杯32强",
    "challenge_cup_r16": "挑战杯16强",
    "challenge_cup_quarterfinal": "挑战杯四分之一决赛",
    "challenge_cup_semifinal": "挑战杯半决赛",
    "challenge_cup_final": "挑战杯决赛",
    "super_cup_semifinal": "超级杯半决赛",
    "super_cup_final": "超级杯决赛",
}

WEEK_CUP_EVENTS = {
    3: ("winners_cup_group_1",),
    5: ("challenge_cup_r32",),
    7: ("winners_cup_group_2",),
    10: ("winners_cup_group_3",),
    11: ("challenge_cup_r16",),
    14: ("winners_cup_group_4",),
    17: ("challenge_cup_quarterfinal",),
    18: ("winners_cup_group_5",),
    22: ("winners_cup_group_6",),
    23: ("challenge_cup_semifinal",),
    24: ("super_cup_semifinal",),
    28: ("super_cup_final",),
    30: ("winners_cup_quarterfinal_leg_1",),
    32: ("winners_cup_quarterfinal_leg_2",),
    34: ("winners_cup_semifinal_leg_1",),
    36: ("winners_cup_semifinal_leg_2",),
    43: ("challenge_cup_final",),
    44: ("winners_cup_final_leg_1",),
    45: ("winners_cup_final_leg_2",),
}

PROMOTION_PLAYOFF_WEEKS = {
    46: ("promotion_playoff_semi_leg_1", "升级附加赛半决赛首回合"),
    47: ("promotion_playoff_semi_leg_2", "升级附加赛半决赛次回合"),
    48: ("promotion_playoff_final_leg_1", "升级附加赛决赛首回合"),
    49: ("promotion_playoff_final_leg_2", "升级附加赛决赛次回合"),
}


def build_league_schedule(teams: list[Team]) -> list[list[Fixture]]:
    if len(teams) % 2 != 0:
        raise ValueError("联赛赛程生成器要求球队数量必须为偶数。")

    rotation = list(teams)
    rounds: list[list[Fixture]] = []
    team_count = len(rotation)

    for round_index in range(team_count - 1):
        pairings: list[Fixture] = []
        for index in range(team_count // 2):
            home = rotation[index]
            away = rotation[team_count - 1 - index]
            if round_index % 2 == 1:
                home, away = away, home
            pairings.append(
                Fixture(
                    round_number=round_index + 1,
                    home_team=home,
                    away_team=away,
                    competition=home.division,
                )
            )
        rounds.append(pairings)
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]

    second_half: list[list[Fixture]] = []
    for offset, round_fixtures in enumerate(rounds, start=len(rounds) + 1):
        second_half.append(
            [
                Fixture(
                    round_number=offset,
                    home_team=fixture.away_team,
                    away_team=fixture.home_team,
                    competition=fixture.competition,
                )
                for fixture in round_fixtures
            ]
        )
    return rounds + second_half


def build_week_calendar(rounds: list[list[Fixture]]) -> list[WeekScheduleEntry]:
    total_rounds = len(rounds)
    if total_rounds != 38:
        raise ValueError("当前赛历设计要求联赛固定为 38 轮。")

    league_round_by_week = {
        **{week_number: week_number for week_number in range(1, 25)},
        **{week_number: week_number - 4 for week_number in range(29, 43)},
    }

    weeks: list[WeekScheduleEntry] = []
    for week_number in range(1, TOTAL_WEEKS + 1):
        if week_number in WINTER_BREAK_WEEKS:
            weeks.append(
                WeekScheduleEntry(
                    week_number=week_number,
                    label="冬窗休赛期",
                    kind="winter_break",
                )
            )
            continue

        if week_number in SUMMER_BREAK_WEEKS:
            weeks.append(
                WeekScheduleEntry(
                    week_number=week_number,
                    label="夏窗休赛期",
                    kind="summer_break",
                )
            )
            continue

        if week_number in PROMOTION_PLAYOFF_WEEKS:
            stage_key, stage_label = PROMOTION_PLAYOFF_WEEKS[week_number]
            weeks.append(
                WeekScheduleEntry(
                    week_number=week_number,
                    label=stage_label,
                    kind="promotion_playoff",
                    promotion_playoff_stage=stage_key,
                )
            )
            continue

        league_round = league_round_by_week.get(week_number)
        cup_events = WEEK_CUP_EVENTS.get(week_number, ())
        if league_round is not None:
            label = "联赛周"
            if cup_events:
                cup_labels = " / ".join(CUP_EVENT_LABELS[event_key] for event_key in cup_events)
                label = f"联赛周 + {cup_labels}"
            weeks.append(
                WeekScheduleEntry(
                    week_number=week_number,
                    label=label,
                    kind="league_week",
                    premier_round_numbers=(league_round,),
                    second_round_numbers=(league_round,),
                    cup_events=cup_events,
                )
            )
            continue

        if cup_events:
            weeks.append(
                WeekScheduleEntry(
                    week_number=week_number,
                    label=" / ".join(CUP_EVENT_LABELS[event_key] for event_key in cup_events),
                    kind="cup_week",
                    cup_events=cup_events,
                )
            )
            continue

        weeks.append(
            WeekScheduleEntry(
                week_number=week_number,
                label="无比赛周",
                kind="open_week",
            )
        )

    return weeks
