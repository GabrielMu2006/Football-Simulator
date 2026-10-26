"""Per-save immutable weekly material, committed together with match facts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from football_simulator import runtime
from football_simulator.persistence_v2 import store
from football_simulator.press import PressError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def enabled(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='press_activation'").fetchone() is not None


def enable(save_name):
    """Explicit opt-in only. Does not change world, projections or current-save pointer."""
    path = store.database_path(save_name)
    if not path.is_file():
        raise PressError("存档尚未初始化。")
    conn = store._connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        world = json.loads(conn.execute("SELECT data_json FROM world").fetchone()[0])
        conn.execute("CREATE TABLE IF NOT EXISTS press_activation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), world_id TEXT NOT NULL, instance_id TEXT NOT NULL, first_season INTEGER NOT NULL, first_week INTEGER NOT NULL, enabled_version INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS press_weeks (world_id TEXT NOT NULL, season INTEGER NOT NULL, week INTEGER NOT NULL, source_version INTEGER NOT NULL, packet_json TEXT NOT NULL, source_json TEXT NOT NULL, packet_sha256 TEXT NOT NULL, source_sha256 TEXT NOT NULL, PRIMARY KEY(world_id,season,week))")
        row = conn.execute("SELECT * FROM press_activation WHERE singleton=1").fetchone()
        if row is None:
            first_week = int(world["season"]["week"]) + int(bool(world["season"].get("season_complete")))
            conn.execute("INSERT INTO press_activation VALUES(1,?,?,?,?,?)", (world["world_id"], str(uuid.uuid4()), int(world["season_number"]), first_week, int(world["version"])))
            row = conn.execute("SELECT * FROM press_activation WHERE singleton=1").fetchone()
        if row["world_id"] != world["world_id"]:
            raise PressError("报刊记录与当前世界不匹配。")
        conn.commit()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def capture(conn, world, completed_week):
    """Called only inside store.write_world's transaction, after projections exist."""
    if not enabled(conn):
        return
    activation = conn.execute("SELECT * FROM press_activation WHERE singleton=1").fetchone()
    if activation is None:
        return
    if activation["world_id"] != world["world_id"]:
        raise PressError("拒绝将另一世界写入报刊存档。")
    season, week = int(world["season_number"]), int(completed_week)
    if not 1 <= week <= 52:
        raise PressError("无效的完成周。")
    if (season, week) < (activation["first_season"], activation["first_week"]):
        return
    if conn.execute("SELECT 1 FROM press_weeks WHERE world_id=? AND season=? AND week=?", (world["world_id"], season, week)).fetchone():
        return
    if world["season"]["week"] != min(52, week + 1) or (week == 52 and not world["season"].get("season_complete")):
        raise PressError("只允许在完成周的真实提交边界冻结材料。")
    if any(f["status"] != "completed" for f in world["season"]["fixtures"] if int(f["week"]) == week):
        raise PressError("本周仍有比赛未完成。")
    from football_simulator.press.builder import build_packet
    from football_simulator.press.validation import validate_packet
    packet, sources = build_packet(conn, world, week, dict(activation))
    validate_packet(packet, sources)
    conn.execute("INSERT INTO press_weeks VALUES(?,?,?,?,?,?,?,?)", (world["world_id"], season, week, world["version"], canonical(packet), canonical(sources), digest(packet), digest(sources)))


def load_week(save_name, season, week):
    """Read exactly the frozen package; never reconstruct an uncaptured old week."""
    conn = store.open_readonly(save_name)
    try:
        conn.execute("BEGIN")
        if not enabled(conn):
            raise PressError("本存档尚未启用周报材料冻结。")
        identity = conn.execute("SELECT world_id FROM world").fetchone()[0]
        row = conn.execute("SELECT * FROM press_weeks WHERE world_id=? AND season=? AND week=?", (identity, int(season), int(week))).fetchone()
        if row is None:
            raise PressError("该周没有启用后冻结的材料；不补生成历史周报。")
        packet, sources = json.loads(row["packet_json"]), json.loads(row["source_json"])
        if digest(packet) != row["packet_sha256"] or digest(sources) != row["source_sha256"]:
            raise PressError("周报材料校验和不符。")
        return {"packet": packet, "sources": sources, "manifest": {"packet_sha256": row["packet_sha256"], "source_sha256": row["source_sha256"], "source_world_version": row["source_version"], "schema_version": packet["meta"]["schema_version"]}}
    finally:
        conn.close()


def list_weeks(save_name):
    conn = store.open_readonly(save_name)
    try:
        if not enabled(conn):
            return []
        return [dict(row) for row in conn.execute("SELECT season,week,source_version,packet_sha256 FROM press_weeks ORDER BY season,week")]
    finally:
        conn.close()


def export_week(save_name, season, week, destination):
    """Exclusive new export directory; never overwrite a save or existing export."""
    from football_simulator.press.validation import validate_packet
    material = load_week(save_name, season, week)
    validate_packet(material["packet"], material["sources"])
    destination = Path(destination).resolve()
    protect_output(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name, value in (("weekly.json", material["packet"]), ("sources.json", material["sources"]), ("manifest.json", material["manifest"])):
        (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def protect_output(path):
    path = Path(path).resolve()
    project = Path(__file__).resolve().parents[2]
    protected = [runtime.data_root() / n for n in ("saves", "backups", "trash")]
    protected += [runtime.user_data_root(), project / "release"]
    if any(path == p.resolve() or p.resolve() in path.parents for p in protected):
        raise PressError("输出不能位于游戏存档、备份、回收站或正式发布目录。")
