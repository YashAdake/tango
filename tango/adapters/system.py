"""Windows process and application adapters.

Every verifier here answers by looking at the operating system, never by reading
what the executor returned. ``process.start`` reporting a PID is a claim;
finding that PID alive in the process table is evidence.
"""

from __future__ import annotations

import shutil
import subprocess
import time
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
    "spotify": "spotify",
}


class ProcessTableUnavailable(RuntimeError):
    """The OS could not be asked what is running.

    Distinct from "nothing is running", and the distinction matters: an empty
    process table would make every verifier report REFUTED and every cleanup
    report nothing to do. Not knowing is its own answer.
    """


# One command can ask for the process table ten times — once per verifier,
# once per cleanup candidate. Each call spawns a process, and under that load
# tasklist starts timing out, which is how a correct system produced
# UNVERIFIABLE for something it could plainly see. Cached briefly instead.
_CACHE_TTL = 1.0
_cache: tuple[float, list[list[str]]] | None = None


def invalidate_process_cache() -> None:
    """Drop the cached table.

    Called after anything that changes what is running. A verifier asking
    "did it stop?" must never be answered from a snapshot taken before the
    stop — that would turn verification into an echo.
    """
    global _cache
    _cache = None


def _snapshot_win32() -> list[list[str]]:
    """Read the process table through the Windows API.

    ``tasklist`` was measured at **4.5 seconds** on a loaded machine — it spawns
    a process and formats CSV to answer a question the kernel can answer in
    microseconds. That single subprocess was the slowest thing in the system,
    and the source of the timeouts that made verifiers report UNVERIFIABLE for
    processes they could plainly see.
    """
    import ctypes
    import ctypes.wintypes as wt

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE:
        raise ProcessTableUnavailable("CreateToolhelp32Snapshot failed")

    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    rows: list[list[str]] = []
    try:
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            raise ProcessTableUnavailable("Process32First failed")
        while True:
            name = entry.szExeFile.decode("utf-8", errors="replace")
            rows.append([name, str(entry.th32ProcessID)])
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def _tasklist_fallback() -> list[list[str]]:
    """Subprocess fallback, for when the API call is unavailable."""
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessTableUnavailable(f"could not read the process table: {exc}") from exc
    if out.returncode != 0:
        raise ProcessTableUnavailable(f"tasklist exited {out.returncode}")
    return [[p.strip('"') for p in line.split('","')] for line in out.stdout.splitlines()]


def _tasklist() -> list[list[str]]:
    """Process table rows as ``[image_name, pid]``, or an explicit failure.

    Never an empty list on error: "nothing is running" is a claim, and an
    unreadable table is an absence of one.
    """
    global _cache
    if _cache is not None and (time.monotonic() - _cache[0]) < _CACHE_TTL:
        return _cache[1]
    try:
        rows = _snapshot_win32()
    except (ProcessTableUnavailable, AttributeError, OSError):
        rows = _tasklist_fallback()
    _cache = (time.monotonic(), rows)
    return rows


def _running_pids() -> set[int]:
    """Snapshot of live PIDs, straight from the OS."""
    return {
        int(parts[1]) for parts in _tasklist() if len(parts) > 1 and parts[1].isdigit()
    }


def _process_names() -> set[str]:
    return {parts[0].strip('"').lower() for parts in _tasklist() if parts}


# --------------------------------------------------------------- process.start


def verify_process_started(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    """Independent check: is the PID actually in the process table?"""
    if result.provider_ref is None:
        return VerifyResult(VerifyStatus.UNVERIFIABLE, [], "no pid was reported")
    pid = int(result.provider_ref)
    try:
        live = _running_pids()
    except ProcessTableUnavailable as exc:
        return VerifyResult(VerifyStatus.UNVERIFIABLE, [], str(exc))
    if pid in live:
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
    try:
        proc = _spawn(cmd, cwd)
    except OSError as exc:
        return ToolResult(ok=False, summary=f"could not start '{cmd}': {exc}")
    invalidate_process_cache()
    return ToolResult(
        ok=True, provider_ref=str(proc.pid), raw=f"pid={proc.pid}", summary=f"started {cmd}"
    )


def _spawn(cmd: str, cwd: str | None) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


# ---------------------------------------------------------------- process.stop


def verify_process_stopped(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    pid = int(args.get("pid", result.provider_ref or 0))
    try:
        live = _running_pids()
    except ProcessTableUnavailable as exc:
        return VerifyResult(VerifyStatus.UNVERIFIABLE, [], str(exc))
    if pid not in live:
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
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolResult(ok=False, provider_ref=str(pid), summary=f"taskkill failed: {exc}")
    invalidate_process_cache()
    # Success is decided by the verifier looking at the process table, not by
    # taskkill's exit code — it reports success for a pid that was already gone.
    return ToolResult(ok=True, provider_ref=str(pid), summary=f"asked {pid} to stop")


# ------------------------------------------------------------------ app.launch


def verify_app_launched(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    """Check the process table for the app's image name — not the launcher's
    exit code, which only tells us the launcher ran."""
    app = args.get("app", "")
    exe = KNOWN_APPS.get(app, app)
    stem = Path(exe).stem.lower()
    try:
        names = _process_names()
    except ProcessTableUnavailable as exc:
        return VerifyResult(VerifyStatus.UNVERIFIABLE, [], str(exc))
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
    try:
        subprocess.Popen(cmd, shell=True)
    except OSError as exc:
        return ToolResult(ok=False, provider_ref=exe, summary=f"could not launch {app}: {exc}")
    invalidate_process_cache()
    return ToolResult(ok=True, provider_ref=exe, summary=f"launched {app}")
