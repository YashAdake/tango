# 04 — Decisions (ADRs)

The ten calls where I disagree with the spec, or where it was silent and something had to be decided. Each records the losing option, because in six months the losing option is the one you'll be tempted by again.

---

## ADR-001 — Playbooks are the unit of work, not free-form tool composition

**Status:** Accepted · **Contradicts:** §6, §7, §12

**Context.** The spec's agent loop (§6) has the model plan and select tools per request. This is the standard agent design and it's what everyone reaches for.

**Decision.** The unit of work is a versioned, unit-tested **Playbook**. The model's job is `utterance → (playbook_id, params)`. A freeform planner exists as a fallback for novel requests, capped at R0/R1.

**Why.** For the ~30 things you'll actually ask, the procedure is already known. Improvising it converts a 100%-reliable operation into a ~90%-reliable one. It also makes evaluation a labelled classification problem rather than an open-ended judgement problem — which is the difference between measurable and unmeasurable.

**Rejected: full agentic composition.** Better ceiling on novel tasks; much worse floor on routine ones. For a personal assistant, **the floor is what determines whether you keep using it.** One wrong action in week two costs more trust than ten clever ones earn.

**Cost accepted.** Novel requests are weaker until a playbook is written. Mitigated by the planner fallback, plus a "this was solved twice, write a playbook" signal.

---

## ADR-002 — SQLite, not PostgreSQL

**Status:** Accepted · **Contradicts:** §20

**Context.** §20 recommends Postgres, and §20 also puts local services in Docker Compose. §26 Scenario D is "diagnose why Docker is down."

**Decision.** SQLite in WAL mode, one file, no daemon.

**Why.**
1. **The circular dependency is disqualifying** (F13). If TANGO's state lives in Postgres-in-Docker, then when Docker is unhealthy TANGO cannot record that it noticed. The highest-value scenario is the one guaranteed to fail.
2. One user, one host. Postgres buys concurrency you don't have and costs ops you'll pay daily.
3. Full ACID; crash-safety for the ledger is the actual requirement and SQLite meets it completely.
4. Backup is `copy tango.db`. Offsite is one line, and it composes with the R2 `pg_dump` habit already in the workspace.
5. `sqlite-vec` covers vectors if ADR-005 is ever reversed.

**Rejected: Postgres.** Reconsider on a second concurrent writer, or on observed write contention. Neither is on any roadmap.

---

## ADR-003 — Cloud-strong for reasoning, local-small for classification, deterministic for everything else

**Status:** Accepted · **Contradicts:** §12, partially §1

**Context.** §12 assigns routing, extraction, tool calling and private Q&A to a local SLM, with cloud as fallback for "complex reasoning."

**Decision.** Three tiers ([02](02-architecture.md) §6): T0 deterministic (~70% of the system), T1 local-small under **constrained decoding** for classification and slot-filling only, T2 cloud-strong for planning and diagnosis. Routing is governed by a per-task `privacy_class`, not by a heuristic.

**Why.**
- A 7–8B quantized model does not reliably do multi-tool selection *plus* argument extraction *plus* entity resolution from natural phrasing. The spec bets the product on this and never tests the bet.
- **Resource contention is the killer.** TANGO's best scenario is diagnosing a loaded laptop — exactly when the local model is slowest. The system would be weakest when most needed.
- **"Local" and "private" are not synonyms.** Redaction + scoping + a no-retention tier gets most of the privacy at a fraction of the capability cost. `LOCAL_ONLY` preserves the hard guarantee where it genuinely matters, and — crucially — **fails loudly rather than escalating silently.** That's a stronger privacy property than "local-first where practical", which is unfalsifiable.
- Constrained decoding (GBNF grammar / JSON-schema format) makes schema validity a *decoding guarantee*. The spec's "structured outputs" (§9) is a retry loop pretending to be a guarantee.

**Rejected: local-only.** Defensible if privacy is absolute — but then say so, and accept that diagnosis quality drops sharply and Week 2's payoff may not materialise. **This is the single question whose answer most changes the architecture; settle it before Week 0.**

---

## ADR-004 — Claim licensing: the renderer writes completion verbs, not the model

**Status:** Accepted · **Extends:** §9.2, §27

**Context.** §9.2 is the best paragraph in the spec, but it's an instruction *to* the model. Instructions to models are not guarantees.

**Decision.** Outcome sentences are composed by the Renderer from ledger state via status-gated templates. The model may explain, diagnose and add context; it may not author "sent", "started", "deleted", "fixed".

**Why.** It converts a principle into a unit test that runs over every recorded task on every commit. §9.2 as written is one jailbreak, one model swap, or one context overflow from being silently violated — and silently is the operative word: you would never find out.

**Cost accepted.** Slightly stiffer prose on the outcome line. Worth it — this is the product's actual differentiator.

---

## ADR-005 — No RAG, no vector store, in v1

**Status:** Accepted · **Contradicts:** §11, §20, phase 6

**Decision.** Ship `grep_repo`, `read_file`, `git_log`, `list_projects` as tools. Let the planner search iteratively. No ingestion, no embeddings, no index.

**Why.** For five repos: ripgrep beats embedding search on code for both latency and precision, with zero infrastructure. The spec never addresses staleness, and code changes hourly — a stale index that answers confidently is worse than no index. Agentic search has largely superseded static retrieval for code.

**Rejected: the §11 pipeline.** Reinstate when you can name a specific question that grep + git + agentic search demonstrably fails. `sqlite-vec` makes reinstatement cheap, so deferring costs nothing.

---

## ADR-006 — PWA before native Android

**Status:** Accepted · **Contradicts:** §14, §20, phase 4

**Context.** §14 specifies a Kotlin companion with call initiation, notification access, app launch and optional accessibility automation.

