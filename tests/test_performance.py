"""Latency gates.

Added after an audit found `tango projects` taking 8.5 seconds — a 7× miss on
the spec's own target that survived a week because nothing measured it. The
gates checked everything I had decided to care about, and nothing I had not.

Latency is not a nice-to-have here. Docs/06 §5 argues it decides whether Tango
feels present at all, and a personal assistant nobody reaches for has failed
regardless of how correct it is.

These are deliberately generous — they catch regressions of the kind that
actually happened (seconds, not milliseconds), not normal machine variance.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Generous ceilings: the failure being guarded against was 8.5s against a 1.2s
# target, not 300ms against 250ms. A flaky perf gate gets muted, which is worse
# than not having one.
BUDGETS = {
    "projects": 3.0,
    "tools": 3.0,
    "pending": 3.0,
    "running": 4.0,
}


def _time(args: list[str], db: Path) -> tuple[float, int, str]:
    cmd = [sys.executable, "-m", "tango.cli", "--db", str(db), *args]
    started = time.monotonic()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)
    return time.monotonic() - started, p.returncode, p.stdout + p.stderr


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return tmp_path_factory.mktemp("perf") / "perf.db"


@pytest.mark.parametrize("command,budget", BUDGETS.items())
def test_command_stays_within_budget(command, budget, db):
    elapsed, code, out = _time([command], db)
    assert code == 0, f"`tango {command}` exited {code}: {out[:200]}"
    assert elapsed < budget, (
        f"`tango {command}` took {elapsed:.2f}s (budget {budget}s). "
        "Something on the startup path got slower — check for an eager probe."
    )


def test_startup_does_not_probe_for_a_model(db):
    """The specific regression: `select_model()` ran at construction, so every
    command paid ~4s to look for Ollama even when it could not use one.

    Deterministic first, model only as fallback — that has to be true of the
    entry point too, not merely of the routing logic.
    """
    from tango.models import LazyLocalModel

    model = LazyLocalModel()
    started = time.monotonic()
    _ = model.name, model.tier
    assert (time.monotonic() - started) < 0.05, "constructing the model touched the network"


def test_availability_is_probed_once_not_per_call():
    """A cached negative matters most: probing for something absent is the slow
    case, and the router may ask more than once in a session."""
    from tango.models import OllamaModel

    model = OllamaModel(host="http://127.0.0.1:1")  # nothing listens here
    first_started = time.monotonic()
    model.available()
    first = time.monotonic() - first_started

    repeat_started = time.monotonic()
    for _ in range(20):
        model.available()
    repeat = time.monotonic() - repeat_started

    assert repeat < max(first, 0.05), "availability was re-probed on every call"


def test_routing_a_known_utterance_needs_no_network():
    """The Alexa half must never touch a model. If it does, sub-second is gone."""
    from tango.aggregates import built_capabilities
    from tango.playbook import PlaybookRegistry
    from tango.projects import ProjectRegistry
    from tango.router import Router

    playbooks = PlaybookRegistry()
    playbooks.load_dir(ROOT / "playbooks")
    router = Router(
        ProjectRegistry.load(ROOT / "hosts"),
        known_playbooks=built_capabilities(set(playbooks.names())),
        model=_ExplodingModel(),
    )

    started = time.monotonic()
    for utterance in ("start optiresume", "what's the state of everything",
                      "is prod ok", "shut everything down", "delete the database"):
        router.route(utterance)
    assert (time.monotonic() - started) < 1.0


class _ExplodingModel:
    """Fails loudly if the deterministic path ever reaches for it."""

    name = "must-not-be-used"
    tier = "T1"

    def available(self) -> bool:
        raise AssertionError("the rules path consulted the model")

    def complete(self, prompt: str, *, system: str = "", schema: object = None) -> object:
        raise AssertionError("the rules path called the model")
