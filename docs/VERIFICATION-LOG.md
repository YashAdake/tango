# Verification Log

A record of what was checked after each module, what it found, and what changed.

**The rule (standing):** no module is "done" when its own tests pass. It is done
when `python scripts/verify.py` is green, it has been audited against its
acceptance criteria in [16](16-architecture-and-implementation-plan.md) §14.2,
and its seams with everything already built have been exercised. Unit tests
prove parts; **seams are where systems break**, and seams have no owner unless
somebody goes looking.

---

## V1 — Phase 0, stories S0.2–S0.5 (store, ledger, tools, renderer)

**Date:** 2026-08-19 · **Verdict:** PASS after 3 fixes · **Gate:** 6/6 green

### What was checked

| Check | Method | Result |
|---|---|---|
| Each part behaves | 169 unit tests | pass |
| **Parts compose** | new `tests/test_integration.py` — real registry + real ledger + real renderer, only the outside world faked | **found 3 defects** |
| Contract audit | `check_contracts()` over the shipped registry | clean |
| Schema matches spec | live PRAGMA + table dump vs docs/16 §7.1 | matches |
| Types | `mypy --strict`, 10 files | clean |
| Style | `ruff` | clean |

### Defects found — all three invisible to unit tests

**D1 · The execution seam did not exist.** `ToolRegistry` and `Ledger` had no
connection between them, and nothing created task rows (unit tests inserted raw
SQL). Each part passed its own tests while the system could not actually run a
tool. There was also a signature mismatch waiting: a tool's verifier takes
`(result, args)` because it needs the arguments to know what to check, while the
ledger only knows `result`.

*Fix:* new `tango/executor.py` — owns task lifecycle, binds registry to ledger,
derives task status from the ledger rather than from anyone's claim. It is the
single place that knows about all four subsystems, which keeps the others
independently testable.

**D2 · The renderer emitted an unlicensed claim of its own.** `render_task`
appended *"Stopped there."* on a failed task. "stopped" is a completion verb, so
the renderer violated the very rule it enforces — and the per-step check never
saw it, because task-level commentary was never checked at all.

*Fix:* suffix text is now validated separately and must contain **no** completion
verbs, since it describes the shape of the outcome rather than any single action.
Wording changed to *"Nothing further ran."*

**D3 · Windows console encoding crashed the tooling.** cp1252 cannot encode the
typographic characters used throughout Tango's output; the verification script
died on its own banner.

*Fix:* explicit UTF-8 reconfiguration. **Carried forward:** the CLI in S0.6 needs
the same treatment, and so does anything the Host Agent prints.

### Earlier find, same class

While writing S0.5 the exhaustive verb × status sweep caught `LICENSED_VERBS`
being defined but never consulted, so "cancelled" tripped on a `CANCELLED`
action. Three of the four defects so far have been in the *checking* machinery
rather than the logic — worth remembering when reading a green suite.

### Spec conformance (docs/16 §14.2)

| AC | Status |
|---|---|
| S0.2 migration idempotent, WAL on | ✅ WAL + `synchronous=FULL` + FK enforcement verified live |
| S0.3 kill mid-`COMMITTING` → restart reconciles, zero duplicate effects | ✅ unit + integration (real store close/reopen from disk) |
| S0.4 R2+ without verifier fails at import | ✅ raises at construction, not at review |
| S0.4 verifiers independent of actor | ✅ signature pinned; source-grepped for `result.ok` |
| S0.5 replay → 0 unlicensed verbs | ✅ exhaustive verb × status sweep |
| Schema per §7.1 | ✅ all 7 tables incl. capability-freeze and TOCTOU columns |

### Known gaps — deliberate, tracked

- `resource_lock` table exists but is unused — per-resource locking is Phase 6 (docs/17 C4).
- `check_contracts()` runs in tests and in `scripts/verify.py`, not at import. Acceptable while the registry is static; revisit when playbooks load tools dynamically.
- `StepOutcome.evidence` is populated but not yet surfaced to the user — that is the `why?` affordance, S0.6.
- Adapters are only exercised against fakes. **They touch the real OS and Docker and have never run for real** — first genuine execution is S0.6, and that is a real risk to watch, not a formality.

---

## Template for future entries

```
## V<n> — <phase/stories>
Date · Verdict · Gate result
### What was checked      (table: check / method / result)
### Defects found         (each: what, why unit tests missed it, fix)
### Spec conformance      (AC table from docs/16 §14.2)
### Known gaps            (deliberate, with the phase that closes them)
```

