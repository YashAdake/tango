"""SQLite store — the single source of truth for task, action and evidence state.

SQLite rather than Postgres because Tango diagnoses Docker and must not depend
on it (docs/04 ADR-002). WAL mode, ``synchronous=FULL``: the ledger's crash
guarantee is only as good as the durability of the write that precedes an
external call, so we pay the fsync.

``tango-core`` is the single writer (docs/17 C4). Concurrency between tasks is
handled by advisory resource locks in this module, not by database-level
concurrency.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task (
    id                  TEXT PRIMARY KEY,
    goal                TEXT NOT NULL,
    route               TEXT NOT NULL,
    playbook_id         TEXT,
    playbook_version    INTEGER,
    status              TEXT NOT NULL,
    privacy_class       TEXT NOT NULL,
    -- Capability freeze: computed BEFORE any untrusted content is retrieved.
    -- A call outside this set is refused, never escalated (docs/16 §10).
    frozen_tools        TEXT,
    frozen_scopes       TEXT,
    max_integrity       INTEGER NOT NULL DEFAULT 0,
    max_confidentiality INTEGER NOT NULL DEFAULT 0,
    trace_id            TEXT NOT NULL,
    surface             TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_status ON task(status);
CREATE INDEX IF NOT EXISTS idx_task_trace  ON task(trace_id);

CREATE TABLE IF NOT EXISTS action (
    id                TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL REFERENCES task(id),
    step_id           TEXT NOT NULL,
    tool              TEXT NOT NULL,
    args_canonical    TEXT NOT NULL,
    args_hash         TEXT NOT NULL,
    -- Derived and persisted BEFORE the provider call. Recovery queries by this
    -- key rather than re-sending (docs/16 §6).
    idempotency_key   TEXT NOT NULL UNIQUE,
    -- Does the provider itself deduplicate? Gmail: yes. Docker: no.
    -- Recovery behaviour branches on this.
    sink_idempotent   INTEGER NOT NULL DEFAULT 0,
    risk              INTEGER NOT NULL,
    status            TEXT NOT NULL,
    provider_ref      TEXT,
    raw_response      TEXT,
    policy_version    INTEGER NOT NULL DEFAULT 1,
    detail            TEXT,
    created_at        TEXT NOT NULL,
    committed_at      TEXT,
    settled_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_task   ON action(task_id);
CREATE INDEX IF NOT EXISTS idx_action_status ON action(status);

CREATE TABLE IF NOT EXISTS evidence (
    id           TEXT PRIMARY KEY,
    action_id    TEXT NOT NULL REFERENCES action(id),
    kind         TEXT NOT NULL,
    payload      TEXT NOT NULL,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_action ON evidence(action_id);

CREATE TABLE IF NOT EXISTS confirmation_request (
    id                TEXT PRIMARY KEY,
    action_id         TEXT NOT NULL REFERENCES action(id),
    nonce             TEXT NOT NULL UNIQUE,
    -- Binds the EXACT arguments authorised. If anything changed between
    -- proposal and confirmation the nonce no longer matches (TOCTOU defence).
    binds_args_hash   TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    consumed_at       TEXT,
    surface           TEXT,
    untrusted_sources TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_lock (
    resource   TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES task(id),
    acquired_at TEXT NOT NULL
);

-- Append-only. Never pruned in v1.
CREATE TABLE IF NOT EXISTS audit_event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    resource  TEXT,
    verdict   TEXT NOT NULL,
    trace_id  TEXT,
    detail    TEXT,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_event(trace_id);
"""


def utcnow() -> str:
    """Single source of time. ISO-8601, UTC, always."""
    return datetime.now(UTC).isoformat()


class Store:
    """Owns the SQLite connection and schema. One instance per process."""

    def __init__(self, path: Path | str = "data/tango.db") -> None:
        self.path = Path(path)
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            self.path,
            isolation_level=None,  # explicit transaction control
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    def _configure(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        # FULL, not NORMAL: a PROPOSED row must survive power loss, because the
        # external call happens after it. This is the whole crash guarantee.
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")

    def _migrate(self) -> None:
        self.conn.executescript(_SCHEMA)
        cur = self.conn.execute("SELECT value FROM schema_meta WHERE key='version'")
        row = cur.fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(row["value"]) != SCHEMA_VERSION:
            raise RuntimeError(
                f"schema version mismatch: db={row['value']} code={SCHEMA_VERSION}"
            )

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Explicit transaction. Rolls back on any exception."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def audit(
        self,
        actor: str,
        action: str,
        verdict: str,
        *,
        resource: str | None = None,
        trace_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append an audit row. Every refusal must leave one of these behind —
        a refusal nobody learns about is a near-miss you will repeat."""
        self.conn.execute(
            "INSERT INTO audit_event(actor, action, resource, verdict, trace_id, detail, ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (actor, action, resource, verdict, trace_id, detail, utcnow()),
        )

    def close(self) -> None:
        self.conn.close()
