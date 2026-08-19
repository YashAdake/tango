"""Playbooks — the unit of work.

The model does not compose procedures. For the ~30 things actually asked for
daily, the procedure is already known, and letting a model improvise it converts
a 100%-reliable operation into a ~90%-reliable one (docs/04 ADR-001). A playbook
is a versioned, testable, declarative recipe; the model's whole job is to pick
one and fill its slots.

The ``when``/``on_fail`` grammar is deliberately tiny and frozen (docs/17 M10) —
anything richer belongs in Python, not YAML, or it grows into an accidental DSL
nobody can reason about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tango.render import StepOutcome
from tango.tools import ToolCall
from tango.types import ActionStatus, Risk, TaskStatus


class PlaybookError(RuntimeError):
    """A playbook that is malformed, or asked to do something it cannot."""


class OnFail:
    ABORT = "abort"
    """Stop the playbook. Later steps would be meaningless or unsafe."""
    CONTINUE = "continue"
    """Record the failure and carry on. Produces an honest PARTIAL."""
    RETRY = "retry"
    """Retry once. Only for steps whose tool is genuinely idempotent."""

    ALL = frozenset({ABORT, CONTINUE, RETRY})


# Frozen expression grammar. Two forms only:
#   params.X == literal        params.X in [a, b]
_WHEN_EQ = re.compile(r"^params\.(\w+)\s*==\s*(.+)$")
_WHEN_IN = re.compile(r"^params\.(\w+)\s+in\s+\[(.*)\]$")
# Substitution: $name, ${name}, or ${name.attr}
_SUBST = re.compile(r"\$\{([\w.]+)\}|\$(\w+)")


def _literal(raw: str) -> Any:
    raw = raw.strip().strip("'\"")
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if raw.isdigit():
        return int(raw)
    return raw


def evaluate_when(expr: str | None, params: dict[str, Any]) -> bool:
    """Evaluate a step guard.

    Both unknown syntax *and* an unknown parameter raise. A guard referencing a
    parameter nobody declared is a bug, not a false condition — and the failure
    mode it produces is the worst kind: a step that silently never runs, in a
    system whose entire promise is that it does not silently do nothing.

    (Found exactly this way in V2 verification: ``has_compose`` was being
    stripped before evaluation, so the database step vanished from every run
    while the task still reported COMPLETED.)
    """
    if expr is None:
        return True
    expr = expr.strip()

    if m := _WHEN_IN.match(expr):
        name, options = m.group(1), m.group(2)
        if name not in params:
            raise PlaybookError(
                f"guard {expr!r} references undeclared parameter '{name}'; "
                "declare it (with a default) or the step would silently never run"
            )
        allowed = [_literal(o) for o in options.split(",") if o.strip()]
        return bool(params[name] in allowed)

    if m := _WHEN_EQ.match(expr):
        name = m.group(1)
        if name not in params:
            raise PlaybookError(
                f"guard {expr!r} references undeclared parameter '{name}'; "
                "declare it (with a default) or the step would silently never run"
            )
        return bool(params[name] == _literal(m.group(2)))

    raise PlaybookError(
        f"unsupported 'when' expression: {expr!r}. "
        "Grammar is frozen to 'params.X == literal' and 'params.X in [a, b]'."
    )


def substitute(value: Any, params: dict[str, Any]) -> Any:
    """Resolve $name / ${name.attr} against params.

    A reference that cannot be resolved is an error, never an empty string —
    silently passing "" to a tool is how you delete the wrong thing.
    """
    if isinstance(value, dict):
        return {k: substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, params) for v in value]
    if not isinstance(value, str):
        return value

    # A lone "$name" keeps the referent's native type (int, bool, dict).
    whole = _SUBST.fullmatch(value)
    if whole:
        return _resolve(whole.group(1) or whole.group(2), params)

    def repl(m: re.Match[str]) -> str:
        return str(_resolve(m.group(1) or m.group(2), params))

    return _SUBST.sub(repl, value)


def _resolve(path: str, params: dict[str, Any]) -> Any:
    head, *rest = path.split(".")
    if head not in params:
        raise PlaybookError(f"'${path}' is not a parameter of this playbook")
    current = params[head]
    for attr in rest:
        if isinstance(current, dict) and attr in current:
            current = current[attr]
        elif hasattr(current, attr):
            current = getattr(current, attr)
        else:
            raise PlaybookError(f"'${path}' — '{attr}' not found on {head}")
    return current


@dataclass(frozen=True)
class Step:
    id: str
    tool: str
    label: str
    args: dict[str, Any] = field(default_factory=dict)
    when: str | None = None
    on_fail: str = OnFail.ABORT

    def __post_init__(self) -> None:
        if self.on_fail not in OnFail.ALL:
            raise PlaybookError(f"step '{self.id}': unknown on_fail '{self.on_fail}'")


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "str"
    required: bool = True
    default: Any = None
    resolver: str | None = None
    """Deterministic resolver that turns a phrase into an ID. Real-world
    entities are never authored by a model (docs/04 ADR-009)."""


@dataclass(frozen=True)
class Playbook:
    id: str
    version: int
    description: str
    params: tuple[Param, ...]
    steps: tuple[Step, ...]
    risk: Risk = Risk.R1_REVERSIBLE
    compensate: tuple[Step, ...] = ()

    def bind(self, given: dict[str, Any]) -> dict[str, Any]:
        """Validate and complete the parameter set before anything executes."""
        bound: dict[str, Any] = {}
        for p in self.params:
            if p.name in given and given[p.name] is not None:
                bound[p.name] = given[p.name]
            elif p.default is not None:
                bound[p.name] = p.default
            elif p.required:
                raise PlaybookError(f"playbook '{self.id}' requires parameter '{p.name}'")
        unknown = set(given) - {p.name for p in self.params}
        if unknown:
            raise PlaybookError(f"playbook '{self.id}': unknown parameters {sorted(unknown)}")
        return bound

    def plan(self, params: dict[str, Any]) -> list[tuple[Step, ToolCall]]:
        """Resolve guards and substitutions into concrete calls.

        Runs before execution so the capability freeze can see the full tool set
        a task will use, before any untrusted content is anywhere near it.
        """
        planned: list[tuple[Step, ToolCall]] = []
        for step in self.steps:
            if not evaluate_when(step.when, params):
                continue
            args = substitute(dict(step.args), params)
            planned.append((step, ToolCall(tool=step.tool, args=args, step_id=step.id)))
        return planned

    def tool_names(self, params: dict[str, Any]) -> set[str]:
        return {call.tool for _, call in self.plan(params)}


# ------------------------------------------------------------------- loading


def playbook_from_dict(data: dict[str, Any]) -> Playbook:
    try:
        params = tuple(
            Param(
                name=name,
                type=spec.get("type", "str"),
                required=spec.get("required", True),
                default=spec.get("default"),
                resolver=spec.get("resolver"),
            )
            for name, spec in (data.get("params") or {}).items()
        )
        steps = tuple(
            Step(
                id=s["id"],
                tool=s["tool"],
                label=s.get("label", s["id"]),
                args=s.get("args", {}),
                when=s.get("when"),
                on_fail=s.get("on_fail", OnFail.ABORT),
            )
            for s in data["steps"]
        )
        compensate = tuple(
            Step(id=s["id"], tool=s["tool"], label=s.get("label", s["id"]), args=s.get("args", {}))
            for s in (data.get("compensate") or [])
        )
    except KeyError as exc:
        raise PlaybookError(f"playbook '{data.get('id', '?')}' is missing {exc}") from exc

    if not steps:
        raise PlaybookError(f"playbook '{data.get('id', '?')}' has no steps")

    return Playbook(
        id=data["id"],
        version=int(data["version"]),
        description=data.get("description", ""),
        params=params,
        steps=steps,
        risk=Risk[data["risk"]] if "risk" in data else Risk.R1_REVERSIBLE,
        compensate=compensate,
    )


class PlaybookRegistry:
    def __init__(self) -> None:
        self._books: dict[str, Playbook] = {}

    def register(self, pb: Playbook) -> Playbook:
        if pb.id in self._books:
            raise PlaybookError(f"duplicate playbook id: {pb.id}")
        self._books[pb.id] = pb
        return pb

    def get(self, pid: str) -> Playbook:
        if pid not in self._books:
            raise KeyError(f"unknown playbook: {pid}")
        return self._books[pid]

    def names(self) -> list[str]:
        return sorted(self._books)

    def all(self) -> list[Playbook]:
        return [self._books[n] for n in self.names()]

    def load_dir(self, path: Path) -> int:
        """Load every *.yaml in a directory. Uses PyYAML when available and
        falls back to a strict JSON read, so the core has no hard dependency on
        a parser it only needs at startup."""
        import json

        count = 0
        for file in sorted(path.glob("*.yaml")) + sorted(path.glob("*.json")):
            raw = file.read_text(encoding="utf-8")
            try:
                import yaml  # type: ignore[import-untyped]

                data = yaml.safe_load(raw)
            except ImportError:
                data = json.loads(raw)
            self.register(playbook_from_dict(data))
            count += 1
        return count


# ------------------------------------------------------------------ execution


@dataclass
class PlaybookRun:
    playbook_id: str
    version: int
    task_id: str
    steps: list[StepOutcome]
    status: TaskStatus
    aborted_at: str | None = None


def run_playbook(
    pb: Playbook,
    params: dict[str, Any],
    task_id: str,
    executor: Any,
) -> PlaybookRun:
    """Execute a playbook step by step, honouring ``on_fail``.

    ``abort`` stops the run — later steps would be meaningless (there is no
    point probing an API whose database never came up) or unsafe. The result is
    still reported honestly as ``PARTIAL``, never as a failure that erases the
    steps that did succeed.
    """
    bound = pb.bind(params)
    outcomes: list[StepOutcome] = []
    aborted_at: str | None = None

    for step, call in pb.plan(bound):
        outcome = executor.run(task_id, call)

        if outcome.status is not ActionStatus.VERIFIED and step.on_fail == OnFail.RETRY:
            retry_call = ToolCall(tool=call.tool, args=call.args, step_id=f"{step.id}#retry")
            retried = executor.run(task_id, retry_call)
            if retried.status is ActionStatus.VERIFIED:
                outcome = retried

        outcomes.append(outcome.to_step(step.label))

        if outcome.status is not ActionStatus.VERIFIED and step.on_fail == OnFail.ABORT:
            aborted_at = step.id
            break

    status = executor.settle_task(task_id)
    if aborted_at and status is TaskStatus.COMPLETED:
        # Every executed step passed but the run stopped early: honest reporting
        # means PARTIAL, because the playbook did not finish.
        status = TaskStatus.PARTIAL

    return PlaybookRun(
        playbook_id=pb.id,
        version=pb.version,
        task_id=task_id,
        steps=outcomes,
        status=status,
        aborted_at=aborted_at,
    )
