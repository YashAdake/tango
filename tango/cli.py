"""The CLI — Phase 0's only surface.

Deliberately thin. Everything it does goes through the same router → playbook →
executor → ledger → renderer path that voice and Telegram will use later, so
this is a real integration surface rather than a debugging shortcut.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from tango.aggregates import built_capabilities, is_aggregate, run_aggregate
from tango.executor import Executor
from tango.ledger import Evidence, Ledger, ToolResult, VerifyResult
from tango.playbook import PlaybookRegistry, run_playbook
from tango.projects import ProjectRegistry
from tango.render import StepOutcome, false_success_signal, render_task
from tango.router import Route, Router
from tango.session import SessionStore, resolve_placeholders
from tango.store import Store
from tango.types import ActionStatus, PrivacyClass, Risk, TaskStatus, VerifyStatus

# The Windows console defaults to cp1252 and cannot encode the typographic
# characters used throughout Tango's output. Found in V1 verification; the same
# applies to anything the Host Agent prints.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False, help="Tango — personal operating assistant")


class Core:
    """Everything wired together, once."""

    def __init__(self, db: Path, playbooks: Path, hosts: Path) -> None:
        self.store = Store(db)
        self.ledger = Ledger(self.store)

        import tango.adapters.docker  # noqa: F401
        import tango.adapters.inspect  # noqa: F401
        import tango.adapters.system  # noqa: F401
        from tango.tools import REGISTRY

        self.tools = REGISTRY
        self.executor = Executor(ledger=self.ledger, registry=self.tools, store=self.store)
        self.playbooks = PlaybookRegistry()
        self.playbooks.load_dir(playbooks)
        self.projects = ProjectRegistry.load(hosts)
        self.sessions = SessionStore(self.store)

        from tango.models import select_model

        # Handed over unprobed: the router calls available() only when every
        # rule has missed, so commands that never need a model never wait.
        self.model = select_model()
        self.router = Router(
            self.projects,
            known_playbooks=built_capabilities(set(self.playbooks.names())),
            model=self.model,
        )

    def recover(self) -> list[tuple[str, ActionStatus]]:
        """Reconcile interrupted commits before accepting new work."""
        return self.ledger.recover(probe=None)

    def close(self) -> None:
        self.store.close()


def _core(ctx: typer.Context) -> Core:
    return ctx.obj  # type: ignore[no-any-return]


@app.callback()
def main(
    ctx: typer.Context,
    db: Path = typer.Option(Path("data/tango.db"), "--db", help="Ledger database path"),
    playbooks: Path = typer.Option(Path("playbooks"), "--playbooks"),
    hosts: Path = typer.Option(Path("hosts"), "--hosts"),
) -> None:
    core = Core(db, playbooks, hosts)
    settled = core.recover()
    if settled:
        typer.secho(
            f"Reconciled {len(settled)} interrupted action(s) from a previous run.",
            fg=typer.colors.YELLOW,
        )

    for pending, outcome in core.executor.tick():
        typer.secho(f"[due] {pending.label}: {outcome.status}", fg=typer.colors.CYAN)

    ctx.obj = core


@app.command()
def do(ctx: typer.Context, utterance: list[str]) -> None:
    """Run an utterance: tango do start optiresume"""
    core = _core(ctx)
    text = " ".join(utterance)
    session = core.sessions.current("cli")
    decision = core.router.route(text, context=session.as_context())
    decision.params = resolve_placeholders(decision.params, session.as_context())

    if decision.route is Route.REFUSE:
        typer.secho(decision.message, fg=typer.colors.RED)
        core.store.audit("cli", f"refuse:{decision.reason}", "DENIED", detail=text)
        core.sessions.record(session, utterance=text, status="REFUSED")
        raise typer.Exit(2)

    if decision.route is Route.DECLINE:
        typer.secho(decision.message, fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    if decision.route is Route.CLARIFY:
        typer.secho(decision.message, fg=typer.colors.YELLOW)
        if decision.candidates:
            typer.echo("  " + ", ".join(decision.candidates))
        raise typer.Exit(1)

    assert decision.playbook_id is not None

    # Aggregate reads compute over the whole workspace rather than sequencing
    # tool calls; they record one ledger action, not twenty (tango/aggregates.py).
    if decision.playbook_id == "shutdown_all":
        ctx.invoke(stop, ctx=ctx, pid=None)
        return

    if is_aggregate(decision.playbook_id):
        agg_params = dict(decision.params)
        if "port" in agg_params:
            agg_params["port"] = int(agg_params["port"])
        result = run_aggregate(
            decision.playbook_id, core.projects, agg_params, core.ledger, core.executor
        )
        typer.echo(result.text)
        core.sessions.record(
            session, utterance=text, playbook=decision.playbook_id,
            project=agg_params.get("project") if agg_params.get("project") != "*" else None,
            target=str(agg_params.get("target")) if agg_params.get("target") else None,
            status="OK",
        )
        return

    if decision.playbook_id == "dev_down":
        _stop_project(ctx, core, session, text, decision)
        return

    pb = core.playbooks.get(decision.playbook_id)
    params: dict[str, Any] = dict(decision.params)
    project = params.get("_project")
    declared = {p.name for p in pb.params}
    if "has_compose" in declared:
        params["has_compose"] = bool(project and project.compose_path)
    if "has_dev_cmd" in declared:
        params["has_dev_cmd"] = bool(project and project.dev_cmd)

    task_id = core.executor.new_task(
        goal=text,
        surface="cli",
        playbook_id=pb.id,
        playbook_version=pb.version,
        privacy_class=PrivacyClass.LOCAL_ONLY,
    )
    run = run_playbook(pb, params, task_id, core.executor)

    typer.echo(render_task(run.steps, run.status))
    core.sessions.record(
        session, utterance=text, playbook=pb.id,
        project=params.get("project"), task_id=task_id, status=str(run.status),
    )

    signal = false_success_signal(
        " ".join(s.label for s in run.steps), [s.status for s in run.steps]
    )
    if signal > 0.5:
        typer.secho(f"[tripwire {signal:.2f}] response reviewed", fg=typer.colors.MAGENTA)
    if run.aborted_at:
        typer.secho(f"(stopped after '{run.aborted_at}')", fg=typer.colors.YELLOW)


def _stop_project(ctx: typer.Context, core: Core, session: Any, text: str, decision: Any) -> None:
    """Stop a project: its containers, and the processes Tango started for it.

    Both halves matter. Containers alone leaves the dev server running while
    reporting success, which is a false claim about the world — and a playbook
    with every step guarded off would otherwise report "Nothing to do".
    """
    from tango.cleanup import stop_all

    project = decision.params.get("_project")
    steps: list[StepOutcome] = []

    if project is not None and project.compose_path:
        pb = core.playbooks.get("dev_down")
        params = dict(decision.params)
        params["has_compose"] = True
        task_id = core.executor.new_task(
            goal=text, surface="cli", playbook_id=pb.id, playbook_version=pb.version,
            privacy_class=PrivacyClass.LOCAL_ONLY,
        )
        steps.extend(run_playbook(pb, params, task_id, core.executor).steps)

    if project is not None:
        try:
            results = stop_all(core.store, core.ledger, core.executor,
                               project_path=project.path)
        except Exception as exc:
            typer.secho(f"I can't read the process table right now — {exc}",
                        fg=typer.colors.YELLOW)
            raise typer.Exit(1) from exc
        steps.extend(
            StepOutcome(label=proc.label, status=status) for proc, status in results
        )

    if not steps:
        typer.echo(f"Nothing of {project.id if project else 'that'} was running.")
        core.sessions.record(session, utterance=text, playbook="dev_down",
                             project=getattr(project, "id", None), status="NOOP")
        return

    status = (TaskStatus.COMPLETED
              if all(s.status is ActionStatus.VERIFIED for s in steps)
              else TaskStatus.PARTIAL)
    typer.echo(render_task(steps, status))
    core.sessions.record(session, utterance=text, playbook="dev_down",
                         project=getattr(project, "id", None), status=str(status))


@app.command()
def doctor(
    hosts: Path = typer.Option(Path("hosts"), "--hosts"),
    playbooks: Path = typer.Option(Path("playbooks"), "--playbooks"),
    db: Path = typer.Option(Path("data/tango.db"), "--db"),
    model: str = typer.Option("qwen3:4b", "--model"),
) -> None:
    """Validate this machine. Run it first on any new host."""
    from tango.doctor import report, run_all

    raise typer.Exit(report(run_all(hosts, playbooks, db, model)))


@app.command()
def status(
    ctx: typer.Context,
    prod: bool = typer.Option(True, "--prod/--no-prod", help="Probe production URLs"),
) -> None:
    """What's the state of everything."""
    from tango.status import collect, render

    core = _core(ctx)
    snapshot = collect(core.projects, check_prod=prod)

    task_id = core.executor.new_task(goal="status of everything", surface="cli", route="status")
    action_id = core.ledger.propose(
        task_id=task_id, step_id="collect", tool="status.collect",
        args={"prod": prod}, risk=Risk.R0_READ,
    )
    core.ledger.commit(
        action_id,
        executor=lambda: ToolResult(ok=True, raw=json.dumps(snapshot.as_dict()),
                                    summary=f"{len(snapshot.projects)} projects observed"),
        verifier=lambda r: VerifyResult(
            VerifyStatus.VERIFIED, [Evidence("snapshot", r.raw)], "observed"
        ),
    )
    core.executor.settle_task(task_id)

    typer.echo(render(snapshot))


