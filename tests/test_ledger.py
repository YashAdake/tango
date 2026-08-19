"""S0.3 acceptance: the ledger must never lose an effect and never duplicate one.

The headline test is :func:`test_crash_during_commit_reconciles_never_resends` —
that is the story's stated AC (docs/16 §14.2) and the reason this subsystem
exists at all.
"""

from __future__ import annotations

import pytest

from tango.ledger import (
    DuplicateAction,
    Evidence,
    Ledger,
    LedgerError,
    ToolResult,
    VerifyResult,
    canonical_args,
    idempotency_key,
)
from tango.store import Store
from tango.types import ActionStatus, Risk, VerifyStatus


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.conn.execute(
        "INSERT INTO task(id, goal, route, status, privacy_class, trace_id, surface,"
        " created_at, updated_at) VALUES('t1','test','playbook','RUNNING','LOCAL_ONLY',"
        "'tr1','cli','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
    )
    yield s
    s.close()


@pytest.fixture()
def ledger(store):
    return Ledger(store)


def _ok(ref: str = "ref-1") -> ToolResult:
    return ToolResult(ok=True, provider_ref=ref, raw="{}", summary="done")


# ------------------------------------------------------------------ canonical


def test_canonical_args_is_order_independent():
    assert canonical_args({"b": 2, "a": 1}) == canonical_args({"a": 1, "b": 2})


def test_idempotency_key_is_stable_and_specific():
    a = idempotency_key("t1", "s1", "docker.up", {"project": "x"})
    b = idempotency_key("t1", "s1", "docker.up", {"project": "x"})
    c = idempotency_key("t1", "s1", "docker.up", {"project": "y"})
    assert a == b
    assert a != c


# -------------------------------------------------------------------- propose


def test_propose_persists_before_any_external_call(ledger, store):
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="docker.up", args={"p": "x"}, risk=Risk.R1_REVERSIBLE
    )
    row = store.conn.execute("SELECT * FROM action WHERE id=?", (aid,)).fetchone()
    assert row["status"] == ActionStatus.PROPOSED
    assert row["idempotency_key"]  # key exists before commit, not after


def test_duplicate_proposal_is_refused_not_duplicated(ledger):
    kw = dict(
        task_id="t1", step_id="s1", tool="docker.up", args={"p": "x"}, risk=Risk.R1_REVERSIBLE
    )
    first = ledger.propose(**kw)
    with pytest.raises(DuplicateAction) as exc:
        ledger.propose(**kw)
    assert exc.value.action_id == first  # a double hotkey press is a no-op


# --------------------------------------------------------------------- commit


def test_verified_requires_a_verifier_that_says_so(ledger):
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="docker.up", args={}, risk=Risk.R1_REVERSIBLE
    )
    status = ledger.commit(
        aid,
        executor=_ok,
        verifier=lambda r: VerifyResult(
            VerifyStatus.VERIFIED, [Evidence("container", "healthy")], "container is healthy"
        ),
    )
    assert status == ActionStatus.VERIFIED
    assert ledger.evidence_for(aid)[0].payload == "healthy"


def test_missing_verifier_yields_unverifiable_never_verified(ledger):
    """A tool with no verifier can never produce a VERIFIED claim."""
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="mystery.op", args={}, risk=Risk.R2_EXTERNAL
    )
    assert ledger.commit(aid, executor=_ok, verifier=None) == ActionStatus.UNVERIFIABLE


def test_verifier_refutation_beats_a_successful_tool_result(ledger):
    """The tool said ok. The world says otherwise. The world wins."""
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="docker.up", args={}, risk=Risk.R1_REVERSIBLE
    )
    status = ledger.commit(
        aid,
        executor=_ok,
        verifier=lambda r: VerifyResult(VerifyStatus.REFUTED, [], "container exited"),
    )
    assert status == ActionStatus.REFUTED
    assert ledger.get(aid)["detail"] == "container exited"


def test_tool_exception_is_refuted_not_swallowed(ledger):
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="docker.up", args={}, risk=Risk.R1_REVERSIBLE
    )

    def boom() -> ToolResult:
        raise ConnectionError("docker daemon unreachable")

    assert ledger.commit(aid, executor=boom) == ActionStatus.REFUTED
    assert "docker daemon unreachable" in ledger.get(aid)["detail"]


def test_cannot_commit_a_settled_action(ledger):
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="docker.up", args={}, risk=Risk.R1_REVERSIBLE
    )
    ledger.commit(aid, executor=_ok, verifier=lambda r: VerifyResult(VerifyStatus.VERIFIED))
    with pytest.raises(LedgerError):
        ledger.commit(aid, executor=_ok)


# -------------------------------------------------------------------- recover


def test_crash_during_commit_reconciles_never_resends(ledger, store):
    """S0.3 AC: kill the process mid-commit; restart must not duplicate the effect.

    Simulates the exact crash window — the COMMITTING row is written, the
    provider call may or may not have landed, and the process dies. Recovery
    must ask the provider, not repeat the action.
    """
    sends: list[str] = []

    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="email.send", args={"to": "r"},
        risk=Risk.R3_CONSEQUENTIAL, sink_idempotent=True,
    )

    # Crash: status reaches COMMITTING, then the process dies before settling.
    store.conn.execute(
        "UPDATE action SET status=?, committed_at=? WHERE id=?",
        (ActionStatus.COMMITTING, "2026-01-01T00:00:01Z", aid),
    )

    # Restart. The provider confirms the message did land.
    def probe(tool: str, key: str, args: str) -> VerifyResult:
        return VerifyResult(
            VerifyStatus.VERIFIED, [Evidence("message_id", "msg-42")], "found by idempotency key"
        )

    settled = Ledger(store).recover(probe=probe)

    assert settled == [(aid, ActionStatus.VERIFIED)]
    assert sends == []  # nothing was re-sent
    assert ledger.evidence_for(aid)[0].payload == "msg-42"


def test_recovery_without_a_probe_is_honest_not_optimistic(ledger, store):
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="email.send", args={}, risk=Risk.R3_CONSEQUENTIAL
    )
    store.conn.execute("UPDATE action SET status=? WHERE id=?", (ActionStatus.COMMITTING, aid))

    settled = Ledger(store).recover(probe=None)

    assert settled == [(aid, ActionStatus.UNVERIFIABLE)]
    assert "could not be queried" in ledger.get(aid)["detail"]


def test_recovery_leaves_an_audit_trail(ledger, store):
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="email.send", args={}, risk=Risk.R3_CONSEQUENTIAL
    )
    store.conn.execute("UPDATE action SET status=? WHERE id=?", (ActionStatus.COMMITTING, aid))
    Ledger(store).recover()

    row = store.conn.execute(
        "SELECT * FROM audit_event WHERE resource=?", (aid,)
    ).fetchone()
    assert row["action"] == "reconcile:email.send"
    assert row["verdict"] == ActionStatus.UNVERIFIABLE


def test_recovery_is_idempotent(ledger, store):
    """Running recovery twice must not re-settle already-settled rows."""
    aid = ledger.propose(
        task_id="t1", step_id="s1", tool="email.send", args={}, risk=Risk.R3_CONSEQUENTIAL
    )
    store.conn.execute("UPDATE action SET status=? WHERE id=?", (ActionStatus.COMMITTING, aid))
    assert len(Ledger(store).recover()) == 1
    assert Ledger(store).recover() == []
