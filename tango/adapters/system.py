"""Windows process and application adapters.

Every verifier here answers by looking at the operating system, never by reading
what the executor returned. ``process.start`` reporting a PID is a claim;
finding that PID alive in the process table is evidence.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from tango.ledger import Evidence, ToolResult, VerifyResult
from tango.tools import REGISTRY
from tango.types import Risk, VerifyStatus

# Allowlist. The model never supplies a path — it selects a key, the mapping
# supplies the executable (docs/04 ADR-009).
KNOWN_APPS: dict[str, str] = {
    "vscode": "code",
    "chrome": "chrome",
    "explorer": "explorer",
    "terminal": "wt",
}


def _running_pids() -> set[int]:
    """Snapshot of live PIDs, straight from the OS."""
    out = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10
    )
    pids: set[int] = set()
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _process_names() -> set[str]:
    out = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10
    )
    names: set[str] = set()
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts:
            names.add(parts[0].strip('"').lower())
    return names


# --------------------------------------------------------------- process.start


def verify_process_started(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    """Independent check: is the PID actually in the process table?"""
    if result.provider_ref is None:
        return VerifyResult(VerifyStatus.UNVERIFIABLE, [], "no pid was reported")
    pid = int(result.provider_ref)
    if pid in _running_pids():
        return VerifyResult(
            VerifyStatus.VERIFIED,
            [Evidence("pid", str(pid))],
            f"process {pid} is running",
        )
    return VerifyResult(
        VerifyStatus.REFUTED, [Evidence("pid_absent", str(pid))], f"process {pid} is not running"
    )


@REGISTRY.tool(
    "process.start",
    risk=Risk.R1_REVERSIBLE,
    verifier=verify_process_started,
    compensate="process.stop",
    description="Start a long-running process in a working directory.",
)
def process_start(cmd: str, cwd: str | None = None) -> ToolResult:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return ToolResult(
        ok=True, provider_ref=str(proc.pid), raw=f"pid={proc.pid}", summary=f"started {cmd}"
    )


# ---------------------------------------------------------------- process.stop


def verify_process_stopped(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    pid = int(args.get("pid", result.provider_ref or 0))
    if pid not in _running_pids():
        return VerifyResult(
            VerifyStatus.VERIFIED, [Evidence("pid_absent", str(pid))], f"process {pid} is gone"
        )
    return VerifyResult(
        VerifyStatus.REFUTED, [Evidence("pid", str(pid))], f"process {pid} is still running"
    )


@REGISTRY.tool(
    "process.stop",
    risk=Risk.R1_REVERSIBLE,
    verifier=verify_process_stopped,
    description="Terminate a process by pid. no-compensate: stopping is itself the undo.",
)
def process_stop(pid: int) -> ToolResult:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
    return ToolResult(ok=True, provider_ref=str(pid), summary=f"stopped {pid}")


# ------------------------------------------------------------------ app.launch


def verify_app_launched(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    """Check the process table for the app's image name — not the launcher's
    exit code, which only tells us the launcher ran."""
    app = args.get("app", "")
    exe = KNOWN_APPS.get(app, app)
    stem = Path(exe).stem.lower()
    names = _process_names()
    hit = next((n for n in names if n.startswith(stem)), None)
    if hit:
        return VerifyResult(
            VerifyStatus.VERIFIED, [Evidence("process", hit)], f"{app} is running ({hit})"
        )
    return VerifyResult(
        VerifyStatus.REFUTED, [Evidence("process_absent", stem)], f"{app} is not running"
    )


@REGISTRY.tool(
    "app.launch",
    risk=Risk.R1_REVERSIBLE,
    verifier=verify_app_launched,
    description="Launch an allowlisted desktop application. no-compensate: the "
    "user may be using it; closing it is a separate deliberate action.",
)
def app_launch(app: str, path: str | None = None) -> ToolResult:
    if app not in KNOWN_APPS:
        return ToolResult(ok=False, summary=f"'{app}' is not an allowlisted application")
    exe = KNOWN_APPS[app]
    if shutil.which(exe) is None:
        return ToolResult(ok=False, summary=f"'{exe}' was not found on PATH")
    cmd = [exe] + ([path] if path else [])
    subprocess.Popen(cmd, shell=True)
    return ToolResult(ok=True, provider_ref=exe, summary=f"launched {app}")
