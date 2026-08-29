"""Query Service 层（阶段 2）。

职责：把 SQLite 中的存档数据投影为稳定的只读 DTO，供 UI 使用。
UI 层不得绕过本包直接读取数据库，也不得导入 state.py 私有实现。

约定（全部模块必须遵守）：
- 每个查询函数第一个参数是 ``sqlite3.Connection``（由 ``base.open_read_connection``
  打开，``PRAGMA query_only=ON``）；函数本身必须是纯只读的。
- 所有返回值是 ``@dataclass(frozen=True)`` 的 DTO，或其列表；绝不把
  ``sqlite3.Row``、dict-of-dict 或内部 JSON 结构直接暴露给调用方。
- 实体引用统一使用 ``base`` 中的稳定 ID 原语（PlayerRef/TeamRef/SeasonRef/
  CompetitionRef/MatchRef）。真实球员的稳定 ID 是注册表/比赛统计的
  ``real::<姓名slug>``（``data.real_player_id``）；默认球员没有跨赛季身份，
  ID 统一合成为 ``default:<team_id>:<slot_number>``。
- 赛事身份使用规范的中文常量字符串（一级联赛/次级联赛/优胜者杯/挑战杯/
  超级杯/升级附加赛），与持久化层 ``matches.competition`` 一致。
- 查询层数据统计口径：基于 ``player_match_stats``（全部比赛，含次级联赛与
  附加赛，按比赛当时球队归属）。这是实施方案 §8.5/§12.1 规定的球员页口径；
  它与旧快照（只累计 premier+cup、按当前球队归属）**有意不同**，不要试图
  与旧快照对齐。
- 同分裁决等展示排序必须确定性（不得调用随机源）。
- 未知/缺失数据用 None 表达并给出明确的空状态语义，不伪造数值。
"""

from football_simulator.queries import base
from football_simulator.queries.base import (
    CompetitionRef,
    MatchRef,
    MissingSaveError,
    PlayerRef,
    SeasonRef,
    TeamRef,
    canonical_player_id_for_name,
    default_player_id,
    open_read_connection,
    resolve_current_season,
    season_id_for,
)
from football_simulator.queries.competition_queries import (
    get_competition_profile,
    list_competitions,
)
from football_simulator.queries.dashboard_queries import get_dashboard
from football_simulator.queries.history_queries import (
    get_competition_history,
    get_season_archive_detail,
    list_season_summaries,
)
from football_simulator.queries.match_queries import (
    get_match_detail,
    get_match_neighbors,
    list_matches,
)
from football_simulator.queries.player_queries import (
    get_player_career,
    get_player_season_profile,
    list_players,
)
from football_simulator.queries.team_queries import (
    get_team_season_profile,
    list_teams,
)

__all__ = [
    "ALL_COMPETITIONS",
    "CompetitionRef",
    "MatchRef",
    "MissingSaveError",
    "PlayerRef",
    "SeasonRef",
    "TeamRef",
    "base",
    "canonical_player_id_for_name",
    "default_player_id",
    "get_competition_history",
    "get_competition_profile",
    "get_dashboard",
    "get_match_detail",
    "get_match_neighbors",
    "get_player_career",
    "get_player_season_profile",
    "get_season_archive_detail",
    "get_team_season_profile",
    "list_competitions",
    "list_matches",
    "list_players",
    "list_season_summaries",
    "list_teams",
    "open_read_connection",
    "resolve_current_season",
    "season_id_for",
]
