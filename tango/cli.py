"""The CLI — Phase 0's only surface.

Deliberately thin. Everything it does goes through the same router → playbook →
executor → ledger → renderer path that voice and Telegram will use later, so
this is a real integration surface rather than a debugging shortcut.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer

from tango.executor import Executor
from tango.ledger import Ledger
from tango.playbook import PlaybookRegistry, run_playbook
from tango.projects import ProjectRegistry
from tango.render import false_success_signal, render_task
from tango.router import Route, Router
from tango.store import Store
from tango.types import ActionStatus, PrivacyClass

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
        import tango.adapters.system  # noqa: F401
        from tango.tools import REGISTRY

        self.tools = REGISTRY
        self.executor = Executor(ledger=self.ledger, registry=self.tools, store=self.store)
        self.playbooks = PlaybookRegistry()
        self.playbooks.load_dir(playbooks)
        self.projects = ProjectRegistry.load(hosts)
        self.router = Router(self.projects, known_playbooks=set(self.playbooks.names()))

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
    ctx.obj = core


@app.command()
def do(ctx: typer.Context, utterance: list[str]) -> None:
    """Run an utterance: tango do start optiresume"""
    core = _core(ctx)
    text = " ".join(utterance)
    decision = core.router.route(text)

    if decision.route is Route.REFUSE:
        typer.secho(decision.message, fg=typer.colors.RED)
        core.store.audit("cli", f"refuse:{decision.reason}", "DENIED", detail=text)
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
    pb = core.playbooks.get(decision.playbook_id)
    params: dict[str, Any] = dict(decision.params)
    project = params.get("_project")
    if "has_compose" in {p.name for p in pb.params}:
        params["has_compose"] = bool(project and project.compose_path)

    task_id = core.executor.new_task(
        goal=text,
        surface="cli",
        playbook_id=pb.id,
        playbook_version=pb.version,
        privacy_class=PrivacyClass.LOCAL_ONLY,
    )
    run = run_playbook(pb, params, task_id, core.executor)

    typer.echo(render_task(run.steps, run.status))

    signal = false_success_signal(
        " ".join(s.label for s in run.steps), [s.status for s in run.steps]
    )
    if signal > 0.5:
        typer.secho(f"[tripwire {signal:.2f}] response reviewed", fg=typer.colors.MAGENTA)
    if run.aborted_at:
        typer.secho(f"(stopped after '{run.aborted_at}')", fg=typer.colors.YELLOW)


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
