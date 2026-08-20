"""Undo windows and pending confirmations — the friction layer.

An assistant that needs a tap for everything useful is slower than doing the
task yourself, and gets abandoned in week three. That is the most common way
personal-agent projects die, so this module exists to make safety cheap rather
than merely present (docs/04 ADR-007).

Two mechanisms:

* **Undo window** — the action is scheduled, announced, and executes after a
  cancellable delay. Preferred wherever a compensate path exists, because
  reversible-by-construction protects against *Tango* being wrong, not only
  against you being wrong.
* **Pending confirmation** — the action waits for an explicit nonce. Used when
  there is no way back, when untrusted content is in the task, or at R4.

Both are durable. A window that only exists in memory silently becomes
"executed immediately" the moment the process restarts, which is the failure
mode this whole system is built to avoid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tango.store import Store, utcnow
from tango.types import ActionStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_action (
    action_id    TEXT PRIMARY KEY REFERENCES action(id),
    task_id      TEXT NOT NULL,
    tool         TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    label        TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- 'undo_window' | 'confirm'
    nonce        TEXT,
    execute_at   TEXT,                   -- undo windows only
    expires_at   TEXT,
    reason       TEXT,
    untrusted    TEXT,
    created_at   TEXT NOT NULL,
    resolved_at  TEXT,
    resolution   TEXT                    -- 'executed' | 'cancelled' | 'expired'
);
CREATE INDEX IF NOT EXISTS idx_pending_task ON pending_action(task_id);
"""


@dataclass(frozen=True)
class Pending:
    action_id: str
    task_id: str
    tool: str
    args: dict[str, Any]
    label: str
    kind: str
    nonce: str | None
    execute_at: str | None
    expires_at: str | None
    reason: str
    untrusted: tuple[str, ...]

    @property
    def seconds_left(self) -> float:
        """Time until an undo window fires. Negative means it is due."""
        if not self.execute_at:
            return 0.0
        return (datetime.fromisoformat(self.execute_at) - datetime.now(UTC)).total_seconds()

    @property
    def is_due(self) -> bool:
        return bool(self.execute_at) and self.seconds_left <= 0

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.fromisoformat(self.expires_at) <= datetime.now(UTC)

    def describe(self) -> str:
        if self.kind == "undo_window":
            left = self.seconds_left
            when = "now" if left <= 0 else f"in {left:.0f}s"
            return f"{self.label} — running {when}"
        return f"{self.label} — waiting for you"


class PendingQueue:
    """Durable store of actions that have not run yet."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.store.conn.executescript(SCHEMA)

    # -------------------------------------------------------------- creation

    def hold(
        self,
        *,
        action_id: str,
        task_id: str,
        tool: str,
        args: dict[str, Any],
        label: str,
        kind: str,
        reason: str,
        nonce: str | None = None,
        undo_seconds: int = 0,
        ttl_seconds: int = 300,
        untrusted: tuple[str, ...] = (),
    ) -> Pending:
        now = datetime.now(UTC)
        execute_at = (
            (now + timedelta(seconds=undo_seconds)).isoformat()
            if kind == "undo_window" else None
        )
        expires_at = (
            (now + timedelta(seconds=ttl_seconds)).isoformat()
            if kind == "confirm" else None
        )

        self.store.conn.execute(
            "INSERT OR REPLACE INTO pending_action("
            " action_id, task_id, tool, args_json, label, kind, nonce, execute_at,"
            " expires_at, reason, untrusted, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (action_id, task_id, tool, json.dumps(args, default=str), label, kind, nonce,
             execute_at, expires_at, reason, json.dumps(list(untrusted)), utcnow()),
        )
        return self._row_to_pending(
            self.store.conn.execute(
                "SELECT * FROM pending_action WHERE action_id=?", (action_id,)
            ).fetchone()
        )

    # ----------------------------------------------------------------- reads

    def outstanding(self) -> list[Pending]:
        rows = self.store.conn.execute(
            "SELECT * FROM pending_action WHERE resolved_at IS NULL ORDER BY created_at"
        ).fetchall()
        return [self._row_to_pending(r) for r in rows]

    def by_nonce(self, nonce: str) -> Pending | None:
        row = self.store.conn.execute(
            "SELECT * FROM pending_action WHERE nonce=? AND resolved_at IS NULL", (nonce,)
        ).fetchone()
        return self._row_to_pending(row) if row else None

    def due(self) -> list[Pending]:
        """Undo windows whose timer has elapsed."""
        return [p for p in self.outstanding() if p.kind == "undo_window" and p.is_due]

    # ------------------------------------------------------------ resolution

    def resolve(self, action_id: str, resolution: str) -> None:
        self.store.conn.execute(
            "UPDATE pending_action SET resolved_at=?, resolution=? WHERE action_id=?",
            (utcnow(), resolution, action_id),
        )

    def cancel(self, action_id: str) -> bool:
        """Cancel before the window closes. The action never runs."""
        row = self.store.conn.execute(
            "SELECT * FROM pending_action WHERE action_id=? AND resolved_at IS NULL",
            (action_id,),
        ).fetchone()
        if row is None:
            return False
        self.resolve(action_id, "cancelled")
        self.store.conn.execute(
            "UPDATE action SET status=?, detail=?, settled_at=? WHERE id=?",
            (ActionStatus.CANCELLED, "cancelled during the undo window", utcnow(), action_id),
        )
        self.store.audit(actor="user", action=f"cancel:{row['tool']}", verdict="CANCELLED",
                         resource=action_id, detail=row["label"])
        return True

    def cancel_all(self) -> int:
        """Panic control: stop everything that has not run yet."""
        return sum(1 for p in self.outstanding() if self.cancel(p.action_id))

    def expire_stale(self) -> list[Pending]:
        """Retire confirmations nobody answered.

        Expiry is a terminal state with a visible message, never a silent drop —
        silent expiry is how users learn the system cannot be trusted to
        remember what they asked for.
        """
        expired: list[Pending] = []
        for pending in self.outstanding():
            if pending.kind != "confirm" or not pending.is_expired:
                continue
            self.resolve(pending.action_id, "expired")
            self.store.conn.execute(
                "UPDATE action SET status=?, detail=?, settled_at=? WHERE id=?",
                (ActionStatus.EXPIRED, "the confirmation timed out; nothing ran",
                 utcnow(), pending.action_id),
            )
            expired.append(pending)
        return expired

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _row_to_pending(row: Any) -> Pending:
        return Pending(
            action_id=row["action_id"], task_id=row["task_id"], tool=row["tool"],
            args=json.loads(row["args_json"]), label=row["label"], kind=row["kind"],
            nonce=row["nonce"], execute_at=row["execute_at"], expires_at=row["expires_at"],
            reason=row["reason"] or "", untrusted=tuple(json.loads(row["untrusted"] or "[]")),
        )
