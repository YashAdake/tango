"""Read-only inspection tools — R0, the safe majority of daily use.

Everything here observes and reports; nothing changes the world. That is why
these need no confirmation, no undo, and carry no blast radius — and it is why
the highest-value capability in the whole system ("what's the state of
everything?") is also the cheapest to make safe.

Their verifiers are trivially satisfied: the postcondition of a read is that the
read happened. What matters is that the *evidence* is real, so every result
carries the raw observation the answer was derived from.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from tango.ledger import Evidence, ToolResult, VerifyResult
from tango.tools import REGISTRY
from tango.types import Risk, VerifyStatus


def _observed(result: ToolResult, args: dict[str, Any]) -> VerifyResult:
    """A read's postcondition is that an observation was obtained."""
    if result.raw:
        return VerifyResult(VerifyStatus.VERIFIED, [Evidence("observation", result.raw[:2000])],
                            result.summary)
    return VerifyResult(VerifyStatus.UNVERIFIABLE, [], "no observation returned")


def _git(repo: str, *args: str, timeout: float = 20.0) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return 127, str(exc)


# ------------------------------------------------------------------- git state


@REGISTRY.tool("git.state", risk=Risk.R0_READ, verifier=_observed,
               description="Branch, dirty files, and divergence from upstream.")
def git_state(path: str) -> ToolResult:
    if not (Path(path) / ".git").exists():
        return ToolResult(ok=True, raw="{}", summary="not a git repository")

    _, branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    _, porcelain = _git(path, "status", "--porcelain")
    dirty = [ln for ln in porcelain.splitlines() if ln.strip()]

    ahead = behind = 0
    code, counts = _git(path, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
    if code == 0 and counts:
        parts = counts.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])

    _, last = _git(path, "log", "-1", "--format=%h %s")

    state = {"branch": branch, "dirty": len(dirty), "ahead": ahead,
             "behind": behind, "last_commit": last}
    bits = [branch]
    if dirty:
        bits.append(f"{len(dirty)} uncommitted")
    if ahead:
        bits.append(f"{ahead} unpushed")
    if behind:
        bits.append(f"{behind} behind")
    if not dirty and not ahead and not behind:
        bits.append("clean")

    return ToolResult(ok=True, provider_ref=branch, raw=json.dumps(state),
                      summary=" · ".join(bits))


@REGISTRY.tool("git.log_since", risk=Risk.R0_READ, verifier=_observed,
               description="Commits in a window, for cross-repo digests.")
def git_log_since(path: str, since: str = "7 days ago") -> ToolResult:
    if not (Path(path) / ".git").exists():
        return ToolResult(ok=True, raw="[]", summary="not a git repository")
    code, out = _git(path, "log", f"--since={since}", "--format=%h|%ad|%s",
                     "--date=short", "--no-merges")
    if code != 0:
        return ToolResult(ok=False, summary=out[:200])
    commits = [
        dict(zip(("sha", "date", "subject"), ln.split("|", 2), strict=False))
        for ln in out.splitlines() if ln.strip()
    ]
    return ToolResult(ok=True, raw=json.dumps(commits),
                      summary=f"{len(commits)} commit(s) since {since}")


# ----------------------------------------------------------------- ports & net


@REGISTRY.tool("port.inspect", risk=Risk.R0_READ, verifier=_observed,
               description="What is holding a TCP port.")
def port_inspect(port: int) -> ToolResult:
    free = True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        free = s.connect_ex(("127.0.0.1", int(port))) != 0

    holders: list[dict[str, Any]] = []
    if not free:
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, timeout=15).stdout
            pids = {
                ln.split()[-1]
                for ln in out.splitlines()
                if f":{port} " in ln and "LISTENING" in ln and ln.split()[-1].isdigit()
            }
            for pid in pids:
                task = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                      capture_output=True, text=True, timeout=10).stdout
                name = task.split('","')[0].strip('"') if '","' in task else "?"
                holders.append({"pid": int(pid), "name": name})
        except (OSError, subprocess.TimeoutExpired):
            pass

    state = {"port": int(port), "free": free, "holders": holders}
    if free:
        summary = f"port {port} is free"
    elif holders:
        summary = "port {} held by {}".format(
            port, ", ".join(f"{h['name']} (pid {h['pid']})" for h in holders)
        )
    else:
        summary = f"port {port} is in use (holder unknown)"
    return ToolResult(ok=True, provider_ref=str(port), raw=json.dumps(state), summary=summary)


@REGISTRY.tool("http.probe", risk=Risk.R0_READ, verifier=_observed,
               timeout_s=20.0, description="Probe a URL for status and latency.")
def http_probe(url: str, timeout_s: float = 8.0) -> ToolResult:
    import time

    started = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tango/0.1"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310
            status = r.status
        ms = int((time.monotonic() - started) * 1000)
        state = {"url": url, "status": status, "ms": ms}
        return ToolResult(ok=True, provider_ref=str(status), raw=json.dumps(state),
                          summary=f"{status} in {ms}ms")
    except urllib.error.HTTPError as exc:
        ms = int((time.monotonic() - started) * 1000)
        # An HTTP error is still a successful *observation* — the site answered.
        return ToolResult(ok=True, provider_ref=str(exc.code),
                          raw=json.dumps({"url": url, "status": exc.code, "ms": ms}),
                          summary=f"{exc.code} in {ms}ms")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return ToolResult(ok=True, raw=json.dumps({"url": url, "error": str(reason)}),
                          summary=f"unreachable: {reason}")


# ---------------------------------------------------------------- docker & proc


@REGISTRY.tool("docker.ps", risk=Risk.R0_READ, verifier=_observed,
               description="Running containers, or why Docker cannot be asked.")
def docker_ps() -> ToolResult:
    if shutil.which("docker") is None:
        return ToolResult(ok=True, raw="[]", summary="docker not installed")
    try:
        p = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolResult(ok=True, raw="[]", summary=f"docker unreachable: {exc}")
    if p.returncode != 0:
        return ToolResult(ok=True, raw="[]", summary="docker daemon not running")
    rows = [
        {"name": n, "status": s}
        for n, _, s in (ln.partition("\t") for ln in p.stdout.splitlines() if ln.strip())
    ]
    return ToolResult(ok=True, raw=json.dumps(rows), summary=f"{len(rows)} container(s) running")


@REGISTRY.tool("process.find", risk=Risk.R0_READ, verifier=_observed,
               description="Find running processes whose image matches a name.")
def process_find(name: str) -> ToolResult:
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolResult(ok=False, summary=str(exc))
    hits = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and name.lower() in parts[0].lower() and parts[1].isdigit():
            hits.append({"name": parts[0], "pid": int(parts[1])})
    return ToolResult(ok=True, raw=json.dumps(hits), summary=f"{len(hits)} match(es) for '{name}'")
