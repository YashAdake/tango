# 01 — Verdict & Critique

Review of `TANGO_Comprehensive_Project_Specification.md` v1.0.

---

## 1. What the spec gets right

Credit first, because these are not obvious and most specs miss them.

| # | What it got right | Why it matters |
|---|---|---|
| R1 | **Separation of intelligence from execution** (§Exec Summary, §29) | This is *the* correct organising principle for tool-using agents. Everything good in the document descends from it. |
| R2 | **"Never trust model claims"** (§9.2) | The highest-value paragraph in the document. A model saying "done" is not evidence. Almost nobody builds this. |
| R3 | **Distinguishing *submitted* from *confirmed-complete*** (§9.2, §23) | A genuinely sophisticated distinction. Most agents collapse these and lie by default. |
| R4 | **Risk classes R0–R4 with default policies** (§7) | Right abstraction, right granularity. Keep it nearly as-is. |
| R5 | **Naming prompt injection as a first-class threat** (§17) | The "a malicious document must not be able to grant itself permission" line is exactly correct. |
| R6 | **Explicit non-goals** (§4) | Rare and valuable. "Replacing OS security with AI judgment" as an anti-goal is mature. |
| R7 | **Avoiding anthropomorphic completion claims** (§27) | "Avoid 'I fixed it' unless the postcondition is verified" — this is a product principle most teams never articulate. |

These seven survive into the revised plan unchanged. The problem is not the philosophy. **The problem is that the philosophy is asserted rather than mechanised, and the build plan makes it impossible to honour.**

---

## 2. The structural diagnosis

The spec has three layers, and they are of very different quality:

- **Principles layer (§1, §9.2, §17, §29):** excellent. Keep.
- **Architecture layer (§5, §7, §10–20):** plausible-looking, but it is a *catalogue of components* rather than a design. It names 15 components and specifies the internals of none. There is no state machine, no contract, no interface, and no number anywhere in 32 sections.
- **Plan layer (§22, §23, §31):** actively harmful. It will consume months before producing a usable system, and it sequences safety after execution.

The tell is that the document is **uniformly detailed**. Real designs are lumpy — 80% of the thinking goes into the 20% that's hard. Here, the Voice Pipeline (§13, genuinely easy, well-trodden) gets the same weight as Reliability & Accuracy (§9, the hardest thing in the project). That's the signature of a document optimised for completeness rather than for the parts that decide success.

### 2.1 You have already run this experiment

This is not a hypothetical concern.

```
d:\my\TrailMesh_Failed\
  TRAILMESH_ENTERPRISE_ARCHITECTURE_v1.0.md   (2026-08-10, "Implementation Ready")
  packages\crypto
  packages\mesh-core
  packages\protocol
  packages\shared-types
  packages\transport-interface
  apps\web

d:\my\TrailMesh\            (the retry, days later)
  apps\mobile
  apps\web
  packages\design-tokens
```

Eight days ago, a comprehensive architecture document of the same genre produced **five infrastructure packages and no product**, and got renamed `_Failed`. The retry is product-shaped: two apps and a token package.

TANGO's §21 proposes the identical structure — `server/{api,orchestrator,models,tools,policy,memory,rag,verification,connectors}`, plus `desktop-agent`, `android`, `web-ui`, `infra`. Nine server packages before one working request.

The lesson from your own repo is not "specs are bad." It's **the order is wrong**: architecture packages first is a reliable way to spend the motivation budget before reaching the thing that would have told you whether the product was worth building.

---

## 3. Findings, ranked

Severity: **FATAL** = kills the project. **SEVERE** = kills reliability or trust. **SIGNIFICANT** = costly, fixable. **MINOR** = polish.

### FATAL

#### F1 — There is no value model. The spec never establishes that TANGO is worth building.
Thirty-two sections, and not one asks: *which tasks are frequent enough, slow enough today, and reliable enough for an agent to be worth the round trip?*

Look at the flagship scenarios (§26):
- **"Start my development environment"** — a 12-line `.ps1` bound to a hotkey. Zero latency, 100% reliable, no AI.
- **"Email Rahul the project PDF"** — ~25 seconds in Gmail, including finding the file. TANGO's version requires resolving the contact, resolving the file, generating a draft, rendering a preview, and waiting for you to confirm. **It is slower**, and it can be wrong.
- **"Why is my project down?"** — *this one is genuinely good.* Reading Docker status + container logs + recent git history and forming a hypothesis is real work that a model does faster than you do.
- **"Call Mom"** — you are holding the phone.

Three of the four flagship scenarios are net-negative on time. That's a product problem, not an engineering problem, and no amount of architecture fixes it.

