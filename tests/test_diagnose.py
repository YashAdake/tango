"""Diagnosis evidence layer — deterministic, testable, model-free.

The value of splitting collection from reasoning is that this half can be
pinned exactly. A log scanner that finds "password authentication failed"
finds it every time, and cannot invent a cause that was not in the logs.
"""

from __future__ import annotations

import os
import time

import pytest

from tango.diagnose import Dossier, Finding, diagnose_project, recently_changed, scan_logs
from tango.projects import Project
from tango.render import COMPLETION_VERBS

# ------------------------------------------------------------------ log scan


def test_authentication_failure_is_found_and_weighted_high():
    logs, findings = scan_logs(
        "INFO starting up\n"
        "asyncpg.InvalidPasswordError: password authentication failed for user 'api'\n"
        "INFO retrying\n"
    )
    assert any("authentication failure" in f.detail for f in findings)
    assert max(f.weight for f in findings) == 3
    assert any("InvalidPasswordError" in line for line in logs)


@pytest.mark.parametrize("line,expected", [
    ("Error: listen EADDRINUSE: address already in use :::3000", "port already in use"),
    ("psycopg.OperationalError: connection refused", "connection refused"),
    ("Error: Cannot find module 'next'", "missing file or module"),
    ("Killed process 1234 (node) total-vm:8GB, OOMKill", "out of memory"),
    ("PermissionError: [Errno 13] Permission denied: '/data'", "permission denied"),
    ("Traceback (most recent call last):", "unhandled exception"),
])
def test_common_failure_shapes_are_recognised(line, expected):
    _, findings = scan_logs(line)
    assert any(expected in f.detail for f in findings)


def test_each_signal_is_reported_once_not_per_line():
    """A hundred identical errors are one finding, not a hundred."""
    _, findings = scan_logs("connection refused\n" * 100)
    assert len([f for f in findings if "connection refused" in f.detail]) == 1


def test_clean_logs_produce_nothing():
    logs, findings = scan_logs("INFO ready\nINFO listening on 3000\nINFO healthy\n")
    assert logs == []
    assert findings == []


def test_the_strongest_signal_wins_over_a_generic_error():
    """"error" appears everywhere; "password authentication failed" does not."""
    _, findings = scan_logs(
        "error: something\nerror: another\npassword authentication failed\n"
    )
    top = sorted(findings, key=lambda f: -f.weight)[0]
    assert "authentication" in top.detail


def test_scan_keeps_the_evidence_line_itself():
    """A finding without the line it came from is an assertion, not evidence."""
    _, findings = scan_logs("FATAL: could not connect to server at 5432")
    assert any("5432" in f.evidence for f in findings)


# ------------------------------------------------------------ recent changes


def test_a_recently_touched_env_file_is_noticed(tmp_path):
    """"It worked yesterday" usually has a change behind it, and the change is
    usually in a file nobody committed."""
    (tmp_path / ".env").write_text("DB_PASSWORD=new")
    found = recently_changed(str(tmp_path), within_minutes=60)
    assert [name for name, _ in found] == [".env"]


def test_an_old_file_is_not_flagged(tmp_path):
    target = tmp_path / ".env"
    target.write_text("x")
    old = time.time() - 60 * 60 * 24
    os.utime(target, (old, old))
    assert recently_changed(str(tmp_path), within_minutes=60) == []


def test_a_missing_directory_is_not_an_error(tmp_path):
    assert recently_changed(str(tmp_path / "nope")) == []


# ---------------------------------------------------------------- the dossier


def test_findings_rank_by_how_much_they_narrow_the_search():
    d = Dossier(target="api")
    d.add("git", "3 uncommitted files", 1)
    d.add("container", "api exited (code 1)", 3)
    d.add("health", "endpoint returns 502", 2)
    assert [f.kind for f in d.ranked] == ["container", "health", "git"]
    assert d.strongest is not None
    assert "exited" in d.strongest.detail


def test_weak_evidence_does_not_get_promoted_to_a_conclusion():
    """Three pieces of context are still context. Nothing here is a cause."""
    d = Dossier(target="api")
    d.add("git", "2 uncommitted files", 1)
    d.add("git", "last commit: abc123", 1)
    assert d.strongest is None
    assert "stands out" in d.render()


def test_render_presents_evidence_without_concluding():
    d = Dossier(target="api")
    d.add("container", "api exited (code 1)", 3)
    text = d.render()
    assert "exited" in text
    assert "not a diagnosis" in text, "the renderer implied a cause it has not earned"