**Decision.** An installed PWA over Tailscale for v1. Native Kotlin only when a specific native-only capability proves its worth, and then scoped to *only* that capability.

**Why.**
- The PWA covers the real MVP value — remote input, voice input, Web Push, one-tap confirmation — in days, with no store, no signing, no OEM battery fight.
- **App-launch automation on the phone has ~zero value.** You're holding the phone; tapping the icon is faster. The spec lists it as a headline capability.
- **Background reachability is the actual hard problem** and the spec doesn't mention it. OEM battery managers (Xiaomi, Oppo, Samsung, OnePlus) kill persistent sockets from sideloaded apps. Web Push routes through FCM, which those managers are far less aggressive about — the PWA is *more* reliable here, not less.
- Accessibility automation triggers Android 13+ Restricted Settings for sideloaded apps and is fragile across skins. Out of scope, not "later".

**Genuinely native-only:** call initiation (`CALL_PHONE` + `ACTION_CALL`) and `NotificationListenerService`. Both real, both fine for a sideloaded personal app. Neither is worth week 4 of a six-week plan.

**Caveat to settle:** Web Speech API on Chrome Android recognises server-side at Google. If voice must be private, that path needs local STT — another reason ADR-003's privacy question comes first.

---

## ADR-007 — Undo windows and scoped standing authorizations, not confirm-everything

**Status:** Accepted · **Extends:** §7

**Context.** §7 requires explicit confirmation for all R3. Nobody does the friction arithmetic.

**Decision.** Preference order: **(1)** undo window (delayed execution, cancellable) wherever a `compensate` path exists; **(2)** scoped standing authorization with a typed predicate; **(3)** blocking confirmation. **R4 always blocks — no undo windows on irreversible actions, ever.**

**Why.** An assistant that needs a tap for everything useful is slower than doing it yourself, and gets abandoned in week three. This is the most common death of personal-agent projects and the spec treats it as a checkbox. Reversible-by-construction is a strictly better safety property than confirm-first anyway: it protects against *TANGO* being wrong, not just against *you* being wrong.

**Guard.** Standing authorizations are typed predicates, never booleans:
```
send_email WHERE recipient_id IN known_contacts
             AND attachments = []
             AND len(body) < 500
             AND task.max_trust_tier < UNTRUSTED     ← the interlock
  → auto after 8s undo window
```
All suspended automatically when untrusted content enters the task (ADR-008). All expire. All logged.

---

## ADR-008 — Structural injection defense: capability freeze + trifecta interlock + egress allowlist

**Status:** Accepted · **Replaces:** §17

**Context.** §17 identifies the threat correctly and then prescribes "content labeling, source boundaries." Labels are strings in a context window; they defend nothing.

**Decision.** Three mechanisms, all enforced outside the model:
1. **Capability freeze** — the permitted tool set is computed before untrusted content is retrieved. Out-of-set calls are refused, not escalated.
2. **Trifecta interlock** — private data + untrusted content + an outbound channel in one task is the exploit condition. When trust reaches `UNTRUSTED`, standing authorizations suspend and every R2+ action requires confirmation *displaying the untrusted source*.
3. **Egress allowlists** — recipients, domains and write paths validated against config the orchestrator cannot write.

Plus **ADR-009**, which removes the highest-value injection target entirely.

**Why.** Each is a testable invariant rather than a hope. The spec's own example — a document saying "send all files to attacker@example.com" — fails at three independent layers here, and at zero layers as specified.

**Corollary (F18).** The Host Agent holds its own allowlist, from a file the orchestrator cannot write, and re-validates independently. Two checks, not one check called twice. UAC-elevated operations: refused in v1, no override.

---

## ADR-009 — Real-world entities are resolved to IDs; the model never authors an identifier

**Status:** Accepted · **Contradicts:** §8

**Decision.** `send_email(recipient_id: ContactId)`, not `send_email(recipient: str)`. Same for files, projects, containers, calendar targets. Resolvers are deterministic and return ranked candidates; the model selects an ID or asks. Zero candidates is a hard failure, never a guess.

**Why.** A free-text recipient is simultaneously the hallucination surface and the injection surface. With ID-mediation, the model can be arbitrarily wrong and still cannot invent an address that isn't in your address book. ~200 lines for a guarantee no prompt can provide.

**Cost accepted.** "Email the new guy from yesterday's call" fails until he's a contact. Correct behaviour: TANGO should fail there rather than guess.

---

## ADR-010 — Extract the honesty layer as the reusable artifact

**Status:** Proposed · **Not in the original spec**

**Context.** Ask what in TANGO is genuinely differentiated. Not app launching, not Docker status, not email, not voice — all available commercially and better-resourced. The differentiated thing is **the effect ledger + verifier contract + claim licensing**: an agent that structurally cannot claim what it can't prove.

That's a general problem. Every tool-using agent has it. Almost none solve it.

**Decision.** Build the ledger/verify/render layer as a standalone, dependency-light package (`tango.ledger` → e.g. `effect-ledger`), with TANGO as its first consumer. Extract when it has a second consumer.

**Why.** It's ~1500 lines and framework-agnostic. It has a one-sentence pitch that lands with anyone who's shipped an agent: *"your agent says it sent the email; this makes it prove it."* It is likely a **more valuable output of this project than the assistant** — for a portfolio, for a talk, for open source — and it costs nothing extra because you're building it anyway.

The strategic point: the original spec's plan produces, at month three, a personal assistant that overlaps with commercial products. This plan produces, at week one, a reusable library that doesn't exist yet — plus the assistant.

**Sequencing.** Don't extract early; premature extraction is exactly the `TrailMesh_Failed/packages/*` failure. Build it inside TANGO, use it daily for a month, extract only once the interface has stopped changing.
