"""Diagnosis — gather evidence deterministically, reason over it separately.

The split matters more than either half. Evidence collection is code: it runs
the same way every time, it is testable without a model, and what it returns is
*fact*. Reasoning over that evidence is the model's job, and it happens later,
in Phase 4, over exactly what this module produced.

Doing it the other way round — letting a model decide what to look at — is how
you get a confident narrative built on whatever happened to be in context. Here
the model gets a fixed dossier and is asked to explain it.

Two rules this module enforces on itself:

* **Everything here is R0.** Diagnosis observes; it never repairs. Remediation
  is a separate, confirmed action, because "I found the problem and fixed it"
  is two claims and the second one needs its own evidence.
* **Findings are ranked by what they rule out**, not by what they suggest. A
  container that exited three seconds after a config file changed is a much
  stronger signal than a container that is merely unhealthy, and the ordering
  should say so.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tango.adapters.inspect import docker_ps, git_state, http_probe, port_inspect
from tango.projects import Project, ProjectRegistry


@dataclass
class Finding:
    """One observed fact that bears on the problem."""

    kind: str
    detail: str
    weight: int = 1
    """How much this narrows the search. 3 = probably the cause, 1 = context."""
    evidence: str = ""

    def __str__(self) -> str:
        return self.detail


@dataclass
class Dossier:
    """Everything gathered about a problem, before anyone interprets it."""

    target: str
    findings: list[Finding] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def add(self, kind: str, detail: str, weight: int = 1, evidence: str = "") -> None:
        self.findings.append(Finding(kind, detail, weight, evidence))

    @property
    def ranked(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (-f.weight, f.kind))

    @property
    def strongest(self) -> Finding | None:
        ranked = self.ranked
        return ranked[0] if ranked and ranked[0].weight >= 2 else None

    def render(self) -> str:
        """Present the evidence. Deliberately does not conclude.

        Without a model this is still useful — it is the five minutes of
        tab-hopping you would have done, already done. What it must not do is
        pretend to a diagnosis it has not earned.
        """
        lines = [f"Evidence for {self.target}:", ""]
        if not self.findings:
            return f"I couldn't find anything wrong with {self.target}."

        for finding in self.ranked:
            mark = "!" if finding.weight >= 3 else " "
            lines.append(f" {mark} {finding.detail}")

        if self.logs:
            lines.append("")
            lines.append("Recent log lines:")
            lines.extend(f"    {line}" for line in self.logs[-8:])

        strongest = self.strongest
        lines.append("")
        if strongest:
            lines.append(f"Most likely relevant: {strongest.detail}")
            lines.append("(That is what the evidence points at, not a diagnosis — "
                         "I have not verified a cause.)")
        else:
            lines.append("Nothing here stands out as a likely cause.")
        return "\n".join(lines)

    def as_prompt(self) -> str:
        """The dossier, formatted for a model to reason over (Phase 4).

        Everything is labelled as observation. The model is being asked to
        explain evidence, not to recall what usually goes wrong.
        """
        parts = [f"Target: {self.target}", "", "Observations:"]
        parts.extend(f"- [{f.kind}] {f.detail}" for f in self.ranked)
        if self.logs:
            parts += ["", "Log excerpt:", *(f"  {line}" for line in self.logs[-25:])]
        return "\n".join(parts)


# ----------------------------------------------------------------- collectors

# Lines worth surfacing. Ordered by how often they are the actual cause.
_LOG_SIGNALS: tuple[tuple[re.Pattern[str], str, int], ...] = (
    (re.compile(r"password authentication failed|auth.*fail", re.I),
     "authentication failure", 3),
    (re.compile(r"connection refused|could not connect|econnrefused", re.I),
     "connection refused", 3),
    (re.compile(r"address already in use|EADDRINUSE", re.I), "port already in use", 3),
    (re.compile(r"no such file or directory|module not found|cannot find module", re.I),
     "missing file or module", 3),
    (re.compile(r"out of memory|oomkill", re.I), "out of memory", 3),
    (re.compile(r"permission denied", re.I), "permission denied", 2),
    (re.compile(r"timeout|timed out", re.I), "timeout", 2),
    (re.compile(r"\b(fatal|panic)\b", re.I), "fatal error", 2),
    (re.compile(r"\btraceback\b|unhandled exception", re.I), "unhandled exception", 2),
    (re.compile(r"\berror\b", re.I), "error", 1),
)


def scan_logs(text: str, limit: int = 40) -> tuple[list[str], list[Finding]]:
    """Pull the lines that matter out of a wall of output.

    Deliberately keyword-based rather than model-based: this runs before any
    model sees anything, and a deterministic filter cannot hallucinate a cause
    that was not in the logs.
    """
    interesting: list[str] = []
    findings: list[Finding] = []
    seen: set[str] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pattern, label, weight in _LOG_SIGNALS:
            if not pattern.search(stripped):
                continue
            interesting.append(stripped[:300])
            if label not in seen:
                seen.add(label)
                findings.append(Finding("log", f"logs show {label}", weight,
                                        evidence=stripped[:300]))
            break
    return interesting[-limit:], findings


def container_logs(name: str, lines: int = 200) -> str:
    try:
        p = subprocess.run(["docker", "logs", "--tail", str(lines), name],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        return p.stdout + p.stderr
    except (OSError, subprocess.TimeoutExpired):
        return ""


def container_state(name: str) -> dict[str, Any] | None:
    try:
        p = subprocess.run(["docker", "inspect", name, "--format", "{{json .State}}"],
                           capture_output=True, text=True, timeout=20)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        parsed: Any = json.loads(p.stdout.strip())
        return parsed if isinstance(parsed, dict) else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def recently_changed(path: str, within_minutes: int = 120) -> list[tuple[str, int]]:
    """Config files touched recently.

    "It worked yesterday" almost always has a change behind it, and the change
    is usually not in the code — it is in a `.env` nobody committed.
    """
    watch = (".env", ".env.local", "docker-compose.yml", "docker-compose.dev.yml",
             "package.json", "requirements.txt", "pyproject.toml", "alembic.ini")
    cutoff = datetime.now(UTC) - timedelta(minutes=within_minutes)
    found: list[tuple[str, int]] = []
    base = Path(path)
    if not base.is_dir():
        return found
    for name in watch:
        target = base / name
        if not target.is_file():
            continue
        mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=UTC)
        if mtime > cutoff:
            found.append((name, int((datetime.now(UTC) - mtime).total_seconds() / 60)))
    return found


# ------------------------------------------------------------------ diagnosis


def diagnose_project(project: Project, *, deep: bool = True) -> Dossier:
    """Build the dossier for one project. Every step is read-only."""
    dossier = Dossier(target=project.id)

    # 1. Containers — the most common cause, and the most concrete evidence.
    if project.container:
        state = container_state(project.container)
        if state is None:
            dossier.add("container", f"{project.container} does not exist", 2)
        else:
            status = state.get("Status", "unknown")
            health = (state.get("Health") or {}).get("Status")
            dossier.raw["container_state"] = state
            if status == "exited":
                code = state.get("ExitCode")
                dossier.add("container", f"{project.container} exited (code {code})", 3,
                            evidence=json.dumps(state)[:400])
            elif health and health != "healthy":
                dossier.add("container", f"{project.container} is running but {health}", 3)
            else:
                dossier.add("container", f"{project.container} is {status}"
                            + (f" and {health}" if health else ""), 1)

            if deep and status in ("exited", "restarting") or health == "unhealthy":
                logs, log_findings = scan_logs(container_logs(project.container))
                dossier.logs.extend(logs)
                dossier.findings.extend(log_findings)

    # 2. Health endpoint — distinguishes "not running" from "running badly".
    if project.health_url:
        probe = http_probe(url=project.health_url, timeout_s=5.0)
        data = json.loads(probe.raw) if probe.raw else {}
        if "status" in data:
            code = int(data["status"])
            if code >= 500:
                dossier.add("health", f"health endpoint returns {code}", 3)
            elif code >= 400:
                dossier.add("health", f"health endpoint returns {code}", 2)
            else:
                dossier.add("health", f"health endpoint is {code} in {data.get('ms')}ms", 1)
        else:
            dossier.add("health", f"health endpoint unreachable: {data.get('error', '?')}", 3)

    # 3. Recent config changes — the "it worked yesterday" explanation.
    for name, minutes in recently_changed(project.compose_path or project.path):
        dossier.add("change", f"{name} changed {minutes} minutes ago", 3,
                    evidence=f"{name} mtime {minutes}m ago")

    # 4. Git state — context, rarely the cause, occasionally decisive.
    git = git_state(path=project.path)
    if git.raw and git.raw != "{}":
        data = json.loads(git.raw)
        dossier.raw["git"] = data
        if data.get("dirty"):
            dossier.add("git", f"{data['dirty']} uncommitted file(s) on {data.get('branch')}", 1)
        if data.get("last_commit"):
            dossier.add("git", f"last commit: {data['last_commit']}", 1)

    # 5. Ports — a dev server that will not start usually cannot bind.
    for url in (project.health_url, project.prod_url):
        if not url or "localhost" not in url:
            continue
        match = re.search(r":(\d{2,5})", url)
        if not match:
            continue
        result = port_inspect(port=int(match.group(1)))
        data = json.loads(result.raw) if result.raw else {}
        if data.get("free"):
            dossier.add("port", f"port {match.group(1)} is free — nothing is listening", 2)
        else:
            holders = data.get("holders") or []
            names = ", ".join(h["name"] for h in holders) or "something"
            dossier.add("port", f"port {match.group(1)} held by {names}", 1)

    return dossier


def diagnose_workspace(registry: ProjectRegistry) -> Dossier:
    """Nothing named — look for whatever is actually wrong."""
    dossier = Dossier(target="the workspace")

    ps = docker_ps()
    if "not running" in ps.summary or "not installed" in ps.summary:
        dossier.add("docker", "Docker is not running — no container can be up", 3)
        return dossier

    running = {r["name"] for r in (json.loads(ps.raw) if ps.raw else [])}
    for project in sorted(registry.projects.values(), key=lambda p: p.id):
        if project.container and project.container not in running:
            dossier.add("container", f"{project.id}: {project.container} is not running", 2)
        if not project.prod_url:
            continue
        probe = http_probe(url=project.prod_url, timeout_s=6.0)
        data = json.loads(probe.raw) if probe.raw else {}
        if "status" not in data:
            dossier.add("prod", f"{project.id}: production unreachable "
                        f"({data.get('error', '?')})", 3)
        elif int(data["status"]) >= 400:
            dossier.add("prod", f"{project.id}: production returns {data['status']}", 3)

    if not dossier.findings:
        dossier.add("ok", "everything I can see is healthy", 1)
    return dossier
