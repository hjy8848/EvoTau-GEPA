"""Atomic, durable accounting at the transport boundary, including failures."""

import json
import sqlite3
import time
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, path, limit=30_000):
        if limit > 30_000:
            raise ValueError("Global hard cap cannot exceed 30,000")
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, cap INTEGER)")
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (limit,))
            if db.execute("SELECT cap FROM settings").fetchone()[0] != limit:
                raise ValueError("Persistent budget limit cannot change on resume")
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY, role TEXT, model TEXT, started REAL,
                state TEXT, latency REAL, metadata TEXT)""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=60)

    def reserve(self, role, model):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            cap = db.execute("SELECT cap FROM settings").fetchone()[0]
            if used >= cap:
                raise BudgetExceeded(f"Request cap reached: {used}/{cap}")
            return db.execute(
                "INSERT INTO requests(role, model, started, state) VALUES (?, ?, ?, 'reserved')",
                (role, model, time.time()),
            ).lastrowid

    def finish(self, request_id, success, latency, metadata):
        with self.connect() as db:
            db.execute(
                "UPDATE requests SET state=?, latency=?, metadata=? WHERE id=?",
                ("success" if success else "failed", latency, json.dumps(metadata), request_id),
            )

    def summary(self):
        with self.connect() as db:
            counts = dict(db.execute("SELECT state, COUNT(*) FROM requests GROUP BY state"))
            cap = db.execute("SELECT cap FROM settings").fetchone()[0]
            usage = [json.loads(x[0] or "{}") for x in db.execute("SELECT metadata FROM requests")]
        return {
            "cap": cap,
            "reserved_or_sent": sum(counts.values()),
            "states": counts,
            "remaining": cap - sum(counts.values()),
            "cost": None,
            "prompt_tokens": sum(x.get("usage", {}).get("prompt_tokens", 0) for x in usage),
            "completion_tokens": sum(x.get("usage", {}).get("completion_tokens", 0) for x in usage),
            "cached_tokens": sum(
                x.get("usage", {}).get("prompt_tokens_details", {}).get("cached_tokens", 0)
                for x in usage
            ),
            "retries": 0,
            "reservations_uncertain_or_inflight": counts.get("reserved", 0),
        }
