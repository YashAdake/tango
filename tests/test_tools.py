"""S0.4 acceptance: the tool contract enforces the honesty preconditions.

These are the checks that make docs/16 §10 structural. A tool that could never
report success honestly must not be constructible at all.
"""

from __future__ import annotations

import pytest

from tango.ledger import ToolResult, VerifyResult
from tango.tools import ContractViolation, Tool, ToolRegistry, check_contracts
from tango.types import Risk, VerifyStatus


def _noop() -> ToolResult:
    return ToolResult(ok=True)


def _verifier(result: ToolResult, args: dict) -> VerifyResult:
    return VerifyResult(VerifyStatus.VERIFIED)


def test_r2_tool_without_verifier_cannot_be_constructed():
    """An unverified side-effecting tool could only ever report UNVERIFIABLE,
    so it is a contract violation at declaration time, not a runtime surprise."""
    with pytest.raises(ContractViolation, match="no verifier"):
        Tool(name="email.send", risk=Risk.R2_EXTERNAL, executor=_noop)


def test_r3_tool_without_verifier_cannot_be_constructed():
    with pytest.raises(ContractViolation):
        Tool(name="file.delete", risk=Risk.R3_CONSEQUENTIAL, executor=_noop)


def test_r0_read_tool_needs_no_verifier():
    tool = Tool(name="status.read", risk=Risk.R0_READ, executor=_noop)
    assert tool.verifier is None


def test_duplicate_registration_is_refused():
    reg = ToolRegistry()
    reg.register(Tool(name="a", risk=Risk.R0_READ, executor=_noop))
    with pytest.raises(ContractViolation, match="duplicate"):
        reg.register(Tool(name="a", risk=Risk.R0_READ, executor=_noop))


def test_check_contracts_flags_dangling_compensate():
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="x.up",
            risk=Risk.R1_REVERSIBLE,
            executor=_noop,
            verifier=_verifier,
            compensate="x.down",  # never registered
        )
    )
    problems = check_contracts(reg)
    assert any("not registered" in p for p in problems)


def test_check_contracts_requires_deliberate_compensate_decision():
    reg = ToolRegistry()
    reg.register(Tool(name="x.up", risk=Risk.R1_REVERSIBLE, executor=_noop, verifier=_verifier))
    assert any("without compensate" in p for p in check_contracts(reg))


def test_no_compensate_can_be_declared_explicitly():
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="x.up",
            risk=Risk.R1_REVERSIBLE,
            executor=_noop,
            verifier=_verifier,
            description="Does a thing. no-compensate: nothing to undo.",
        )
    )
    assert check_contracts(reg) == []


def test_shipped_registry_is_contract_clean():
    """The real adapters must satisfy every contract rule."""
    import tango.adapters.docker  # noqa: F401
    import tango.adapters.system  # noqa: F401
    from tango.tools import REGISTRY

    assert check_contracts(REGISTRY) == []
    assert "docker.compose_up" in REGISTRY.names()
    assert "process.start" in REGISTRY.names()
    assert "app.launch" in REGISTRY.names()


def test_verifiers_are_independent_of_their_executors():
    """A verifier must consult the world, not the tool's own return value.

    Enforced structurally: verifiers take (result, args) and the ToolResult
    carries only identifiers to look *up*. This test pins the signature so a
    future 'verifier' that just returns result.ok cannot slip in unnoticed.
    """
    import inspect

    import tango.adapters.docker  # noqa: F401
    import tango.adapters.system  # noqa: F401
    from tango.tools import REGISTRY

    for tool in REGISTRY.all():
        if tool.verifier is None:
            continue
        params = list(inspect.signature(tool.verifier).parameters)
        assert params == ["result", "args"], f"{tool.name} verifier has signature {params}"
        source = inspect.getsource(tool.verifier)
        assert "result.ok" not in source, (
            f"{tool.name}'s verifier reads result.ok — that is the tool's own "
            "claim, not independent evidence"
        )
