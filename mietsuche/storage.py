"""SQLite-Datenbank für gesehene Anzeigen und deren Status.

Die Übersichts-App liest später dieselbe Datenbank.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .filters import Verdict
from .models import Listing

STATUSES = ("neu", "angefragt", "antwort", "besichtigung", "zusage", "abgesagt", "uninteressant")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    address      TEXT,
    city         TEXT,
    zip_code     TEXT,
    rooms        REAL,
    living_area  REAL,
    rent_cold    REAL,
    rent_warm    REAL,
    image_url    TEXT,
    contact_name TEXT,
    matches      INTEGER NOT NULL,
    checks       TEXT NOT NULL,
    data         TEXT NOT NULL,
    letter       TEXT,
    status       TEXT NOT NULL DEFAULT 'neu',
    notes        TEXT NOT NULL DEFAULT '',
    first_seen   TEXT NOT NULL,
    notified_at  TEXT,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_matches ON listings(matches, first_seen);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint   TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        # Suchlauf und Web-App laufen in verschiedenen Threads.
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)

    def seen(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM listings WHERE key = ?", (key,)).fetchone() is not None

    def add(self, listing: Listing, verdict: Verdict, letter: str | None) -> None:
        data = asdict(listing)
        data.pop("raw", None)
        now = _now()
        self.db.execute(
            """INSERT OR IGNORE INTO listings
               (key, source, url, title, address, city, zip_code, rooms, living_area,
                rent_cold, rent_warm, image_url, contact_name, matches, checks, data,
                letter, first_seen, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing.key, listing.source, listing.url, listing.title, listing.address,
                listing.city, listing.zip_code, listing.rooms, listing.living_area,
                listing.rent_cold, listing.warm_rent_estimate[0], listing.image_url,
                listing.contact_name, int(verdict.matches),
                json.dumps(verdict.as_dict(), ensure_ascii=False),
                json.dumps(data, ensure_ascii=False), letter, now, now,
            ),
        )
        self.db.commit()

    def mark_notified(self, key: str) -> None:
        now = _now()
        self.db.execute(
            "UPDATE listings SET notified_at = ?, updated_at = ? WHERE key = ?", (now, now, key)
        )
        self.db.commit()

    def set_status(self, key: str, status: str, notes: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unbekannter Status: {status}")
        params: list = [status, _now()]
        sql = "UPDATE listings SET status = ?, updated_at = ?"
        if notes is not None:
            sql += ", notes = ?"
            params.append(notes)
        self.db.execute(sql + " WHERE key = ?", (*params, key))
        self.db.commit()

    def pending_notifications(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM listings WHERE matches = 1 AND notified_at IS NULL ORDER BY first_seen"
        ).fetchall()

    def matches(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM listings WHERE matches = 1 ORDER BY first_seen DESC"
        ).fetchall()

    def priced(self) -> list[sqlite3.Row]:
        """Alle gesehenen Anzeigen mit Kaltmiete und Fläche, für den Preisvergleich."""
        return self.db.execute(
            "SELECT key, title, data, rent_cold, living_area, first_seen FROM listings "
            "WHERE rent_cold IS NOT NULL AND living_area > 0"
        ).fetchall()

    def update_data(self, key: str, data: dict) -> None:
        self.db.execute(
            "UPDATE listings SET data = ?, updated_at = ? WHERE key = ?",
            (json.dumps(data, ensure_ascii=False), _now(), key),
        )
        self.db.commit()

    def update_url(self, key: str, url: str) -> None:
        """Portale ändern manchmal die Adresse einer Anzeige; dann die neue merken."""
        row = self.db.execute("SELECT url, data FROM listings WHERE key = ?", (key,)).fetchone()
        if row is None or row["url"] == url:
            return
        data = json.loads(row["data"])
        data["url"] = url
        self.db.execute("UPDATE listings SET url = ?, data = ?, updated_at = ? WHERE key = ?",
                        (url, json.dumps(data, ensure_ascii=False), _now(), key))
        self.db.commit()

    def get(self, key: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM listings WHERE key = ?", (key,)).fetchone()

    def add_push_subscription(self, subscription: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO push_subscriptions (endpoint, data, created_at) VALUES (?, ?, ?)",
            (subscription["endpoint"], json.dumps(subscription), _now()),
        )
        self.db.commit()

    def remove_push_subscription(self, endpoint: str) -> None:
        self.db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        self.db.commit()

    def push_subscriptions(self) -> list[dict]:
        rows = self.db.execute("SELECT data FROM push_subscriptions").fetchall()
        return [json.loads(r["data"]) for r in rows]
