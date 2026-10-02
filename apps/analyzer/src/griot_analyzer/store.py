"""Local, resumable analyzer state in SQLite (~/.griot/analyzer.sqlite)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from griot_analyzer.identify import FileIdentity

DEFAULT_HOME = Path.home() / ".griot"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    sha1        TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    identity    TEXT NOT NULL,             -- FileIdentity json
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed | skipped
    features    TEXT,                      -- TrackFeatures json (by alias)
    error       TEXT,
    submitted   INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS files_status ON files(status);
"""


class Store:
    def __init__(self, home: Path = DEFAULT_HOME) -> None:
        home.mkdir(parents=True, exist_ok=True)
        self.home = home
        self.db = sqlite3.connect(home / "analyzer.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def known(self, sha1: str) -> bool:
        return self.db.execute("SELECT 1 FROM files WHERE sha1 = ?", (sha1,)).fetchone() is not None

    def add(self, ident: FileIdentity) -> None:
        self.db.execute(
            "INSERT INTO files (sha1, path, identity) VALUES (?, ?, ?) "
            "ON CONFLICT(sha1) DO UPDATE SET path = excluded.path",
            (ident.sha1, ident.path, json.dumps(asdict(ident))),
        )
        self.db.commit()

    def pending(self, limit: int | None = None, retry_failed: bool = False) -> list[sqlite3.Row]:
        statuses = ("pending", "failed") if retry_failed else ("pending",)
        q = f"SELECT * FROM files WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY path"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self.db.execute(q, statuses).fetchall()

    def done(self, sha1: str, features_json: str) -> None:
        self.db.execute(
            "UPDATE files SET status='done', features=?, error=NULL, submitted=0, "
            "updated_at=CURRENT_TIMESTAMP WHERE sha1=?",
            (features_json, sha1),
        )
        self.db.commit()

    def failed(self, sha1: str, error: str) -> None:
        self.db.execute(
            "UPDATE files SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP WHERE sha1=?",
            (error[:2000], sha1),
        )
        self.db.commit()

    def unsubmitted(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM files WHERE status='done' AND submitted=0").fetchall()

    def mark_submitted(self, sha1s: list[str]) -> None:
        self.db.executemany("UPDATE files SET submitted=1 WHERE sha1=?", [(s,) for s in sha1s])
        self.db.commit()

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) n FROM files GROUP BY status").fetchall()
        c = {r["status"]: r["n"] for r in rows}
        c["submitted"] = self.db.execute("SELECT COUNT(*) FROM files WHERE submitted=1").fetchone()[0]
        return c

    def all_features(self) -> list[str]:
        return [r["features"] for r in self.db.execute("SELECT features FROM files WHERE status='done'")]
