"""The effect ledger — two-phase commit for every side-effecting action.

The measured problem this exists to solve: 45-76% of agent failures are the
agent claiming success when it failed, dropping to ~3% where independent state
verification exists (docs/11 Finding 1). Everything here serves that number.

Lifecycle::

    propose()   durable row + idempotency key, fsync'd BEFORE anything external
       |
    policy      AUTO | UNDO_WINDOW | CONFIRM | DENY
       |
    commit()    status=COMMITTING, then the provider call
       |
    verify()    independent postcondition check -> VERIFIED | REFUTED | UNVERIFIABLE

The crash-critical window is between the ``COMMITTING`` write and the provider
response. A row found in that state on startup means "an external effect may or
may not have landed" — :meth:`Ledger.recover` reconciles it against the provider
by idempotency key and **never blindly retries** (verify-before-retry).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tango.store import Store, utcnow
from tango.types import ActionStatus, Risk, VerifyStatus


def canonical_args(args: dict[str, Any]) -> str:
    """Deterministic serialization. Two logically identical calls must produce
    byte-identical output, or the idempotency key is worthless."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)


def idempotency_key(task_id: str, step_id: str, tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{task_id}|{step_id}|{tool}|{canonical_args(args)}".encode()
    ).hexdigest()


@dataclass(frozen=True)
class Evidence:
    """A fact collected by a verifier. Stored, replayable, quotable to the user."""

    kind: str
    payload: str


@dataclass(frozen=True)
class VerifyResult:
    """What a verifier returns.

    ``UNVERIFIABLE`` is not a failure — it means no independent check existed.
    The renderer speaks it differently from ``VERIFIED`` and that distinction is
    the product.
    """

    status: VerifyStatus
    evidence: list[Evidence] = field(default_factory=list)
    detail: str = ""


@dataclass(frozen=True)
class ToolResult:
    """What a tool adapter returns from the provider call."""

    ok: bool
    provider_ref: str | None = None
    """Message ID, container ID, PID — whatever the verifier will check against."""
    raw: str = ""
    summary: str = ""


class LedgerError(RuntimeError):
    pass


class DuplicateAction(LedgerError):
    """Raised when an identical action has already been proposed.

    Not an error condition in itself — it is the idempotency guarantee working.
    A double hotkey press lands here and becomes a no-op.
    """

    def __init__(self, action_id: str, status: ActionStatus) -> None:
        super().__init__(f"action {action_id} already exists with status {status}")
        self.action_id = action_id
        self.status = status