**Fix:** before any code, write down twenty things you actually did last week that were tedious, with frequency and duration. Build only for the ones where `frequency × (manual_time − tango_time)` is meaningfully positive *and* the action can be verified. My prediction from your workspace: multi-project status, cross-repo "what changed", environment orchestration, and failure diagnosis will dominate — and none of them are the scenarios in §26. See [03-roadmap.md](03-roadmap.md) §1.

#### F2 — The "narrow MVP" (§23) is roadmap phases 0–5.
§23 claims the MVP is "deliberately narrow", then lists: web UI + local SLM + 5–10 tools + Windows app launch + system status + file read + Docker status + web search + email draft + confirmation workflow + audit history + **an Android client**.

Cross-referencing §22, that is phases 0, 1, 2, 3, 4 *and* part of 5 — six of ten phases. Realistically 8–12 weeks solo before the first day of genuine use. The motivation budget for a personal project is roughly two weeks of no payoff.

**Fix:** a true MVP is one input surface, one playbook, three tools, and the full honesty spine — shipped in two days. [03-roadmap.md](03-roadmap.md) Week 0.

#### F3 — Build order is architecture-first, which is the documented failure mode in this workspace.
§22 phase 0 is "repo, backend, auth, logging, model gateway" — four pieces of infrastructure before a single user-visible behaviour. §21 mandates nine server packages.

**Fix:** vertical slice first. One utterance → one playbook → one verified effect → one honest sentence, through a single file if necessary. Extract packages only when a second implementation exists to justify the seam.

---

### SEVERE

#### F4 — The local-SLM bet is the riskiest decision in the document and is never examined.
§12 assigns to the local SLM: command routing, structured extraction, routine tool calling, and private document Q&A. §20 says "small/medium quantized instruct models... fits consumer GPU/RAM constraints."

What the spec doesn't confront:

- A 7–8B quantized instruct model does **not** reliably select among 10+ tools *and* extract correct arguments *and* resolve entities from naturally-phrased speech. Expect meaningful error rates on argument extraction with realistic phrasing — and the spec's own R2+ policy means those errors surface as wrong confirmation prompts, which trains you to stop reading them.
- **Resource contention is fatal to the use case.** TANGO's best scenario is diagnosing a laptop under load. That is exactly when the GPU/RAM is busy and the local model is slowest. The system is weakest precisely when it's most needed.
- **Cold start.** If Ollama has unloaded the model, first token can be many seconds. §25 asks for "fast for local commands" with no number and no cold-path plan.
- The spec treats "local" and "private" as the same thing. They aren't. **Redaction + scoping + a no-retention API tier gets most of the privacy at a fraction of the capability cost**, and a local-only *fallback* preserves the hard guarantee where it's actually needed.

**Fix:** invert the assignment. Deterministic code does everything it can. The local model does *classification and slot-filling only, under constrained decoding* (llama.cpp GBNF grammars or Ollama JSON-schema format — schema validity should be a decoding guarantee, not a retry loop). A strong cloud model does freeform planning and diagnosis, behind a redaction pass, with an explicit `privacy_class` on every task: `LOCAL_ONLY` tasks **never** egress, and if the local model can't handle one, TANGO says so rather than silently escalating. See [02-architecture.md](02-architecture.md) §6.

#### F5 — Verification is asserted, never designed.
§9 is the most important section and is one paragraph of nouns plus six examples. There is no verifier contract: no return type, no evidence format, no distinction between "checked and true", "checked and false", and "could not check". §18's `Verification` entity has `checks, evidence, status` with none of the three defined.

Without a typed contract, "verification" degrades into an `if` statement someone forgets to write, and §9.2's guarantee quietly becomes false.

**Fix:** a four-valued status — `VERIFIED | REFUTED | UNVERIFIABLE | PENDING` — with typed evidence, declared per tool, and a test that fails CI if any side-effecting tool ships without a verifier. [02-architecture.md](02-architecture.md) §4.3.

#### F6 — The confirmation model will make TANGO slower than doing the task yourself.
§7 requires explicit confirmation for all R3. §26 Scenario B walks through it approvingly. Nobody does the arithmetic: request → resolve → draft → render preview → **wait for human** → send → verify. The human wait dominates, and it costs a context switch, which is the expensive part.

An assistant that requires a confirmation tap for everything useful is an assistant you stop using in week three. This is the most common way personal-agent projects die, and the spec treats it as a checkbox.

