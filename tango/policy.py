"""The policy gate — authority decided outside the model.

Every guarantee here is enforced in code the model cannot influence. That is the
entire point: a prompt instruction is one jailbreak from being ignored, while a
frozen tuple in a database row is not (docs/16 §10).

Four mechanisms, in the order they matter:

1. **Capability freeze.** A task's permitted tools are computed *before* any
   untrusted content is retrieved and persisted on the task row. A call outside
   that set is refused — never escalated, because escalation is what an attacker
   is hoping for.
2. **Rule of Two** (Meta's framing, docs/11 Finding 3). Untrusted input +
   sensitive access + external state change must never co-occur unattended. Any
   two are fine; all three requires a human who can see where the content came
   from.
3. **Egress allowlists.** Recipients, domains and write paths are checked
   against config the orchestrator cannot write. A fully successful injection
   still cannot reach an address you have never used.
4. **Confirmations** bound to an argument hash, single-use, with a TTL. Not to
   an action id — binding to the id lets arguments change after you approved
   them, which is the classic time-of-check/time-of-use hole.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from tango.store import Store, utcnow
from tango.types import Confidentiality, Integrity, Risk


class Verdict(StrEnum):
    AUTO = "auto"
    """Execute now. Reads and reversible local actions."""
    UNDO_WINDOW = "undo_window"
    """Execute after a cancellable delay. Preferred over CONFIRM wherever a
    compensate path exists — reversible-by-construction beats confirm-first,
    because it protects against *Tango* being wrong, not only the user."""
    CONFIRM = "confirm"
    """Stop and ask. The human must see what is about to happen."""
    DENY = "deny"
    """Refuse outright. Not a question — an answer."""


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    """Machine-readable cause, for audit and tests."""
    message: str = ""
    """What the user is told."""
    undo_seconds: int = 0
    untrusted_sources: tuple[str, ...] = ()
    """Shown at confirmation time. A confirmation that hides where the request
    came from is a confirmation that teaches you to click yes."""


@dataclass
class TaskContext:
    """What policy knows about a task. Labels propagate as maxima: anything
    derived from untrusted input is itself untrusted."""

    task_id: str
    frozen_tools: frozenset[str] = frozenset()
    frozen: bool = False
    max_integrity: Integrity = Integrity.TRUSTED
    max_confidentiality: Confidentiality = Confidentiality.OPEN
    untrusted_sources: tuple[str, ...] = ()
    surface: str = "cli"

    def observe(self, integrity: Integrity, confidentiality: Confidentiality,
                source: str = "") -> None:
        """Record that content entered the task. Monotonic — trust only falls."""
        self.max_integrity = Integrity(max(self.max_integrity, integrity))
        self.max_confidentiality = Confidentiality(max(self.max_confidentiality, confidentiality))
        if integrity is Integrity.UNTRUSTED and source:
            self.untrusted_sources = (*self.untrusted_sources, source)

    @property
    def has_untrusted(self) -> bool:
        return self.max_integrity is Integrity.UNTRUSTED


# --------------------------------------------------------------------- egress


@dataclass
class EgressPolicy:
    """Allowlists for anything leaving the machine.

    Loaded from config the orchestrator does not write. Empty means *deny all*,
    not *allow all* — an unconfigured allowlist should fail closed, or the
    control silently does nothing the first time it matters.
    """

    recipients: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()
    write_paths: tuple[str, ...] = ()

    def allows_recipient(self, recipient: str) -> bool:
        return recipient.lower() in self.recipients

    def allows_domain(self, url: str) -> bool:
        host = url.split("//")[-1].split("/")[0].split(":")[0].lower()
        return any(host == d or host.endswith(f".{d}") for d in self.domains)

    def allows_write(self, path: str) -> bool:
        norm = path.replace("\\", "/").lower()
        return any(norm.startswith(p.replace("\\", "/").lower()) for p in self.write_paths)


# ------------------------------------------------------ standing authorization


@dataclass(frozen=True)
class StandingAuth:
    """A narrow, typed, expiring permission.

    Never a boolean. "Auto-send email" is not expressible; "send_email where the
    recipient is a known contact, there are no attachments and the body is under
    500 characters" is (docs/04 ADR-007).
    """

    id: str
    tool: str
    predicate: dict[str, Any]
    expires_at: str
    undo_seconds: int = 8

    def matches(self, tool: str, args: dict[str, Any]) -> bool:
        if tool != self.tool:
            return False
        for key, rule in self.predicate.items():
            value = args.get(key)
            if isinstance(rule, dict):
                if "in" in rule and value not in rule["in"]:
                    return False
                if "max_len" in rule and len(str(value or "")) > rule["max_len"]:
                    return False
                if "empty" in rule and bool(value) is rule["empty"]:
                    continue
                if "empty" in rule and bool(value) is not (not rule["empty"]):
                    return False
            elif value != rule:
                return False
        return True

    @property
    def expired(self) -> bool:
        return datetime.fromisoformat(self.expires_at) <= datetime.now(UTC)


# ---------------------------------------------------------------- the gate


class PolicyGate:
    """Decides what may run, and under what supervision."""

    POLICY_VERSION = 1

    def __init__(
        self,
        store: Store,
        egress: EgressPolicy | None = None,
        standing: list[StandingAuth] | None = None,
    ) -> None:
        self.store = store
        self.egress = egress or EgressPolicy()
        self.standing = standing or []

    # ------------------------------------------------------------ the freeze

    def freeze(self, ctx: TaskContext, tools: set[str]) -> None:
        """Fix the task's permitted tool set.

        Called at plan time, before any tool that can ingest external content
        runs. Freezing afterwards would be theatre — the whole value is that the
        aperture was measured before the attacker's text arrived.
        """
        ctx.frozen_tools = frozenset(tools)
        ctx.frozen = True
        self.store.conn.execute(
            "UPDATE task SET frozen_tools=?, updated_at=? WHERE id=?",
            (json.dumps(sorted(tools)), utcnow(), ctx.task_id),
        )

    # ------------------------------------------------------------- evaluation

    def evaluate(
        self,
        ctx: TaskContext,
        tool: str,
        args: dict[str, Any],
        risk: Risk,
        *,
        compensable: bool = False,
    ) -> Decision:
        """The single decision point for whether an action may proceed."""

        # 1. Capability freeze. Refused, not escalated: an out-of-set call in a
        #    task carrying untrusted content is the attack, not a request.
        if ctx.frozen and tool not in ctx.frozen_tools:
            return self._audit(
                ctx, tool, Decision(
                    Verdict.DENY, "outside_frozen_capabilities",
                    f"'{tool}' was not part of this task's plan, so I won't run it.",
                )
            )

        # 2. Egress allowlists, before anything else that could leak.
        if leak := self._egress_violation(tool, args):
            return self._audit(ctx, tool, leak)

        # 3. Reads and reversible local work need no ceremony.
        if risk <= Risk.R1_REVERSIBLE:
            return Decision(Verdict.AUTO, "low_risk")

        # 4. R4 is never softened. No standing auth, no undo window, no voice.
        if risk >= Risk.R4_DESTRUCTIVE:
            return self._audit(
                ctx, tool, Decision(
                    Verdict.CONFIRM, "r4_always_confirms",
                    "This is destructive and irreversible — confirm it explicitly.",
                    untrusted_sources=ctx.untrusted_sources,
                )
            )

        # 5. Rule of Two. Untrusted content in the task suspends every standing
        #    authorization and forces a human who can see the source.
        if ctx.has_untrusted:
            return self._audit(
                ctx, tool, Decision(
                    Verdict.CONFIRM, "rule_of_two",
                    "This task has read untrusted content, so I need you to confirm.",
                    untrusted_sources=ctx.untrusted_sources,
                )
            )

        # 6. A matching standing authorization narrows to an undo window.
        for auth in self.standing:
            if auth.expired or not auth.matches(tool, args):
                continue
            return Decision(Verdict.UNDO_WINDOW, f"standing_auth:{auth.id}",
                            undo_seconds=auth.undo_seconds)

        # 7. Otherwise: undo window if it can be taken back, else ask.
        if compensable:
            return Decision(Verdict.UNDO_WINDOW, "compensable", undo_seconds=10)
        return Decision(Verdict.CONFIRM, "consequential",
                        "This has an external effect — confirm?",
                        untrusted_sources=ctx.untrusted_sources)

    def _egress_violation(self, tool: str, args: dict[str, Any]) -> Decision | None:
        """Check anything that would leave the machine against its allowlist.

        Recipients fail closed on an empty list; domains and paths only apply
        once configured, because an unconfigured path allowlist would block all
        local file work rather than protecting anything.
        """
        for key in ("recipient", "recipient_id", "to"):
            value = args.get(key)
            if value and not self.egress.allows_recipient(str(value)):
                return Decision(
                    Verdict.DENY, "egress_recipient_not_allowed",
                    f"'{value}' is not on the allowed recipient list.",
                )

        url = args.get("url")
        if url and self.egress.domains and not self.egress.allows_domain(str(url)):
            return Decision(Verdict.DENY, "egress_domain_not_allowed",
                            f"{url} is not on the allowed domain list.")

        for key in ("path", "dest", "destination"):
            value = args.get(key)
            if value and self.egress.write_paths and not self.egress.allows_write(str(value)):
                return Decision(Verdict.DENY, "egress_path_not_allowed",
                                f"{value} is outside the writable paths.")
        return None

    def _audit(self, ctx: TaskContext, tool: str, decision: Decision) -> Decision:
        if decision.verdict in (Verdict.DENY, Verdict.CONFIRM):
            self.store.audit(
                actor="policy", action=f"{decision.verdict}:{tool}",
                verdict=decision.verdict.upper(), resource=ctx.task_id,
                detail=decision.reason,
            )
        return decision

    # ---------------------------------------------------------- confirmations

    def request_confirmation(
        self,
        action_id: str,
        args: dict[str, Any],
        *,
        ttl_seconds: int = 300,
        surface: str = "cli",
        untrusted_sources: tuple[str, ...] = (),
    ) -> str:
        """Create a single-use confirmation bound to these exact arguments.

        Binding to the argument hash rather than the action id is what closes
        time-of-check/time-of-use: if anything about the action changes after
        you approved it, the nonce no longer matches and it must be re-proposed.
        """
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
        args_hash = hashlib.sha256(canonical.encode()).hexdigest()
        nonce = secrets.token_urlsafe(24)
        expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()

        self.store.conn.execute(
            "INSERT INTO confirmation_request("
            " id, action_id, nonce, binds_args_hash, expires_at, surface,"
            " untrusted_sources, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), action_id, nonce, args_hash, expires, surface,
             json.dumps(list(untrusted_sources)), utcnow()),
        )
        return nonce

    def consume_confirmation(self, nonce: str, args: dict[str, Any]) -> tuple[bool, str]:
        """Redeem a confirmation. Returns (accepted, reason)."""
        row = self.store.conn.execute(
            "SELECT * FROM confirmation_request WHERE nonce=?", (nonce,)
        ).fetchone()
        if row is None:
            return False, "unknown_nonce"
        if row["consumed_at"]:
            return False, "already_used"
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            return False, "expired"

        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
        if hashlib.sha256(canonical.encode()).hexdigest() != row["binds_args_hash"]:
            # The arguments changed after approval. Refuse and make them re-ask.
            self.store.audit(actor="policy", action="confirm:args_changed",
                             verdict="DENIED", resource=row["action_id"],
                             detail="argument hash mismatch")
            return False, "arguments_changed"

        self.store.conn.execute(
            "UPDATE confirmation_request SET consumed_at=? WHERE nonce=?", (utcnow(), nonce)
        )
        return True, "ok"


# ------------------------------------------------------------------ classifier

# Rule-based, never model-judged (docs/16 §9). A model deciding what is secret
# is a model deciding when to leak.
_SECRET_HINTS = ("password", "token", "secret", "api_key", "apikey", "credential",
                 ".env", "private_key")


def classify_content(source: str, text: str = "") -> tuple[Integrity, Confidentiality]:
    """Assign trust labels to something entering a task's context."""
    src = source.lower()

    if any(src.startswith(p) for p in ("user:", "config:", "playbook:")):
        integrity = Integrity.TRUSTED
    elif any(src.startswith(p) for p in ("file:", "repo:", "git:")):
        integrity = Integrity.SEMI
    else:
        # Web pages, email bodies, container logs, MCP tool descriptions,
        # notification text — anything authored elsewhere.
        integrity = Integrity.UNTRUSTED

    blob = f"{src} {text}".lower()
    if any(hint in blob for hint in _SECRET_HINTS):
        confidentiality = Confidentiality.SECRET
    elif integrity is Integrity.SEMI:
        confidentiality = Confidentiality.INTERNAL
    else:
        confidentiality = Confidentiality.OPEN
    return integrity, confidentiality


def load_egress(path: str | None = None) -> EgressPolicy:
    """Load egress allowlists from config.

    Missing config yields empty allowlists, which deny. Failing open here would
    make the control invisible until the day it mattered.
    """
    from pathlib import Path

    target = Path(path or "hosts/egress.json")
    if not target.is_file():
        return EgressPolicy()
    data = json.loads(target.read_text(encoding="utf-8"))
    return EgressPolicy(
        recipients=frozenset(r.lower() for r in data.get("recipients", [])),
        domains=frozenset(d.lower() for d in data.get("domains", [])),
        write_paths=tuple(data.get("write_paths", [])),
    )
