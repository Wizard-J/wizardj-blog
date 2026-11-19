"""Owner-only SQLite storage for complete chat turns and imported context."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid
from contextlib import closing


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS turns_by_session ON turns (session_id, id);

CREATE TABLE IF NOT EXISTS turn_searches (
    turn_id INTEGER PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
    results TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def require_private(metadata, is_type, location):
    if not is_type(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise PermissionError(f"{location} must be owned by the assistant user and private")


class State:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_private(self.path.parent.lstat(), stat.S_ISDIR, "State directory")
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(self.path, flags, 0o600)
        try:
            require_private(os.fstat(fd), stat.S_ISREG, "State database")
        finally:
            os.close(fd)
        with closing(self.connect()) as connection:
            connection.executescript(SCHEMA)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def new_id():
        return str(uuid.uuid4())

    def list_sessions(self):
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT id, title, updated_at FROM sessions ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        sessions = [dict(row) for row in rows]
        return {"sessions": sessions, "latest_id": sessions[0]["id"] if sessions else None}

    def session(self, session_id):
        with closing(self.connect()) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT id, title FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            turns = connection.execute(
                """SELECT user_text, assistant_text, created_at, results
                   FROM turns LEFT JOIN turn_searches ON turn_searches.turn_id = turns.id
                   WHERE session_id = ? ORDER BY turns.id""",
                (session_id,),
            ).fetchall()
        messages = []
        for turn in turns:
            messages.extend((
                {"role": "user", "content": turn["user_text"], "created_at": turn["created_at"]},
                {"role": "assistant", "content": turn["assistant_text"], "created_at": turn["created_at"],
                 "searches": json.loads(turn["results"]) if turn["results"] else []},
            ))
        return {**dict(row), "messages": messages}

    def save_turn(self, session_id, user, assistant, searches=()):
        if not all(isinstance(value, str) and value.strip() for value in (session_id, user, assistant)):
            raise ValueError("A complete chat turn is required")
        title = " ".join(user.split())[:72]
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = now()
            connection.execute(
                """INSERT INTO sessions (id, title, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at""",
                (session_id, title, timestamp),
            )
            turn = connection.execute(
                "INSERT INTO turns (session_id, user_text, assistant_text, created_at) VALUES (?, ?, ?, ?)",
                (session_id, user, assistant, timestamp),
            )
            if searches:
                connection.execute("INSERT INTO turn_searches VALUES (?, ?)",
                                   (turn.lastrowid, json.dumps(searches, ensure_ascii=False)))
            return turn.lastrowid

    def turns_after(self, after_id=0, limit=100):
        if not isinstance(after_id, int) or after_id < 0 or not isinstance(limit, int) or limit < 1:
            raise ValueError("Invalid turn range")
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT id, session_id, user_text AS user, assistant_text AS assistant, created_at
                   FROM turns WHERE id > ? ORDER BY id LIMIT ?""",
                (after_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_context(self):
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT content, updated_at FROM context WHERE singleton = 1"
            ).fetchone()
        return dict(row) if row is not None else {"content": "", "updated_at": None}

    def set_context(self, content):
        if not isinstance(content, str):
            raise ValueError("Context must be text")
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = now()
            connection.execute(
                """INSERT INTO context (singleton, content, updated_at) VALUES (1, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE
                   SET content = excluded.content, updated_at = excluded.updated_at""",
                (content, timestamp),
            )
        return {"content": content, "updated_at": timestamp}