---

## V2 — Phase 0, story S0.6 (playbook engine, router, resolvers, CLI)

**Date:** 2026-08-19 · **Verdict:** PASS after 4 fixes · **Gate:** 6/6 green

### What was checked

| Check | Method | Result |
|---|---|---|
| Playbook semantics | 20 unit tests: guards, substitution, binding, on_fail | pass |
| Router behaviour | 15 unit tests incl. every refusal pattern | **found 1 defect** |
| Resolvers | exact / alias / ambiguous / unknown | pass |
| **Real execution** | CLI run against the actual machine, real OS and Docker | **found 2 defects** |
| Adapters vs reality | first ever non-fake execution (V1 flagged this as open risk) | **works** |
| Full gate | `scripts/verify.py` | **found 1 defect** |

### The headline: the adapters work, and the honesty guarantee held under real failure

First genuine execution outside fakes. `tango do start myjson` started a dev
server (PID 23980) and **independently verified it in the OS process table**,
then launched and verified the editor.

Then `tango do start optiresume` hit a real failure — Docker Desktop was not
running — and produced:

```
Database — failed: compose up failed: unable to get image 'postgres:16-alpine':
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
Nothing further ran.
(stopped after 'db')
```

It did not say "started your dev environment". `on_fail: abort` stopped the run,
the task settled `PARTIAL`, and the sentence carried the provider's actual error.
That is the whole thesis, working on its first contact with reality.

### Defects found

**D4 · A step silently never ran.** `has_compose` was not a declared playbook
parameter, so the CLI stripped it, so the guard `params.has_compose == true`
evaluated false, so the **database step vanished from every run** — while the
task still reported `COMPLETED`. The most dangerous defect found so far: no
error, no warning, a confident success message, and a service that never started.

*Fix:* `evaluate_when` now **raises** when a guard references an undeclared
parameter. A guard that quietly evaluates false is a step that silently never
runs, which is precisely what this system exists not to do. (The docstring
warning against this was written before the code that did it.)

**D5 · Router missed a natural phrasing.** `"shut everything down"` did not
match — the pattern allowed `shut everything` but not the trailing particle.
English puts it either side of the object.

*Fix:* pattern accepts both orders, longest alternatives first so `shut` cannot
swallow `shut down`. Verified across six phrasings; single-project stop still
routes to `dev_down`.

**D6 · An incidental side effect during testing.** The first real run started a
dev server on the workspace machine that nothing was tracking. Stopped via
`process.stop` with verification.

*Note for later:* Tango starts real processes. Phase 1 needs a `tango stop` that
uses ledger history to find what it started, rather than the operator
remembering PIDs.

**D7 · Gate caught lint/type drift** across the four new modules (import order,
`Any` leakage through guard returns, a `typer` idiom conflicting with `B008`).
Fixed; `B008` scoped narrowly to `cli.py` rather than disabled project-wide.

### Spec conformance (docs/16 §14.2, S0.6)

| AC | Status |
|---|---|
| Regex router, no model in the path | ✅ zero model calls; the model's interface is already the one it will inherit |
| `dev_up` playbook end-to-end | ✅ verified per-step against the real OS |
| PARTIAL demonstrated by a failing step | ✅ demonstrated by a *real* failure, not a simulated one |
| Refusals never fall through to a playbook | ✅ refusals evaluated first; `"take prod down"` also matches a stop pattern and is still refused |
| Capability freeze can see the tool set up front | ✅ `Playbook.tool_names()` before execution |

### Known gaps — deliberate, tracked

- `status_all` and `shutdown_all` route but have no playbook yet — Phase 1.
- Compose path untested end-to-end because Docker was down; retest when it runs.
- `hosts/default/projects.json` is a starting map, not verified against every project's real dev command — Phase 1 with the daily-jobs list.
- No `tango stop` yet (see D6).
- Retry `on_fail` is implemented but only lightly exercised.

---

## V3 — Phase 0, story S0.7 part 1 (eval harness, config from reality)

**Date:** 2026-08-19 · **Verdict:** PASS after 5 fixes · **Gate:** 7/7 green
**Routing: 27/27 (100%) on measurable rows, all four accuracy gates green**

### Context

