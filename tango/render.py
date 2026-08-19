"""The renderer — where Tango's honesty becomes a property of the text.

Outcome sentences are *composed from ledger state*, never authored by a model.
A model may explain, diagnose and add colour; it may not write the verb that
says something happened. That is the difference between an instruction in a
prompt (one jailbreak away from being violated silently) and a unit test.

The rule is narrow and absolute:

    A completion verb may appear only if the ledger says the action was VERIFIED.

Everything else — tone, personality, explanation — is free. See docs/02 §3.3 and
docs/06 §6: this is not compliance paperwork, it is the mechanism that makes the
relationship possible. Tony Stark never asks Jarvis "are you sure?".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tango.types import ActionStatus, Grounding, TaskStatus

# Verbs that assert an effect on the world. Only VERIFIED licenses these.
COMPLETION_VERBS: frozenset[str] = frozenset(
    {
        "started", "stopped", "sent", "created", "deleted", "removed", "launched",
        "opened", "closed", "restarted", "installed", "updated", "saved", "moved",
        "renamed", "committed", "pushed", "deployed", "fixed", "killed", "freed",
        "scheduled", "cancelled", "added",
    }
)

# What each ledger state is allowed to say. The distinction between VERIFIED and
# UNVERIFIABLE is the entire product.
LICENSED_VERBS: dict[ActionStatus, frozenset[str]] = {
    ActionStatus.VERIFIED: COMPLETION_VERBS,
    ActionStatus.UNVERIFIABLE: frozenset({"submitted", "requested", "attempted", "asked"}),
    ActionStatus.REFUTED: frozenset({"failed", "could not", "was unable"}),
    ActionStatus.PENDING_CONFIRM: frozenset({"ready to", "waiting to", "would"}),
    ActionStatus.UNDO_WINDOW: frozenset({"about to", "will"}),
    ActionStatus.DENIED: frozenset({"refused", "declined", "blocked"}),
    ActionStatus.EXPIRED: frozenset({"expired", "timed out"}),
    ActionStatus.CANCELLED: frozenset({"cancelled"}),
}

_WORD = re.compile(r"[a-z']+")


class ClaimViolation(AssertionError):
    """Text asserted an outcome the ledger does not support."""


def find_unlicensed_claims(text: str, status: ActionStatus) -> list[str]:
    """Return completion verbs present in ``text`` that ``status`` does not license.

    This is the function CI runs over every recorded task. If it ever returns a
    non-empty list for shipped text, the guarantee has been broken.

    A few verbs are legitimate for their own terminal state — "cancelled" is an
    accurate report of a CANCELLED action, not a claim that work happened — so
    each status licenses its own vocabulary in addition to nothing else.
    """
    if status == ActionStatus.VERIFIED:
        return []
    words = set(_WORD.findall(text.lower()))
    permitted = LICENSED_VERBS.get(status, frozenset())
    return sorted((words & COMPLETION_VERBS) - permitted)


def assert_licensed(text: str, status: ActionStatus) -> None:
    offenders = find_unlicensed_claims(text, status)
    if offenders:
        raise ClaimViolation(
            f"text claims {offenders} but action status is {status}: {text!r}"
        )


# ----------------------------------------------------------------- rendering


@dataclass(frozen=True)
class StepOutcome:
    """One executed step, as the renderer sees it."""

    label: str
    status: ActionStatus
    detail: str = ""
    evidence: str = ""


_PHRASE: dict[ActionStatus, str] = {
    ActionStatus.VERIFIED: "{label}",
    ActionStatus.UNVERIFIABLE: "{label} — submitted, but I couldn't confirm it",
    ActionStatus.REFUTED: "{label} — failed: {detail}",
    ActionStatus.PENDING_CONFIRM: "{label} — ready, waiting on you",
    ActionStatus.UNDO_WINDOW: "{label} — about to run",
    ActionStatus.DENIED: "{label} — refused: {detail}",
    ActionStatus.EXPIRED: "{label} — the confirmation timed out; nothing ran",
    ActionStatus.CANCELLED: "{label} — cancelled",
}


def render_step(outcome: StepOutcome) -> str:
    template = _PHRASE.get(outcome.status, "{label} — {detail}")
    text = template.format(label=outcome.label, detail=outcome.detail or "no detail")
    assert_licensed(text, outcome.status)
    return text


def render_task(steps: list[StepOutcome], task_status: TaskStatus) -> str:
    """Compose the user-facing outcome line.

    Leads with what happened. ``PARTIAL`` is stated as partial — never rounded up
    to success, which is the failure mode this whole subsystem exists to prevent.
    """
    if not steps:
        return "Nothing to do."

    parts = [render_step(s) for s in steps]
    body = " · ".join(parts)

    # Task-level commentary must contain no completion verbs at all: it describes
    # the *shape* of the outcome, and a verb here would be a claim no single
    # action backs. (An earlier draft said "Stopped there." — which reads as an
    # effect on the world and tripped the checker. Correctly.)
    suffix = ""
    if task_status is TaskStatus.PARTIAL:
        good = sum(1 for s in steps if s.status is ActionStatus.VERIFIED)
        suffix = f"\n({good} of {len(steps)} confirmed — the rest are above.)"
    elif task_status is TaskStatus.FAILED:
        suffix = "\nNothing further ran."

    if suffix:
        offenders = sorted(set(_WORD.findall(suffix.lower())) & COMPLETION_VERBS)
        if offenders:
            raise ClaimViolation(
                f"task-level commentary asserts {offenders}, which no single "
                f"action backs: {suffix!r}"
            )
    return body + suffix


# ---------------------------------------------------------- factual grounding


_GROUNDING_PREFIX: dict[Grounding, str] = {
    # A grounded answer needs no hedge; the source is available on request.
    Grounding.GROUNDED: "",
    # Must be audible. In speech there is no footnote to fall back on.
    Grounding.RECALLED: "From memory, not checked — ",
    Grounding.CONFLICTED: "Sources disagree — ",
}


def render_fact(answer: str, grounding: Grounding, *, source: str | None = None) -> str:
    """Facts have no postcondition, so citation is the analogue of verification
    (docs/13 §4.3). ``RECALLED`` always carries its marker."""
    prefix = _GROUNDING_PREFIX[grounding]
    text = f"{prefix}{answer}"
    if grounding is Grounding.RECALLED:
        text += " Want me to check?"
    elif grounding is Grounding.GROUNDED and source:
        text += f" ({source})"
    return text


# ------------------------------------------------- false-success tripwire

# LLM judges score AUROC <=0.65 on false success because they anchor on
# confident closing language — which is exactly what false success produces
# (docs/11 Finding 1). A dumb lexical detector scores 0.83-0.95 at 3300x lower
# latency. This is telemetry, never a gate: it flags for review, it does not judge.
_CONFIDENT_CLOSERS = (
    "all set", "you're all set", "everything is working", "successfully completed",
    "all done", "task complete", "everything looks good", "no further action",
)


def false_success_signal(text: str, statuses: list[ActionStatus]) -> float:
    """Cheap tripwire: confident closing language over non-VERIFIED outcomes.

    Returns 0.0-1.0. High values mean "a human should look at this response",
    not "this is wrong".
    """
    if not statuses or all(s is ActionStatus.VERIFIED for s in statuses):
        return 0.0
    lowered = text.lower()
    hits = sum(1 for phrase in _CONFIDENT_CLOSERS if phrase in lowered)
    unverified = sum(1 for s in statuses if s is not ActionStatus.VERIFIED) / len(statuses)
    return min(1.0, (hits * 0.4) + (unverified * 0.3 if hits else 0.0))


def summarize_for_log(steps: list[StepOutcome]) -> dict[str, Any]:
    return {
        "steps": len(steps),
        "verified": sum(1 for s in steps if s.status is ActionStatus.VERIFIED),
        "statuses": [s.status.value for s in steps],
    }
