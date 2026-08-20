"""The eval harness — Tango's instrument.

For an LLM system the golden set is not a test, it is the measuring device. It
is the only way to answer "did that prompt change regress anything?" or "is a 4B
model enough?" without guessing (docs/05).

Two disciplines it enforces:

* **Corpus / holdout split** (docs/17 C3). The router uses the corpus as its
  memory, so measuring on the corpus is measuring on training data. Every gate
  reads the sealed holdout only.
* **Not-yet-built is not failure.** Rows referencing playbooks a phase has not
  reached are reported separately. Counting them as failures hides real
  regressions in noise; counting them as passes is a lie.

    python evals/run.py                     measure the current router
    python evals/run.py --show-failures     with each miss explained
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tango.aggregates import built_capabilities  # noqa: E402
from tango.playbook import PlaybookRegistry  # noqa: E402
from tango.projects import ProjectRegistry  # noqa: E402
from tango.router import Route, Router  # noqa: E402

HOLDOUT_FRACTION = 0.30

# Gates (docs/16 §3.4). Measured on the holdout only.
GATES = {
    "routing_top1": 0.95,
    "param_exact": 0.92,
    "refusal": 1.00,
    "clarify": 0.85,
}


@dataclass
class Row:
    id: str
    utterance: str
    expect: dict[str, Any]
    strata: str = "unknown"
    lang: str = "en"
    author: str = "unknown"
    context: dict[str, Any] | None = None
    phase: int | None = None

    @property
    def is_holdout(self) -> bool:
        """Deterministic split by id hash, so it is stable across runs and
        machines — a holdout that reshuffles is not a holdout.

        Owner-authored rows are preferred for the holdout: they are the real
        distribution, and that is what a gate should be measured against.
        """
        weight = 0.45 if self.author.startswith("owner") else HOLDOUT_FRACTION
        digest = hashlib.sha256(self.id.encode()).digest()
        return (digest[0] / 255.0) < weight

    @property
    def expected_kind(self) -> str:
        if self.expect.get("refuse"):
            return "refuse"
        if self.expect.get("clarify"):
            return "clarify"
        if self.expect.get("decline"):
            return "decline"
        if self.expect.get("route") == "plan":
            return "plan"
        if "playbook" in self.expect:
            return "playbook"
        return "unknown"


def load(path: Path) -> list[Row]:
    rows: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        d = json.loads(line)
        rows.append(
            Row(
                id=d["id"], utterance=d["utterance"], expect=d["expect"],
                strata=d.get("strata", "unknown"), lang=d.get("lang", "en"),
                author=d.get("author", "unknown"), context=d.get("context"),
                phase=d.get("phase"),
            )
        )
    return rows


@dataclass
class Result:
    row: Row
    passed: bool
    got: str
    detail: str = ""
    skipped: bool = False


def judge(row: Row, router: Router, built: set[str]) -> Result:
    decision = router.route(row.utterance, context=row.context)
    kind = row.expected_kind

    # A row for a capability this phase has not built yet measures nothing.
    if row.phase is not None and row.phase > 0:
        return Result(row, False, "-", f"tagged for phase {row.phase}", skipped=True)
    if kind == "playbook":
        wanted = row.expect["playbook"]
        if wanted not in built:
            return Result(row, False, decision.route.value,
                          f"'{wanted}' not built yet", skipped=True)
    if kind == "plan" and "plan" not in built:
        return Result(row, False, decision.route.value,
                      "freeform planner not built yet (Phase 4)", skipped=True)

    if kind == "refuse":
        ok = decision.route is Route.REFUSE
        return Result(row, ok, decision.route.value,
                      "" if ok else f"expected refusal, got {decision.route.value}")

    if kind == "decline":
        ok = decision.route in (Route.DECLINE, Route.REFUSE)
        return Result(row, ok, decision.route.value,
                      "" if ok else f"expected decline, got {decision.route.value}")

    if kind == "clarify":
        ok = decision.route is Route.CLARIFY
        return Result(row, ok, decision.route.value,
                      "" if ok else f"expected a question, got {decision.route.value}")

    if kind == "playbook":
        if decision.route is not Route.PLAYBOOK:
            return Result(row, False, decision.route.value,
                          f"expected {row.expect['playbook']}, got {decision.route.value}"
                          f" ({decision.reason})")
        if decision.playbook_id != row.expect["playbook"]:
            return Result(row, False, decision.playbook_id or "?",
                          f"routed to {decision.playbook_id}, expected {row.expect['playbook']}")
        for key, want in (row.expect.get("params") or {}).items():
            got = decision.params.get(key)
            if want != "*" and got != want:
                return Result(row, False, decision.playbook_id or "?",
                              f"param '{key}': got {got!r}, expected {want!r}")
        return Result(row, True, decision.playbook_id or "?")

    return Result(row, False, decision.route.value, f"unhandled expectation kind: {kind}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="evals/golden.draft.jsonl")
    ap.add_argument("--show-failures", action="store_true")
    ap.add_argument("--all", action="store_true", help="report corpus rows too")
    args = ap.parse_args()

    rows = load(Path(args.set))
    projects = ProjectRegistry.load("hosts")
    playbooks = PlaybookRegistry()
    playbooks.load_dir(Path("playbooks"))
    built = built_capabilities(set(playbooks.names()))
    router = Router(projects, known_playbooks=built)

    scope = rows if args.all else [r for r in rows if r.is_holdout]
    results = [judge(r, router, built) for r in scope]

    live = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]

    by_kind: dict[str, list[Result]] = defaultdict(list)
    for res in live:
        by_kind[res.row.expected_kind].append(res)

    print(f"\nTANGO eval — {Path(args.set).name}")
    print(f"router: regex (no model) · playbooks built: {', '.join(sorted(built)) or 'none'}")
    print(f"rows: {len(rows)} total · {len(scope)} in scope"
          f" ({'all' if args.all else 'holdout'}) · {len(skipped)} not-yet-built\n")

    if not live:
        print("  nothing measurable yet in this scope.\n")
        return 0

    gate_map = {"playbook": "routing_top1", "refuse": "refusal",
                "clarify": "clarify", "decline": "clarify"}
    failed_gates: list[str] = []

    for kind in sorted(by_kind):
        group = by_kind[kind]
        passed = sum(1 for r in group if r.passed)
        rate = passed / len(group)
        gate_name = gate_map.get(kind)
        gate = GATES.get(gate_name or "", 0.0)
        status = "PASS" if rate >= gate else "FAIL"
        if rate < gate:
            failed_gates.append(f"{kind} {rate:.0%} < {gate:.0%}")
        bar = "█" * int(rate * 20) + "░" * (20 - int(rate * 20))
        print(f"  [{status}]  {kind:9} {bar} {passed:>2}/{len(group)}  {rate:>5.0%}"
              f"   gate {gate:.0%}")

    total_pass = sum(1 for r in live if r.passed)
    print(f"\n  overall: {total_pass}/{len(live)} ({total_pass / len(live):.0%}) on measurable rows")

    by_lang: dict[str, list[Result]] = defaultdict(list)
    for res in live:
        by_lang[res.row.lang].append(res)
    if len(by_lang) > 1:
        detail = "  ".join(
            f"{lang}: {sum(1 for r in g if r.passed)}/{len(g)}"
            for lang, g in sorted(by_lang.items())
        )
        print(f"  by language: {detail}")

    misses = [r for r in live if not r.passed]
    if misses and args.show_failures:
        print("\n  misses:")
        for r in misses:
            print(f"    {r.row.id}  {r.row.utterance!r}")
            print(f"        {r.detail}")
    elif misses:
        print(f"\n  {len(misses)} miss(es) — rerun with --show-failures")

    if skipped:
        pending = sorted({r.detail for r in skipped})
        print(f"\n  deferred ({len(skipped)} rows): {'; '.join(pending)}")

    if failed_gates:
        print(f"\n  GATES FAILED: {'; '.join(failed_gates)}\n")
        return 1
    print("\n  all gates green.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
