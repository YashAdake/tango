"""Integration: do store + registry + ledger + renderer actually compose?

Unit tests prove each part against its own fakes. That is not the same as the
parts fitting together, and the seams are where systems break. Everything here
uses the *real* registry, the *real* ledger and the *real* renderer — only the
outside world is faked.
"""

from __future__ import annotations

import pytest

from tango.executor import Executor
from tango.ledger import Evidence, Ledger, ToolResult, VerifyResult
from tango.render import find_unlicensed_claims, render_task
from tango.store import Store
from tango.tools import Tool, ToolCall, ToolRegistry
from tango.types import ActionStatus, Risk, TaskStatus, VerifyStatus


@pytest.fixture()
def rig(tmp_path):
    """A complete, self-contained Tango core with a fake outside world."""
    store = Store(tmp_path / "integration.db")
    ledger = Ledger(store)
    registry = ToolRegistry()
    executor = Executor(ledger=ledger, registry=registry, store=store)
    yield store, ledger, registry, executor
    store.close()


def _register_pair(registry: ToolRegistry, world: dict[str, bool]) -> None:
    """A tool whose effect and verification both go through a shared fake world,
    so the verifier genuinely observes state rather than echoing the executor."""

    def start(name: str) -> ToolResult:
        world[name] = True
        return ToolResult(ok=True, provider_ref=name, summary=f"started {name}")

    def verify_started(result: ToolResult, args: dict) -> VerifyResult:
        name = args["name"]
        if world.get(name):
            return VerifyResult(VerifyStatus.VERIFIED, [Evidence("service", name)], f"{name} up")
        return VerifyResult(VerifyStatus.REFUTED, [], f"{name} is not up")

    def stop(name: str) -> ToolResult:
        world[name] = False
        return ToolResult(ok=True, provider_ref=name, summary=f"stopped {name}")

    def verify_stopped(result: ToolResult, args: dict) -> VerifyResult:
        if not world.get(args["name"]):
            return VerifyResult(VerifyStatus.VERIFIED, [], "down")
        return VerifyResult(VerifyStatus.REFUTED, [], "still up")

    registry.register(
        Tool(name="svc.stop", risk=Risk.R1_REVERSIBLE, executor=stop, verifier=verify_stopped,
             description="Stop. no-compensate: this is the compensate.")
    )
    registry.register(
        Tool(name="svc.start", risk=Risk.R1_REVERSIBLE, executor=start,
             verifier=verify_started, compensate="svc.stop")
    )


# ------------------------------------------------------------- the happy path


def test_full_stack_produces_a_verified_claim(rig):
    """utterance -> task -> tool call -> ledger -> verifier -> licensed sentence."""
    store, ledger, registry, executor = rig
    world: dict[str, bool] = {}
    _register_pair(registry, world)

    task_id = executor.new_task(goal="start the db", surface="cli")
    outcome = executor.run(task_id, ToolCall(tool="svc.start", args={"name": "db"}, step_id="s1"))

    assert outcome.status is ActionStatus.VERIFIED
    assert world["db"] is True

    text = render_task([outcome.to_step("DB up")], TaskStatus.COMPLETED)
    assert text == "DB up"
    assert find_unlicensed_claims(text, ActionStatus.VERIFIED) == []


def test_verifier_refutation_flows_through_to_honest_text(rig):
    """The tool 'succeeds', the world disagrees, and the sentence tells the truth."""
    store, ledger, registry, executor = rig

    def liar(name: str) -> ToolResult:
        return ToolResult(ok=True, provider_ref=name, summary="all good!")

    def truth(result: ToolResult, args: dict) -> VerifyResult:
        return VerifyResult(VerifyStatus.REFUTED, [Evidence("probe", "connection refused")],
                            "connection refused")

    registry.register(
        Tool(name="svc.fake", risk=Risk.R1_REVERSIBLE, executor=liar, verifier=truth,
             description="Lies. no-compensate: nothing happened.")
    )

    task_id = executor.new_task(goal="start api", surface="cli")
    outcome = executor.run(task_id, ToolCall(tool="svc.fake", args={"name": "api"}))

    assert outcome.status is ActionStatus.REFUTED
    text = render_task([outcome.to_step("API")], TaskStatus.FAILED)
    assert "connection refused" in text
    assert find_unlicensed_claims(text, ActionStatus.REFUTED) == []


# ------------------------------------------------------------ multi-step task


