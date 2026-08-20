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
    scope_to_context: bool = False
    """Narrow to the conversation's current project when the utterance names
    none. "How many uncommitted files" means *here*, if here is established."""


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
    (re.compile(r"\bforward\b.*\b(all|every)\b.*\b(mail|email|message)", re.I),
     "R4_bulk", "Bulk-forwarding your mail isn't something I'll set up."),
)

OUT_OF_SCOPE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(order|buy)\b.*\b(pizza|food|coffee)\b", re.I), "out_of_scope"),
    (re.compile(r"\bbook\b.*\b(cab|taxi|flight|ticket)\b", re.I), "out_of_scope"),
    (re.compile(r"\bbank\s+balance\b|\btransfer\b.*\bmoney\b", re.I), "financial_never"),
    (re.compile(r"\bpost\b.*\b(linkedin|twitter|instagram)\b", re.I), "v1_drafts_only"),
    (re.compile(r"\b(delete|close|deactivate)\b.*\b(instagram|twitter|facebook|"
                r"linkedin|google|social)\b.*\baccount\b", re.I), "out_of_scope"),
    (re.compile(r"\b(restart|reboot|shut\s*down|sleep|hibernate)\b\s+"
                r"(my\s+|the\s+)?(laptop|machine|computer|pc|windows)\b", re.I),
     "no_playbook_v1"),
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
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?how'?s?\s+everything(\s+looking)?\s*\??\s*$", re.I),
         "status_all"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?:is|are)\s+prod(uction)?\s+"
                    r"(?:ok|up|healthy|fine|good)\s*\??\s*$", re.I),
         "prod_check", static={"project": "*"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?prod(uction)?\s+(?:status|check)\s*$", re.I),
         "prod_check", static={"project": "*"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?prod\s+theek\s+chal\s+raha\s+hai(\s+na)?\s*\??\s*$",
                    re.I),
         "prod_check", static={"project": "*"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?what\s+did\s+i\s+ship\s+"
                    r"(?:this\s+week|lately|recently)\s*\??\s*$", re.I),
         "git_digest", static={"since": "7d"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?what\s+did\s+i\s+ship\s+today\s*\??\s*$", re.I),
         "git_digest", static={"since": "1d"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?aaj\s+kya\s+kya\s+ship\s+kiya(\s+maine)?\s*\??\s*$",
                    re.I),
         "git_digest", static={"since": "1d"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?:what'?s?\s+)?(?:hogging|holding|on|using)\s+"
                    r"port\s+(?P<port>\d+)\s*\??\s*$", re.I),
         "port_free", static={"mode": "inspect"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?port\s+(?P<port>\d+)\s+pe\s+kaun\s+"
                    r"baitha\s+hai(\s+\w+)?\s*\??\s*$", re.I),
         "port_free", static={"mode": "inspect"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?free\s+(?:up\s+)?port\s+(?P<port>\d+)\s*$", re.I),
         "port_free", static={"mode": "kill"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?:why|what\s+happened)\s*\??\s*$", re.I),
         "diagnose", scope_to_context=True),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?why\s+(?:is|are)\s+(?:the\s+)?"
                    r"(?P<target>.+?)\s+(?:down|broken|failing|not\s+working|dead)"
                    r"\s*\??\s*$", re.I), "diagnose"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?what(?:'s|\s+is|s)?\s+(?:wrong|broken|going\s+on)"
                    r"(?:\s+with\s+(?P<target>.+?))?\s*\??\s*$", re.I), "diagnose"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?P<target>.+?)\s+kyu\s+"
                    r"(?:mar\s+gay[ia]|band\s+ho\s+gay[ia])(\s+phir\s+se)?\s*\??\s*$",
                    re.I), "diagnose"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?diagnose(?:\s+(?P<target>.+?))?\s*$", re.I),
         "diagnose"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?switch\s+to\s+(?P<project>.+?)\s*$", re.I),
         "dev_switch", resolve_project="project"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?open\s+(?:up\s+)?"
                    r"(?P<app>vs\s*code|code|chrome|explorer|terminal)\s*$", re.I),
         "open_app"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?P<app>chrome|code|terminal|explorer)\s+"
                    r"khol\s*(?:do|de|dijiye)?\s*$", re.I),
         "open_app"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?:anything\s+)?uncommitted"
                    r"(?:\s+anywhere)?\s*\??\s*$", re.I), "uncommitted_sweep"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?how\s+many\s+uncommitted"
                    r"(?:\s+files?)?\s*\??\s*$", re.I),
         "uncommitted_sweep", scope_to_context=True),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?play\s+(?:some\s+)?music\s*$", re.I),
         "open_app", static={"app": "spotify"}),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?what'?s?\s+uncommitted"
                    r"(?:\s+anywhere)?\s*\??\s*$", re.I), "uncommitted_sweep"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?sab\s+kuch\s+"
                    r"(?:band|bandh)\s+kar\s*(?:de|do|dijiye)?\s*$", re.I), "shutdown_all"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?did\s+the\s+deploy\s+"
                    r"(?:go\s+through|work|succeed)\s*\??\s*$", re.I),
         "prod_check", static={"project": "*"}),
    # Hinglish: "<project> chalu kar de", "<project> band kar do".
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?P<project>.+?)\s+"
                    r"(?:chalu|start)\s+kar\s*(?:de|do|dijiye)?\s*$", re.I),
         "dev_up", resolve_project="project"),
    Rule(re.compile(r"^\s*(?:tango[,\s]+)?(?P<project>.+?)\s+"
                    r"(?:band|bandh|stop)\s+kar\s*(?:de|do|dijiye)?\s*$", re.I),
         "dev_down", resolve_project="project"),
)

