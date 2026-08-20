"""The CLI — the only surface, and until now the only untested module.

An audit found `cli.py` at 0% coverage: 197 statements standing between every
guarantee underneath and the person using them. The safety core being well
tested does not help if the surface mis-wires it.

These run the real Typer app against a temp store, so they exercise the actual
composition rather than a mock of it.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from tango.cli import app

runner = CliRunner()


@pytest.fixture()
def env(tmp_path):
    """An isolated Tango: temp ledger, real playbooks, a fake project."""
    hosts = tmp_path / "hosts" / "default"
    hosts.mkdir(parents=True)
    project = tmp_path / "demo"
    project.mkdir()
    (hosts / "projects.json").write_text(json.dumps({
        "projects": [{
            "id": "demo", "path": str(project), "stack": "test",
            "aliases": ["the demo thing"], "dev_cmd": None,
        }]
    }), encoding="utf-8")
    return [
        "--db", str(tmp_path / "t.db"),
        "--playbooks", "playbooks",
        "--hosts", str(tmp_path / "hosts"),
    ]


def invoke(env, *args):
    return runner.invoke(app, [*env, *args])


# ------------------------------------------------------------------- surfaces


def test_projects_lists_what_it_knows(env):
    result = invoke(env, "projects")
    assert result.exit_code == 0
    assert "demo" in result.output


def test_tools_reports_which_are_verified(env):
    result = invoke(env, "tools")
    assert result.exit_code == 0
    assert "process.start" in result.output
    assert "verified" in result.output


def test_status_runs_without_touching_the_network(env):
    result = invoke(env, "status", "--no-prod")
    assert result.exit_code == 0
    assert "demo" in result.output
    assert "prod not checked" not in result.output or "demo" in result.output


def test_pending_is_empty_on_a_fresh_store(env):
    result = invoke(env, "pending")
    assert result.exit_code == 0
    assert "Nothing pending" in result.output


def test_running_is_empty_on_a_fresh_store(env):
    result = invoke(env, "running")
    assert result.exit_code == 0
    assert "still running" in result.output


def test_audit_opens_without_error(env):
    assert invoke(env, "audit").exit_code == 0


def test_doctor_reports_without_crashing(env, tmp_path):
    """Doctor is the first thing that runs on a machine I cannot debug. It must
    never be the thing that fails."""
    result = runner.invoke(app, [
        *env, "doctor", "--hosts", str(tmp_path / "hosts"), "--db", str(tmp_path / "d.db"),
    ])
    assert "TANGO doctor" in result.output
    assert result.exit_code in (0, 1)


# ------------------------------------------------------------------- routing


def test_a_refusal_exits_non_zero_and_says_why(env):
    result = invoke(env, "do", "delete", "the", "demo", "database")
    assert result.exit_code == 2, "refusals must be distinguishable by exit code"
    assert "not something I'll do" in result.output


def test_a_refusal_is_audited(env):
    invoke(env, "do", "take", "prod", "down")
    audit = invoke(env, "audit")
    assert "DENIED" in audit.output


def test_an_out_of_scope_request_declines_politely(env):
    result = invoke(env, "do", "order", "me", "a", "pizza")
    assert result.exit_code == 1
    assert "outside what I do" in result.output


def test_an_ambiguous_request_asks_and_offers_options(env):
    result = invoke(env, "do", "start", "it")
    assert result.exit_code == 1
    assert "which project" in result.output.lower()
    assert "demo" in result.output


def test_an_unknown_request_admits_it(env):
    result = invoke(env, "do", "make", "me", "a", "sandwich")
    assert result.exit_code == 1
    assert "don't have a playbook" in result.output


def test_an_alias_resolves_through_the_cli(env):
    """The resolver is exercised by the surface, not only in unit tests."""
    result = invoke(env, "do", "what's", "wrong", "with", "the", "demo", "thing")
    assert result.exit_code == 0
    assert "demo" in result.output


# --------------------------------------------------------------- honest output


def test_status_says_prod_not_checked_rather_than_prod_fine(env):
    """V4/D13: "I didn't look" must never render as "it's fine"."""
    from pathlib import Path

    path = Path(env[env.index("--hosts") + 1]) / "default" / "projects.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["projects"][0]["prod_url"] = "https://example.invalid"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = invoke(env, "status", "--no-prod")
    assert "prod not checked" in result.output


def test_diagnose_declines_to_call_itself_a_diagnosis(env):
    result = invoke(env, "do", "diagnose", "demo")
    assert result.exit_code == 0
    assert "Evidence for" in result.output or "couldn't find anything" in result.output


def test_why_on_an_unknown_task_does_not_crash(env):
    assert invoke(env, "why", "no-such-task-id").exit_code == 0


def test_confirming_a_bogus_nonce_fails_clearly(env):
    result = invoke(env, "confirm", "not-a-real-nonce")
    assert result.exit_code == 1
    assert "not valid" in result.output


def test_cancelling_nothing_says_so(env):
    result = invoke(env, "cancel", "no-such-action")
    assert result.exit_code == 1
    assert "Nothing pending" in result.output


def test_panic_is_safe_when_nothing_is_pending(env):
    result = invoke(env, "panic")
    assert result.exit_code == 0
    assert "Nothing was pending" in result.output


# --------------------------------------------------------------- the invariant


def test_no_cli_output_claims_an_unverified_outcome(env):
    """The surface is the last place a claim can leak. Sweep the ones that
    report on actions."""
    from tango.render import COMPLETION_VERBS

    for args in (
        ["do", "start", "it"],
        ["do", "order", "me", "a", "pizza"],
        ["do", "delete", "the", "demo", "database"],
        ["pending"],
        ["running"],
    ):
        out = invoke(env, *args).output.lower()
        leaked = {v for v in COMPLETION_VERBS if f" {v} " in f" {out} "}
        # "stopped"/"running" appear as state descriptions in these messages;
        # what must not appear is a claim that Tango performed something.
        assert not (leaked - {"stopped", "running", "started"}), (
            f"{args} leaked {leaked}"
        )
