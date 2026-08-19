"""Aggregate reads — capabilities that compute over the whole workspace.

`status_all`, `git_digest`, `prod_check` and `port_free` are not sequences of
tool calls; they are computations over many observations. Forcing them into the
playbook step format would produce twenty ledger rows for one question and bury
the audit trail that consequential actions depend on.

So they are **built-in capabilities**: they still go through the ledger, still
carry their full observation as evidence, and are still routed to by the same
router — but they record one action, not twenty. The playbook engine remains the
unit of work for anything that *changes* the world.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tango.adapters.inspect import git_log_since, http_probe, port_inspect
from tango.ledger import Evidence, Ledger, ToolResult, VerifyResult
from tango.projects import ProjectRegistry
from tango.status import collect, render
from tango.types import ActionStatus, Risk, VerifyStatus


@dataclass
class AggregateResult:
    text: str
    raw: dict[str, Any]
    summary: str


def _as_git_window(value: str) -> str:
    """Normalized windows ("7d", "24h") to what git understands.

    Params carry canonical values; converting to human phrasing is the
    capability's job, not the router's."""
    text = str(value).strip().lower()
    units = {"d": "days", "w": "weeks", "h": "hours", "m": "months", "y": "years"}
    if len(text) > 1 and text[:-1].isdigit() and text[-1] in units:
        return f"{text[:-1]} {units[text[-1]]} ago"
    return text


# ------------------------------------------------------------------ capability


def status_all(projects: ProjectRegistry, params: dict[str, Any]) -> AggregateResult:
    snapshot = collect(projects, check_prod=params.get("prod", True))
    return AggregateResult(
        text=render(snapshot),
        raw=snapshot.as_dict(),
        summary=f"{len(snapshot.projects)} projects observed",
    )


def prod_check(projects: ProjectRegistry, params: dict[str, Any]) -> AggregateResult:
    """Probe production. Reports latency and, importantly, distinguishes
    'unreachable' from 'answered with an error' — they mean different things
    and lead to different next steps."""
    wanted = params.get("project", "*")
    targets = [
        p for p in projects.projects.values()
        if p.prod_url and (wanted in ("*", None) or p.id == wanted)
    ]
    if not targets:
        return AggregateResult("No production URLs configured.", {}, "nothing to probe")

    rows: list[dict[str, Any]] = []
    for project in targets:
        probe = http_probe(url=project.prod_url or "", timeout_s=8.0)
        data = json.loads(probe.raw) if probe.raw else {}
        rows.append({"id": project.id, "url": project.prod_url, **data})

    healthy = [r for r in rows if str(r.get("status", "")).startswith("2")]
    width = max(len(r["id"]) for r in rows)
    lines = [f"{len(healthy)} of {len(rows)} healthy", ""]
    for r in sorted(rows, key=lambda x: str(x["id"])):
        if "status" in r:
            mark = "ok" if str(r["status"]).startswith("2") else f"HTTP {r['status']}"
            lines.append(f"  {str(r['id']).ljust(width)}  {mark}  {r.get('ms', '?')}ms")
        else:
            lines.append(f"  {str(r['id']).ljust(width)}  unreachable — {r.get('error', '?')}")
    return AggregateResult("\n".join(lines), {"probes": rows},
                           f"{len(healthy)}/{len(rows)} healthy")


def git_digest(projects: ProjectRegistry, params: dict[str, Any]) -> AggregateResult:
    """What shipped, across every repo. The thing that is genuinely tedious to
    assemble by hand and trivial to assemble from git."""
    since = _as_git_window(params.get("since", "7d"))
    rows: list[dict[str, Any]] = []
    for project in sorted(projects.projects.values(), key=lambda p: p.id):
        result = git_log_since(path=project.path, since=since)
        commits = json.loads(result.raw) if result.raw else []
        if commits:
            rows.append({"id": project.id, "commits": commits})

    total = sum(len(r["commits"]) for r in rows)
    if not total:
        return AggregateResult(f"No commits since {since}.", {"since": since, "repos": []},
                               "no commits")

    lines = [f"{total} commit(s) across {len(rows)} project(s) since {since}", ""]
    for row in rows:
        lines.append(f"  {row['id']}")
        for c in row["commits"][:12]:
            lines.append(f"    {c.get('date', '')}  {c.get('subject', '')}")
        if len(row["commits"]) > 12:
            lines.append(f"    … and {len(row['commits']) - 12} more")
        lines.append("")
    return AggregateResult("\n".join(lines).rstrip(), {"since": since, "repos": rows},
                           f"{total} commits")


def port_free(projects: ProjectRegistry, params: dict[str, Any]) -> AggregateResult:
    """Inspect a port. Deliberately does *not* kill anything: freeing a port
    terminates a process, which is an R1 action needing its own verified step,
    not a side effect of asking a question."""
    port = int(params.get("port", 3000))
    result = port_inspect(port=port)
    data = json.loads(result.raw) if result.raw else {}
    text = result.summary
    if not data.get("free", True) and data.get("holders"):
        text += "\n  Use 'tango stop' for processes I started, or stop it yourself."
    return AggregateResult(text, data, result.summary)


CAPABILITIES: dict[str, Callable[[ProjectRegistry, dict[str, Any]], AggregateResult]] = {
    "status_all": status_all,
    "prod_check": prod_check,
    "git_digest": git_digest,
    "port_free": port_free,
}


# Capabilities that change state and are dispatched outside the playbook
# engine. Kept beside CAPABILITIES so the built-set has one definition.
BUILTIN_ACTIONS = frozenset({"shutdown_all"})


def is_aggregate(name: str) -> bool:
    return name in CAPABILITIES


def built_capabilities(playbook_names: set[str]) -> set[str]:
    """Everything routable right now: playbooks, aggregate reads, and built-in
    actions. The router, the CLI and the eval harness must all agree on this —
    a second definition is how a working capability gets reported as missing."""
    return set(playbook_names) | set(CAPABILITIES) | set(BUILTIN_ACTIONS)


def run_aggregate(
    name: str,
    projects: ProjectRegistry,
    params: dict[str, Any],
    ledger: Ledger,
    executor: Any,
) -> AggregateResult:
    """Execute an aggregate read through the ledger.

    R0 throughout: reads need no confirmation and carry no blast radius. The
    ledger entry exists so that any answer can later be traced to the exact
    observation it came from.
    """
    task_id = executor.new_task(goal=name, surface="cli", route="aggregate")
    action_id = ledger.propose(
        task_id=task_id, step_id="collect", tool=f"aggregate.{name}",
        args={k: v for k, v in params.items() if not k.startswith("_")},
        risk=Risk.R0_READ,
    )

    holder: dict[str, AggregateResult] = {}

    def _execute() -> ToolResult:
        holder["result"] = CAPABILITIES[name](projects, params)
        return ToolResult(ok=True, raw=json.dumps(holder["result"].raw),
                          summary=holder["result"].summary)

    def _verify(result: ToolResult) -> VerifyResult:
        if result.raw:
            return VerifyResult(VerifyStatus.VERIFIED, [Evidence("observation", result.raw[:4000])],
                                result.summary)
        return VerifyResult(VerifyStatus.UNVERIFIABLE, [], "no observation recorded")

    status = ledger.commit(action_id, executor=_execute, verifier=_verify)
    executor.settle_task(task_id)

    if status is not ActionStatus.VERIFIED or "result" not in holder:
        detail = ledger.get(action_id).get("detail") or "the read did not complete"
        return AggregateResult(f"I couldn't gather that — {detail}", {}, "failed")
    return holder["result"]