**Fix:** three mechanisms, in order of preference.
1. **Undo window instead of confirm gate.** Execute after a 10-second cancellable delay; surface a single "Undo" affordance. Reversible-by-construction beats confirm-first for anything with a real undo path (delayed send, trash-not-delete, stop-after-start).
2. **Scoped standing authorizations.** Not "auto-send email" but `send_email WHERE recipient IN known_contacts AND attachments = [] AND len(body) < 500 → auto after 8s undo`. Narrow, declarative, revocable, logged. §7 gestures at this ("unless the user has explicitly configured a narrow trusted policy") and never designs it.
3. **Confirm on the surface you're already looking at.** A confirmation that requires unlocking your phone costs more than the task.

R4 keeps a hard gate, always. No undo windows on irreversible actions.

#### F7 — Prompt-injection defense is a list of principles, not a mechanism.
§17 correctly identifies the threat, then prescribes "content labeling, source boundaries, tool-level authorization, confirmation policies and output validation." Labels don't defend anything — they're strings in a context window that the model may or may not respect.

The spec never names the actual condition it needs to prevent, which is the conjunction of three things in one task:
1. access to private data, **and**
2. exposure to attacker-controlled content, **and**
3. an outbound channel.

Any two are safe. All three is the exploit.

**Fix:** enforce it structurally, outside the model.
- **Capability freeze:** a task's permitted tool set and scopes are frozen *before* untrusted content enters context. Any call outside the frozen set is refused — not escalated, refused — regardless of risk class.
- **Trifecta interlock:** once `UNTRUSTED` content is in a task's context, every R2+ action requires human confirmation with the untrusted source displayed, and standing authorizations are suspended for that task.
- **Egress allowlists:** recipients, domains and write paths validated against an allowlist the model cannot write to. Even a fully successful injection cannot reach `attacker@example.com`.

[02-architecture.md](02-architecture.md) §3.4.

#### F8 — Tools take free strings where they must take resolved IDs.
§8 specifies `send_email(recipient, subject, body, attachments)` and `make_call(contact)` and `read_file(path)`.

A free-text `recipient` means the model *types an email address*. That is simultaneously the hallucination surface (plausible-but-wrong address) and the injection surface (a malicious document supplies one). Same for `path`.

**Fix:** real-world entities are always resolved deterministically to enumerated candidates; the model may only *choose an ID from a presented list*, never author the value.
```
resolve_contact(query: str) -> [{id, display, channel_hint, confidence}]   # deterministic, ranked
send_email(recipient_id: ContactId, ...)                                    # ID only; unresolvable = hard fail
```
This one change removes an entire class of failure and closes the highest-value injection path. Generalise it to files, projects, containers and calendars.

#### F9 — Idempotency is named but not mechanised, so double-send is guaranteed.
§7 lists "idempotency behavior" as a tool field; §9 says "idempotency keys"; §29 says "design every side-effecting operation for idempotency." No mechanism anywhere. §18's `Action` entity has `arguments_hash` — necessary but nowhere near sufficient.

The bug this produces is the classic one: `send_email` succeeds at the provider, TANGO crashes before recording the response, retry on restart sends it twice. Every agent that skips this ships it.

**Fix:** two-phase commit against a durable ledger. The key is derived and **persisted before the provider call**, and the post-crash path checks the provider for the key rather than re-sending. [02-architecture.md](02-architecture.md) §4.2.

#### F10 — No state machine, despite §30 requiring one.
§30 says "maintain clear state transitions for task and action lifecycle." §18 gives `Task.status` and `Action.status` as untyped fields. The states are never enumerated and the transitions never drawn.

This is the actual core of the system. Unanswered by the spec: What happens to a pending confirmation after 20 minutes? After a reboot? If you confirm an action whose preconditions have since changed? If the same task is confirmed from two devices? If the desktop agent dies mid-playbook?

**Fix:** explicit, durable, testable state machines for `Task`, `Action` and `ConfirmationRequest`, with TTLs and a re-validation step on confirm. [02-architecture.md](02-architecture.md) §5.

---

### SIGNIFICANT

#### F11 — The Android companion is ~5× the work for ~20% of the MVP value.
§14 is directionally honest (it explicitly refuses to be a bypass mechanism, which is good). But it underestimates the hard parts and overestimates the payoff.

What's genuinely fine for a personal sideloaded app: `CALL_PHONE` + `ACTION_CALL`, `ACTION_SENDTO`, `ACTION_VIEW`, and a `NotificationListenerService`. Those work.

