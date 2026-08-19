# 02 — Revised Architecture

The replacement for §5–§21 of the original spec.

---

## 1. The reframe

The original TANGO is *"an assistant that can do anything, safely."* That's a platform, and platforms need users to justify their generality. You have one user.

The revised TANGO is:

> **A workspace operations copilot with a verifiable honesty guarantee.**
> It knows the live state of your projects, runs your routines deterministically, diagnoses failures from real telemetry, is reachable from your phone — and is structurally incapable of claiming success it cannot prove.

Three things follow from that sentence.

**It is scoped to a domain it can actually be excellent at.** Your workspace has five shipping projects (`optiresume`, `myjson`, `airdraw`, `filesflow`, the portfolio) across Vercel, Render and Neon, plus Docker locally. "What's the state of everything" is a real question you cannot answer in under five minutes today, and TANGO can answer it in four seconds. That's the wedge — not email, not phone calls.

**Its differentiator is honesty, not capability.** Anything TANGO can do, some commercial assistant can do too. What none of them do is refuse to say "sent" until they've seen a message ID. That's the interesting engineering, it's the part worth your time, and it's generalisable well beyond this project (see [04-decisions.md](04-decisions.md) ADR-010).

**Consequential external actions are a later chapter, not the MVP.** Email and calls are where confirmation friction (F6) and injection risk (F7) both peak, and where the time saved is smallest. They arrive after the spine is proven, not before.

---

## 2. Component map

Seven components. The original had fifteen.

```
┌─────────────────────────────────────────────────────────────────┐
│  SURFACES        CLI/hotkey  ·  local web UI  ·  PWA (phone)    │
└───────────────────────────────┬─────────────────────────────────┘
                                │  authenticated, mTLS over Tailscale
┌───────────────────────────────▼─────────────────────────────────┐
│  1. GATEWAY        auth, sessions, WebSocket, rate limits       │
├─────────────────────────────────────────────────────────────────┤
│  2. ROUTER         utterance → (playbook, params) | freeform     │
│                    kNN over golden set + constrained slot-fill   │
├─────────────────────────────────────────────────────────────────┤
│  3. PLAYBOOK ENGINE   deterministic, versioned, tested recipes   │
│     └─ fallback: PLANNER (cloud model) for novel requests        │
├─────────────────────────────────────────────────────────────────┤
│  4. EFFECT LEDGER  propose → policy → commit → verify            │
│     ├─ Policy Gate    (frozen capabilities, trifecta interlock)  │
│     └─ Verifier Bus   (per-tool postconditions → Evidence)       │
├─────────────────────────────────────────────────────────────────┤
│  5. RENDERER       ledger → claim-licensed sentence              │
├─────────────────────────────────────────────────────────────────┤
│  6. STORE          SQLite (WAL) · tasks, actions, evidence,      │
│                    audit, memory, standing authorizations        │
└───────────────────────────────┬─────────────────────────────────┘
                                │  separate process, own allowlist
┌───────────────────────────────▼─────────────────────────────────┐
│  7. HOST AGENT     Windows: processes, files, docker, git        │
│                    re-validates every request independently      │
└─────────────────────────────────────────────────────────────────┘
```

