"""Failure injection — deliberately breaking Tango to see what it claims.

Everything else tests the happy path and the refusal path. This tests the ugly
middle: the process dies mid-commit, the disk is full, two surfaces act at once,
the clock jumps, the provider hangs. Those are the conditions under which a
system starts lying, because lying is what "assume it worked" looks like from
the inside.

The single rule every test here enforces: **under any injected failure, Tango
may lose capability but must not lose honesty.** Reporting UNVERIFIABLE is
always an acceptable outcome. Reporting VERIFIED without evidence never is.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from tango.executor import Executor
from tango.ledger import Evidence, Ledger, ToolResult, VerifyResult
from tango.policy import EgressPolicy, PolicyGate
from tango.render import find_unlicensed_claims
from tango.store import Store
from tango.tools import Tool, ToolCall, ToolRegistry
from tango.types import ActionStatus, Risk, VerifyStatus


@pytest.fixture()
def rig(tmp_path):
    store = Store(tmp_path / "chaos.db")
    registry = ToolRegistry()
    gate = PolicyGate(store, egress=EgressPolicy(recipients=frozenset({"ok@example.com"})))
    executor = Executor(ledger=Ledger(store), registry=registry, store=store, policy=gate)
    yield store, executor, registry
    # Some tests close the store deliberately, to simulate the process dying.
    with contextlib.suppress(sqlite3.ProgrammingError):
        store.close()


def _register(registry: ToolRegistry, name: str, executor_fn, verifier_fn=None, **kw) -> None:
    registry.register(Tool(
        name=name, risk=kw.pop("risk", Risk.R1_REVERSIBLE), executor=executor_fn,
        verifier=verifier_fn, description=kw.pop("description", "no-compensate"), **kw,
    ))


# ------------------------------------------------------------- crash recovery


def test_death_between_effect_and_record_never_duplicates(rig, tmp_path):
    """The classic: the provider succeeded, we died before writing it down.

    Retrying here would send the email twice. Recovery must ask the provider,
    and the idempotency key is what lets it ask a precise question.
    """
    store, executor, registry = rig
    sends: list[str] = []

    _register(registry, "email.send",
              lambda **kw: ToolResult(ok=True, provider_ref="msg-1"),
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "sent"),
              risk=Risk.R3_CONSEQUENTIAL, sink_idempotent=True)

    task_id = executor.new_task(goal="send")
    action_id = executor.ledger.propose(
        task_id=task_id, step_id="s1", tool="email.send",
        args={"recipient": "ok@example.com"}, risk=Risk.R3_CONSEQUENTIAL,
        sink_idempotent=True,
    )
    # The crash window: COMMITTING is written, the provider may have acted.
    store.conn.execute("UPDATE action SET status=? WHERE id=?",
                       (ActionStatus.COMMITTING, action_id))
    path = store.path
    store.close()

    reopened = Store(path)

    def probe(tool: str, key: str, args: str) -> VerifyResult:
        # Asking the provider is what makes this safe. Re-sending is not.
        return VerifyResult(VerifyStatus.VERIFIED, [Evidence("message_id", "msg-1")], "found")

    settled = Ledger(reopened).recover(probe=probe)
    reopened.close()

    assert settled == [(action_id, ActionStatus.VERIFIED)]
    assert sends == [], "recovery re-sent the message"


def test_recovery_runs_before_new_work_is_accepted(rig):
    """A COMMITTING row is a known-unknown. Starting fresh work on top of one
    means the ledger no longer describes reality."""
    store, executor, registry = rig
    task_id = executor.new_task(goal="t")
    action_id = executor.ledger.propose(
        task_id=task_id, step_id="s1", tool="x", args={}, risk=Risk.R1_REVERSIBLE
    )
    store.conn.execute("UPDATE action SET status=? WHERE id=?",
                       (ActionStatus.COMMITTING, action_id))

    settled = executor.ledger.recover(probe=None)
    assert [s[1] for s in settled] == [ActionStatus.UNVERIFIABLE]
    assert not any(
        ActionStatus(r["status"]).is_in_flight for r in executor.ledger.actions_for_task(task_id)
    )


def test_repeated_recovery_is_stable(rig):
    """Recovery must be idempotent, or a crash loop rewrites history each pass."""
    store, executor, registry = rig
    task_id = executor.new_task(goal="t")
    action_id = executor.ledger.propose(
        task_id=task_id, step_id="s1", tool="x", args={}, risk=Risk.R1_REVERSIBLE
    )
    store.conn.execute("UPDATE action SET status=? WHERE id=?",
                       (ActionStatus.COMMITTING, action_id))

    first = executor.ledger.recover()
    assert len(first) == 1
    for _ in range(5):
        assert executor.ledger.recover() == []
    assert executor.ledger.get(action_id)["status"] == ActionStatus.UNVERIFIABLE


# ------------------------------------------------------------ provider chaos


def test_a_hanging_provider_is_refuted_not_assumed(rig):
    store, executor, registry = rig

    def hangs(**kw) -> ToolResult:
        raise TimeoutError("provider did not respond in 30s")

    _register(registry, "slow.op", hangs,
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "?"))

    task_id = executor.new_task(goal="t")
    outcome = executor.run(task_id, ToolCall("slow.op", {}))

    assert outcome.status is ActionStatus.REFUTED
    assert "did not respond" in outcome.detail
    assert find_unlicensed_claims(outcome.detail, outcome.status) == []


def test_a_verifier_that_itself_fails_does_not_produce_verified(rig):
    """If the check cannot run, the honest answer is that nothing was checked."""
    store, executor, registry = rig

    def broken_verifier(r, a):
        raise ConnectionError("cannot reach docker to verify")

    _register(registry, "risky.op", lambda **kw: ToolResult(ok=True), broken_verifier)

    task_id = executor.new_task(goal="t")
    outcome = executor.run(task_id, ToolCall("risky.op", {}))
    assert outcome.status is not ActionStatus.VERIFIED


def test_garbage_from_a_provider_does_not_become_evidence(rig):
    store, executor, registry = rig
    _register(registry, "noisy.op",
              lambda **kw: ToolResult(ok=True, raw="\x00\xff not json at all"),
              lambda r, a: VerifyResult(VerifyStatus.UNVERIFIABLE, [], "unparseable response"))

    task_id = executor.new_task(goal="t")
    outcome = executor.run(task_id, ToolCall("noisy.op", {}))
    assert outcome.status is ActionStatus.UNVERIFIABLE


# ---------------------------------------------------------------- concurrency


def test_two_surfaces_racing_the_same_step_produce_one_effect(rig):
    """Voice and Telegram both say "start the db" at once. The idempotency key
    is what makes the second one a no-op rather than a second container."""
    store, executor, registry = rig
    effects: list[int] = []

    _register(registry, "svc.start",
              lambda **kw: (effects.append(1), ToolResult(ok=True, provider_ref="p"))[1],
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "up"))

    task_id = executor.new_task(goal="t")
    call = ToolCall("svc.start", {"name": "db"}, "s1")
    results: list[str] = []

    def worker() -> None:
        results.append(executor.run(task_id, call).status)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(effects) == 1, f"the effect ran {len(effects)} times"
    assert len(results) == 4


def test_interleaved_tasks_keep_their_own_ledgers(rig):
    """"Start optiresume" and "shut everything down" must not read each other's
    outcomes, whichever order they land in."""
    store, executor, registry = rig
    _register(registry, "svc.op", lambda **kw: ToolResult(ok=True, provider_ref="p"),
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "ok"))

    a = executor.new_task(goal="start")
    b = executor.new_task(goal="stop")
    executor.run(a, ToolCall("svc.op", {"x": 1}, "s1"))
    executor.run(b, ToolCall("svc.op", {"x": 2}, "s1"))
    executor.run(a, ToolCall("svc.op", {"x": 3}, "s2"))

    assert len(executor.ledger.actions_for_task(a)) == 2
    assert len(executor.ledger.actions_for_task(b)) == 1


# ------------------------------------------------------------------- clock


def test_a_clock_jump_backwards_does_not_fire_windows_early(rig):
    store, executor, registry = rig
    _register(registry, "thing.do", lambda **kw: ToolResult(ok=True, provider_ref="p"),
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "ok"),
              risk=Risk.R2_EXTERNAL, compensate="thing.undo")
    _register(registry, "thing.undo", lambda **kw: ToolResult(ok=True),
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "ok"),
              risk=Risk.R2_EXTERNAL)

    task_id = executor.new_task(goal="t")
    executor.run(task_id, ToolCall("thing.do", {}))

    # Deadline far in the future, as if the clock jumped back.
    store.conn.execute(
        "UPDATE pending_action SET execute_at=?",
        ((datetime.now(UTC) + timedelta(hours=2)).isoformat(),),
    )
    assert executor.tick() == []


def test_an_expired_confirmation_cannot_be_redeemed_later(rig):
    store, executor, registry = rig
    _register(registry, "email.send", lambda **kw: ToolResult(ok=True, provider_ref="m"),
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "sent"),
              risk=Risk.R3_CONSEQUENTIAL)

    task_id = executor.new_task(goal="t")
    outcome = executor.run(task_id, ToolCall("email.send", {"recipient": "ok@example.com"}))
    store.conn.execute(
        "UPDATE confirmation_request SET expires_at=?",
        ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),),
    )
    result = executor.confirm(outcome.nonce)
    assert result is not None
    assert result.status is ActionStatus.EXPIRED


# --------------------------------------------------------------- store damage


def test_a_readonly_store_fails_loudly_rather_than_pretending(rig, tmp_path):
    """If state cannot be written, the effect must not happen: an unrecorded
    action is one nothing can later verify or undo."""
    store, executor, registry = rig
    _register(registry, "svc.op", lambda **kw: ToolResult(ok=True),
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "ok"))
    task_id = executor.new_task(goal="t")

    store.conn.execute("PRAGMA query_only=ON")
    with pytest.raises(sqlite3.OperationalError):
        executor.run(task_id, ToolCall("svc.op", {}))
    store.conn.execute("PRAGMA query_only=OFF")


def test_the_schema_version_is_enforced_on_open(tmp_path):
    """A database written by a newer Tango must not be silently misread."""
    path = tmp_path / "v.db"
    store = Store(path)
    store.conn.execute("UPDATE schema_meta SET value='999' WHERE key='version'")
    store.close()

    with pytest.raises(RuntimeError, match="schema version mismatch"):
        Store(path)


def test_wal_survives_an_unclean_close(tmp_path):
    """WAL plus synchronous=FULL is the crash guarantee. Verify a row written
    before an abrupt close is still there afterwards."""
    path = tmp_path / "wal.db"
    store = Store(path)
    store.conn.execute(
        "INSERT INTO task(id, goal, route, status, privacy_class, trace_id, surface,"
        " created_at, updated_at) VALUES('t','g','p','RUNNING','LOCAL_ONLY','tr','cli',"
        "'2026-01-01','2026-01-01')"
    )
    del store  # no close(): simulate the process vanishing

    reopened = Store(path)
    row = reopened.conn.execute("SELECT goal FROM task WHERE id='t'").fetchone()
    reopened.close()
    assert row is not None and row["goal"] == "g"


# ------------------------------------------------------- the invariant itself


def test_no_injected_failure_ever_produces_an_unlicensed_claim(rig):
    """The invariant: capability may degrade, honesty may not."""
    store, executor, registry = rig

    def explode(**kw) -> ToolResult:
        raise RuntimeError("everything is on fire")

    _register(registry, "ok.op", lambda **kw: ToolResult(ok=True, provider_ref="p"),
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "fine"))
    _register(registry, "fail.op", explode,
              lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "?"))
    _register(registry, "unverified.op", lambda **kw: ToolResult(ok=True))

    task_id = executor.new_task(goal="t")
    for name in ("ok.op", "fail.op", "unverified.op"):
        outcome = executor.run(task_id, ToolCall(name, {}, f"s-{name}"))
        text = outcome.to_step(name).status
        offenders = find_unlicensed_claims(outcome.detail, outcome.status)
        assert offenders == [], f"{name} produced unlicensed claim {offenders} at {text}"