def test_partial_task_reports_partially(rig):
    """Three steps, one fails: the sentence must not round up to success."""
    store, ledger, registry, executor = rig
    world: dict[str, bool] = {}
    _register_pair(registry, world)

    def broken(name: str) -> ToolResult:
        return ToolResult(ok=False, summary="port 8000 already in use")

    registry.register(
        Tool(name="svc.broken", risk=Risk.R1_REVERSIBLE, executor=broken,
             verifier=lambda r, a: VerifyResult(VerifyStatus.REFUTED, [], "did not start"),
             description="Fails. no-compensate.")
    )

    task_id = executor.new_task(goal="dev up", surface="cli")
    steps = [
        executor.run(task_id, ToolCall("svc.start", {"name": "db"}, "s1")).to_step("DB up"),
        executor.run(task_id, ToolCall("svc.broken", {"name": "api"}, "s2")).to_step("API"),
        executor.run(task_id, ToolCall("svc.start", {"name": "web"}, "s3")).to_step("Web up"),
    ]

    text = render_task(steps, TaskStatus.PARTIAL)
    assert "2 of 3 confirmed" in text
    assert "port 8000 already in use" in text
    assert "DB up" in text and "Web up" in text


def test_task_status_is_derived_from_the_ledger_not_guessed(rig):
    store, ledger, registry, executor = rig
    world: dict[str, bool] = {}
    _register_pair(registry, world)

    task_id = executor.new_task(goal="mixed", surface="cli")
    executor.run(task_id, ToolCall("svc.start", {"name": "a"}, "s1"))
    assert executor.task_status(task_id) is TaskStatus.COMPLETED

    registry.register(
        Tool(name="svc.nope", risk=Risk.R1_REVERSIBLE,
             executor=lambda name: ToolResult(ok=False, summary="nope"),
             verifier=lambda r, a: VerifyResult(VerifyStatus.REFUTED, [], "nope"),
             description="no-compensate")
    )
    executor.run(task_id, ToolCall("svc.nope", {"name": "b"}, "s2"))
    assert executor.task_status(task_id) is TaskStatus.PARTIAL


# ------------------------------------------------------- idempotency & safety


def test_repeating_a_step_is_a_no_op_not_a_second_effect(rig):
    """A double hotkey press must not start the service twice."""
    store, ledger, registry, executor = rig
    calls: list[str] = []

    def counting(name: str) -> ToolResult:
        calls.append(name)
        return ToolResult(ok=True, provider_ref=name)

    registry.register(
        Tool(name="svc.count", risk=Risk.R1_REVERSIBLE, executor=counting,
             verifier=lambda r, a: VerifyResult(VerifyStatus.VERIFIED, [], "ok"),
             description="no-compensate")
    )

    task_id = executor.new_task(goal="dupe", surface="cli")
    call = ToolCall("svc.count", {"name": "db"}, "s1")
    first = executor.run(task_id, call)
    second = executor.run(task_id, call)

    assert calls == ["db"], "the effect happened twice"
    assert first.status is ActionStatus.VERIFIED
    assert second.deduplicated is True


def test_unknown_tool_is_refused_and_audited(rig):
    store, ledger, registry, executor = rig
    task_id = executor.new_task(goal="bad", surface="cli")

    outcome = executor.run(task_id, ToolCall("does.not.exist", {}))

    assert outcome.status is ActionStatus.DENIED
    row = store.conn.execute(
        "SELECT * FROM audit_event WHERE verdict='DENIED'"
    ).fetchone()
    assert row is not None, "a refusal with no audit row is a near-miss you will repeat"


def test_every_action_is_traceable_to_its_task(rig):
    store, ledger, registry, executor = rig
    world: dict[str, bool] = {}
    _register_pair(registry, world)

    task_id = executor.new_task(goal="trace", surface="cli")
    executor.run(task_id, ToolCall("svc.start", {"name": "db"}, "s1"))
    executor.run(task_id, ToolCall("svc.start", {"name": "web"}, "s2"))

    actions = ledger.actions_for_task(task_id)
    assert len(actions) == 2
    assert all(a["task_id"] == task_id for a in actions)
    assert all(a["idempotency_key"] for a in actions)


# ------------------------------------------------------------ crash recovery


def test_crash_recovery_survives_a_real_store_reopen(rig, tmp_path):
    """Not a simulated crash — close the store, reopen from disk, recover."""
    store, ledger, registry, executor = rig
    path = store.path
    task_id = executor.new_task(goal="crash", surface="cli")
    action_id = ledger.propose(
        task_id=task_id, step_id="s1", tool="email.send",
        args={"to": "r"}, risk=Risk.R3_CONSEQUENTIAL, sink_idempotent=True,
    )
    store.conn.execute(
        "UPDATE action SET status=? WHERE id=?", (ActionStatus.COMMITTING, action_id)
    )
    store.close()

    reopened = Store(path)
    settled = Ledger(reopened).recover(probe=None)
    assert settled == [(action_id, ActionStatus.UNVERIFIABLE)]
    reopened.close()
