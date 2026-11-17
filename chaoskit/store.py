import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .rules import Rule


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY, time TEXT DEFAULT CURRENT_TIMESTAMP,
                    method TEXT, path TEXT, status INTEGER, ms INTEGER,
                    rule TEXT, outcome TEXT, reason TEXT
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=0.5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def rules(self):
        with self.connect() as db:
            return [Rule.model_validate_json(row["data"]) for row in db.execute("SELECT data FROM rules ORDER BY id")]

    def save(self, rule):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO rules VALUES (?, ?)", (rule.id, rule.model_dump_json()))

    def delete(self, rule_id):
        with self.connect() as db:
            db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))

    def log(self, method, path, status, ms, rule, outcome, reason):
        with self.connect() as db:
            db.execute(
                "INSERT INTO requests(method,path,status,ms,rule,outcome,reason) VALUES (?,?,?,?,?,?,?)",
                (method, path, status, ms, rule, outcome, reason),
            )
            db.execute("DELETE FROM requests WHERE id <= (SELECT MAX(id) - 1000 FROM requests)")

    def recent(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 20")]