The dev machine has no Ollama, no NVIDIA GPU and Docker stopped — so the model
tier cannot be *measured* here. That is the anticipated split (docs/16 §14.1):
the model swaps in on the lab laptop behind the router's existing interface.
What is hardware-independent, and far more valuable right now, is **the
instrument**. Built it, pointed it at the regex router, and it immediately
earned its keep.

### What was checked

| Check | Method | Result |
|---|---|---|
| Config vs reality | read every project's real `package.json` / compose files | **found 2 defects** |
| Routing accuracy | new `evals/run.py` over the golden set | **found 3 defects, 8 misses** |
| Regression | full suite after resolver hardening | pass |
| Gate | `scripts/verify.py`, now 7 gates | pass |

### Defects found

**D8 · The resolver would act on a stopword — three symptoms, one cause.**
`"kill the db"`, `"kill the dev server"` and `"run the tests"` all resolved to
**optiresume**, because the word *"the"* token-matched its alias *"the resume
thing"* and scored 0.4. The system would have stopped a real project because a
sentence contained a definite article. This is the single most dangerous defect
found so far — worse than D4, because D4 did nothing while this one acts on the
wrong target.

*Fix:* stopword list excluded from token matching; overlap scored by how much of
the query it actually explains; and a hard `MIN_RESOLVE_CONFIDENCE = 0.65` floor
below which `resolve()` raises rather than returning a guess. Verified: all
three now ask; real names and aliases still resolve.

**D9 · Guessed config, wrong config.** `hosts/default/projects.json` was written
from assumption. Reality: optiresume uses `docker-compose.dev.yml` (not the
default file) with container `optiresume-dev-db` (not `optiresume-db-1`), and
the `Project` model had **no field for a non-default compose file** — so the
playbook would have quietly brought up the wrong stack.

*Fix:* added `compose_file`, wired through loader, adapter and both playbooks;
config rewritten from each project's actual files. Also added a `has_dev_cmd`
guard so a static site (portfolio) opens the editor and does not try to start a
dev server that does not exist.

**D10 · Context schema mismatch between the golden set and the router.** The
eval provided `last_action: "dev_up myjson"`; the router only read
`prior_project`. So `"actually kill it"` lost its referent and asked a
needless question.

*Fix:* `_prior_project()` accepts either shape. This is a seam defect the
integration tests could not see, because both sides were internally consistent.

**D11 · Missing refusal and decline coverage.** Bulk mail forwarding, social
account deletion and OS-level reboot all fell through to "I don't have a
playbook" — technically safe, but the wrong answer, and refusal correctness has
a 100% gate for a reason.

**D12 · Conversational filler defeated matching.** `"actually kill it"` did not
match because of one leading word. People do not speak in commands. Fixed with a
filler-stripping pass; also added minimal Hinglish verb forms (`chalu kar de`,
`band kar do`), taking `hi-en` from 2/3 to 2/2 measurable.

### Judgement calls worth recording

- `g016 "kill the dev server"` expects resolution to *whatever is running*. That
  needs ledger-backed tracking of what Tango started — the same feature as
  `tango stop` (D6). Tagged **phase 1** rather than contorting the router, and
  the harness now honours `phase` tags as deferred rather than failed.
- The harness reports **not-yet-built separately from failed**. Counting unbuilt
  capabilities as failures buries real regressions in noise; counting them as
  passes is a lie. 34 of 61 rows are currently deferred, and saying so plainly
  is the honest reading of "100%".

### Spec conformance

| AC | Status |
|---|---|
| Golden set exists before the model (S0.1) | ✅ 61 rows, drafted, owner pass pending |
| Corpus/holdout split, gates on holdout (docs/17 C3) | ✅ deterministic hash split; owner rows weighted toward holdout |
| Routing ≥95%, refusal 100%, clarify ≥85% | ✅ 100% across all four categories, measurable rows |
| Eval is a CI gate | ✅ added as the 7th gate in `scripts/verify.py` |

### Known gaps

- **Model tier unbuilt** — needs the lab laptop. The router interface it will inherit is already fixed, so the swap touches nothing downstream.
- **Only 27 of 61 rows measurable** until more playbooks land (Phase 1).
- **Golden set is still Claude-drafted.** The 100% is against *my* guesses at your phrasing. The number only becomes meaningful after the owner edit pass — that is the point of weighting owner rows toward the holdout.
- Compose path still untested end-to-end (Docker down here).
- No `tango stop` yet (D6, carried from V2).
