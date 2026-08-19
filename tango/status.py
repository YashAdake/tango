"""Cross-project situational awareness — the flagship read.

*"What's the state of everything?"* is the capability that most justifies Tango
existing: five minutes of tab-hopping and terminal-switching collapsed into a
few seconds. It is also the safest thing in the system — every observation here
is read-only, so there is no confirmation, no undo, and no blast radius.

Design note: this is **one** ledger action carrying a full observation as
evidence, not twenty. Recording twenty rows for a single question would bury the
audit trail in noise and make the ledger less useful for the consequential
actions it exists to protect.
"""

from __future__ import annotations

import concurrent.futures
import json
from dataclasses import dataclass, field
from typing import Any

from tango.adapters.inspect import docker_ps, git_state, http_probe, port_inspect
from tango.projects import Project, ProjectRegistry


@dataclass
class ProjectStatus:
    id: str
    stack: str = ""
    branch: str = ""
    dirty: int = 0
    ahead: int = 0
    behind: int = 0
    last_commit: str = ""
    dev_running: bool = False
    dev_detail: str = ""
    container: str | None = None
    container_status: str | None = None
    prod_url: str | None = None
    prod_status: str = ""
    prod_ms: int | None = None
    notes: list[str] = field(default_factory=list)

    prod_checked: bool = False
    """Whether production was actually probed. Not looking is not the same as
    finding a problem, and conflating them is the exact dishonesty the ledger
    exists to prevent — it just wears a different costume here."""

    @property
    def needs_attention(self) -> bool:
        """Deliberately narrow. Uncommitted work is normal; an unreachable
        production site is not. Flagging everything trains you to ignore it."""
        prod_bad = self.prod_checked and not self.prod_status.startswith("2")
        container_bad = self.container_status == "exited"
        return bool(prod_bad or container_bad)


@dataclass
class Snapshot:
    projects: list[ProjectStatus]
    containers: list[dict[str, Any]] = field(default_factory=list)
    ports: dict[int, str] = field(default_factory=dict)
    docker_available: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "projects": [p.__dict__ for p in self.projects],
            "containers": self.containers,
            "ports": {str(k): v for k, v in self.ports.items()},
            "docker_available": self.docker_available,
        }


WATCHED_PORTS = (3000, 8000)


def _collect_project(
    project: Project, containers: dict[str, str], check_prod: bool
) -> ProjectStatus:
    st = ProjectStatus(id=project.id, stack=project.stack, container=project.container,
                       prod_url=project.prod_url)

    git = git_state(path=project.path)
    if git.raw and git.raw != "{}":
        data = json.loads(git.raw)
        st.branch = data.get("branch", "")
        st.dirty = data.get("dirty", 0)
        st.ahead = data.get("ahead", 0)
        st.behind = data.get("behind", 0)
        st.last_commit = data.get("last_commit", "")
    else:
        st.notes.append("not a git repository")

    if project.container:
        st.container_status = containers.get(project.container)
        if st.container_status is None:
            st.container_status = "not running"

    if check_prod and project.prod_url:
        st.prod_checked = True
        probe = http_probe(url=project.prod_url, timeout_s=6.0)
        data = json.loads(probe.raw) if probe.raw else {}
        if "status" in data:
            st.prod_status = str(data["status"])
            st.prod_ms = data.get("ms")
        else:
            st.prod_status = "unreachable"
            st.notes.append(str(data.get("error", "no response")))

    return st


def collect(
    registry: ProjectRegistry,
    *,
    check_prod: bool = True,
    ports: tuple[int, ...] = WATCHED_PORTS,
) -> Snapshot:
    """Gather everything, probing production concurrently.

    Prod probes are network-bound and independent, so they run in parallel —
    five sequential six-second timeouts would make the flagship read feel
    broken on a bad network, which is a UX failure masquerading as honesty.
    """
    ps = docker_ps()
    docker_rows = json.loads(ps.raw) if ps.raw else []
    docker_available = "not installed" not in ps.summary and "not running" not in ps.summary
    containers = {r["name"]: r["status"] for r in docker_rows}

    projects = list(registry.projects.values())
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(_collect_project, p, containers, check_prod) for p in projects
        ]
        statuses = [f.result() for f in futures]

    port_state: dict[int, str] = {}
    for port in ports:
        result = port_inspect(port=port)
        data = json.loads(result.raw) if result.raw else {}
        if not data.get("free", True):
            holders = data.get("holders") or []
            port_state[port] = (
                ", ".join(f"{h['name']} (pid {h['pid']})" for h in holders) or "in use"
            )

    statuses.sort(key=lambda s: s.id)
    return Snapshot(projects=statuses, containers=docker_rows, ports=port_state,
                    docker_available=docker_available)


def render(snapshot: Snapshot) -> str:
    """Render for a terminal. Leads with the count that matters, then detail.

    Contains no completion verbs — this is a description of observed state, not
    a claim that anything was done, so claim licensing has nothing to license.
    """
    lines: list[str] = []
    attention = [p for p in snapshot.projects if p.needs_attention]
    running = sum(1 for p in snapshot.projects if p.container_status
                  and p.container_status not in ("not running", "exited"))

    head = f"{len(snapshot.projects)} projects"
    if running:
        head += f" · {running} with containers up"
    if attention:
        head += f" · {len(attention)} needing attention"
    lines.append(head)
    lines.append("")

    width = max((len(p.id) for p in snapshot.projects), default=10)
    for p in snapshot.projects:
        bits: list[str] = []
        if p.branch:
            git = p.branch
            marks = []
            if p.dirty:
                marks.append(f"{p.dirty} uncommitted")
            if p.ahead:
                marks.append(f"{p.ahead} unpushed")
            if p.behind:
                marks.append(f"{p.behind} behind")
            git += f" ({', '.join(marks)})" if marks else " clean"
            bits.append(git)
        if p.container:
            bits.append(f"{p.container}: {p.container_status}")
        if p.prod_url and p.prod_checked:
            mark = "ok" if p.prod_status.startswith("2") else p.prod_status
            bits.append(f"prod {mark}" + (f" {p.prod_ms}ms" if p.prod_ms else ""))
        elif p.prod_url:
            bits.append("prod not checked")
        for note in p.notes:
            bits.append(note)
        flag = " !" if p.needs_attention else "  "
        lines.append(f"{flag}{p.id.ljust(width)}  {' · '.join(bits) or 'no information'}")

    if snapshot.ports:
        lines.append("")
        for port, holder in sorted(snapshot.ports.items()):
            lines.append(f"  port {port} held by {holder}")

    if not snapshot.docker_available:
        lines.append("")
        lines.append("  (Docker is not running — container state unknown, not assumed empty.)")

    return "\n".join(lines)
