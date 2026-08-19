"""``tango doctor`` — environment validation.

The first command to run on a new machine, and before every milestone gate.
Its job is to fail *usefully*: every problem it reports says what to do about
it, so a fresh clone never greets you with a stack trace.

Deliberately dependency-free beyond the standard library — doctor has to run
when nothing else does.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Level(StrEnum):
    OK = "ok"
    WARN = "warn"
    """Degraded but usable — Tango will run with reduced capability."""
    FAIL = "fail"
    """Tango cannot work correctly until this is fixed."""


@dataclass
class Check:
    name: str
    level: Level
    detail: str
    fix: str = ""


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 127, ""


def _http_json(url: str, timeout: float = 3.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            parsed: Any = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------- checks


def check_python() -> Check:
    v = sys.version_info
    if (v.major, v.minor) < (3, 12):
        return Check("python", Level.FAIL, f"{platform.python_version()}",
                     "Tango needs Python 3.12+. Install it and recreate the venv.")
    venv = sys.prefix != sys.base_prefix
    if not venv:
        return Check("python", Level.WARN, f"{platform.python_version()} (not in a venv)",
                     "uv venv --python 3.12  &&  uv pip install -e \".[dev]\"")
    return Check("python", Level.OK, f"{platform.python_version()} in venv")


def check_ollama(model: str) -> list[Check]:
    """Ollama is the T1 tier. Without it Tango still runs every playbook —
    the deterministic path needs no model at all — but freeform requests and
    fuzzy routing degrade."""
    checks: list[Check] = []
    tags = _http_json("http://localhost:11434/api/tags")
    if tags is None:
        return [
            Check("ollama", Level.WARN, "not reachable on :11434",
                  "Install from ollama.com, then: ollama serve\n"
                  "        (Tango still runs all playbooks without it — "
                  "only freeform routing degrades.)")
        ]

    names = [m.get("name", "") for m in tags.get("models", [])]
    checks.append(Check("ollama", Level.OK, f"running, {len(names)} model(s)"))

    if not any(n.split(":")[0] == model.split(":")[0] for n in names):
        checks.append(
            Check("model", Level.WARN, f"'{model}' not pulled",
                  f"ollama pull {model}"
                  + (f"\n        (have: {', '.join(names)})" if names else ""))
        )
    else:
        checks.append(Check("model", Level.OK, model))
    return checks


def check_gpu() -> Check:
    code, out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                      "--format=csv,noheader"])
    if code != 0 or not out:
        return Check("gpu", Level.WARN, "no NVIDIA GPU detected",
                     "CPU inference works but is slow. On the lab laptop this "
                     "should report an RTX 5060.")
    first = out.splitlines()[0]
    return Check("gpu", Level.OK, first.strip())


def check_docker() -> Check:
    code, out = _run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=15)
    if code != 0:
        return Check("docker", Level.WARN, "daemon not reachable",
                     "Start Docker Desktop. Playbooks with a compose step will "
                     "report REFUTED until it runs — honestly, but they will fail.")
    return Check("docker", Level.OK, f"engine {out.splitlines()[0].strip()}")


def check_host_config(hosts: Path) -> list[Check]:
    """Config is host-aware (docs/16 §14.1): this machine and the lab laptop
    see different paths, and a silently-wrong path is worse than a missing one."""
    host = os.environ.get("TANGO_HOST") or socket.gethostname().lower()
    specific = hosts / host / "projects.json"
    fallback = hosts / "default" / "projects.json"

    if specific.is_file():
        source, path = f"hosts/{host}", specific
        level = Level.OK
    elif fallback.is_file():
        source, path = "hosts/default", fallback
        level = Level.WARN
    else:
        return [Check("projects", Level.FAIL, "no projects.json found",
                      f"Create hosts/{host}/projects.json (copy hosts/default as a start).")]

    checks = [
        Check("projects", level, f"{source} (hostname: {host})",
              "" if level is Level.OK else
              f"Using the default profile. Create hosts/{host}/projects.json "
              "with this machine's real paths.")
    ]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [*checks, Check("projects:parse", Level.FAIL, str(exc), "Fix the JSON syntax.")]

    missing = [p["id"] for p in data.get("projects", []) if not Path(p["path"]).is_dir()]
    if missing:
        checks.append(
            Check("project paths", Level.FAIL, f"{len(missing)} path(s) do not exist: "
                  f"{', '.join(missing)}",
                  f"Edit {path} so every 'path' points at a real directory on this machine.")
        )
    else:
        checks.append(Check("project paths", Level.OK,
                            f"{len(data.get('projects', []))} project(s) resolve"))
    return checks


def check_playbooks(path: Path) -> Check:
    if not path.is_dir():
        return Check("playbooks", Level.FAIL, f"{path} missing", "Did the clone complete?")
    try:
        from tango.playbook import PlaybookRegistry

        reg = PlaybookRegistry()
        count = reg.load_dir(path)
    except Exception as exc:
        return Check("playbooks", Level.FAIL, f"{exc.__class__.__name__}: {exc}",
                     "A playbook file is malformed.")
    if count == 0:
        return Check("playbooks", Level.FAIL, "none loaded", "playbooks/ is empty.")
    return Check("playbooks", Level.OK, f"{count} loaded: {', '.join(reg.names())}")


def check_store(db: Path) -> Check:
    try:
        from tango.store import Store

        store = Store(db)
        n = store.conn.execute("SELECT count(*) c FROM action").fetchone()["c"]
        mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        store.close()
    except Exception as exc:
        return Check("store", Level.FAIL, f"{exc.__class__.__name__}: {exc}",
                     f"Cannot open {db}. Check the directory is writable.")
    if mode.lower() != "wal":
        return Check("store", Level.FAIL, f"journal_mode={mode}",
                     "WAL is required for the ledger's crash guarantee.")
    return Check("store", Level.OK, f"{db} · WAL · {n} action(s) recorded")


def check_tools() -> Check:
    try:
        import tango.adapters.docker  # noqa: F401
        import tango.adapters.system  # noqa: F401
        from tango.tools import REGISTRY, check_contracts

        problems = check_contracts(REGISTRY)
    except Exception as exc:
        return Check("tools", Level.FAIL, f"{exc.__class__.__name__}: {exc}", "Import failure.")
    if problems:
        return Check("tools", Level.FAIL, "; ".join(problems),
                     "Every R2+ tool needs a verifier (docs/16 §10).")
    return Check("tools", Level.OK, f"{len(REGISTRY.names())} registered, contracts clean")


def check_editor() -> Check:
    exe = shutil.which("code")
    if exe is None:
        return Check("editor", Level.WARN, "'code' not on PATH",
                     "app.launch vscode will report REFUTED. In VS Code: "
                     "Shell Command: Install 'code' command in PATH.")
    return Check("editor", Level.OK, "code on PATH")


# ---------------------------------------------------------------------- report


def run_all(
    hosts: Path = Path("hosts"),
    playbooks: Path = Path("playbooks"),
    db: Path = Path("data/tango.db"),
    model: str = "qwen3:4b",
) -> list[Check]:
    checks = [check_python()]
    checks += check_ollama(model)
    checks.append(check_gpu())
    checks.append(check_docker())
    checks += check_host_config(hosts)
    checks.append(check_playbooks(playbooks))
    checks.append(check_store(db))
    checks.append(check_tools())
    checks.append(check_editor())
    return checks


_MARK = {Level.OK: "ok  ", Level.WARN: "warn", Level.FAIL: "FAIL"}


def report(checks: list[Check]) -> int:
    print(f"\nTANGO doctor — {socket.gethostname()} · {platform.system()} "
          f"{platform.release()}\n" + "─" * 66)
    for c in checks:
        print(f"  [{_MARK[c.level]}]  {c.name:14} {c.detail}")
        if c.fix and c.level is not Level.OK:
            for line in c.fix.splitlines():
                print(f"           → {line}")
    print("─" * 66)

    fails = [c for c in checks if c.level is Level.FAIL]
    warns = [c for c in checks if c.level is Level.WARN]

    if fails:
        print(f"\n{len(fails)} blocking problem(s). Fix those, then run doctor again.\n")
        return 1
    if warns:
        print(f"\nUsable, with {len(warns)} degraded capability/ies noted above.")
        print("Playbooks and the ledger work regardless — those need no model.\n")
        return 0
    print("\nEverything green. Tango is ready.\n")
    return 0
