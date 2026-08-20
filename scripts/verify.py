"""Module completion gate — run after any module or story is finished.

Unit tests prove parts. This proves the *whole* still holds. It runs every gate
that can run without the lab hardware (Tier A of docs/16 §15) and prints a
sign-off a human can read in five seconds.

    python scripts/verify.py            full gate
    python scripts/verify.py --quick    skip slow gates

Exit code 0 means the module is genuinely done; anything else means it is not,
regardless of how finished it looks.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass

# The Windows console defaults to cp1252, which cannot encode the typographic
# characters Tango uses throughout its output. Without this the tool crashes on
# its own banner — and the same trap awaits the CLI in S0.6.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PY = sys.executable


@dataclass
class Gate:
    name: str
    cmd: list[str]
    why: str
    slow: bool = False


GATES: list[Gate] = [
    Gate("lint", [PY, "-m", "ruff", "check", "tango/", "tests/", "scripts/"],
         "style drift compounds; catch it while the diff is small"),
    Gate("types", [PY, "-m", "mypy", "tango/"],
         "strict typing is the cheapest correctness gate available"),
    Gate("unit", [PY, "-m", "pytest", "tests/", "-q", "--ignore=tests/test_integration.py"],
         "each part behaves against its own contract"),
    Gate("integration", [PY, "-m", "pytest", "tests/test_integration.py", "-q"],
         "the parts actually compose — where systems really break"),
    Gate("contracts", [PY, "-c",
                       "import tango.adapters.docker, tango.adapters.system;"
                       "from tango.tools import REGISTRY, check_contracts;"
                       "p=check_contracts(REGISTRY);"
                       "print('\\n'.join(p)) if p else None;"
                       "raise SystemExit(1 if p else 0)"],
         "no R2+ tool without a verifier; no dangling compensate"),
    Gate("claims", [PY, "-m", "pytest", "tests/test_render.py", "-q", "-k", "licens or verb"],
         "the honesty guarantee: no unproven completion claim can ship"),
    Gate("injection", [PY, "-m", "pytest", "tests/test_policy.py", "-q", "-k", "injection"],
         "untrusted content cannot make Tango act — refused, recorded, and visible"),
    Gate("chaos", [PY, "-m", "pytest", "tests/test_failure_injection.py", "-q"],
         "under injected failure Tango may lose capability, never honesty"),
    Gate("latency", [PY, "-m", "pytest", "tests/test_performance.py", "-q"],
         "a tool nobody reaches for has failed, however correct it is"),
    Gate("eval", [PY, "evals/run.py", "--all"],
         "routing accuracy against the golden set — the instrument, not a test"),
]


def run(gate: Gate) -> tuple[bool, float, str]:
    started = time.monotonic()
    proc = subprocess.run(gate.cmd, capture_output=True, text=True)
    return proc.returncode == 0, time.monotonic() - started, (proc.stdout + proc.stderr)


def main() -> int:
    quick = "--quick" in sys.argv
    gates = [g for g in GATES if not (quick and g.slow)]

    print("\nTANGO — module completion gate\n" + "─" * 60)
    failures: list[tuple[Gate, str]] = []

    for gate in gates:
        ok, secs, output = run(gate)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}]  {gate.name:<12} {secs:5.1f}s   {gate.why}")
        if not ok:
            failures.append((gate, output))

    print("─" * 60)
    if failures:
        for gate, output in failures:
            print(f"\n╭─ {gate.name} output " + "─" * (44 - len(gate.name)))
            for line in output.strip().splitlines()[-25:]:
                print(f"│ {line}")
            print("╰" + "─" * 59)
        print(f"\n{len(failures)} of {len(gates)} gates failed — the module is NOT done.\n")
        return 1

    print(f"\nAll {len(gates)} gates green. Module is done.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
