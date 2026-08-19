"""Phase 1: aggregate reads, status honesty, and ledger-driven cleanup."""

from __future__ import annotations

import json

import pytest

from tango.aggregates import is_aggregate, run_aggregate
from tango.cleanup import started_processes
from tango.executor import Executor
from tango.ledger import Ledger
from tango.projects import Project, ProjectRegistry
from tango.status import ProjectStatus, Snapshot, render
from tango.store import Store
from tango.tools import ToolRegistry
from tango.types import ActionStatus, Risk


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "agg.db")
    yield s
    s.close()


@pytest.fixture()
def rig(store):
    registry = ToolRegistry()
    ledger = Ledger(store)
    return store, ledger, Executor(ledger=ledger, registry=registry, store=store)


@pytest.fixture()
def projects(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    return ProjectRegistry(projects={"demo": Project(id="demo", path=str(d))})


# --------------------------------------------------- status honesty (V4 / D13)


def test_unchecked_production_is_not_reported_as_a_problem():
    """The V4 defect: with --no-prod, an empty prod_status made every project
    look unhealthy. "I didn't look" is not "it's broken"."""
    st = ProjectStatus(id="x", prod_url="https://example.com", prod_checked=False)
    assert st.needs_attention is False


def test_checked_and_failing_production_is_flagged():
    st = ProjectStatus(id="x", prod_url="https://example.com",
                       prod_checked=True, prod_status="503")
    assert st.needs_attention is True


def test_checked_and_healthy_production_is_not_flagged():
    st = ProjectStatus(id="x", prod_url="https://example.com",
                       prod_checked=True, prod_status="200")
    assert st.needs_attention is False


def test_exited_container_is_flagged():
    assert ProjectStatus(id="x", container="c", container_status="exited").needs_attention


def test_render_says_when_it_did_not_look():
    snap = Snapshot(projects=[
        ProjectStatus(id="x", branch="main", prod_url="https://e.com", prod_checked=False)
    ])
    assert "prod not checked" in render(snap)


def test_render_states_docker_unknown_rather_than_empty():
    """Docker being down means container state is unknown. Rendering an empty
    list would imply "nothing is running", which is a different claim."""
    snap = Snapshot(projects=[ProjectStatus(id="x")], docker_available=False)
    assert "not assumed empty" in render(snap)


def test_render_contains_no_completion_verbs():
    """Status describes observed state; it never claims work was done."""
    from tango.render import COMPLETION_VERBS

    snap = Snapshot(projects=[
        ProjectStatus(id="x", branch="main", dirty=3, prod_checked=True, prod_status="200")
    ])
    words = set(render(snap).lower().replace("·", " ").split())
    assert not (words & COMPLETION_VERBS)


# ------------------------------------------------------------ aggregate reads


def test_aggregates_are_recognised():
    assert is_aggregate("status_all")
    assert is_aggregate("git_digest")
    assert not is_aggregate("dev_up")


def test_aggregate_records_exactly_one_ledger_action(rig, projects):
    """One question, one row. Twenty rows would bury the audit trail that
    consequential actions depend on."""
    store, ledger, executor = rig
    run_aggregate("status_all", projects, {"prod": False}, ledger, executor)

    rows = store.conn.execute("SELECT tool, status, risk FROM action").fetchall()
    assert len(rows) == 1
    assert rows[0]["tool"] == "aggregate.status_all"
    assert rows[0]["status"] == ActionStatus.VERIFIED
    assert rows[0]["risk"] == Risk.R0_READ


def test_aggregate_stores_its_observation_as_evidence(rig, projects):
    """An answer must be traceable to what it was derived from."""
    store, ledger, executor = rig
    run_aggregate("status_all", projects, {"prod": False}, ledger, executor)

    action_id = store.conn.execute("SELECT id FROM action").fetchone()["id"]
    evidence = ledger.evidence_for(action_id)
    assert evidence and json.loads(evidence[0].payload)["projects"]


def test_git_digest_on_a_non_repo_is_honest_not_empty(rig, projects):
    store, ledger, executor = rig
    result = run_aggregate("git_digest", projects, {"since": "7 days ago"}, ledger, executor)
    assert "No commits" in result.text


def test_port_free_does_not_kill_anything(rig, projects):
    """Freeing a port terminates a process — an R1 action needing its own
    verified step, not a side effect of asking a question."""
    store, ledger, executor = rig
    run_aggregate("port_free", projects, {"port": 59999}, ledger, executor)

    tools = [r["tool"] for r in store.conn.execute("SELECT tool FROM action")]
    assert tools == ["aggregate.port_free"]
    assert "process.stop" not in tools


# ------------------------------------------------------- cleanup (D6 closed)


def test_started_processes_reads_from_the_ledger_not_memory(rig):
    """The whole point of D6: what Tango started survives Tango restarting."""
    store, ledger, executor = rig
    task_id = executor.new_task(goal="t")
    action_id = ledger.propose(task_id=task_id, step_id="s1", tool="process.start",
                               args={"cmd": "x"}, risk=Risk.R1_REVERSIBLE)
    import os
    store.conn.execute(
        "UPDATE action SET status=?, provider_ref=? WHERE id=?",
        (ActionStatus.VERIFIED, str(os.getpid()), action_id),
    )

    # A brand-new Store over the same file — nothing carried in memory.
    reopened = Store(store.path)
    found = started_processes(reopened)
    reopened.close()

    assert [p.pid for p in found] == [os.getpid()]


def test_unverified_starts_are_never_candidates_for_stopping(rig):
    """Killing a PID we cannot prove we own is the confident-wrong-action this
    whole design exists to prevent."""
    store, ledger, executor = rig
    task_id = executor.new_task(goal="t")
    action_id = ledger.propose(task_id=task_id, step_id="s1", tool="process.start",
                               args={"cmd": "x"}, risk=Risk.R1_REVERSIBLE)
    import os
    store.conn.execute(
        "UPDATE action SET status=?, provider_ref=? WHERE id=?",
        (ActionStatus.UNVERIFIABLE, str(os.getpid()), action_id),
    )
    assert started_processes(store) == []


def test_dead_processes_are_excluded_by_default(rig):
    store, ledger, executor = rig
    task_id = executor.new_task(goal="t")
    action_id = ledger.propose(task_id=task_id, step_id="s1", tool="process.start",
                               args={"cmd": "x"}, risk=Risk.R1_REVERSIBLE)
    store.conn.execute(
        "UPDATE action SET status=?, provider_ref='999999' WHERE id=?",
        (ActionStatus.VERIFIED, action_id),
    )
    assert started_processes(store) == []
    assert len(started_processes(store, only_alive=False)) == 1
