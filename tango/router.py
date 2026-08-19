"""The router: utterance → (playbook, params) | clarify | refuse.

Phase 0 deliberately ships a **regex router with no model in it at all**. The
point is to prove the ledger, verification and claim licensing against a
deterministic brain first — so that when the model arrives in S0.7 and something
breaks, there is exactly one suspect (docs/16 §14.2).

The interface here is the one the model will inherit unchanged. That is the
whole trick: swapping brains must not touch anything downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tango.projects import AmbiguousResolution, ProjectRegistry, ResolutionError


class Route(StrEnum):
    PLAYBOOK = "playbook"
    CLARIFY = "clarify"
    REFUSE = "refuse"
    DECLINE = "decline"
    """Understood, but out of scope — different from a refusal on safety grounds."""


@dataclass
class Decision:
    route: Route
    playbook_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    message: str = ""
    confidence: float = 1.0
    candidates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    playbook: str
    """Named groups in the pattern become playbook params."""
    static: dict[str, Any] = field(default_factory=dict)
    resolve_project: str | None = None
    """Name of the group whose value should go through the project resolver."""


# Refusals come first: an utterance that matches one must never fall through to
# a playbook, whatever else it looks like.
REFUSALS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\b(drop|delete|wipe|destroy)\b.*\b(database|db|data)\b", re.I),
     "R4_destructive", "Deleting a database is not something I'll do."),
    (re.compile(r"\bcurl\b.*\|\s*(sh|bash)", re.I),
     "arbitrary_shell", "I don't pipe downloaded scripts into a shell."),
    (re.compile(r"\b(take|bring)\s+(down\s+)?prod(uction)?\b", re.I),
     "prod_destructive", "I won't take production down."),
    (re.compile(r"\bsend\b.*\.env\b|\b\.env\b.*\bsend\b", re.I),
     "secret_egress", "That file holds credentials; I won't send it anywhere."),
    (re.compile(r"\bturn off\b.*\bconfirmation", re.I),
     "policy_change_requires_config",
     "Confirmation policy is a config edit, not a request."),
    (re.compile(r"\bwipe\b.*\b(workspace|everything|all)\b", re.I),
     "R4_destructive", "That's too broad and irreversible for me to run."),
)

OUT_OF_SCOPE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(order|buy)\b.*\b(pizza|food|coffee)\b", re.I), "out_of_scope"),
    (re.compile(r"\bbook\b.*\b(cab|taxi|flight|ticket)\b", re.I), "out_of_scope"),
    (re.compile(r"\bbank\s+balance\b|\btransfer\b.*\bmoney\b", re.I), "financial_never"),
    (re.compile(r"\bpost\b.*\b(linkedin|twitter|instagram)\b", re.I), "v1_drafts_only"),
)

RULES: tuple[Rule, ...] = (
    # Order matters: more specific patterns first.
    # English puts the particle either side of the object: "shut down everything"
    # and "shut everything down" are the same request. Longest alternatives
    # first, or "shut" swallows "shut down".
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?:shutdown|shut\s+down|shut|stop|kill|close)\s+"
                    r"(?:everything|all|it\s+all)(?:\s+down)?\s*$", re.I), "shutdown_all"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?:start|run|launch|fire up|boot)\s+"
                    r"(?P<project>.+?)\s*$", re.I), "dev_up", resolve_project="project"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?:stop|kill|shut down|halt)\s+"
                    r"(?P<project>.+?)\s*$", re.I), "dev_down", resolve_project="project"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?:what'?s?|how'?s?)\s+"
                    r"(?:the\s+)?(?:state|status)\s+of\s+everything\s*\??\s*$", re.I),
         "status_all"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?status\s*$", re.I), "status_all"),
)

# Utterances that name no target and have no prior context.
NEEDS_TARGET = re.compile(
    r"^\s*(?:tango[,\s]+)?(start|stop|kill|restart|fix|open|run)\s+(it|that|this)?\s*$", re.I
)


class Router:
    """Deterministic first pass. In S0.7 a model slots in behind this same
    interface as a fallback for what the rules do not match."""

    def __init__(self, projects: ProjectRegistry, known_playbooks: set[str] | None = None) -> None:
        self.projects = projects
        self.known = known_playbooks or set()

    def route(self, utterance: str, context: dict[str, Any] | None = None) -> Decision:
        text = utterance.strip()
        context = context or {}
        if not text:
            return Decision(Route.CLARIFY, reason="empty", message="I didn't catch that.")

        for pattern, reason, message in REFUSALS:
            if pattern.search(text):
                return Decision(Route.REFUSE, reason=reason, message=message)

        for pattern, reason in OUT_OF_SCOPE:
            if pattern.search(text):
                return Decision(
                    Route.DECLINE, reason=reason,
                    message="That's outside what I do.",
                )

        if m := NEEDS_TARGET.match(text):
            verb = m.group(1).lower()
            prior = context.get("prior_project")
            if prior:
                pb = "dev_down" if verb in ("stop", "kill") else "dev_up"
                return Decision(Route.PLAYBOOK, playbook_id=pb, params={"project": prior},
                                confidence=0.7)
            return Decision(Route.CLARIFY, reason="ambiguous_project",
                            message=f"{verb.capitalize()} which project?",
                            candidates=self.projects.ids())

        for rule in RULES:
            m = rule.pattern.match(text)
            if not m:
                continue
            if rule.playbook not in self.known and self.known:
                continue

            params: dict[str, Any] = dict(rule.static)
            params.update({k: v for k, v in m.groupdict().items() if v})

            if rule.resolve_project and rule.resolve_project in params:
                try:
                    project = self.projects.resolve(str(params[rule.resolve_project]))
                except AmbiguousResolution as amb:
                    return Decision(
                        Route.CLARIFY, reason="ambiguous_project", message=str(amb),
                        candidates=[c.id for c in amb.candidates],
                    )
                except ResolutionError as err:
                    return Decision(Route.CLARIFY, reason="unknown_project",
                                    message=str(err), candidates=self.projects.ids())
                params[rule.resolve_project] = project.id
                params["_project"] = project

            return Decision(Route.PLAYBOOK, playbook_id=rule.playbook, params=params)

        return Decision(
            Route.CLARIFY, reason="no_match",
            message="I don't have a playbook for that yet.",
            candidates=sorted(self.known),
        )
