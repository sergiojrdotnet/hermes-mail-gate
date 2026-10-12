import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from mail_gate.paths import data_dir

EVENT_RETENTION_DAYS = 90
SEEN_RETENTION_DAYS = 7
GLOBAL_BOT = "*"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    bot TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('address', 'domain')),
    value TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('allow', 'block', 'trust')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (bot, scope, value)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    bot TEXT NOT NULL,
    route TEXT NOT NULL,
    outcome TEXT NOT NULL,
    sender TEXT,
    sender_domain TEXT,
    subject TEXT,
    message_id TEXT,
    size INTEGER,
    attachments INTEGER,
    auth TEXT
);
CREATE INDEX IF NOT EXISTS events_bot_at ON events (bot, at);
CREATE TABLE IF NOT EXISTS held (
    id INTEGER PRIMARY KEY,
    bot TEXT NOT NULL,
    sender TEXT NOT NULL,
    sender_domain TEXT NOT NULL,
    message_id TEXT,
    subject TEXT,
    received_at TEXT NOT NULL,
    auth TEXT,
    mail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS held_bot_sender ON held (bot, sender);
CREATE TABLE IF NOT EXISTS seen (
    bot TEXT NOT NULL,
    route TEXT NOT NULL,
    message_id TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (bot, route, message_id)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or data_dir() / "mail-gate.db"
        self.conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(SCHEMA)
        self.path.chmod(0o600)

    def close(self) -> None:
        self.conn.close()

    def purge(self) -> None:
        now = datetime.now(timezone.utc)
        self.conn.execute("DELETE FROM events WHERE at < ?",
                          ((now - timedelta(days=EVENT_RETENTION_DAYS)).isoformat(timespec="seconds"),))
        self.conn.execute("DELETE FROM seen WHERE at < ?",
                          ((now - timedelta(days=SEEN_RETENTION_DAYS)).isoformat(timespec="seconds"),))

    def mark_seen(self, bot: str, route: str, message_id: str) -> bool:
        cursor = self.conn.execute("INSERT OR IGNORE INTO seen (bot, route, message_id, at) VALUES (?, ?, ?, ?)",
                                   (bot, route, message_id, now_iso()))
        return cursor.rowcount == 1

    def rules_for(self, bot: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM rules WHERE bot IN (?, ?)", (bot, GLOBAL_BOT)).fetchall()

    def all_rules(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM rules ORDER BY bot, scope, value").fetchall()
        return [dict(row) for row in rows]

    def set_rule(self, bot: str, scope: str, value: str, action: str) -> None:
        self.conn.execute(
            "INSERT INTO rules (bot, scope, value, action, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (bot, scope, value) DO UPDATE SET action = excluded.action, created_at = excluded.created_at",
            (bot, scope, value.lower(), action, now_iso()))

    def delete_rule(self, bot: str, scope: str, value: str) -> bool:
        cursor = self.conn.execute("DELETE FROM rules WHERE bot = ? AND scope = ? AND value = ?",
                                   (bot, scope, value.lower()))
        return cursor.rowcount > 0

    def log(self, bot: str, route: str, outcome: str, mail: dict[str, Any], auth: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (at, bot, route, outcome, sender, sender_domain, subject, message_id, size, attachments,"
            " auth) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), bot, route, outcome, mail.get("from"), mail.get("from_domain"), mail.get("subject"),
             mail.get("message_id"), mail.get("size"), len(mail.get("attachments") or []), auth))

    def hold(self, bot: str, mail: dict[str, Any], auth: str) -> None:
        self.conn.execute(
            "INSERT INTO held (bot, sender, sender_domain, message_id, subject, received_at, auth, mail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (bot, mail["from"], mail["from_domain"], mail.get("message_id"), mail.get("subject"), now_iso(), auth,
             json.dumps(mail)))

    def held_for(self, bot: str, sender: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM held WHERE bot = ? AND sender = ? ORDER BY id", (bot, sender)).fetchall()

    def held_by_id(self, held_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM held WHERE id = ?", (held_id,)).fetchone()

    def held_matching(self, bot: str, scope: str, value: str) -> list[sqlite3.Row]:
        column = "sender" if scope == "address" else "sender_domain"
        return self.conn.execute(f"SELECT * FROM held WHERE (bot = ? OR ? = ?) AND {column} = ? ORDER BY id",
                                 (bot, bot, GLOBAL_BOT, value.lower())).fetchall()

    def drop_held(self, held_id: int) -> None:
        self.conn.execute("DELETE FROM held WHERE id = ?", (held_id,))

    def pending(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT bot, sender, sender_domain, COUNT(*) AS messages, MIN(received_at) AS first_at,"
            " MAX(received_at) AS last_at, MAX(auth) AS auth,"
            " (SELECT subject FROM held latest WHERE latest.bot = held.bot AND latest.sender = held.sender"
            "  ORDER BY latest.id DESC LIMIT 1) AS last_subject"
            " FROM held GROUP BY bot, sender ORDER BY last_at DESC").fetchall()
        return [dict(row) for row in rows]

    def events(self, bot: Optional[str] = None, outcome: Optional[str] = None, limit: int = 200,
               before_id: Optional[int] = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if bot:
            clauses.append("bot = ?")
            params.append(bot)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        if before_id:
            clauses.append("id < ?")
            params.append(before_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        return [dict(row) for row in rows]

    def counters(self, period: str, buckets: int, bot: Optional[str] = None) -> list[dict[str, Any]]:
        bucket_sql = {
            "day": "substr(at, 1, 10)",
            "week": "strftime('%Y-W%W', at)",
            "month": "substr(at, 1, 7)",
        }[period]
        span = {"day": buckets, "week": buckets * 7, "month": buckets * 31}[period]
        since = (datetime.now(timezone.utc) - timedelta(days=span)).isoformat(timespec="seconds")
        params: list[Any] = [since]
        bot_clause = ""
        if bot:
            bot_clause = "AND bot = ?"
            params.append(bot)
        rows = self.conn.execute(
            f"SELECT {bucket_sql} AS bucket, bot, outcome, COUNT(*) AS total FROM events"
            f" WHERE at >= ? {bot_clause} GROUP BY bucket, bot, outcome ORDER BY bucket", params).fetchall()
        return [dict(row) for row in rows]