class Ledger:
    """Records and settles every side-effecting action."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # ---------------------------------------------------------------- propose

    def propose(
        self,
        *,
        task_id: str,
        step_id: str,
        tool: str,
        args: dict[str, Any],
        risk: Risk,
        sink_idempotent: bool = False,
    ) -> str:
        """Durably record intent. Returns the action id.

        This write is fsync'd before any external call happens. That ordering is
        the entire crash guarantee: on restart we can always tell the difference
        between "never started" and "may have landed".
        """
        key = idempotency_key(task_id, step_id, tool, args)
        canon = canonical_args(args)
        args_hash = hashlib.sha256(canon.encode()).hexdigest()

        existing = self.store.conn.execute(
            "SELECT id, status FROM action WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing is not None:
            raise DuplicateAction(existing["id"], ActionStatus(existing["status"]))

        action_id = str(uuid.uuid4())
        with self.store.tx() as conn:
            conn.execute(
                "INSERT INTO action("
                " id, task_id, step_id, tool, args_canonical, args_hash,"
                " idempotency_key, sink_idempotent, risk, status, created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    task_id,
                    step_id,
                    tool,
                    canon,
                    args_hash,
                    key,
                    int(sink_idempotent),
                    int(risk),
                    ActionStatus.PROPOSED,
                    utcnow(),
                ),
            )
        return action_id

    # ----------------------------------------------------------------- commit

    def commit(
        self,
        action_id: str,
        executor: Callable[[], ToolResult],
        verifier: Callable[[ToolResult], VerifyResult] | None = None,
    ) -> ActionStatus:
        """Run the provider call, then verify it independently.

        ``executor`` performs the external effect. ``verifier`` checks the world
        afterwards — and must not derive its answer from ``executor``'s return
        value, or it is not verification (enforced by contract tests).

        Absence of a verifier yields ``UNVERIFIABLE``, never ``VERIFIED``.
        """
        row = self._require(action_id)
        status = ActionStatus(row["status"])
        if status not in (ActionStatus.PROPOSED, ActionStatus.CONFIRMED, ActionStatus.UNDO_WINDOW):
            raise LedgerError(f"cannot commit action in state {status}")

        # Mark the crash-critical window open before touching anything external.
        self._set_status(action_id, ActionStatus.COMMITTING, committed_at=utcnow())

        try:
            result = executor()
        except Exception as exc:
            self._settle(
                action_id,
                ActionStatus.REFUTED,
                detail=f"tool raised: {exc.__class__.__name__}: {exc}",
            )
            return ActionStatus.REFUTED

        self.store.conn.execute(
            "UPDATE action SET provider_ref=?, raw_response=? WHERE id=?",
            (result.provider_ref, result.raw, action_id),
        )

        if not result.ok:
            self._settle(action_id, ActionStatus.REFUTED, detail=result.summary)
            return ActionStatus.REFUTED

        if verifier is None:
            self._settle(
                action_id,
                ActionStatus.UNVERIFIABLE,
                detail="no verifier declared for this tool",
            )
            return ActionStatus.UNVERIFIABLE

        try:
            verdict = verifier(result)
        except Exception as exc:
            # The check itself could not run — Docker unreachable, network gone,
            # a bug in the verifier. That is not evidence the action succeeded,
            # and it is not evidence it failed. Say exactly that.
            self._settle(
                action_id,
                ActionStatus.UNVERIFIABLE,
                detail=f"could not verify: {exc.__class__.__name__}: {exc}",
            )
            return ActionStatus.UNVERIFIABLE

        self._record_evidence(action_id, verdict.evidence)
        final = {
            VerifyStatus.VERIFIED: ActionStatus.VERIFIED,
            VerifyStatus.REFUTED: ActionStatus.REFUTED,
            VerifyStatus.UNVERIFIABLE: ActionStatus.UNVERIFIABLE,
        }[verdict.status]
        self._settle(action_id, final, detail=verdict.detail)
        return final

    # ---------------------------------------------------------------- recover

    def recover(
        self,
        probe: Callable[[str, str, str], VerifyResult | None] | None = None,
    ) -> list[tuple[str, ActionStatus]]:
        """Reconcile every in-flight action left by a crash.

        Called on startup, before any new work is accepted. For each row stuck in
        ``COMMITTING`` the effect may or may not have landed, so we ask the
        provider — we never re-send. Without a probe the honest answer is
        ``UNVERIFIABLE``.

        ``probe(tool, idempotency_key, args_canonical)`` returns the observed
        outcome, or ``None`` if the provider cannot answer.
        """
        rows = self.store.conn.execute(
            "SELECT id, tool, idempotency_key, args_canonical FROM action WHERE status=?",
            (ActionStatus.COMMITTING,),
        ).fetchall()

        settled: list[tuple[str, ActionStatus]] = []
        for row in rows:
            verdict = (
                probe(row["tool"], row["idempotency_key"], row["args_canonical"])
                if probe is not None
                else None
            )
            if verdict is None:
                final = ActionStatus.UNVERIFIABLE
                detail = "interrupted mid-commit; provider could not be queried"
                evidence: list[Evidence] = []
            else:
                final = {
                    VerifyStatus.VERIFIED: ActionStatus.VERIFIED,
                    VerifyStatus.REFUTED: ActionStatus.REFUTED,
                    VerifyStatus.UNVERIFIABLE: ActionStatus.UNVERIFIABLE,
                }[verdict.status]
                detail = verdict.detail or "reconciled after interrupted commit"
                evidence = verdict.evidence

            self._record_evidence(row["id"], evidence)
            self._settle(row["id"], final, detail=detail)
            self.store.audit(
                actor="ledger.recover",
                action=f"reconcile:{row['tool']}",
                verdict=final,
                resource=row["id"],
                detail=detail,
            )
            settled.append((row["id"], final))
        return settled

    # ------------------------------------------------------------------ reads

    def get(self, action_id: str) -> dict[str, Any]:
        return dict(self._require(action_id))

    def evidence_for(self, action_id: str) -> list[Evidence]:
        rows = self.store.conn.execute(
            "SELECT kind, payload FROM evidence WHERE action_id=? ORDER BY collected_at",
            (action_id,),
        ).fetchall()
        return [Evidence(kind=r["kind"], payload=r["payload"]) for r in rows]

    def actions_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.store.conn.execute(
            "SELECT * FROM action WHERE task_id=? ORDER BY created_at", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- internal

    def _require(self, action_id: str) -> Any:
        row = self.store.conn.execute(
            "SELECT * FROM action WHERE id=?", (action_id,)
        ).fetchone()
        if row is None:
            raise LedgerError(f"no such action: {action_id}")
        return row

    def _set_status(self, action_id: str, status: ActionStatus, **cols: str) -> None:
        assignments = "".join(f", {k}=?" for k in cols)
        self.store.conn.execute(
            f"UPDATE action SET status=?{assignments} WHERE id=?",
            (status, *cols.values(), action_id),
        )

    def _settle(self, action_id: str, status: ActionStatus, *, detail: str = "") -> None:
        self.store.conn.execute(
            "UPDATE action SET status=?, detail=?, settled_at=? WHERE id=?",
            (status, detail, utcnow(), action_id),
        )

    def _record_evidence(self, action_id: str, evidence: list[Evidence]) -> None:
        for ev in evidence:
            self.store.conn.execute(
                "INSERT INTO evidence(id, action_id, kind, payload, collected_at) "
                "VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), action_id, ev.kind, ev.payload, utcnow()),
            )
