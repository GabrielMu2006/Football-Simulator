"""SQLite 存档 schema（阶段 1，对应实施方案 §6.3）。

设计原则（方案风险 B）：只把需要稳定查询和外键的实体规范化；杯赛继续模拟
所需的内部阶段状态暂存 ``season_runtime.data_json``。旧 JSON 不做导入。

与现行为的边界：
- ``player_match_stats`` 按比赛当时两队注册阵容写入（appeared=1，即使六项为 0），
  ``team_id`` 为比赛当时所属球队 —— 这是新增查询数据，不改变任何评分/奖项公式。
- ``player_team_tenures`` 是 ``player_match_stats`` 之上的派生视图（比赛当时
  归属），比在转会时刻写 tenure 行更精确；转会事件本身记录在 ``transfers``。
- ``awards`` 是从赛季归档 JSON 派生的规范化副本，供阶段 2 查询使用。
"""

from __future__ import annotations

import sqlite3

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS save_meta (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS seasons (
        season_id INTEGER PRIMARY KEY,
        season_number INTEGER NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('active', 'completed'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS teams (
        team_id INTEGER PRIMARY KEY,
        ordinal INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL UNIQUE,
        division TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS players (
        team_id INTEGER NOT NULL REFERENCES teams(team_id),
        roster_index INTEGER NOT NULL,
        player_id TEXT NOT NULL,
        name TEXT,
        position TEXT NOT NULL,
        ability INTEGER NOT NULL,
        is_real INTEGER NOT NULL CHECK (is_real IN (0, 1)),
        slot_number INTEGER NOT NULL,
        initial_market_value REAL,
        PRIMARY KEY (team_id, roster_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_players_player_id ON players(player_id)",
    "CREATE INDEX IF NOT EXISTS idx_players_is_real ON players(is_real)",
    """
    CREATE TABLE IF NOT EXISTS real_player_pool (
        ordinal INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        position TEXT NOT NULL,
        ability INTEGER NOT NULL,
        initial_market_value REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS season_runtime (
        season_id INTEGER PRIMARY KEY REFERENCES seasons(season_id),
        data_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS season_archives (
        season_id INTEGER PRIMARY KEY REFERENCES seasons(season_id),
        archive_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY,
        season_id INTEGER NOT NULL REFERENCES seasons(season_id),
        category TEXT NOT NULL CHECK (category IN ('premier', 'second', 'cup', 'playoff')),
        competition TEXT NOT NULL,
        week_number INTEGER NOT NULL,
        round_number INTEGER NOT NULL,
        ordinal INTEGER NOT NULL,
        home_team_id INTEGER NOT NULL REFERENCES teams(team_id),
        away_team_id INTEGER NOT NULL REFERENCES teams(team_id),
        status TEXT NOT NULL CHECK (status IN ('scheduled', 'completed')),
        home_goals INTEGER,
        away_goals INTEGER,
        UNIQUE (season_id, category, week_number, round_number, ordinal)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_matches_season_comp_week ON matches(season_id, category, week_number, round_number)",
    "CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team_id, away_team_id)",
    """
    CREATE TABLE IF NOT EXISTS match_events (
        match_id TEXT NOT NULL REFERENCES matches(match_id),
        sequence_no INTEGER NOT NULL,
        event_text TEXT NOT NULL,
        PRIMARY KEY (match_id, sequence_no)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_match_stats (
        match_id TEXT NOT NULL REFERENCES matches(match_id),
        player_id TEXT NOT NULL,
        team_id INTEGER NOT NULL REFERENCES teams(team_id),
        appeared INTEGER NOT NULL CHECK (appeared IN (0, 1)),
        goals INTEGER NOT NULL DEFAULT 0,
        assists INTEGER NOT NULL DEFAULT 0,
        chances_created INTEGER NOT NULL DEFAULT 0,
        successful_defenses INTEGER NOT NULL DEFAULT 0,
        successful_saves INTEGER NOT NULL DEFAULT 0,
        clean_sheets INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (match_id, player_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pms_player ON player_match_stats(player_id, match_id)",
    "CREATE INDEX IF NOT EXISTS idx_pms_team ON player_match_stats(team_id)",
    """
    CREATE TABLE IF NOT EXISTS player_settlements (
        season_id INTEGER NOT NULL REFERENCES seasons(season_id),
        stage TEXT NOT NULL CHECK (stage IN ('winter', 'final')),
        player_key TEXT NOT NULL,
        season_rating REAL,
        market_value REAL,
        PRIMARY KEY (season_id, stage, player_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_settlements_player ON player_settlements(player_key, season_id)",
    """
    CREATE TABLE IF NOT EXISTS transfers (
        transfer_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
        season_number INTEGER NOT NULL,
        week_number INTEGER NOT NULL,
        window TEXT NOT NULL,
        trade_id TEXT,
        team_a TEXT NOT NULL,
        team_b TEXT NOT NULL,
        team_a_players_json TEXT NOT NULL,
        team_b_players_json TEXT NOT NULL,
        team_a_total_value REAL NOT NULL,
        team_b_total_value REAL NOT NULL,
        value_gap REAL NOT NULL,
        approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
        status TEXT NOT NULL,
        recalculated INTEGER NOT NULL CHECK (recalculated IN (0, 1)),
        reason TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transfers_season ON transfers(season_number)",
    """
    CREATE TABLE IF NOT EXISTS drafts (
        draft_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
        season_number INTEGER NOT NULL,
        log_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_drafts_season ON drafts(season_number)",
    """
    CREATE TABLE IF NOT EXISTS pending_actions (
        ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL CHECK (type IN ('ability_review', 'transfer_review', 'draft')),
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pending_type ON pending_actions(type)",
    """
    CREATE TABLE IF NOT EXISTS awards (
        award_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
        season_id INTEGER NOT NULL REFERENCES seasons(season_id),
        competition TEXT,
        award_type TEXT NOT NULL,
        rank INTEGER,
        player_key TEXT,
        player_label TEXT,
        team_name TEXT,
        score REAL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_awards_unique ON awards (
        season_id, IFNULL(competition, ''), award_type, IFNULL(rank, -1), IFNULL(player_key, '')
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_awards_player ON awards(player_key, season_id)",
    # 派生视图：球员按“比赛当时所属球队”的效力时段（阶段 2 查询用）。
    """
    CREATE VIEW IF NOT EXISTS player_team_tenures AS
    SELECT
        m.season_id AS season_id,
        pms.player_id AS player_id,
        pms.team_id AS team_id,
        MIN(m.week_number) AS from_week,
        MAX(m.week_number) AS to_week,
        COUNT(*) AS match_count
    FROM player_match_stats AS pms
    JOIN matches AS m ON m.match_id = pms.match_id
    WHERE pms.appeared = 1
    GROUP BY m.season_id, pms.player_id, pms.team_id
    """,
)


def apply_schema(conn: sqlite3.Connection) -> None:
    """在新数据库上创建全部表/索引/视图，并写入 schema_version。"""
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    from football_simulator.persistence.connection import SCHEMA_VERSION

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
