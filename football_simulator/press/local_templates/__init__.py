"""Versioned, deterministic newspaper expression. No provider or credentials."""

ENGINE_ID = 'local-template'
TEMPLATE_VERSION = 'local-news/5'


def install_columns(conn, table):
    columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
    for name in ('engine_id', 'template_version'):
        if name not in columns:
            # NULL is deliberately legacy DeepSeek, never the new default.
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} TEXT')


def engine_of(row):
    return (row['engine_id'] if 'engine_id' in row.keys() else None) or 'deepseek'
