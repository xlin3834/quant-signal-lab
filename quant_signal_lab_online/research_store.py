"""SQLite research store, separate from the paper-trading account."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from presets import DEFAULTS, PRESET_ORDER

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preset (
    name TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot (
    id INTEGER PRIMARY KEY, venue TEXT NOT NULL, entity_key TEXT NOT NULL,
    grade TEXT, exchange_time_ms INTEGER, received_time_ms INTEGER NOT NULL,
    connection_status TEXT NOT NULL, payload TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS favorite (
    category TEXT NOT NULL, entity_key TEXT NOT NULL, label TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (category, entity_key));
CREATE TABLE IF NOT EXISTS researcher (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
    links TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS article (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
    category TEXT NOT NULL, summary_zh TEXT NOT NULL, summary_source TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, state TEXT NOT NULL,
    detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alert (
    id INTEGER PRIMARY KEY, entity_key TEXT NOT NULL, grade TEXT NOT NULL,
    message TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS probe (
    name TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT NOT NULL, checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repo_cache (
    full_name TEXT PRIMARY KEY, payload TEXT NOT NULL, received_time TEXT NOT NULL, error TEXT);
CREATE INDEX IF NOT EXISTS snapshot_entity ON snapshot (entity_key, id);
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def default_path():
    import os
    return os.environ.get(
        "QSL_RESEARCH_DB",
        str(Path(__file__).resolve().parent / "data" / "research.sqlite3"),
    )


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '1')")
    for name in PRESET_ORDER:
        conn.execute(
            "INSERT OR IGNORE INTO preset VALUES (?, ?, ?)",
            (name, json.dumps(DEFAULTS[name].to_dict(), ensure_ascii=False), utc_now()),
        )
    conn.commit()
    return conn


def save_preset(conn, config):
    problems = config.errors()
    if problems:
        raise ValueError("；".join(problems))
    conn.execute(
        "INSERT OR REPLACE INTO preset VALUES (?, ?, ?)",
        (config.name, json.dumps(config.to_dict(), ensure_ascii=False), utc_now()),
    )
    conn.commit()


def load_presets(conn):
    from presets import StrategyConfig
    rows = conn.execute("SELECT name, payload FROM preset").fetchall()
    found = {row["name"]: StrategyConfig.from_dict(json.loads(row["payload"])) for row in rows}
    return {name: found.get(name, DEFAULTS[name]) for name in PRESET_ORDER}


def reset_preset(conn, name):
    save_preset(conn, DEFAULTS[name])


def add_snapshot(conn, *, venue, entity_key, grade, exchange_time_ms, received_time_ms,
                 connection_status, payload, error=None):
    cursor = conn.execute(
        "INSERT INTO snapshot (venue, entity_key, grade, exchange_time_ms, received_time_ms, "
        "connection_status, payload, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (venue, entity_key, grade, exchange_time_ms, received_time_ms, connection_status,
         json.dumps(payload, ensure_ascii=False), error),
    )
    conn.commit()
    return cursor.lastrowid


def recent_snapshots(conn, entity_key, limit=2):
    rows = conn.execute(
        "SELECT * FROM snapshot WHERE entity_key=? ORDER BY id DESC LIMIT ?",
        (entity_key, limit),
    ).fetchall()
    return [dict(row) | {"payload": json.loads(row["payload"])} for row in rows]


def add_alert(conn, entity_key, grade, message):
    conn.execute(
        "INSERT INTO alert (entity_key, grade, message, created_at) VALUES (?, ?, ?, ?)",
        (entity_key, grade, message, utc_now()),
    )
    conn.commit()


def list_alerts(conn, limit=30):
    return [dict(row) for row in conn.execute(
        "SELECT * FROM alert ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def set_favorite(conn, category, entity_key, label, enabled):
    if enabled:
        conn.execute(
            "INSERT OR REPLACE INTO favorite VALUES (?, ?, ?, ?)",
            (category, entity_key, label, utc_now()),
        )
    else:
        conn.execute("DELETE FROM favorite WHERE category=? AND entity_key=?", (category, entity_key))
    conn.commit()


def list_favorites(conn, category=None):
    if category:
        rows = conn.execute("SELECT * FROM favorite WHERE category=? ORDER BY created_at DESC", (category,))
    else:
        rows = conn.execute("SELECT * FROM favorite ORDER BY created_at DESC")
    return [dict(row) for row in rows.fetchall()]


def is_favorite(conn, category, entity_key):
    return conn.execute(
        "SELECT 1 FROM favorite WHERE category=? AND entity_key=?", (category, entity_key),
    ).fetchone() is not None


def add_researcher(conn, name, category, links, notes):
    if not name.strip():
        raise ValueError("研究者姓名不能为空")
    conn.execute(
        "INSERT INTO researcher (name, category, links, notes, created_at) VALUES (?, ?, ?, ?, ?)",
        (name.strip(), category.strip() or "未分类", links.strip(), notes.strip(), utc_now()),
    )
    conn.commit()


def list_researchers(conn, query=""):
    rows = conn.execute("SELECT * FROM researcher ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows if _matches(query, row["name"], row["category"], row["notes"], row["links"])]


def add_article(conn, title, url, category, summary_zh):
    if not url.strip().startswith(("https://", "http://")):
        raise ValueError("文章需要 http 或 https 原文链接")
    conn.execute(
        "INSERT INTO article (title, url, category, summary_zh, summary_source, created_at) "
        "VALUES (?, ?, ?, ?, '手工', ?)",
        (title.strip() or url.strip(), url.strip(), category.strip() or "未分类", summary_zh.strip(), utc_now()),
    )
    conn.commit()


def list_articles(conn, query="", category=""):
    rows = [dict(row) for row in conn.execute("SELECT * FROM article ORDER BY id DESC")]
    if category:
        rows = [row for row in rows if row["category"] == category]
    return [row for row in rows if _matches(query, row["title"], row["url"], row["summary_zh"], row["category"])]


def add_task(conn, title, detail):
    if not title.strip():
        raise ValueError("任务标题不能为空")
    conn.execute(
        "INSERT INTO task (title, state, detail, created_at) VALUES (?, '待办', ?, ?)",
        (title.strip(), detail.strip(), utc_now()),
    )
    conn.commit()


def set_task_state(conn, task_id, state):
    if state not in ("待办", "完成"):
        raise ValueError("任务状态无效")
    conn.execute("UPDATE task SET state=? WHERE id=?", (state, task_id))
    conn.commit()


def list_tasks(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM task ORDER BY id DESC").fetchall()]


def save_probe(conn, name, status, detail):
    conn.execute("INSERT OR REPLACE INTO probe VALUES (?, ?, ?, ?)", (name, status, detail[:500], utc_now()))
    conn.commit()


def latest_probe(conn, name):
    row = conn.execute("SELECT * FROM probe WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


def save_repo(conn, full_name, payload, error=None):
    conn.execute(
        "INSERT OR REPLACE INTO repo_cache VALUES (?, ?, ?, ?)",
        (full_name, json.dumps(payload, ensure_ascii=False), utc_now(), error),
    )
    conn.commit()


def load_repos(conn):
    return [dict(row) | {"payload": json.loads(row["payload"])} for row in conn.execute(
        "SELECT * FROM repo_cache ORDER BY full_name")]


def _matches(query, *fields):
    needle = query.strip().lower()
    if not needle:
        return True
    return any(needle in str(field).lower() for field in fields)
