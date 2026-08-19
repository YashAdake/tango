"""S0.5 acceptance: Tango cannot claim what the ledger does not support.

The exhaustive test at the bottom is the one that matters — it sweeps every
completion verb against every non-VERIFIED status, which is the property CI will
enforce over recorded tasks for the life of the project.
"""

from __future__ import annotations

import pytest

from tango.render import (
    COMPLETION_VERBS,
    LICENSED_VERBS,
    ClaimViolation,
    StepOutcome,
    assert_licensed,
    false_success_signal,
    find_unlicensed_claims,
    render_fact,
    render_step,
    render_task,
)
from tango.types import ActionStatus, Grounding, TaskStatus

# --------------------------------------------------------------- licensing


def test_verified_licenses_completion_verbs():
    assert find_unlicensed_claims("Started the database.", ActionStatus.VERIFIED) == []


def test_unverifiable_cannot_say_sent():
    offenders = find_unlicensed_claims("Sent the email.", ActionStatus.UNVERIFIABLE)
    assert offenders == ["sent"]


def test_refuted_cannot_say_started():
    with pytest.raises(ClaimViolation):
        assert_licensed("Started the API.", ActionStatus.REFUTED)


def test_pending_cannot_claim_completion():
    with pytest.raises(ClaimViolation):
        assert_licensed("Deleted the branch.", ActionStatus.PENDING_CONFIRM)


@pytest.mark.parametrize("verb", sorted(COMPLETION_VERBS))
@pytest.mark.parametrize(
    "status",
    [
        ActionStatus.UNVERIFIABLE,
        ActionStatus.REFUTED,
        ActionStatus.PENDING_CONFIRM,
        ActionStatus.DENIED,
        ActionStatus.EXPIRED,
        ActionStatus.CANCELLED,
    ],
)
def test_no_completion_verb_survives_a_non_verified_status(verb, status):
    """Exhaustive sweep: the guarantee holds for every verb x every state.

    The one carve-out is a verb that names its own terminal state — "cancelled"
    on a CANCELLED action reports the state, it does not claim work happened.
    """
    expected = [] if verb in LICENSED_VERBS.get(status, frozenset()) else [verb]
    assert find_unlicensed_claims(f"I {verb} it.", status) == expected


# ---------------------------------------------------------------- rendering


def test_render_step_is_clean_when_verified():
    text = render_step(StepOutcome("DB up", ActionStatus.VERIFIED))
    assert text == "DB up"


def test_render_step_states_unverifiable_honestly():
    text = render_step(StepOutcome("Email to Rahul", ActionStatus.UNVERIFIABLE))
    assert "couldn't confirm" in text
    assert find_unlicensed_claims(text, ActionStatus.UNVERIFIABLE) == []


def test_render_step_surfaces_the_failure_reason():
    text = render_step(
        StepOutcome("API", ActionStatus.REFUTED, detail="still returning 502")
    )
    assert "502" in text and "failed" in text


def test_partial_is_never_rounded_up_to_success():
    """The failure mode the whole subsystem exists to prevent."""
    steps = [
        StepOutcome("DB up", ActionStatus.VERIFIED),
        StepOutcome("API", ActionStatus.REFUTED, detail="port in use"),
        StepOutcome("Editor open", ActionStatus.VERIFIED),
    ]
    text = render_task(steps, TaskStatus.PARTIAL)
    assert "2 of 3 confirmed" in text
    assert "port in use" in text


def test_every_rendered_step_is_self_policing():
    """render_step asserts internally; a bad template would raise, not ship."""
    for status in ActionStatus:
        if status in (ActionStatus.PROPOSED, ActionStatus.POLICY_HELD,
                      ActionStatus.CONFIRMED, ActionStatus.COMMITTING):
            continue
        render_step(StepOutcome("thing", status, detail="because"))


def test_empty_task_says_so():
    assert render_task([], TaskStatus.COMPLETED) == "Nothing to do."


# ------------------------------------------------------------------- facts


def test_recalled_facts_carry_an_audible_marker():
    text = render_fact("Arthur Morgan.", Grounding.RECALLED)
    assert text.startswith("From memory")
    assert "Want me to check?" in text


def test_grounded_facts_need_no_hedge():
    text = render_fact("Arthur Morgan.", Grounding.GROUNDED, source="rockstargames.com")
    assert text.startswith("Arthur Morgan.")
    assert "memory" not in text


def test_conflicted_facts_say_so_first():
    assert render_fact("It varies.", Grounding.CONFLICTED).startswith("Sources disagree")


# --------------------------------------------------------------- tripwire


def test_tripwire_ignores_fully_verified_work():
    assert false_success_signal("All done.", [ActionStatus.VERIFIED]) == 0.0


def test_tripwire_flags_confident_language_over_unverified_outcomes():
    signal = false_success_signal(
        "All set — everything is working.",
        [ActionStatus.UNVERIFIABLE, ActionStatus.REFUTED],
    )
    assert signal > 0.5


def test_tripwire_stays_quiet_on_honest_reporting():
    assert (
        false_success_signal(
            "The API failed to come up; the database is fine.",
            [ActionStatus.REFUTED, ActionStatus.VERIFIED],
        )
        == 0.0
    )
