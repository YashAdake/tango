"""The execution seam: registry + ledger + task lifecycle.

The store knows *state*, the registry knows *capabilities*, the ledger knows
*truth*, and the renderer knows *what may be said*. None of them know about each
other. This module is the only place that does, which keeps each testable alone
and makes the composition a single reviewable surface.

It also bridges one deliberate signature difference: a tool's verifier takes
``(result, args)`` because it needs the arguments to know what to go and check,
while the ledger only knows about ``result``. The binding happens here, once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from tango.ledger import DuplicateAction, Evidence, Ledger, ToolResult, VerifyResult
from tango.render import StepOutcome
from tango.store import Store, utcnow
from tango.tools import ToolCall, ToolRegistry
from tango.types import ActionStatus, PrivacyClass, TaskStatus


@dataclass
class Outcome:
    """Result of one executed step, in the vocabulary the renderer speaks."""

    action_id: str | None
    status: ActionStatus
    detail: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    deduplicated: bool = False
    """True when an identical action already existed — the idempotency
    guarantee working, not an error."""

    def to_step(self, label: str) -> StepOutcome:
        return StepOutcome(
            label=label,
            status=self.status,
            detail=self.detail,
            evidence=self.evidence[0].payload if self.evidence else "",
        )


class Executor:
    """Runs tool calls through the ledger and keeps task state honest."""

    def __init__(self, ledger: Ledger, registry: ToolRegistry, store: Store) -> None:
        self.ledger = ledger
        self.registry = registry
        self.store = store

    # ------------------------------------------------------------------ tasks

    def new_task(
        self,
        goal: str,
        *,
        surface: str = "cli",
        route: str = "playbook",
        privacy_class: PrivacyClass = PrivacyClass.LOCAL_ONLY,
        playbook_id: str | None = None,
        playbook_version: int | None = None,
        trace_id: str | None = None,
    ) -> str:
        """Open a task. Defaults to ``LOCAL_ONLY`` — the safe direction; a task
        is widened deliberately, never by omission."""
        task_id = str(uuid.uuid4())
        now = utcnow()
        with self.store.tx() as conn:
            conn.execute(
                "INSERT INTO task(id, goal, route, playbook_id, playbook_version, status,"
                " privacy_class, trace_id, surface, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id, goal, route, playbook_id, playbook_version,
                    TaskStatus.RUNNING, privacy_class,
                    trace_id or str(uuid.uuid4()), surface, now, now,
                ),
            )
        return task_id

    def task_status(self, task_id: str) -> TaskStatus:
        """Derive task status from the ledger — never from what anyone claimed.

        Any settled action that is not VERIFIED means the task did not fully
        succeed, and ``PARTIAL`` is a real answer rather than a rounding error.
        """
        rows = self.ledger.actions_for_task(task_id)
        if not rows:
            return TaskStatus.RUNNING

        statuses = [ActionStatus(r["status"]) for r in rows]
        if any(not s.is_terminal for s in statuses):
            return TaskStatus.RUNNING
        verified = sum(1 for s in statuses if s is ActionStatus.VERIFIED)
        if verified == len(statuses):
            return TaskStatus.COMPLETED
        if verified == 0:
            return TaskStatus.FAILED
        return TaskStatus.PARTIAL

    def settle_task(self, task_id: str) -> TaskStatus:
        status = self.task_status(task_id)
        self.store.conn.execute(
            "UPDATE task SET status=?, updated_at=? WHERE id=?",
            (status, utcnow(), task_id),
        )
        return status

    # -------------------------------------------------------------- execution

    def run(self, task_id: str, call: ToolCall) -> Outcome:
        """Execute one tool call end-to-end: propose, commit, verify.

        Every refusal path leaves an audit row. A refusal nobody can later find
        is a near-miss you are guaranteed to repeat.
        """
        try:
            tool = self.registry.get(call.tool)
        except KeyError:
            self.store.audit(
                actor="executor",
                action=f"invoke:{call.tool}",
                verdict=ActionStatus.DENIED,
                resource=task_id,
                detail="tool is not registered",
            )
            return Outcome(
                action_id=None,
                status=ActionStatus.DENIED,
                detail=f"'{call.tool}' is not a tool I have",
            )

        try:
            action_id = self.ledger.propose(
                task_id=task_id,
                step_id=call.step_id,
                tool=tool.name,
                args=call.args,
                risk=tool.risk,
                sink_idempotent=tool.sink_idempotent,
            )
        except DuplicateAction as dup:
            # Not a failure: the same step was already proposed. Report the
            # existing outcome rather than repeating the effect.
            existing = self.ledger.get(dup.action_id)
            return Outcome(
                action_id=dup.action_id,
                status=ActionStatus(existing["status"]),
                detail=existing["detail"] or "already done in this task",
                evidence=self.ledger.evidence_for(dup.action_id),
                deduplicated=True,
            )

        args = dict(call.args)

        def _execute() -> ToolResult:
            return tool.executor(**args)

        def _verify(result: ToolResult) -> VerifyResult:
            assert tool.verifier is not None  # guarded below
            return tool.verifier(result, args)

        status = self.ledger.commit(
            action_id,
            executor=_execute,
            verifier=_verify if tool.verifier is not None else None,
        )
        row = self.ledger.get(action_id)
        return Outcome(
            action_id=action_id,
            status=status,
            detail=row["detail"] or "",
            evidence=self.ledger.evidence_for(action_id),
        )

    def run_all(self, task_id: str, calls: list[tuple[str, ToolCall]]) -> list[StepOutcome]:
        """Run a labelled sequence of calls, returning renderer-ready steps.

        Deliberately does not stop on failure — ``on_fail`` policy belongs to the
        playbook engine (S0.6). Here, every step reports its own truth.
        """
        steps: list[StepOutcome] = []
        for label, call in calls:
            steps.append(self.run(task_id, call).to_step(label))
        self.settle_task(task_id)
        return steps

    # ------------------------------------------------------------- inspection

    def task_record(self, task_id: str) -> dict[str, Any]:
        row = self.store.conn.execute("SELECT * FROM task WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"no such task: {task_id}")
        return dict(row)