Deliberately absent from v1: Model Gateway as a component (it's a 60-line module), Memory Service (a table), RAG Service (deleted — F12), Connector Layer (arrives with the first connector), Vector Store (deleted), Observability as a component (structured logs + one trace ID).

**Rule:** a component earns its name when it has two implementations or two consumers. Until then it's a module.

---

## 3. The five inversions

These are the substantive changes from the original design. Each one flips a default the spec chose.

### 3.1 Inversion 1 — Playbooks over free-form agency

**Spec's model:** the LLM sees a tool registry and composes calls to achieve a goal.
**Revised model:** the unit of work is a **Playbook** — a declarative, versioned, unit-tested script with typed parameters. The model's job is to pick one and fill its slots.

Why: for the top ~30 things you'll actually ask, the *procedure is already known and fixed*. There is nothing for a model to figure out about starting your dev environment. Letting it improvise converts a 100%-reliable operation into a ~90%-reliable one and calls it intelligence.

```yaml
id: dev_up
version: 3
description: Start the development environment for a project
risk: R1
params:
  project: {type: ProjectId, resolver: resolve_project, required: true}
  services: {type: enum[all, db, api, web], default: all}
steps:
  - id: db
    when: params.services in [all, db]
    tool: docker.compose_up
    args: {project: $project.path, service: db}
    verify: docker.container_healthy(name: "${project.slug}-db", timeout: 45s)
    on_fail: abort            # don't start an API against a dead DB
  - id: api
    when: params.services in [all, api]
    tool: process.start
    args: {cmd: $project.api_cmd, cwd: $project.path}
    verify: http.responds(url: "${project.api_health}", within: 30s)
    on_fail: continue         # report partial, don't hide it
  - id: editor
    tool: app.launch
    args: {app: vscode, path: $project.path}
    verify: process.exists(name: "Code.exe")
compensate:                   # explicit undo path
  - tool: docker.compose_down
    args: {project: $project.path}
```

Properties this buys you:

- **Testable in CI without a model.** Playbooks are code. `pytest` runs them against fakes.
- **Trivially evaluable.** `utterance → (playbook_id, params)` is a labelled classification problem. That *is* the golden set ([05](05-eval-and-safety.md)).
- **Partial failure is first-class.** `on_fail: continue` plus per-step verification produces "DB up, API not responding, editor open" instead of "started your environment" or a stack trace.
- **Growth without regression.** Adding capability #31 cannot break capability #7, because they don't share a decision.
- **Undo is designed, not improvised.** `compensate` is the mechanism F6's undo windows and §29's rollback expectations both need.

The **freeform planner** remains for genuinely novel requests, but it is the *fallback*, it is cloud-model-backed, and its output is capped at R0/R1 tools unless the user promotes it. When the planner solves something twice, that's the signal to write a playbook.

### 3.2 Inversion 2 — The Effect Ledger (two-phase commit)

**Spec's model:** the execution engine "runs tool calls and tracks state."
**Revised model:** every side-effecting call is a durable, two-phase transaction. This is the fix for F9 and half of F5.

```
PROPOSE   write Action{status=PROPOSED, idempotency_key, args_canonical, policy_version}
          ── durable fsync before anything external happens ──
POLICY    evaluate → AUTO | UNDO_WINDOW | CONFIRM | DENY
COMMIT    status=COMMITTING, call the adapter, record raw response
VERIFY    run the verifier → Evidence rows → status=VERIFIED | REFUTED | UNVERIFIABLE
```

The idempotency key is `sha256(task_id | step_id | tool | canonical_args)`, persisted **before** the external call. On restart, any `COMMITTING` row is a *known-unknown*: the recovery path **queries the provider for that key** and never blindly re-sends. If the provider can't be queried, the action terminates as `UNVERIFIABLE` and TANGO says so.

The three-value outcome model matters more than it looks:

| Ledger state | What TANGO is allowed to say |
|---|---|
| `VERIFIED` | "Sent. Message ID `<abc>`." |
| `REFUTED` | "Failed — the API is still returning 502." |
| `UNVERIFIABLE` | "Submitted to Gmail. I couldn't confirm delivery." |
| `PENDING_CONFIRM` | "Ready to send. Confirm?" |
| `EXPIRED` | "That confirmation timed out; nothing was sent." |

Most agents have two states (worked / errored) and lie in the gap. The gap is where trust is won.

### 3.3 Inversion 3 — Claim licensing

**Spec's model:** §9.2 and §27 tell the model not to overclaim.
**Revised model:** the model is not permitted to write completion verbs at all. The **Renderer** composes the outcome sentence from ledger state, using a status-gated template. The model may add context, explanation, and diagnosis — it may not author "sent", "started", "deleted", "fixed".

```python
LICENSED_VERBS = {
    "VERIFIED":     ["sent", "started", "created", "deleted", "stopped"],
    "UNVERIFIABLE": ["submitted", "requested", "attempted"],
    "REFUTED":      ["failed to", "could not"],
    "PENDING":      ["ready to", "waiting to"],
}
```

This is the difference between a principle and a guarantee. §9.2 as written is an instruction in a prompt — one jailbreak, one fine-tune change, one context overflow away from being violated silently. As a renderer constraint it's a unit test:

```python
def test_no_unlicensed_completion_claims():
    for action in replay_all_recorded_tasks():
        text = render(action)
        assert not contains_verb(text, LICENSED_VERBS["VERIFIED"]) \
               or action.status == "VERIFIED"
```

Run it over every recorded task on every commit. That test is the product.

### 3.4 Inversion 4 — Trust tiers and capability freezing

**Spec's model:** "content labeling, source boundaries" (§17).
**Revised model:** trust is a typed property of every context item, and it mechanically constrains what the task can do. Fix for F7 and F18.

```python
class TrustTier(IntEnum):
    TRUSTED   = 0   # user utterance, TANGO config, playbook definitions
    SEMI      = 1   # your own files, your own repos, your own commit messages
    UNTRUSTED = 2   # email bodies, web pages, container logs, notification text,
                    #   dependency READMEs, anything authored elsewhere
```

Three enforced rules, all outside the model:

1. **Capability freeze.** A task's permitted tool set and argument scopes are computed at plan time, *before* any `UNTRUSTED` content is retrieved, and written to the Task row. A call outside the frozen set is **refused**, not escalated. Injected instructions cannot widen the aperture because the aperture was measured before they arrived.

2. **Trifecta interlock.** The moment `max(trust_tier)` in a task's context reaches `UNTRUSTED`, standing authorizations are suspended for that task and every R2+ action requires explicit confirmation **that displays the untrusted source alongside the action**. You see *"this came from an email from unknown@x.com"* next to the send button.

3. **Egress allowlists.** Recipients, domains and write paths are validated against allowlists stored in a config the orchestrator process cannot write. A fully successful injection still cannot reach an address you've never mailed.

**Host Agent independence (F18):** the Host Agent holds its *own* copy of the allowlist and its own risk classifications, loaded from a file the orchestrator has no write permission to, and re-validates every request. Two independent checks. If the orchestrator is compromised, the agent still refuses to delete `C:\Windows`. UAC-elevated operations are refused outright in v1 — no exceptions, no policy override.

### 3.5 Inversion 5 — Resolver-mediated entities

**Spec's model:** `send_email(recipient: str, ...)`, `read_file(path: str)`.
**Revised model:** the model never authors a real-world identifier. Fix for F8.

```python
resolve_contact(query: str) -> list[Candidate]   # deterministic, ranked, from YOUR address book
resolve_project(query: str) -> list[Candidate]   # from the project registry
resolve_file(query: str, scope: ProjectId) -> list[Candidate]

send_email(recipient_id: ContactId, ...)         # ID only
read_file(file_id: FileId)                       # ID only; path never crosses the model boundary
```

Resolution rules:
- **Exactly one high-confidence candidate** → proceed.
- **Multiple candidates** → the model *selects by ID*, or asks you, and the choice is logged.
- **Zero candidates** → hard failure. Never "I'll guess an address."

The model can be as wrong as it likes and still cannot invent a recipient that isn't in your address book. This is a much stronger property than any prompt can give you, and it costs about 200 lines.

---

## 4. Contracts

### 4.1 Tool contract

```python
@tool(
    name="docker.compose_up",
    risk=Risk.R1,
    platforms=["windows"],
    scopes=["docker:write"],
    idempotent=True,                    # safe to retry as-is
    timeout_s=90,
    verifier="docker.container_healthy",
    compensate="docker.compose_down",
)
def compose_up(project: ProjectId, service: str | None = None) -> ToolResult: ...
```

`ToolResult` carries `ok`, `raw` (the untruncated adapter response, stored as evidence), `summary` (for the model), and `provider_ref` (message ID, container ID, PID — whatever the verifier will check).

**CI gate:** any tool with `risk >= R2` and no `verifier` fails the build. This is how F5 stops being a good intention.

### 4.2 Verifier contract

```python
class VerifyResult(NamedTuple):
    status: Literal["VERIFIED", "REFUTED", "UNVERIFIABLE"]
    evidence: list[Evidence]     # typed, storable, replayable
    checked_at: datetime
    detail: str                  # human-readable, shown on REFUTED
```

`UNVERIFIABLE` is a **first-class success-adjacent outcome**, not an error. "I asked Android to place the call; the platform doesn't tell me whether it connected" is the honest answer, and the original spec deserves credit for spotting this in §9.1 — it just never gave it a type.

Verifiers must be **independent of the actor**. Verifying `process.start` by checking the return code of `process.start` is not verification; enumerating running processes is. A test asserts that a verifier never reads the tool's own return value as its sole evidence.

### 4.3 Playbook contract

Every playbook declares: `id`, `version`, `risk` (max over its steps), typed `params` with resolvers, ordered `steps` (each with `tool`, `args`, `verify`, `on_fail ∈ {abort, continue, retry}`), and an optional `compensate` chain. Playbooks are versioned; the ledger records which version ran, so a past task can be replayed and explained even after the playbook changes.

---

## 5. State machines

Fix for F10. These are durable — every transition is a committed row.

### 5.1 Task

```
DRAFT ──► PLANNED ──► RUNNING ──┬──► COMPLETED         (all steps VERIFIED)
                        │       ├──► PARTIAL           (some VERIFIED, some not)
                        │       ├──► FAILED            (abort-on-fail step REFUTED)
                        │       └──► NEEDS_INPUT ──► RUNNING
                        └──► CANCELLED
```

`PARTIAL` is the state most agents don't have, and it's the honest answer surprisingly often. Never collapse it into `COMPLETED`.

### 5.2 Action

```
PROPOSED ──► POLICY_HELD ──► PENDING_CONFIRM ──┬──► CONFIRMED ──► COMMITTING ──┬──► VERIFIED
     │            │                            └──► EXPIRED                    ├──► REFUTED
     │            └──► DENIED                                                  └──► UNVERIFIABLE
     └──► UNDO_WINDOW ──┬──► COMMITTING
                        └──► CANCELLED
```

`COMMITTING` is the crash-critical state. On startup, every `COMMITTING` row is reconciled against the provider using its idempotency key **before** the system accepts new work.

### 5.3 ConfirmationRequest

The entity §29 demands ("server-side and tamper-resistant") and §18 forgot.

```python
class ConfirmationRequest:
    id: UUID
    action_id: UUID
    nonce: str                  # single-use, cryptographically random
    binds_args_hash: str        # the EXACT args being authorised
    expires_at: datetime        # TTL, default 5 min (R3), 90 s (voice)
    consumed_at: datetime|None
    surface: str                # which device/surface confirmed
    untrusted_sources: list[str]  # shown to the user at confirm time (§3.4 rule 2)
```

Three rules that close the loopholes:
- **Confirmation authorises an argument hash, not an action ID.** If anything about the action changed between proposal and confirmation, the nonce no longer matches and it must be re-proposed. This defeats time-of-check/time-of-use.
- **Preconditions are re-validated at confirm time,** not just proposal time. Confirming a 20-minute-old "restart the API" must re-check that the API is still the one you meant.
- **Single-use, and expiry is a terminal state with its own user-visible message.** Silent expiry is how users learn not to trust the system.

### 5.4 Data model deltas

Beyond §18: `ConfirmationRequest` (above), `Evidence` (first-class, typed, with `action_id` and `kind`), `IdempotencyKey` on `Action` (distinct from `arguments_hash`), `Policy.version` (recorded on every action), `Plan.revision` + `supersedes` (auditable replans), `TrustTier` on every `ContextItem`, `StandingAuthorization` (predicate, scope, expiry, revocation), and `Playbook.version` on `Task`.

---

## 6. Model routing

Fix for F4. Three tiers, each with a job it's actually good at.

| Tier | What runs there | What it does | Why |
|---|---|---|---|
| **T0 — Deterministic** | Python | Entity resolution, all playbook execution, all verification, all policy | No model. No variance. ~70% of the system by volume. |
| **T1 — Local small** | Ollama, 4–8B, **constrained decoding** | Intent classification, slot filling, redaction, short summarisation | Private, cheap, warm. Schema validity is a decoding guarantee (GBNF grammar / JSON-schema format), not a retry loop. |
| **T2 — Cloud strong** | Claude Sonnet/Opus | Freeform planning, failure diagnosis, log reasoning, code Q&A | This is where model quality actually changes the answer, and where local models are weakest. |

**Routing is a task property, not a heuristic.** Every task carries a `privacy_class`:

- `LOCAL_ONLY` — never egresses. If T1 can't handle it, TANGO says *"I can't do that locally"* rather than silently escalating. This is a real guarantee, unlike "local-first where practical".
- `REDACTED_OK` — T2 permitted after a redaction pass (secrets, tokens, absolute paths, contact PII) with a preview available on demand.
- `OPEN` — T2 freely.

Default is `REDACTED_OK`; anything touching credentials, email bodies or contacts is `LOCAL_ONLY` by classification rule, not by the model's judgement.

**Intent routing does not have to start as an LLM problem.** With 30 intents and a golden set, embedding + kNN gets you a strong baseline with near-zero latency and perfect reproducibility. Use T1 for slot extraction, where it's genuinely needed. Measure both ([05](05-eval-and-safety.md)) before assuming you need more.

---

## 7. Availability & failure model

Fix for F14 — the question the original never asks.

| Condition | Behaviour | User sees |
|---|---|---|
| Laptop asleep, request from phone | Queue durably; WoL over Tailscale if enabled | "Queued — your laptop is asleep. Waking it." / "Will run when you're back." |
| Local model cold | Execute deterministic path immediately; T1 warms in background | Answer arrives; no stall |
| Cloud unreachable | T0/T1 continue; T2 features degrade explicitly | "I can start it, but I can't diagnose without the cloud model." |
| Host Agent down | R0 queries from cache with staleness marked | "Docker status as of 11 minutes ago (agent offline)." |
| Store corrupt | Refuse all R2+; read-only mode | Explicit, loud |

**Panic controls** (absent from the original entirely): a global pause that halts all pending and queued actions, and `undo last` that runs the `compensate` chain of the most recent compensable task. Both reachable from every surface in one action.

---

## 8. Response rules

Replaces §27, made enforceable:

1. **Lead with outcome, in licensed language.** "DB up. API not responding. Editor open." — not a narration of steps.
2. **Evidence on request, never by default.** One line, `why?` expands it.
3. **Detail scales with failure.** Routine success is one line; a `REFUTED` action gets the check that failed, the evidence, and a proposed next action.
4. **Never narrate reasoning.** The original gets this right (§27) — keep it.
5. **Uncertainty is stated, never smoothed.** "I couldn't confirm" is a complete, acceptable answer.
6. **Every response carries a `trace_id`** so any sentence can be reconstructed from the ledger.

---

## 9. Repository layout

Replaces §21. Nine server packages become one package with modules; extract only on the second consumer.

```
tango/
  tango/
    gateway.py          # FastAPI app, auth, WS
    router.py           # utterance → (playbook, params)
    playbooks/          # *.yaml + loader + engine
    tools/              # adapters, one module per domain
    verify/             # verifiers, one per side-effecting tool
    policy.py           # risk, trust tiers, standing auths, freeze
    ledger.py           # propose/commit/verify, idempotency, recovery
    render.py           # claim-licensed output
    store.py            # SQLite, migrations
    models.py           # T0/T1/T2 routing, redaction
  agent/                # Host Agent — SEPARATE PROCESS, own allowlist
  surfaces/
    cli/
    web/                # local UI; the PWA is the same build
  evals/                # golden set + harness — exists before the agent
  tests/
  docs/
```

`agent/` is a separate process with a separate config owner, per §3.4. Everything else is one importable package until it demonstrably isn't.
