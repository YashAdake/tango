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


def _tail(name: str, lines: int = 6) -> str:
    """Last few log lines, so a failure carries its own explanation."""
    try:
        p = subprocess.run(["docker", "logs", "--tail", str(lines), name],
                           capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace")
        return (p.stdout + p.stderr).strip()[:1200]
    except (OSError, subprocess.TimeoutExpired):
        return ""


def container_healthy(name: str, timeout_s: float = 90.0) -> VerifyResult:
    """Wait for a container to be running and, where declared, healthy.

    Three outcomes, and the distinction between the last two is the point:

    * **VERIFIED** — running, and healthy if it says so.
    * **REFUTED** — it exited, or its healthcheck ran out of retries. Something
      is wrong and there are logs to read.
    * **UNVERIFIABLE** — still *starting* when we stopped watching. Nothing is
      known to be wrong; we ran out of patience.

    That third case used to report REFUTED, which is a different claim
    entirely — it would send you debugging a database that was merely slow.
    Found the first time this ran against a real Postgres recovering from an
    unclean shutdown: a 40-second fsync, perfectly healthy, reported as failed.
    """
    deadline = time.monotonic() + timeout_s
    last_status = last_health = None
    while time.monotonic() < deadline:
        state = _inspect(name)
        if state is None:
            last_status, last_health = "absent", None
            time.sleep(1.0)
            continue

        status = state.get("Status", "unknown")
        health = (state.get("Health") or {}).get("Status")
        last_status, last_health = status, health

        if status == "running" and health in (None, "healthy"):
            return VerifyResult(
                VerifyStatus.VERIFIED,
                [Evidence("container_state", json.dumps({"status": status, "health": health}))],
                f"{name} is {status}" + (f" and {health}" if health else ""),
            )
        if status == "exited":
            return VerifyResult(
                VerifyStatus.REFUTED,
                [Evidence("container_state", json.dumps(state)),
                 Evidence("logs", _tail(name))],
                f"{name} exited (code {state.get('ExitCode')})",
            )
        if health == "unhealthy":
            # Docker exhausted its retries. That is a real verdict, not
            # impatience — but the logs are what make it actionable.
            return VerifyResult(
                VerifyStatus.REFUTED,
                [Evidence("container_state", json.dumps(state)),
                 Evidence("logs", _tail(name))],
                f"{name} is running but failing its healthcheck",
            )
        time.sleep(1.0)

    if last_status == "absent":
        return VerifyResult(
            VerifyStatus.REFUTED, [Evidence("container_absent", name)],
            f"{name} never appeared",
        )
    return VerifyResult(
        VerifyStatus.UNVERIFIABLE,
        [Evidence("timeout", f"status={last_status} health={last_health}"),
         Evidence("logs", _tail(name))],
        f"{name} was still starting after {timeout_s:.0f}s — it may yet come up",
    )


# ------------------------------------------------------------ compose_up/down


def verify_compose_up(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    container = args.get("container")
    if not container:
        return VerifyResult(
            VerifyStatus.UNVERIFIABLE, [], "no container name given to verify against"
        )
    # 90s, not 45: a Postgres recovering from an unclean shutdown spent 40s
    # on fsync alone. Waiting longer costs patience; giving up early costs a
    # false verdict.
    return container_healthy(container, timeout_s=float(args.get("timeout_s", 90)))


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