@app.command()
def running(ctx: typer.Context) -> None:
    """Processes Tango started that are still alive."""
    from tango.cleanup import started_processes

    core = _core(ctx)
    try:
        procs = started_processes(core.store)
    except Exception as exc:
        typer.secho(f"I can't read the process table right now — {exc}",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(1) from exc
    if not procs:
        typer.echo("Nothing that I started is still running.")
        return
    for proc in procs:
        typer.echo(f"  {proc.label}   started {proc.started_at[:19]}")


@app.command()
def stop(
    ctx: typer.Context,
    pid: list[int] = typer.Option(None, "--pid", help="Limit to specific pids"),
) -> None:
    """Stop the processes Tango started, verifying each one."""
    from tango.cleanup import stop_all

    core = _core(ctx)
    try:
        results = stop_all(core.store, core.ledger, core.executor,
                           pids=list(pid) if pid else None)
    except Exception as exc:
        typer.secho(f"I can't read the process table right now — {exc}",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(1) from exc
    if not results:
        typer.echo("Nothing that I started is still running.")
        return

    steps = [
        StepOutcome(label=proc.label, status=status, detail="")
        for proc, status in results
    ]
    task_status = (
        TaskStatus.COMPLETED
        if all(s.status is ActionStatus.VERIFIED for s in steps)
        else TaskStatus.PARTIAL
    )
    typer.echo(render_task(steps, task_status))


@app.command()
def pending(ctx: typer.Context) -> None:
    """Actions waiting to run, or waiting on you."""
    core = _core(ctx)
    items = core.executor.pending.outstanding()
    if not items:
        typer.echo("Nothing pending.")
        return
    for item in items:
        typer.secho(item.describe(), bold=True)
        if item.untrusted:
            typer.secho(f"    from untrusted: {', '.join(item.untrusted)}",
                        fg=typer.colors.YELLOW)
        if item.nonce:
            typer.echo(f"    confirm with: tango confirm {item.nonce}")
        else:
            typer.echo(f"    cancel with:  tango cancel {item.action_id}")


@app.command()
def confirm(ctx: typer.Context, nonce: str) -> None:
    """Approve a pending action and run it."""
    core = _core(ctx)
    outcome = core.executor.confirm(nonce)
    if outcome is None:
        typer.secho("That confirmation is not valid — it may have been used, "
                    "expired, or never existed.", fg=typer.colors.RED)
        raise typer.Exit(1)
    label = core.ledger.get(outcome.action_id)["tool"] if outcome.action_id else "Action"
    step = StepOutcome(label=label, status=outcome.status, detail=outcome.detail)
    status = TaskStatus.COMPLETED if outcome.status is ActionStatus.VERIFIED else TaskStatus.PARTIAL
    typer.echo(render_task([step], status))


@app.command()
def cancel(ctx: typer.Context, action_id: str) -> None:
    """Stop a pending action before it runs."""
    core = _core(ctx)
    if core.executor.pending.cancel(action_id):
        typer.echo("Cancelled — it will not run.")
    else:
        typer.secho("Nothing pending with that id.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)


@app.command()
def panic(ctx: typer.Context) -> None:
    """Cancel everything that has not run yet."""
    core = _core(ctx)
    count = core.executor.pending.cancel_all()
    typer.echo(f"Cancelled {count} pending action(s)." if count
               else "Nothing was pending.")


@app.command()
def why(ctx: typer.Context, task_id: str) -> None:
    """Show the evidence behind a task's claims."""
    core = _core(ctx)
    for action in core.ledger.actions_for_task(task_id):
        typer.secho(f"{action['tool']}  →  {action['status']}", bold=True)
        if action["detail"]:
            typer.echo(f"  {action['detail']}")
        for ev in core.ledger.evidence_for(action["id"]):
            typer.echo(f"  · {ev.kind}: {ev.payload[:160]}")


@app.command()
def projects(ctx: typer.Context) -> None:
    """List the projects Tango knows about on this host."""
    core = _core(ctx)
    typer.secho(f"host profile: {core.projects.hostname}", bold=True)
    for pid in core.projects.ids():
        p = core.projects.get(pid)
        mark = "ok " if p.exists else "MISSING"
        typer.echo(f"  [{mark}] {pid:14} {p.stack or '-'}")


@app.command()
def tools(ctx: typer.Context) -> None:
    """List registered tools with their risk and verifier status."""
    core = _core(ctx)
    for tool in core.tools.all():
        verifier = "verified" if tool.verifier else "UNVERIFIED"
        typer.echo(f"  {tool.name:22} {tool.risk.name:16} {verifier}")


@app.command()
def audit(ctx: typer.Context, limit: int = 20) -> None:
    """Recent audit events."""
    core = _core(ctx)
    rows = core.store.conn.execute(
        "SELECT ts, actor, action, verdict, detail FROM audit_event ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    for r in rows:
        typer.echo(f"{r['ts'][:19]}  {r['verdict']:12} {r['action']}  {r['detail'] or ''}")


if __name__ == "__main__":
    app()
