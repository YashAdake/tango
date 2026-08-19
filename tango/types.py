"""Core enums and value types.

These are the vocabulary the whole system speaks. Two are load-bearing beyond
their apparent simplicity:

* :class:`VerifyStatus` — four values, not two. The gap between "it worked" and
  "it errored" is where agents lie; ``UNVERIFIABLE`` names that gap so the
  renderer can speak it honestly (docs/02 §4.2).
* :class:`Risk` — determines the policy verdict, and R4 can never be softened
  by a standing authorization or an undo window (docs/16 §12).
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class Risk(IntEnum):
    """Blast radius of a tool call. Ordering is meaningful; comparisons are used."""

    R0_READ = 0
    """Read-only. Auto-executes when permission exists."""

    R1_REVERSIBLE = 1
    """Local, reversible. Auto-executes; a compensate chain is recorded."""

    R2_EXTERNAL = 2
    """External side effect, low blast radius. Undo-window or preview."""

    R3_CONSEQUENTIAL = 3
    """Consequential. Explicit confirmation, or a narrow standing authorization."""

    R4_DESTRUCTIVE = 4
    """Irreversible or destructive. Hard confirm on a visual surface. No voice,
    no standing authorization, no undo-window substitute — ever."""


class ActionStatus(StrEnum):
    """Effect-ledger lifecycle (docs/16 §7.2).

    ``COMMITTING`` is the crash-critical state: a row left here means an external
    call may or may not have landed. Recovery reconciles it against the provider
    by idempotency key before any new work is accepted — it is never blindly
    retried (docs/17 A3, "verify-before-retry").
    """

    PROPOSED = "PROPOSED"
    POLICY_HELD = "POLICY_HELD"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    CONFIRMED = "CONFIRMED"
    UNDO_WINDOW = "UNDO_WINDOW"
    COMMITTING = "COMMITTING"
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    UNVERIFIABLE = "UNVERIFIABLE"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_ACTION_STATES

    @property
    def is_in_flight(self) -> bool:
        """True while an external effect may already have happened."""
        return self is ActionStatus.COMMITTING


_TERMINAL_ACTION_STATES = frozenset(
    {
        ActionStatus.VERIFIED,
        ActionStatus.REFUTED,
        ActionStatus.UNVERIFIABLE,
        ActionStatus.DENIED,
        ActionStatus.EXPIRED,
        ActionStatus.CANCELLED,
    }
)


class TaskStatus(StrEnum):
    """Task lifecycle. ``PARTIAL`` never collapses into ``COMPLETED``."""

    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    NEEDS_INPUT = "NEEDS_INPUT"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_TASK_STATES


_TERMINAL_TASK_STATES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.PARTIAL,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)


class VerifyStatus(StrEnum):
    """Outcome of an independent postcondition check.

    ``UNVERIFIABLE`` is success-adjacent, not an error: the action was submitted
    and the world may well have changed, but no independent check was available.
    Saying so is the honest answer, and it licenses different verbs than
    ``VERIFIED`` does (docs/02 §4.2).
    """

    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class Integrity(IntEnum):
    """How much the *source* of a context item is trusted to issue instructions.

    Propagates as a maximum through derivations: anything derived from untrusted
    input is itself untrusted. Drives the capability freeze and the Rule of Two
    (docs/16 §10).
    """

    TRUSTED = 0
    """The owner's own utterance, Tango's config, playbook definitions."""

    SEMI = 1
    """The owner's own files, repos, commit messages."""

    UNTRUSTED = 2
    """Authored elsewhere: web pages, email bodies, container logs, MCP tool
    descriptions, notification text, dependency READMEs."""


class Confidentiality(IntEnum):
    """How restricted the *content* is on egress. Propagates as a maximum."""

    OPEN = 0
    INTERNAL = 1
    """Project detail, paths, non-secret config. Redact before cloud egress."""
    SECRET = 2
    """Credentials, tokens, contact PII. Never egresses; forces LOCAL_ONLY."""


class PrivacyClass(StrEnum):
    """Per-task egress policy. Classification is rule-based, never model-judged."""

    LOCAL_ONLY = "LOCAL_ONLY"
    """Never leaves the machine. If the local tier cannot handle it, Tango says
    so rather than silently escalating."""

    REDACTED_OK = "REDACTED_OK"
    """Default. Cloud tier permitted after a deterministic redaction pass."""

    OPEN = "OPEN"


class Grounding(StrEnum):
    """Honesty state for *facts*, which have no postcondition to verify.

    Citation is the analogue of verification here. ``RECALLED`` carries a
    mandatory audible marker — in speech there is no footnote to fall back on
    (docs/13 §4.3).
    """

    GROUNDED = "GROUNDED"
    RECALLED = "RECALLED"
    CONFLICTED = "CONFLICTED"
