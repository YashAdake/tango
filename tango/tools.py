"""Tool contract and registry.

A tool is a typed, declared capability — never a raw function the model can
reach. The declaration carries everything policy and the ledger need to know
*before* the call happens: blast radius, whether a verifier exists, whether the
provider itself deduplicates, and how to undo it.

Two rules are enforced here rather than documented and hoped for:

1. **Any tool at R2 or above must declare a verifier.** Without one the ledger
   can only ever return ``UNVERIFIABLE``, so an unverified side-effecting tool is
   a tool that can never honestly report success. :func:`check_contracts` fails
   at import time.
2. **A verifier must be independent of its tool.** Checking a tool's own return
   value is not verification (docs/02 §4.2). Verifiers receive the
   :class:`~tango.ledger.ToolResult` only for identifiers to look *up*; the
   contract test asserts they consult the world.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tango.ledger import ToolResult, VerifyResult
from tango.types import Risk

Executor = Callable[..., ToolResult]
Verifier = Callable[[ToolResult, dict[str, Any]], VerifyResult]


@dataclass(frozen=True)
class Tool:
    """A declared capability."""

    name: str
    risk: Risk
    executor: Executor
    verifier: Verifier | None = None
    compensate: str | None = None
    """Name of the tool that undoes this one. Required for undo windows."""
    sink_idempotent: bool = False
    """Does the *provider* deduplicate by our key? Gmail yes, Docker no.
    Recovery behaviour after a crash depends on this."""
    scopes: tuple[str, ...] = ()
    timeout_s: float = 30.0
    platforms: tuple[str, ...] = ("windows",)
    description: str = ""

    def __post_init__(self) -> None:
        if self.risk >= Risk.R2_EXTERNAL and self.verifier is None:
            raise ContractViolation(
                f"tool '{self.name}' is {self.risk.name} but declares no verifier; "
                "it could never report success honestly"
            )


class ContractViolation(RuntimeError):
    """A tool declaration that would break the honesty guarantee."""


class ToolRegistry:
    """The set of tools that exist. Lookup is by name and always explicit —
    there is no discovery, and the model never enumerates this freely."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ContractViolation(f"duplicate tool registration: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def tool(
        self,
        name: str,
        *,
        risk: Risk,
        verifier: Verifier | None = None,
        compensate: str | None = None,
        sink_idempotent: bool = False,
        scopes: tuple[str, ...] = (),
        timeout_s: float = 30.0,
        description: str = "",
    ) -> Callable[[Executor], Executor]:
        """Decorator form. The function stays a plain callable; the declaration
        lives alongside it in the registry."""

        def decorator(fn: Executor) -> Executor:
            self.register(
                Tool(
                    name=name,
                    risk=risk,
                    executor=fn,
                    verifier=verifier,
                    compensate=compensate,
                    sink_idempotent=sink_idempotent,
                    scopes=scopes,
                    timeout_s=timeout_s,
                    description=description or (fn.__doc__ or "").strip().split("\n")[0],
                )
            )
            return fn

        return decorator

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[n] for n in self.names()]


REGISTRY = ToolRegistry()


def check_contracts(registry: ToolRegistry | None = None) -> list[str]:
    """Static contract audit. Returns violations; empty list means clean.

    Run at import and in CI. This is what makes the guarantees in docs/16 §10
    structural rather than a matter of remembering.
    """
    reg = registry or REGISTRY
    problems: list[str] = []
    for tool in reg.all():
        if tool.risk >= Risk.R2_EXTERNAL and tool.verifier is None:
            problems.append(f"{tool.name}: {tool.risk.name} without a verifier")
        # Some R1 effects genuinely have no undo (a launched app the user then
        # started working in). That is allowed, but it must be a deliberate
        # declaration rather than an omission.
        if (
            tool.risk >= Risk.R1_REVERSIBLE
            and tool.compensate is None
            and "no-compensate" not in tool.description
        ):
            problems.append(
                f"{tool.name}: {tool.risk.name} without compensate; "
                "declare one or note 'no-compensate' in the description"
            )
        if tool.compensate and tool.compensate not in reg.names():
            problems.append(f"{tool.name}: compensate '{tool.compensate}' is not registered")
    return problems


@dataclass
class ToolCall:
    """A resolved intent to invoke a tool. Produced by the playbook engine,
    never authored freely by a model."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    step_id: str = "s1"
