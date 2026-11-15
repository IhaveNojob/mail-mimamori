"""SQLite storage: family / member / mail_item / mail_image / notification_log."""
import json
import sqlite3
import threading
from datetime import datetime

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS family (
  id INTEGER PRIMARY KEY,
  parent_name TEXT NOT NULL,
  photographer_code TEXT UNIQUE NOT NULL,
  family_code TEXT UNIQUE NOT NULL,
  history_token TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS member (
  id INTEGER PRIMARY KEY,
  family_id INTEGER NOT NULL REFERENCES family(id),
  line_user_id TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL,              -- owner | photographer | family
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_item (
  id INTEGER PRIMARY KEY,
  family_id INTEGER NOT NULL REFERENCES family(id),
  state TEXT NOT NULL,             -- collecting | analyzed
  sender TEXT, kind TEXT, summary TEXT, amount INTEGER, due_date TEXT, action TEXT,
  importance TEXT,                 -- 要対応 | 確認 | 捨ててよい
  status TEXT NOT NULL DEFAULT '未対応',  -- 未対応 | 対応済み | あとで | 不要
  raw_json TEXT,
  created_at TEXT NOT NULL,
  last_image_at TEXT NOT NULL,
  notified_at TEXT,
  trash_notified INTEGER NOT NULL DEFAULT 0,
  reminded_7 INTEGER NOT NULL DEFAULT 0,
  reminded_1 INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mail_image (
  id INTEGER PRIMARY KEY,
  mail_id INTEGER NOT NULL REFERENCES mail_item(id),
  path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_log (
  id INTEGER PRIMARY KEY,
  to_user TEXT NOT NULL,
  text TEXT NOT NULL,
  buttons_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS line_outbox (
  id INTEGER PRIMARY KEY,
  to_user TEXT NOT NULL,
  reply_token TEXT,
  message_json TEXT NOT NULL,
  state TEXT NOT NULL,             -- queued | sent
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS line_fetch (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  state TEXT NOT NULL,             -- queued | working | done
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_run (
  name TEXT PRIMARY KEY,
  last_date TEXT NOT NULL
);
"""


def now() -> datetime:
    return datetime.now(config.TZ)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            (config.DATA_DIR / "images").mkdir(exist_ok=True)
            _conn = sqlite3.connect(config.DATA_DIR / "app.db", check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
        return _conn


def reset_for_tests():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def q(sql: str, args=()) -> list[sqlite3.Row]:
    with _lock:
        return conn().execute(sql, args).fetchall()


def one(sql: str, args=()) -> sqlite3.Row | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql: str, args=()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def log_notification(to_user: str, text: str, buttons: list | None = None):
    ex(
        "INSERT INTO notification_log (to_user, text, buttons_json, created_at) VALUES (?,?,?,?)",
        (to_user, text, json.dumps(buttons, ensure_ascii=False) if buttons else None, now_iso()),
    )