def test_render_contains_no_completion_verbs():
    """Diagnosis observes. It never claims to have done anything."""
    d = Dossier(target="api")
    d.add("container", "api exited (code 1)", 3)
    d.add("change", ".env changed 4 minutes ago", 3)
    words = set(d.render().lower().replace("(", " ").replace(")", " ").split())
    assert not (words & COMPLETION_VERBS)


def test_nothing_wrong_says_so_plainly():
    assert "couldn't find anything wrong" in Dossier(target="api").render()


def test_the_prompt_form_labels_everything_as_observation():
    """The model is asked to explain evidence, not to recall what usually
    goes wrong."""
    d = Dossier(target="api")
    d.add("container", "api exited (code 1)", 3)
    d.logs.append("password authentication failed")
    prompt = d.as_prompt()
    assert "Observations:" in prompt
    assert "[container]" in prompt
    assert "password authentication failed" in prompt


def test_logs_are_capped_so_a_dossier_stays_readable():
    d = Dossier(target="api")
    d.logs.extend(f"line {i}" for i in range(500))
    d.add("log", "logs show error", 1)
    assert d.render().count("line ") <= 8


# ----------------------------------------------------------- whole-project


def test_diagnosing_a_project_is_read_only(tmp_path):
    """Diagnosis observes; it never repairs. "I found the problem and fixed it"
    is two claims, and the second needs its own evidence."""
    project = Project(id="demo", path=str(tmp_path))
    dossier = diagnose_project(project, deep=False)
    assert isinstance(dossier, Dossier)
    assert dossier.target == "demo"


def test_a_recent_config_change_surfaces_as_strong_evidence(tmp_path):
    (tmp_path / ".env").write_text("DB_PASSWORD=changed")
    project = Project(id="demo", path=str(tmp_path), compose_path=str(tmp_path))
    dossier = diagnose_project(project, deep=False)
    assert any(f.kind == "change" and f.weight == 3 for f in dossier.findings)


def test_findings_carry_their_evidence_forward():
    f = Finding("log", "logs show timeout", 2, evidence="upstream timed out after 30s")
    assert "30s" in f.evidence
    assert str(f) == "logs show timeout"


# ------------------------------------ container health semantics (V11 / D31)


def test_still_starting_is_unverifiable_not_refuted(monkeypatch):
    """"Still coming up" and "it failed" are different claims. Conflating them
    sends you debugging a database that is merely slow — found against a real
    Postgres doing a 40-second fsync after an unclean shutdown."""
    from tango.adapters import docker as dk

    monkeypatch.setattr(dk, "_inspect",
                        lambda name: {"Status": "running", "Health": {"Status": "starting"}})
    monkeypatch.setattr(dk, "_tail", lambda name, lines=6: "still starting up")

    result = dk.container_healthy("slow-db", timeout_s=0.5)
    assert result.status.value == "UNVERIFIABLE"
    assert "may yet come up" in result.detail


def test_exhausted_retries_is_refuted_with_logs(monkeypatch):
    from tango.adapters import docker as dk

    monkeypatch.setattr(dk, "_inspect",
                        lambda name: {"Status": "running", "Health": {"Status": "unhealthy"}})
    monkeypatch.setattr(dk, "_tail", lambda name, lines=6: "FATAL: password authentication failed")

    result = dk.container_healthy("bad-db", timeout_s=5)
    assert result.status.value == "REFUTED"
    assert any("authentication" in e.payload for e in result.evidence), (
        "a failure verdict without its logs is an assertion, not evidence"
    )


def test_exited_is_refuted(monkeypatch):
    from tango.adapters import docker as dk

    monkeypatch.setattr(dk, "_inspect", lambda name: {"Status": "exited", "ExitCode": 1})
    monkeypatch.setattr(dk, "_tail", lambda name, lines=6: "boom")
    assert dk.container_healthy("dead", timeout_s=5).status.value == "REFUTED"


def test_healthy_is_verified(monkeypatch):
    from tango.adapters import docker as dk

    monkeypatch.setattr(dk, "_inspect",
                        lambda name: {"Status": "running", "Health": {"Status": "healthy"}})
    assert dk.container_healthy("good", timeout_s=5).status.value == "VERIFIED"


def test_a_container_with_no_healthcheck_counts_as_up(monkeypatch):
    from tango.adapters import docker as dk

    monkeypatch.setattr(dk, "_inspect", lambda name: {"Status": "running"})
    assert dk.container_healthy("plain", timeout_s=5).status.value == "VERIFIED"
