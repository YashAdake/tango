"""Stopping what Tango started — the ledger as memory.

D6 (docs/VERIFICATION-LOG.md V2): the first real run left a dev server running
that nothing was tracking, and stopping it meant reading a PID out of a debug
dump. A system that starts real processes has to know what it started.

It already does. Every ``process.start`` wrote a durable row with its PID and a
verified outcome, so "what is still running that I began?" is a query, not a
guess — and one that survives a reboot of Tango itself, because the answer lives
on disk rather than in memory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from tango.adapters.system import _running_pids
from tango.ledger import Ledger
from tango.store import Store
from tango.types import ActionStatus


@dataclass(frozen=True)
class StartedProcess:
    action_id: str
    task_id: str
    pid: int
    tool: str
    args: dict[str, str]
    started_at: str
    alive: bool

    @property
    def label(self) -> str:
        cmd = self.args.get("cmd") or self.args.get("app") or self.tool
        cwd = self.args.get("cwd", "")
        tail = f" in {cwd.rsplit('/', 1)[-1]}" if cwd else ""
        return f"{cmd}{tail} (pid {self.pid})"


def started_processes(store: Store, *, only_alive: bool = True) -> list[StartedProcess]:
    """Every process Tango verifiably started, newest first.

    Only ``VERIFIED`` rows count: an unverified start may never have happened,
    and killing a PID we cannot prove we own is exactly the kind of confident
    wrong action the whole design exists to prevent.
    """
    rows = store.conn.execute(
        "SELECT id, task_id, tool, args_canonical, provider_ref, committed_at "
        "FROM action WHERE tool = 'process.start' AND status = ? "
        "ORDER BY committed_at DESC",
        (ActionStatus.VERIFIED,),
    ).fetchall()

    live = _running_pids()
    found: list[StartedProcess] = []
    seen: set[int] = set()

    for row in rows:
        ref = row["provider_ref"]
        if not ref or not str(ref).isdigit():
            continue
        pid = int(ref)
        if pid in seen:
            continue
        seen.add(pid)

        alive = pid in live
        if only_alive and not alive:
            continue
        try:
            args = json.loads(row["args_canonical"])
        except json.JSONDecodeError:
            args = {}
        found.append(
            StartedProcess(
                action_id=row["id"], task_id=row["task_id"], pid=pid, tool=row["tool"],
                args={k: str(v) for k, v in args.items()},
                started_at=row["committed_at"] or "", alive=alive,
            )
        )
    return found


def stop_all(
    store: Store,
    ledger: Ledger,
    executor: object,
    *,
    pids: list[int] | None = None,
) -> list[tuple[StartedProcess, ActionStatus]]:
    """Stop tracked processes, each through the ledger like any other action.

    Cleanup is not exempt from verification: a stop that did not stop must
    report ``REFUTED``, or "shut everything down" becomes a claim nobody checked.
    """
    from tango.tools import ToolCall

    targets = [p for p in started_processes(store) if pids is None or p.pid in pids]
    if not targets:
        return []

    task_id = executor.new_task(  # type: ignore[attr-defined]
        goal="stop processes Tango started", surface="cli", route="cleanup"
    )

    results: list[tuple[StartedProcess, ActionStatus]] = []
    for proc in targets:
        outcome = executor.run(  # type: ignore[attr-defined]
            task_id, ToolCall(tool="process.stop", args={"pid": proc.pid},
                              step_id=f"stop-{proc.pid}")
        )
        results.append((proc, outcome.status))
    executor.settle_task(task_id)  # type: ignore[attr-defined]
    return results
