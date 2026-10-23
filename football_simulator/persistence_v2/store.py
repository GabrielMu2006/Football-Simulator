"""新存档持久化（只支持新格式）。

设计：
- SQLite 是唯一事实来源：``world`` 表保存权威世界 JSON，规范化的
  ``teams/players/matches/player_match_stats/honors/awards/events/archives``
  是供查询与审计的投影，每次写入在同一事务中重建。
- 读取页面使用只读连接，不写盘、不模拟、不抽签。
- 导出包是 zip：``save.sqlite3``（Online Backup 一致性副本）+ ``config.json``
  + ``manifest.json``（应用/格式/规则版本、世界 ID、校验和）。导入先在临时
  目录校验，再原子安装；默认拒绝重名，失败不改动目标。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from football_simulator import runtime
from football_simulator.game.rules import RULES_VERSION, WORLD_FORMAT_VERSION
from football_simulator.version import APP_VERSION

FORMAT_NAME = "football-simulator-world"
FORMAT_VERSION = WORLD_FORMAT_VERSION
DB_FILE_NAME = "save.sqlite3"
MANIFEST_FILE_NAME = "manifest.json"
EXPORT_EXTENSION = ".fsarchive"

SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS world (world_id TEXT PRIMARY KEY, version INTEGER NOT NULL, data_json TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS teams (team_id TEXT PRIMARY KEY, club_en TEXT NOT NULL, name_zh TEXT NOT NULL, stadium_id TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS players (player_id TEXT PRIMARY KEY, name TEXT, position TEXT NOT NULL, is_real INTEGER NOT NULL, ability INTEGER NOT NULL, slot_number INTEGER NOT NULL, team_id TEXT, initial_market_value REAL, market_value REAL)",
    "CREATE TABLE IF NOT EXISTS matches (match_id TEXT PRIMARY KEY, season_number INTEGER NOT NULL, competition TEXT NOT NULL, stage TEXT NOT NULL, week INTEGER NOT NULL, home_team_id TEXT NOT NULL, away_team_id TEXT NOT NULL, stadium_id TEXT NOT NULL, venue_mode TEXT NOT NULL, status TEXT NOT NULL, home_goals INTEGER, away_goals INTEGER, home_goals_et INTEGER, away_goals_et INTEGER, home_pk INTEGER, away_pk INTEGER, winner_team_id TEXT, decided_by TEXT, neutral_reason TEXT, home_advantage_team_id TEXT, extra_time_played INTEGER, events_json TEXT, shootout_json TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_matches_season_week ON matches(season_number, week)",
    "CREATE INDEX IF NOT EXISTS idx_matches_comp ON matches(season_number, competition)",
    "CREATE TABLE IF NOT EXISTS player_match_stats (match_id TEXT NOT NULL, season_number INTEGER NOT NULL, player_id TEXT NOT NULL, team_id TEXT NOT NULL, appeared INTEGER NOT NULL, goals INTEGER NOT NULL, assists INTEGER NOT NULL, chances_created INTEGER NOT NULL, successful_defenses INTEGER NOT NULL, successful_saves INTEGER NOT NULL, clean_sheets INTEGER NOT NULL, PRIMARY KEY (match_id, player_id))",
    "CREATE INDEX IF NOT EXISTS idx_pms_player ON player_match_stats(player_id)",
    "CREATE INDEX IF NOT EXISTS idx_pms_team ON player_match_stats(team_id)",
    "CREATE TABLE IF NOT EXISTS honors (season_number INTEGER NOT NULL, team_id TEXT NOT NULL, competition TEXT NOT NULL, rank INTEGER NOT NULL, points INTEGER NOT NULL, tier TEXT NOT NULL, PRIMARY KEY (season_number, team_id, competition))",
    "CREATE TABLE IF NOT EXISTS awards (season_number INTEGER NOT NULL, competition TEXT NOT NULL, award_type TEXT NOT NULL, rank INTEGER NOT NULL, player_id TEXT NOT NULL, player_label TEXT, team_id TEXT, value REAL, PRIMARY KEY (season_number, competition, award_type, rank))",
    "CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, season_number INTEGER NOT NULL, week INTEGER NOT NULL, event_type TEXT NOT NULL, competition TEXT, team_id TEXT, player_id TEXT, match_id TEXT, payload_json TEXT NOT NULL, text TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_events_season_week ON events(season_number, week)",
    "CREATE TABLE IF NOT EXISTS archives (season_number INTEGER PRIMARY KEY, archive_json TEXT NOT NULL)",
)


class SaveFormatError(ValueError):
    pass


def save_dir(save_name: str) -> Path:
    return runtime.save_root() / runtime.normalize_save_name(save_name)


def database_path(save_name: str) -> Path:
    return save_dir(save_name) / DB_FILE_NAME


def config_path(save_name: str) -> Path:
    return save_dir(save_name) / runtime.SAVE_CONFIG_FILE_NAME


def _readonly_uri(path: Path) -> str:
    """构造 SQLite 只读 file URI。

    直接拼 ``file:{path}`` 在 Windows 上会踩到三类真实路径：
    - 盘符与反斜线（``E:\\...``）不是合法的 URI 路径；
    - ``#`` 会被当成 fragment 起点，``?`` 会被当成查询串起点；
    - ``%`` 会被当成百分号转义（中文目录或 ``%TEMP%`` 风格名字会解码失败）。
    ``Path.as_uri()`` 生成规范 ``file:///`` 形式并转义非 ASCII 与保留字符，
    再把只读参数接在查询串上。
    """
    return Path(path).resolve().as_uri() + "?mode=ro"


def _connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(_readonly_uri(path), uri=True, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def create_database(save_name: str, world: dict, save_config: dict) -> Path:
    directory = save_dir(save_name)
    # 允许“先建档、后初始化”的两步流程：目录必须为空或不存在。
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / DB_FILE_NAME
    if path.exists():
        raise FileExistsError(f"存档 '{save_name}' 已经初始化。")
    conn = _connect(path)
    try:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    finally:
        conn.close()
    (directory / runtime.SAVE_CONFIG_FILE_NAME).write_text(
        json.dumps(save_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_world(save_name, world)
    return path


def _encode_meta(key: str, value) -> Tuple[str, str]:
    return key, json.dumps(value, ensure_ascii=False)


def write_world(save_name: str, world: dict) -> int:
    """在一个事务里写权威世界与全部规范化投影。"""
    world = dict(world)
    world.pop("_fixture_index", None)
    version = int(world.get("version", 0)) + 1
    world["version"] = version
    world.setdefault("world_id", str(uuid.uuid4()))
    updated_at = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(world, ensure_ascii=False, default=str)
    path = database_path(save_name)
    conn = _connect(path)
    try:
        with conn:
            conn.execute("DELETE FROM meta")
            conn.executemany(
                "INSERT INTO meta(key, value_json) VALUES(?, ?)",
                [
                    _encode_meta("format", FORMAT_NAME),
                    _encode_meta("format_version", FORMAT_VERSION),
                    _encode_meta("schema_version", 2),
                    _encode_meta("app_version", APP_VERSION),
                    _encode_meta("rules_version", RULES_VERSION),
                    _encode_meta("world_id", world["world_id"]),
                    _encode_meta("save_name", save_name),
                    _encode_meta("season_number", world["season_number"]),
                ],
            )
            conn.execute("DELETE FROM world")
            conn.execute(
                "INSERT INTO world(world_id, version, data_json, updated_at) VALUES(?,?,?,?)",
                (world["world_id"], version, payload, updated_at),
            )
            conn.execute("DELETE FROM teams")
            conn.execute("DELETE FROM players")
            for team_id, team in world["teams"].items():
                conn.execute(
                    "INSERT INTO teams(team_id, club_en, name_zh, stadium_id) VALUES(?,?,?,?)",
                    (team_id, team["club_en"], team.get("name_zh", team["club_en"]), team["stadium_id"]),
                )
            for player_id, player in world["players"].items():
                conn.execute(
                    "INSERT INTO players(player_id, name, position, is_real, ability, slot_number, team_id, initial_market_value, market_value) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        player_id,
                        player.get("name"),
                        player["position"],
                        1 if player.get("is_real") else 0,
                        int(player["ability"]),
                        int(player["slot_number"]),
                        player.get("team_id"),
                        player.get("initial_market_value"),
                        player.get("market_value"),
                    ),
                )
            # 历史赛季的规范化事实必须保留；只替换当前赛季的投影。
            current_season = int(world["season_number"])
            conn.execute("DELETE FROM matches WHERE season_number = ?", (current_season,))
            conn.execute("DELETE FROM player_match_stats WHERE season_number = ?", (current_season,))
            conn.execute("DELETE FROM events WHERE season_number = ?", (current_season,))
            conn.execute("DELETE FROM honors")
            conn.execute("DELETE FROM awards")
            conn.execute("DELETE FROM archives")
            for fixture in world["season"]["fixtures"]:
                if fixture["status"] != "completed":
                    continue
                conn.execute(
                    "INSERT INTO matches(match_id, season_number, competition, stage, week, home_team_id, away_team_id, stadium_id, venue_mode, status, home_goals, away_goals, home_goals_et, away_goals_et, home_pk, away_pk, winner_team_id, decided_by, neutral_reason, home_advantage_team_id, extra_time_played, events_json, shootout_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        fixture["match_id"],
                        int(fixture["season_number"]),
                        fixture["competition"],
                        fixture["stage"],
                        int(fixture["week"]),
                        fixture["home_team_id"],
                        fixture["away_team_id"],
                        fixture["stadium_id"],
                        fixture["venue_mode"],
                        fixture["status"],
                        fixture.get("home_goals"),
                        fixture.get("away_goals"),
                        fixture.get("home_goals_et"),
                        fixture.get("away_goals_et"),
                        fixture.get("home_pk"),
                        fixture.get("away_pk"),
                        fixture.get("winner_team_id"),
                        fixture.get("decided_by"),
                        fixture.get("neutral_reason"),
                        fixture.get("home_advantage_team_id"),
                        1 if fixture.get("extra_time_played") else 0,
                        json.dumps(fixture.get("events", []), ensure_ascii=False),
                        json.dumps(fixture.get("shootout", []), ensure_ascii=False),
                    ),
                )
                for player_id, fact in (fixture.get("player_facts") or {}).items():
                    conn.execute(
                        "INSERT INTO player_match_stats(match_id, season_number, player_id, team_id, appeared, goals, assists, chances_created, successful_defenses, successful_saves, clean_sheets) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            fixture["match_id"],
                            int(fixture["season_number"]),
                            player_id,
                            fact["team_id"],
                            1 if fact.get("appeared") else 0,
                            int(fact.get("goals", 0)),
                            int(fact.get("assists", 0)),
                            int(fact.get("chances_created", 0)),
                            int(fact.get("successful_defenses", 0)),
                            int(fact.get("successful_saves", 0)),
                            int(fact.get("clean_sheets", 0)),
                        ),
                    )
            season = world["season"]
            for row in season.get("honor_rows", []):
                conn.execute(
                    "INSERT OR REPLACE INTO honors(season_number, team_id, competition, rank, points, tier) VALUES(?,?,?,?,?,?)",
                    (
                        int(world["season_number"]),
                        row["team_id"],
                        row["competition"],
                        int(row["rank"]),
                        int(row["points"]),
                        row["tier"],
                    ),
                )
            current_awards = (season.get("awards") or {}).get("competitions", {}) or {}
            for competition, awards in current_awards.items():
                for award_type, winners in awards.items():
                    if not isinstance(winners, list):
                        continue
                    for rank, winner in enumerate(winners or [], start=1):
                        conn.execute(
                            "INSERT OR REPLACE INTO awards(season_number, competition, award_type, rank, player_id, player_label, team_id, value) VALUES(?,?,?,?,?,?,?,?)",
                            (
                                int(world["season_number"]),
                                competition,
                                award_type,
                                rank,
                                winner.get("player_id"),
                                winner.get("label"),
                                winner.get("team_id"),
                                winner.get("value"),
                            ),
                        )
            for season_archive in world.get("archives", []):
                conn.execute(
                    "INSERT INTO archives(season_number, archive_json) VALUES(?,?)",
                    (int(season_archive["season_number"]), json.dumps(season_archive, ensure_ascii=False)),
                )
                for row in season_archive.get("honor_rows", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO honors(season_number, team_id, competition, rank, points, tier) VALUES(?,?,?,?,?,?)",
                        (
                            int(season_archive["season_number"]),
                            row["team_id"],
                            row["competition"],
                            int(row["rank"]),
                            int(row["points"]),
                            row["tier"],
                        ),
                    )
                for competition, awards in (season_archive.get("awards", {}).get("competitions", {}) or {}).items():
                    for award_type, winners in awards.items():
                        if not isinstance(winners, list):
                            continue
                        for rank, winner in enumerate(winners or [], start=1):
                            conn.execute(
                                "INSERT OR REPLACE INTO awards(season_number, competition, award_type, rank, player_id, player_label, team_id, value) VALUES(?,?,?,?,?,?,?,?)",
                                (
                                    int(season_archive["season_number"]),
                                    competition,
                                    award_type,
                                    rank,
                                    winner.get("player_id"),
                                    winner.get("label"),
                                    winner.get("team_id"),
                                    winner.get("value"),
                                ),
                            )
            for event in season.get("events", []):
                conn.execute(
                    "INSERT OR REPLACE INTO events(event_id, season_number, week, event_type, competition, team_id, player_id, match_id, payload_json, text) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        event["event_id"],
                        int(world["season_number"]),
                        int(event.get("week", 0)),
                        event["type"],
                        event.get("competition"),
                        event.get("team_id"),
                        event.get("player_id"),
                        event.get("match_id"),
                        json.dumps(event.get("payload", {}), ensure_ascii=False),
                        event.get("text", ""),
                    ),
                )
    finally:
        conn.close()
    return version


def load_world(save_name: str, *, readonly: bool = False) -> dict:
    path = database_path(save_name)
    if not path.exists():
        raise FileNotFoundError(f"存档 '{save_name}' 尚未初始化。")
    conn = _connect(path, readonly=readonly)
    try:
        try:
            row = conn.execute("SELECT data_json FROM world ORDER BY version DESC LIMIT 1").fetchone()
        except sqlite3.DatabaseError as exc:
            raise SaveFormatError(
                f"存档 '{save_name}' 不是当前新格式；本版本只支持新建存档，不迁移、不删除原文件。"
            ) from exc
        if row is None:
            raise SaveFormatError(f"存档 '{save_name}' 没有世界数据。")
        try:
            world = json.loads(row["data_json"])
        except json.JSONDecodeError as exc:
            raise SaveFormatError(f"存档 '{save_name}' 的世界数据损坏。") from exc
    finally:
        conn.close()
    if int(world.get("format_version", 0)) != FORMAT_VERSION:
        raise SaveFormatError(
            f"存档 '{save_name}' 不是当前新格式（format_version={world.get('format_version')}），"
            "本版本只支持新建存档。"
        )
    if world.get("rules_version") != RULES_VERSION:
        raise SaveFormatError(
            f"存档 '{save_name}' 使用旧规则 {world.get('rules_version')}，当前规则为 {RULES_VERSION}；"
            "本版本不迁移旧规则存档。"
        )
    world["_fixture_index"] = {fixture["match_id"]: fixture for fixture in world["season"]["fixtures"]}
    return world


def open_readonly(save_name: str) -> sqlite3.Connection:
    path = database_path(save_name)
    if not path.exists():
        raise FileNotFoundError(f"存档 '{save_name}' 尚未初始化。")
    return _connect(path, readonly=True)


def fetch_match(save_name: str, match_id: str) -> Optional[dict]:
    conn = open_readonly(save_name)
    try:
        row = conn.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()
    finally:
        conn.close()
    return _match_row_to_dict(row) if row else None


def fetch_matches(
    save_name: str,
    *,
    season: Optional[int] = None,
    competition: Optional[str] = None,
    week: Optional[int] = None,
    team_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[dict]:
    clauses = []
    params: List[object] = []
    for clause, value in (
        ("season_number = ?", season),
        ("competition = ?", competition),
        ("week = ?", week),
        ("status = ?", status),
    ):
        if value is not None:
            clauses.append(clause)
            params.append(value)
    if team_id is not None:
        clauses.append("(home_team_id = ? OR away_team_id = ?)")
        params.extend([team_id, team_id])
    sql = "SELECT * FROM matches"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY season_number DESC, week ASC, match_id ASC"
    conn = open_readonly(save_name)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_match_row_to_dict(row) for row in rows]


def fetch_match_player_stats(save_name: str, match_id: str) -> List[dict]:
    conn = open_readonly(save_name)
    try:
        rows = conn.execute(
            "SELECT * FROM player_match_stats WHERE match_id = ? ORDER BY team_id, player_id", (match_id,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def fetch_player_stats(
    save_name: str,
    player_id: Optional[str] = None,
    *,
    season: Optional[int] = None,
    competition: Optional[str] = None,
) -> List[dict]:
    clauses = []
    params: List[object] = []
    if player_id is not None:
        clauses.append("p.player_id = ?")
        params.append(player_id)
    if season is not None:
        clauses.append("p.season_number = ?")
        params.append(season)
    if competition is not None:
        clauses.append("m.competition = ?")
        params.append(competition)
    sql = (
        "SELECT p.*, m.competition, m.week, m.home_team_id, m.away_team_id, m.home_goals, m.away_goals, "
        "m.home_goals_et, m.away_goals_et, m.home_pk, m.away_pk, m.winner_team_id, m.decided_by "
        "FROM player_match_stats p JOIN matches m ON m.match_id = p.match_id"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY p.season_number DESC, m.week DESC, m.match_id"
    conn = open_readonly(save_name)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _match_row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for key in ("events_json", "shootout_json"):
        try:
            data[key] = json.loads(data.get(key) or "[]")
        except json.JSONDecodeError:
            data[key] = []
    return data


def read_meta(save_name: str) -> dict:
    path = database_path(save_name)
    if not path.exists():
        return {}
    conn = _connect(path, readonly=True)
    try:
        rows = conn.execute("SELECT key, value_json FROM meta").fetchall()
    finally:
        conn.close()
    return {row["key"]: json.loads(row["value_json"]) for row in rows}


# ---------------------------------------------------------------------------
# 导出 / 导入（完整包）
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_package(save_name: str, dest_path: Path) -> Path:
    save_name = runtime.normalize_save_name(save_name)
    world = load_world(save_name)
    dest_path = Path(dest_path)
    if dest_path.suffix != EXPORT_EXTENSION:
        dest_path = dest_path.with_suffix(EXPORT_EXTENSION)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fs_export_") as tmp:
        tmp_db = Path(tmp) / DB_FILE_NAME
        src = sqlite3.connect(str(database_path(save_name)))
        dst = sqlite3.connect(str(tmp_db))
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        config_file = config_path(save_name)
        config_data = config_file.read_text(encoding="utf-8") if config_file.exists() else "{}"
        manifest = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "app_version": APP_VERSION,
            "rules_version": RULES_VERSION,
            "world_id": world["world_id"],
            "save_name": save_name,
            "season_number": world["season_number"],
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "database_sha256": _sha256(tmp_db),
            "config_sha256": hashlib.sha256(config_data.encode("utf-8")).hexdigest(),
            "files": [DB_FILE_NAME, runtime.SAVE_CONFIG_FILE_NAME, MANIFEST_FILE_NAME],
        }
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(tmp_db, DB_FILE_NAME)
            archive.writestr(runtime.SAVE_CONFIG_FILE_NAME, config_data)
            archive.writestr(MANIFEST_FILE_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    return dest_path


def validate_package(package_path: Path, target_name: str) -> dict:
    package_path = Path(package_path)
    if not package_path.exists():
        raise FileNotFoundError(f"导入包不存在：{package_path}")
    if package_path.suffix != EXPORT_EXTENSION:
        raise SaveFormatError(
            "只支持完整存档包（.fsarchive）；裸 SQLite/JSON 旧存档不再支持，请新建存档。"
        )
    try:
        archive = zipfile.ZipFile(package_path)
    except zipfile.BadZipFile as exc:
        raise SaveFormatError("导入包不是有效的 zip 存档包。") from exc
    with archive:
        names = set(archive.namelist())
        required = {DB_FILE_NAME, MANIFEST_FILE_NAME}
        if not required.issubset(names):
            raise SaveFormatError("导入包缺少数据库或 manifest。")
        try:
            manifest = json.loads(archive.read(MANIFEST_FILE_NAME).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SaveFormatError("manifest 无法解析。") from exc
        if manifest.get("format") != FORMAT_NAME or int(manifest.get("format_version", 0)) != FORMAT_VERSION:
            raise SaveFormatError("导入包格式或版本不受支持（只支持新存档）。")
        if manifest.get("rules_version") != RULES_VERSION:
            raise SaveFormatError(
                f"导入包规则版本 {manifest.get('rules_version')} 与当前 {RULES_VERSION} 不一致。"
            )
        with tempfile.TemporaryDirectory(prefix="fs_import_check_") as tmp:
            extracted = Path(tmp) / DB_FILE_NAME
            with archive.open(DB_FILE_NAME) as source, open(extracted, "wb") as target:
                shutil.copyfileobj(source, target)
            if _sha256(extracted) != manifest.get("database_sha256"):
                raise SaveFormatError("数据库校验和不匹配，包可能已损坏。")
            conn = _connect(extracted, readonly=True)
            try:
                check = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise SaveFormatError(f"数据库完整性检查失败：{check}")
                meta = {row["key"]: json.loads(row["value_json"]) for row in conn.execute("SELECT key, value_json FROM meta")}
                row = conn.execute("SELECT data_json FROM world").fetchone()
            except sqlite3.DatabaseError as exc:
                raise SaveFormatError("导入包不是有效的游戏数据库。") from exc
            finally:
                conn.close()
            if meta.get("format") != FORMAT_NAME or int(meta.get("format_version", 0)) != FORMAT_VERSION:
                raise SaveFormatError("导入包数据库格式不是当前新存档格式。")
            if row is None:
                raise SaveFormatError("导入包没有世界数据。")
            world = json.loads(row["data_json"])
            if world.get("world_id") != manifest.get("world_id"):
                raise SaveFormatError("manifest 与数据库的世界 ID 不一致。")
            if runtime.normalize_save_name(manifest.get("save_name", "")) != manifest.get("save_name"):
                raise SaveFormatError("导入包内的存档名无效。")
    return {"manifest": manifest, "target_name": runtime.normalize_save_name(target_name)}


def import_package(package_path: Path, target_name: str, *, overwrite: bool = False) -> Path:
    validated = validate_package(package_path, target_name)
    target_name = validated["target_name"]
    if runtime.save_exists(target_name):
        raise FileExistsError(f"存档 '{target_name}' 已存在；默认拒绝重名导入。")
    target_dir = save_dir(target_name)
    staging = Path(tempfile.mkdtemp(prefix="fs_import_stage_", dir=str(runtime.data_root())))
    try:
        with zipfile.ZipFile(package_path) as archive:
            archive.extract(DB_FILE_NAME, staging)
            config_data = archive.read(runtime.SAVE_CONFIG_FILE_NAME).decode("utf-8") if runtime.SAVE_CONFIG_FILE_NAME in archive.namelist() else "{}"
        (staging / runtime.SAVE_CONFIG_FILE_NAME).write_text(config_data, encoding="utf-8")
        # 再次确认目标未在验证后被创建。
        if target_dir.exists():
            raise FileExistsError(f"存档 '{target_name}' 已存在；默认拒绝重名导入。")
        os.replace(staging, target_dir)
        staging = None
    except Exception:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return target_dir
