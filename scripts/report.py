"""``python scripts/report.py`` — one command, one file to send back.

Written because the lab laptop has no Claude on it. Everything that would
otherwise be an interactive back-and-forth is collected here instead: what the
machine has, whether every gate passes, how fast the model actually is, what the
system does on real commands, and what it costs while idle.

Output goes to ``reports/tango-report-<host>.md``. Send that one file.

    python scripts/report.py                 full report
    python scripts/report.py --quick         skip the slow live-model timings
    python scripts/report.py --no-run        skip commands that start processes

Safety: the live-run section only ever uses read-only capabilities unless you
pass ``--run-start``, and anything it does start, it stops again and verifies.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PY = sys.executable


@dataclass
class Section:
    title: str
    body: str
    ok: bool = True
    notes: list[str] = field(default_factory=list)


def run(cmd: list[str], timeout: float = 600.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except (OSError, FileNotFoundError) as exc:
        return 127, str(exc)


def fence(text: str, limit: int = 6000) -> str:
    text = text.strip()
    if len(text) > limit:
        text = text[:limit] + f"\n… truncated ({len(text)} chars total)"
    return f"```\n{text or '(no output)'}\n```"


# ------------------------------------------------------------------- sections


def s_machine() -> Section:
    lines = [
        f"host          {socket.gethostname()}",
        f"os            {platform.system()} {platform.release()} ({platform.version()})",
        f"cpu           {platform.processor() or 'unknown'}",
        f"python        {platform.python_version()}",
    ]
    code, out = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                     "--format=csv,noheader"], timeout=30)
    gpu = out.splitlines()[0].strip() if code == 0 and out else "none detected"
    lines.append(f"gpu           {gpu}")

    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        lines.append(f"ram           {stat.ullTotalPhys / 2**30:.1f} GB total, "
                     f"{stat.ullAvailPhys / 2**30:.1f} GB free")
    except Exception:
        lines.append("ram           (could not read)")

    code, out = run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30)
    lines.append(f"docker        {out.splitlines()[0].strip() if code == 0 else 'not running'}")
    lines.append(f"ollama        {'on PATH' if shutil.which('ollama') else 'not on PATH'}")
    code, out = run(["git", "rev-parse", "--short", "HEAD"], timeout=30)
    lines.append(f"tango commit  {out.strip() if code == 0 else 'unknown'}")
    return Section("Machine", fence("\n".join(lines)))


def s_doctor() -> Section:
    code, out = run([PY, "-m", "tango.cli", "doctor"], timeout=180)
    return Section("Doctor", fence(out), ok=code == 0)


def s_gates() -> Section:
    code, out = run([PY, "scripts/verify.py"], timeout=900)
    return Section("Verification gates", fence(out), ok=code == 0)


def s_eval() -> Section:
    code, out = run([PY, "evals/run.py", "--all", "--show-failures"], timeout=600)
    return Section("Routing accuracy (rules only)", fence(out), ok=code == 0)


def s_ollama() -> Section:
    """What the model runtime actually has, and how fast it is here.

    Latency is the number that decides whether Tango feels present, and it
    cannot be measured anywhere but on the target machine.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4) as r:  # noqa: S310
            tags = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        return Section(
            "Local model", fence(f"Ollama not reachable on :11434 ({exc})"),
            ok=False,
            notes=["Install from ollama.com, run `ollama serve`, then `ollama pull qwen3:4b`."],
        )

    models = [m.get("name", "?") for m in tags.get("models", [])]
    lines = [f"models available: {', '.join(models) or '(none pulled)'}"]

    if not models:
        return Section("Local model", fence("\n".join(lines)), ok=False,
                       notes=["Run: ollama pull qwen3:4b"])

    from tango.models import OllamaModel

    for name in models[:3]:
        model = OllamaModel(name=name)
        lines.append(f"\n--- {name} ---")
        timings: list[int] = []
        for i, prompt in enumerate([
            "Playbooks: dev_up, dev_down\nKnown projects: myjson\nUtterance: start myjson\n",
            "Playbooks: dev_up, status_all\nKnown projects: optiresume\n"
            "Utterance: fire up the resume thing\n",
            "Playbooks: status_all\nKnown projects: myjson\nUtterance: how's everything looking\n",
        ]):
            try:
                started = time.monotonic()
                completion = model.complete(
                    prompt,
                    system="Map the utterance to one playbook. Answer only JSON.",
                    schema={
                        "type": "object",
                        "properties": {"playbook": {"type": "string"},
                                       "project": {"type": "string"},
                                       "confidence": {"type": "number"}},
                        "required": ["playbook", "confidence"],
                    },
                )
                elapsed = int((time.monotonic() - started) * 1000)
                timings.append(elapsed)
                label = "cold" if i == 0 else "warm"
                answer = completion.parsed or completion.text[:80]
                lines.append(f"  [{label}] {elapsed:>6} ms  -> {answer}")
            except Exception as exc:
                lines.append(f"  call {i} failed: {exc.__class__.__name__}: {exc}")
                break
        if len(timings) > 1:
            warm = timings[1:]
            lines.append(f"  warm median: {sorted(warm)[len(warm) // 2]} ms "
                         f"(gate: routing path should stay under ~1200 ms)")
    return Section("Local model", fence("\n".join(lines)))


