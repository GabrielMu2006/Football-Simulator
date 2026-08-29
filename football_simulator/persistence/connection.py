"""SQLite 存档连接管理（阶段 1）。

约定：
- 每个存档一个 ``save.sqlite3`` 数据库，位于 ``saves/<存档名>/``。
- 连接使用手动事务（``isolation_level=None``），写入统一走 ``BEGIN IMMEDIATE``，
  保证同一存档的读-改-写全程持有写锁：并发写入要么串行成功，要么清晰报错。
- ``PRAGMA foreign_keys=ON``、``journal_mode=WAL``、``synchronous=FULL``。
- ``user_version`` 记录 schema_version；高于本代码支持版本时拒绝打开。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000


class SaveDatabaseError(Exception):
    """存档数据库打开/校验失败。"""


def database_path(save_dir: Path) -> Path:
    return save_dir / "save.sqlite3"


def connect(save_dir: Path, *, create: bool = False, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """打开存档数据库。

    ``create=False`` 时数据库文件必须已存在且 schema_version 可识别；
    ``create=True`` 时允许新建空数据库（由调用方负责建表）。
    """
    path = database_path(save_dir)
    if not create and not path.exists():
        raise FileNotFoundError(f"未找到存档数据库：{path}")
    conn = sqlite3.connect(
        str(path),
        timeout=busy_timeout_ms / 1000.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")

    if not create:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            conn.close()
            raise SaveDatabaseError(f"存档数据库为空或损坏，请重新初始化赛季：{path}")
        if version > SCHEMA_VERSION:
            conn.close()
            raise SaveDatabaseError(
                f"存档 schema 版本（{version}）高于当前支持版本（{SCHEMA_VERSION}），请升级程序。"
            )
    return conn


def begin_immediate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def commit(conn: sqlite3.Connection) -> None:
    conn.execute("COMMIT")


def rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        # 没有活动事务时忽略（例如事务已被自动回滚）。
        pass


def in_transaction(conn: sqlite3.Connection) -> bool:
    return conn.in_transaction
