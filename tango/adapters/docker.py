"""Docker adapters.

Note the asymmetry these verifiers encode: ``docker compose up`` exiting 0 means
the *command* succeeded, which is not the same as the container being healthy.
Between those two facts sits most of what "why is my project down?" is actually
about, so the verifier asks Docker for container state and waits on health
rather than trusting the exit code.

``sink_idempotent=False`` throughout: Docker does not deduplicate by our key, so
crash recovery must query container state rather than re-running the command.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from tango.ledger import Evidence, ToolResult, VerifyResult
from tango.tools import REGISTRY
from tango.types import Risk, VerifyStatus


def _inspect(name: str) -> dict[str, Any] | None:
    """Ask Docker what it thinks the container's state is."""
    out = subprocess.run(
        ["docker", "inspect", name, "--format", "{{json .State}}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        parsed: Any = json.loads(out.stdout.strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def container_healthy(name: str, timeout_s: float = 45.0) -> VerifyResult:
    """Wait for a container to be running and, if it declares a healthcheck,
    healthy. A container that is 'running' but failing its healthcheck is not
    up, and reporting it as up is exactly the lie the ledger exists to prevent.
    """
    deadline = time.monotonic() + timeout_s
    last = "not found"
    while time.monotonic() < deadline:
        state = _inspect(name)
        if state is None:
            last = "container not found"
        else:
            status = state.get("Status", "unknown")
            health = (state.get("Health") or {}).get("Status")
            if status == "running" and health in (None, "healthy"):
                return VerifyResult(
                    VerifyStatus.VERIFIED,
                    [Evidence("container_state", json.dumps({"status": status, "health": health}))],
                    f"{name} is {status}" + (f" and {health}" if health else ""),
                )
            if status == "exited":
                return VerifyResult(
                    VerifyStatus.REFUTED,
                    [Evidence("container_state", json.dumps(state))],
                    f"{name} exited (code {state.get('ExitCode')})",
                )
            last = f"status={status} health={health}"
        time.sleep(1.0)
    return VerifyResult(
        VerifyStatus.REFUTED, [Evidence("timeout", last)], f"{name} did not become healthy: {last}"
    )


# ------------------------------------------------------------ compose_up/down


def verify_compose_up(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    container = args.get("container")
    if not container:
        return VerifyResult(
            VerifyStatus.UNVERIFIABLE, [], "no container name given to verify against"
        )
    return container_healthy(container, timeout_s=float(args.get("timeout_s", 45)))


@REGISTRY.tool(
    "docker.compose_up",
    risk=Risk.R1_REVERSIBLE,
    verifier=verify_compose_up,
    compensate="docker.compose_down",
    sink_idempotent=False,
    timeout_s=120.0,
    description="Bring up a compose service and wait for it to be healthy.",
)
def compose_up(
    project_path: str,
    service: str | None = None,
    container: str | None = None,
    compose_file: str | None = None,
    timeout_s: float = 45.0,
) -> ToolResult:
    # Real projects rarely use the bare default compose file; assuming they do
    # starts the wrong stack silently.
    cmd = ["docker", "compose"]
    if compose_file:
        cmd += ["-f", compose_file]
    cmd += ["up", "-d"] + ([service] if service else [])
    out = subprocess.run(cmd, cwd=project_path, capture_output=True, text=True, timeout=120)
    return ToolResult(
        ok=out.returncode == 0,
        provider_ref=container or service,
        raw=(out.stdout + out.stderr)[:4000],
        summary=f"compose up {service or 'all'}"
        if out.returncode == 0
        else f"compose up failed: {out.stderr.strip()[:200]}",
    )


def verify_compose_down(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    container = args.get("container")
    if not container:
        return VerifyResult(VerifyStatus.UNVERIFIABLE, [], "no container name to verify")
    state = _inspect(container)
    if state is None or state.get("Status") in ("exited", "removing", "dead"):
        return VerifyResult(
            VerifyStatus.VERIFIED, [Evidence("container_absent", container)], f"{container} is down"
        )
    return VerifyResult(
        VerifyStatus.REFUTED,
        [Evidence("container_state", json.dumps(state))],
        f"{container} is still {state.get('Status')}",
    )


@REGISTRY.tool(
    "docker.compose_down",
    risk=Risk.R1_REVERSIBLE,
    verifier=verify_compose_down,
    description="Stop a compose project. no-compensate: this is itself a compensate.",
)
def compose_down(
    project_path: str, container: str | None = None, compose_file: str | None = None
) -> ToolResult:
    cmd = ["docker", "compose"]
    if compose_file:
        cmd += ["-f", compose_file]
    cmd += ["down"]
    out = subprocess.run(cmd, cwd=project_path, capture_output=True, text=True, timeout=120)
    return ToolResult(
        ok=out.returncode == 0,
        provider_ref=container,
        raw=(out.stdout + out.stderr)[:4000],
        summary="compose down",
    )