What the spec doesn't account for:
- **Background reachability is the real problem.** OEM battery management (Xiaomi, Oppo, Samsung, OnePlus) aggressively kills persistent connections from sideloaded apps. A companion that must hold a socket to your laptop will be silently dead when you need it.
- **Reaching the laptop at all** requires the phone and PC on the same LAN, or a mesh VPN. The spec says "prefer private networking/VPN" (§16) without making it a hard dependency, which it is.
- **Accessibility automation** (listed as optional in §14, and as a "later" scope item in §3) triggers Android 13+ Restricted Settings for sideloaded apps and is fragile across OEM skins. Treat as out of scope, not "later".
- **App-launch automation on the phone has almost no value.** You are holding the phone. Tapping the icon is faster.

**Fix:** an installed **PWA** covers the actual MVP value — remote text input, voice input, push notifications, and one-tap confirmations — in days rather than weeks, over Tailscale, with no store, no signing and no OEM battery fight. Go native **only** when call initiation and notification mirroring prove their worth, and then build a thin native app that does only those two things. [04-decisions.md](04-decisions.md) ADR-006.

*One caveat to flag:* the Web Speech API on Chrome Android performs recognition server-side at Google. If voice must be private, that path needs local STT and the PWA can't do it — which is a good reason to settle F4's privacy question early.

#### F12 — RAG is premature and, for code, probably wrong.
§11 specifies a full pipeline: ingest → parse → clean → chunk → embed → index → retrieve → rerank → cite. §22 makes it phase 6.

For a single user with five repositories:
- `ripgrep` + file-path conventions + `git log` beats embedding search on code for both latency and precision, with zero ingestion infrastructure.
- **Staleness is unaddressed.** Your code changes hourly. The spec never says when re-embedding happens, and a stale index that answers confidently is worse than no index.
- Letting the model *drive search tools iteratively* has largely superseded static retrieval for code. It's also strictly less infrastructure.

**Fix:** delete the RAG service from the first six months. Ship `grep_repo`, `read_file`, `git_log`, `list_projects` as tools and let the planner iterate. Revisit embeddings only for genuinely unstructured personal documents, and only when you can name the query that fails without them.

#### F13 — Circular dependency: the diagnostician depends on the thing it diagnoses.
§20 recommends PostgreSQL; §20 also recommends Docker Compose for local services. §26 Scenario D — a flagship — is "why is my project down?", answered by inspecting Docker.

If TANGO's own state lives in Postgres-in-Docker, then **when Docker is unhealthy TANGO cannot record that it noticed.** The one scenario with real value is the one guaranteed to fail.

**Fix:** SQLite in WAL mode, single file, no daemon, ACID, zero ops, and it survives everything else on the box being down. It also removes Docker from the critical path of a Windows-native tool. [04-decisions.md](04-decisions.md) ADR-002.

#### F14 — No availability model. The laptop sleeps.
The architecture puts the orchestrator on the Windows laptop (§5) and the phone connects to it (§14). Nowhere does the spec say what happens when the lid is closed — which, for a laptop, is most of the day.

An assistant that only works when you're already sitting at the machine it runs on has eliminated its own remote use case.

**Fix:** decide explicitly, and design for the answer. Either (a) Wake-on-LAN over the mesh VPN, or (b) commands queue and execute on next wake, with the UI *honestly showing queued state* rather than pretending. (b) is fine and cheap; silently hanging is not. This belongs in the response vocabulary alongside `VERIFIED`/`UNVERIFIABLE`.

#### F15 — Evaluation is phase 9. It must be phase 0.
§24 lists test types; §25 lists metrics with "target direction" and not a single number. §22 puts hardening last.

For an LLM system, an eval set is not a test — it's the **instrument**. Without it you cannot answer "did swapping the model help?", "did that prompt edit regress routing?", "is 8B enough?" You will answer those questions by feel, be wrong, and not know it.

**Fix:** 60 labelled utterances **before the first model call**. They also double as the router's few-shot/kNN corpus, so the cost is negative. [05-eval-and-safety.md](05-eval-and-safety.md).

#### F16 — Zero numbers in thirty-two sections.
No latency target, no p95, no token budget, no cost ceiling, no accuracy threshold, no failure budget. §25's targets are all directional ("increase continuously", "near zero", "high"). Directional targets are unfalsifiable, which means they can never block a release.

**Fix:** commit to numbers you can fail. Suggested starting set in [05-eval-and-safety.md](05-eval-and-safety.md) §3 — e.g. p95 < 1.2 s for R0/R1 playbooks, intent routing ≥ 95% top-1 on the golden set, **zero** unverified completion claims, zero confirmation bypasses.