# A model suggestion below this is worth less than an honest question.
MODEL_MIN_CONFIDENCE = 0.6


# Leading conversational filler. Stripped before matching so "ok, actually
# kill it" routes the same as "kill it" — people do not speak in commands.
_FILLERS = re.compile(
    r"^\s*(?:(?:ok|okay|so|right|now|then|actually|hey|please|"
    r"can you|could you|i want to|i need to|let's|lets)[,\s]+)+",
    re.I,
)


def _strip_fillers(text: str) -> str:
    previous = None
    current = text.strip()
    while previous != current:
        previous = current
        current = _FILLERS.sub("", current).strip()
    return current


def _prior_project(context: dict[str, Any]) -> str | None:
    """Find what "it" refers to.

    Conversation context arrives in more than one shape — an explicit
    ``prior_project``, or the tail of a ``last_action`` like "dev_up myjson".
    Accepting only one shape is how a referent silently goes missing and a
    perfectly clear follow-up turns into a needless question. (The eval caught
    exactly that: the golden set said ``last_action``, the router read
    ``prior_project``, and "actually kill it" lost its referent.)
    """
    if not context:
        return None
    if prior := context.get("prior_project"):
        return str(prior)
    if prior := context.get("prior"):
        return str(prior)
    if last := context.get("last_action"):
        parts = str(last).split()
        if len(parts) >= 2:
            return parts[-1]
    return None


# Utterances that name no target and have no prior context.
NEEDS_TARGET = re.compile(
    r"^\s*(?:tango[,\s]+)?(start|stop|kill|restart|fix|open|run)\s+(it|that|this)?\s*$", re.I
)


class Router:
    """Deterministic first pass. In S0.7 a model slots in behind this same
    interface as a fallback for what the rules do not match."""

    def __init__(
        self,
        projects: ProjectRegistry,
        known_playbooks: set[str] | None = None,
        model: Any | None = None,
    ) -> None:
        self.projects = projects
        self.known = known_playbooks or set()
        self.model = model
        """Optional T1 fallback. None means rules-only — which is the whole of
        Phase 0, and remains fully functional forever."""

    def route(self, utterance: str, context: dict[str, Any] | None = None) -> Decision:
        text = _strip_fillers(utterance)
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
            prior = _prior_project(context)
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
            params.update(
                {k: (int(v) if v.isdigit() else v) for k, v in m.groupdict().items() if v}
            )

            scoped = _prior_project(context) if rule.scope_to_context else None
            if scoped and "project" not in params:
                params["project"] = scoped
            if rule.playbook == "diagnose" and "target" not in params:
                params["target"] = scoped or "*"

            if "target" in params:
                params["target"] = self._canonical_target(str(params["target"]))

            if "app" in params:
                normalized = re.sub(r"\s+", "", str(params["app"]).lower())
                params["app"] = {"vscode": "vscode", "code": "vscode"}.get(
                    normalized, normalized
                )

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

        return self._model_fallback(text)

    # ------------------------------------------------------------ model tier

    def _canonical_target(self, phrase: str) -> str:
        """Normalise "optiresume api" to "optiresume.api".

        A target names either a project or a component within one. Keeping the
        raw phrase would make every consumer re-parse it, and they would each
        get it slightly differently.
        """
        words = phrase.strip().split()
        if not words:
            return phrase
        for split in range(1, len(words) + 1):
            head = " ".join(words[:split])
            try:
                project = self.projects.resolve(head)
            except (AmbiguousResolution, ResolutionError):
                continue
            rest = words[split:]
            return f"{project.id}.{'.'.join(rest)}" if rest else project.id
        return phrase

    def _model_fallback(self, text: str) -> Decision:
        """Ask the local model only when the rules found nothing.

        Deliberately last. The deterministic path is faster, reproducible and
        free; the model exists for phrasings nobody anticipated, not as the
        default route. Its answer is treated as a *suggestion*: the project it
        names still goes through the same resolver, so it cannot conjure a
        target that does not exist (ADR-009).
        """
        if self.model is None or not self.known:
            return Decision(
                Route.CLARIFY, reason="no_match",
                message="I don't have a playbook for that yet.",
                candidates=sorted(self.known),
            )

        from tango.models import ROUTE_SCHEMA, ROUTE_SYSTEM, ModelUnavailable, build_route_prompt

        try:
            completion = self.model.complete(
                build_route_prompt(text, sorted(self.known), self.projects.ids()),
                system=ROUTE_SYSTEM,
                schema=ROUTE_SCHEMA,
            )
        except ModelUnavailable:
            # Absence is a state, not an error: say so rather than guessing.
            return Decision(
                Route.CLARIFY, reason="no_match_model_offline",
                message="I don't have a playbook for that, and the local model isn't running.",
                candidates=sorted(self.known),
            )

        answer = completion.parsed or {}
        playbook = str(answer.get("playbook", "none"))
        confidence = float(answer.get("confidence", 0.0) or 0.0)

        if playbook == "none" or playbook not in self.known or confidence < MODEL_MIN_CONFIDENCE:
            return Decision(
                Route.CLARIFY, reason="no_match",
                message="I don't have a playbook for that yet.",
                candidates=sorted(self.known), confidence=confidence,
            )

        params: dict[str, Any] = {}
        if phrase := str(answer.get("project", "")).strip():
            try:
                project = self.projects.resolve(phrase)
            except (AmbiguousResolution, ResolutionError):
                return Decision(
                    Route.CLARIFY, reason="ambiguous_project",
                    message="Which project did you mean?",
                    candidates=self.projects.ids(), confidence=confidence,
                )
            params["project"] = project.id
            params["_project"] = project

        return Decision(
            Route.PLAYBOOK, playbook_id=playbook, params=params,
            confidence=confidence, reason="model",
        )