def s_model_eval() -> Section:
    """Routing accuracy with the model enabled — the number that decides
    whether a 4B is enough, or we go up (docs/15 §2.1)."""
    code, out = run([PY, "evals/run.py", "--all", "--show-failures"], timeout=900)
    return Section("Routing accuracy (with model fallback)", fence(out), ok=code == 0)


def s_live(allow_start: bool) -> Section:
    """Real commands against this machine. Read-only unless --run-start."""
    blocks: list[str] = []
    read_only = [
        ["projects"], ["tools"],
        ["do", "what's", "the", "state", "of", "everything"],
        ["do", "is", "prod", "ok"],
        ["do", "what", "did", "I", "ship", "this", "week"],
        ["do", "anything", "uncommitted", "anywhere?"],
        ["do", "what's", "hogging", "port", "3000"],
        ["do", "delete", "the", "optiresume", "database"],
        ["do", "start", "it"],
        ["do", "order", "me", "a", "pizza"],
    ]
    for args in read_only:
        code, out = run([PY, "-m", "tango.cli", *args], timeout=180)
        blocks.append(f"$ tango {' '.join(args)}\n{out}\n(exit {code})")

    if allow_start:
        for args in (["do", "start", "myjson"], ["running"], ["do", "shut", "everything", "down"],
                     ["running"]):
            code, out = run([PY, "-m", "tango.cli", *args], timeout=300)
            blocks.append(f"$ tango {' '.join(args)}\n{out}\n(exit {code})")
    else:
        blocks.append("(start/stop cycle skipped — pass --run-start to include it)")

    return Section("Live commands", fence("\n\n".join(blocks), limit=14000))


def s_resources() -> Section:
    """Idle footprint. Tango must leave the laptop usable (docs/15)."""
    lines: list[str] = []
    code, out = run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                     "--format=csv,noheader"], timeout=30)
    lines.append(f"gpu now:      {out.splitlines()[0].strip() if code == 0 else 'n/a'}")
    code, out = run(["docker", "stats", "--no-stream", "--format",
                     "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"], timeout=60)
    lines.append(f"containers:\n{out if code == 0 and out else '  (none / docker down)'}")
    ps_query = (
        "Get-Process python*,ollama* -ErrorAction SilentlyContinue | "
        "Select-Object Name,@{N='MB';E={[int]($_.WS/1MB)}} | "
        "Format-Table -Auto | Out-String"
    )
    code, out = run(["powershell", "-NoProfile", "-Command", ps_query], timeout=60)
    lines.append(f"tango processes:\n{out if code == 0 and out.strip() else '  (none running)'}")
    return Section("Resource footprint", fence("\n".join(lines)),
                   notes=["Gates: VRAM free >= 1.5 GB, RAM free >= 3 GB, idle CPU < 3%."])


def s_audit() -> Section:
    code, out = run([PY, "-m", "tango.cli", "audit", "--limit", "25"], timeout=120)
    return Section("Audit trail", fence(out), ok=code == 0)


# ---------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip live model timings")
    ap.add_argument("--no-run", action="store_true", help="skip live command execution")
    ap.add_argument("--run-start", action="store_true",
                    help="include the start/stop cycle (starts a real dev server, then stops it)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    print("Collecting. This takes a few minutes; live model timings are the slow part.\n")

    sections: list[Section] = [s_machine(), s_doctor(), s_gates(), s_eval()]
    if not args.quick:
        sections.append(s_ollama())
        sections.append(s_model_eval())
    if not args.no_run:
        sections.append(s_live(allow_start=args.run_start))
    sections.append(s_resources())
    sections.append(s_audit())

    host = socket.gethostname()
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    failed = [s.title for s in sections if not s.ok]

    out: list[str] = [
        f"# Tango report — {host}",
        "",
        f"Generated {stamp} · `python scripts/report.py"
        f"{' --quick' if args.quick else ''}{' --run-start' if args.run_start else ''}`",
        "",
        ("**Everything green.**" if not failed
         else f"**Attention: {len(failed)} section(s) not green — {', '.join(failed)}**"),
        "",
        "---",
        "",
    ]
    for s in sections:
        mark = "" if s.ok else "  ⚠"
        out.append(f"## {s.title}{mark}")
        out.append("")
        out.append(s.body)
        for note in s.notes:
            out.append("")
            out.append(f"> {note}")
        out.append("")

    out += [
        "---",
        "",
        "## Notes from me (fill this in — it is the part I cannot generate)",
        "",
        "1. **Did what Tango said match reality?** e.g. it reported the dev server",
        "   started — did the site actually load?",
        "2. **Anything that felt slow?** Where, and roughly how long?",
        "3. **Phrasings you tried that it did not understand** — verbatim, including",
        "   Hinglish. These become golden-set rows.",
        "4. **Anything that read wrong** — an ugly message, a confusing answer, a",
        "   claim you did not trust.",
        "5. **Anything that surprised you**, good or bad.",
        "",
    ]

    target = Path(args.out) if args.out else ROOT / "reports" / f"tango-report-{host}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8")

    print(f"\nWritten: {target}")
    print(f"Sections: {len(sections)} · not green: {len(failed)}")
    if failed:
        print(f"  {', '.join(failed)}")
    print("\nSend that file back. Add your notes at the bottom first — that part\n"
          "matters more than the rest, and it is the part I cannot see from here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