#### F17 — Data model gaps that make §29's guarantees unimplementable.
§29 demands confirmation state be "server-side and tamper-resistant." §18 has no confirmation entity at all. Also missing:
- `ConfirmationRequest` — nonce, TTL, bound to the exact action-argument hash, single-use.
- `IdempotencyKey` on `Action`, distinct from `arguments_hash`, issued pre-call.
- `Evidence` as a first-class row, not a blob inside `Verification`.
- `Policy` versioning — you need to know which policy version authorised a past action.
- `Plan` with revision links, so replans are auditable.
- `TrustTier` on every context item, without which F7's interlock cannot be enforced.
- `Consent`/`StandingAuthorization` with scope and expiry — required by F6.

#### F18 — The desktop agent's trust boundary is left open.
§15 has the right instinct: "isolate privileged operations from the AI process." But nothing closes the loop. If the orchestrator is compromised — by prompt injection, a bad dependency, or an exposed port — and the desktop agent trusts requests from the orchestrator, then the agent is remote-code-execution-as-a-service running as you.

**Fix:** the desktop agent enforces **its own** allowlist, from a config file the orchestrator process cannot write, and re-validates every request rather than trusting the caller's claim about risk class. Two independent checks, not one check called twice. Refuse UAC-elevated operations outright in v1.

#### F19 — Roadmap ordering is backwards in two places.
- **Safety at phase 3, after desktop automation at phase 2.** The confirmation and verification contract must exist before the *second* tool, or every tool written before phase 3 gets retrofitted — and the retrofit is where the gaps live.
- **Voice at phase 7.** Voice fundamentally changes the confirmation model: you cannot show a preview in a voice-only flow. That constraint propagates backwards through the whole safety design. Discovering it at phase 7 means redesigning phases 3–6.

**Fix:** safety spine in Week 0 with the *first* tool. Decide the voice confirmation model on paper in Week 0 even though voice ships last, so the design absorbs the constraint early.

---

### MINOR

#### F20 — "TANGO" doesn't expand.
"Personal AI Operating Assistant" has no T, N, or G. If the name is load-bearing, either commit to it as a plain name (fine — Siri, Alexa and Cortana don't expand either) or find an expansion. Not worth more than five minutes.

#### F21 — §11 contains mojibake.
"ingest â parse â clean â chunk" — UTF-8 arrows decoded as Latin-1. Cosmetic, but it's a tell that the document was pipeline-generated rather than written, which is worth knowing when weighing how much of it reflects considered judgement.

---

## 4. Summary table

| ID | Finding | Severity | Where fixed |
|---|---|---|---|
| F1 | No value model | FATAL | [03](03-roadmap.md) §1 |
| F2 | "MVP" is 6 of 10 phases | FATAL | [03](03-roadmap.md) §2 |
| F3 | Architecture-first build order | FATAL | [03](03-roadmap.md) §2 |
| F4 | Local-SLM bet unexamined | SEVERE | [02](02-architecture.md) §6, [04](04-decisions.md) ADR-003 |
| F5 | Verification asserted, not designed | SEVERE | [02](02-architecture.md) §4.3 |
| F6 | Confirmation friction kills adoption | SEVERE | [02](02-architecture.md) §3.3 |
| F7 | Injection defense is principles, not mechanism | SEVERE | [02](02-architecture.md) §3.4 |
| F8 | Free-string tool arguments | SEVERE | [02](02-architecture.md) §3.5 |
| F9 | Idempotency unmechanised | SEVERE | [02](02-architecture.md) §4.2 |
| F10 | No state machine | SEVERE | [02](02-architecture.md) §5 |
| F11 | Android over-scoped | SIGNIFICANT | [04](04-decisions.md) ADR-006 |
| F12 | RAG premature | SIGNIFICANT | [04](04-decisions.md) ADR-005 |
| F13 | Postgres-in-Docker circular dep | SIGNIFICANT | [04](04-decisions.md) ADR-002 |
| F14 | No availability model | SIGNIFICANT | [02](02-architecture.md) §7 |
| F15 | Eval at phase 9 | SIGNIFICANT | [05](05-eval-and-safety.md) |
| F16 | No numbers anywhere | SIGNIFICANT | [05](05-eval-and-safety.md) §3 |
| F17 | Data model gaps | SIGNIFICANT | [02](02-architecture.md) §5.4 |
| F18 | Desktop agent trust boundary open | SIGNIFICANT | [02](02-architecture.md) §3.4 |
| F19 | Roadmap ordering | SIGNIFICANT | [03](03-roadmap.md) §2 |
| F20 | Backronym doesn't parse | MINOR | — |
| F21 | Mojibake in §11 | MINOR | — |
