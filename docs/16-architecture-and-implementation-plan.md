# 16 — TANGO: System Architecture & Implementation Plan

**The authoritative specification.** Docs [00](00-SUMMARY.md)–[15](15-coexistence-and-performance.md) are the research and decision trail; this document supersedes them where they conflict and is the single source an implementation session should start from.

---

## 1. Document Control

| Field | Value |
|---|---|
| Project | TANGO — Personal AI Operating Assistant |
| Version | **1.1** — red-teamed and amended per [17-plan-review](17-plan-review-v1.1.md); where they differ, 17 governs |
| Date | 2026-08-19 |
| Product Owner | Yash Adake |
| Author / Architect | Claude (Fable 5) |
| Implementation executor | Claude Code sessions, per phase |
| Status | **Ready for PO approval** — one open decision (§19) |
| Target platform | Dedicated laptop: Intel Core Ultra 7 · RTX 5060 8 GB · 24 GB RAM · Windows 11 · Docker Desktop (WSL2) |
| Companion device | Android phone (sideloaded native app + Telegram) |
| Supporting docs | [tango/](.) 00–15, indexed in [README.md](README.md) |

**Reading map:** rationale for any decision here lives in the numbered docs; this document states *what is being built*, not *why the alternatives lost*. For "why", follow the links.

---

## 2. Executive Summary

Tango is a single-user, voice-first personal AI assistant running on the owner's own hardware. It executes real work across the owner's laptop, projects, phone and online services through **deterministic, versioned playbooks** selected by a model but never improvised by one, and it carries a property no commercial assistant has: **it is structurally incapable of claiming an outcome it has not verified.**

The system is two-plane: a small **native Windows host plane** (orchestrator core, desktop agent, voice I/O) and a **containerised service plane** (models, STT/TTS, connectors, search, tracing) under Docker Desktop. Intelligence is tiered — deterministic code first, a small local model for classification under constrained decoding, Claude Opus 5 for reasoning — governed by per-task privacy classes.

**Why this shape:** 2026 measurement shows 45–76% of agent failures are *false success claims*, dropping to ~3% where independent state verification exists; agents score 80–90% on single-turn tasks but 18–24% on sustained multi-step workflows ([11](11-research-findings.md)). Tango's architecture is built directly on those two numbers: verification as a first-class subsystem, and determinism wherever a procedure is known.

**Delivery:** 8 phases over ~7 focus-weeks (**P90 ≈ 10 calendar weeks** with per-phase buffers), a usable system at the end of every phase, hard exit gates, kill criteria stated up front.

---

## 3. Goals, Scope & Requirements

### 3.1 Goals

| ID | Goal | Measure |
|---|---|---|
| G1 | A trustworthy operator for the owner's workspace (5+ projects, local + prod) | Daily use ≥ 5 invocations/day sustained at week 8 |
| G2 | Conversational voice presence — "call and talk to it" | Turn gap ≤ 500 ms; owner prefers voice over typing for status/diagnosis |
| G3 | Zero false success claims | 0 unlicensed completion claims across all recorded tasks, CI-enforced |
| G4 | The laptop remains fully usable while Tango runs | Resource gates (§14.4) green during normal dev work |

### 3.2 Explicitly out of scope (v1)

Multi-user/multi-tenant anything · banking or payment actions · autonomous WhatsApp send ([ADR-011](10-voice-and-consumer-commands.md)) · Android accessibility automation · physical-world sensing · unsupervised multi-hour agent runs · training or fine-tuning models · RAG/vector infrastructure ([ADR-005](04-decisions.md)).

### 3.3 Functional requirements

**Core orchestration**
- FR-C1 Accept requests via voice, CLI, local web, Telegram, and Android assist gesture; single conversation state across all surfaces.
- FR-C2 Route each utterance to exactly one of: playbook (with typed params), freeform plan (capped R0/R1), clarification, or refusal.
- FR-C3 Execute playbooks step-wise with per-step verification, `on_fail` policy, and `compensate` chains.
- FR-C4 Every side-effecting action passes the effect ledger lifecycle (§8.2); no tool call bypasses it.
- FR-C5 Report outcomes in claim-licensed language rendered from ledger state; `PARTIAL` is a first-class outcome.
- FR-C6 Queue requests durably when the host is asleep/offline and state so honestly.

**Voice**
- FR-V1 Wake word ("Tango") on the laptop; one-shot bypass when speech continues within 400 ms; pre-rendered acknowledgement < 50 ms.
- FR-V2 Conversation mode: streaming ASR + LLM + TTS, semantic turn detection, barge-in, AEC.
- FR-V3 Spoken-form rendering for all audio output (§11.4); readbacks capped ~40 s with continuation offer.
- FR-V4 Factual answers carry a grounding state — `GROUNDED` / `RECALLED` (audibly marked) / `CONFLICTED`; factual questions default to search-first.
- FR-V5 Voice R3 requires entity readback + verbal confirm (90 s TTL); voice never executes R4.

**Capabilities (initial catalogue — [09](09-capability-catalogue.md))**
- FR-P1 Workspace status: projects, dev servers, containers, git state, ports, prod probes — one playbook, ≤ 5 s.
- FR-P2 Environment lifecycle: start/stop/switch per project; port-conflict resolution.
- FR-P3 Diagnosis: gather telemetry deterministically → T2 reasoning → evidence-cited hypothesis → remediation *proposed, never auto*.
- FR-P4 Cross-repo digests; uncommitted/unpushed sweeps; deploy and prod checks.
- FR-P5 Consumer commands: alarms/timers/reminders — **time-critical events are phone-native by default** (AlarmClock intents), so laptop sleep cannot silence them, and every confirmation states *where* the event will fire; app launch (laptop + phone); calls (dial-verified only); SMS/Telegram send; WhatsApp deep-link compose.
- FR-P6 Web search (SearXNG) → summarise → optional save-to-docs with sources; spoken readback.
- FR-P7 Email read/summarise/draft (Week 7+): send gated per §12; recipient IDs only.
- FR-P8 Scheduled routines (morning brief, EOD baton draft, prod monitor) via n8n cron → playbooks.

**Safety**
- FR-S1 Dual trust labels (integrity, confidentiality) on every context item, propagating through derivations.
- FR-S2 Capability freeze: task tool-set and scopes computed and persisted *before* any untrusted content is retrieved; out-of-set calls refused.
- FR-S3 Rule-of-Two interlock: untrusted input + sensitive access + external state change never co-occur without human confirmation showing the untrusted source.
- FR-S4 Egress allowlists (recipients, domains, write-paths) in config the orchestrator cannot write.
- FR-S5 Real-world identifiers only via deterministic resolvers returning ranked candidates; model selects IDs, never authors values.
- FR-S6 Confirmations: server-side, single-use nonce bound to the exact argument hash, TTL, re-validation of preconditions at confirm time.
- FR-S7 Standing authorizations: typed predicates with scope + expiry, auto-suspended in untrusted-context tasks, fully audited.
- FR-S8 Panic controls on every surface: global pause; `undo last`.
- FR-S9 Idle re-auth: R3+ requires Windows Hello / phone biometric after 8 h without interaction.
- FR-S10 Host Agent enforces its own allowlist from a file the core cannot write; refuses UAC elevation outright.

**Operations**
- FR-O1 Every response carries a `trace_id` reconstructable from the ledger.
- FR-O2 Nightly SQLite backup + weekly offsite copy; restore drill in Phase 7.
- FR-O3 Automatic degradation ladder (§13.3) driven by measured resource pressure.

### 3.4 Non-functional requirements

| ID | Requirement | Target | Gate |
|---|---|---|---|
| NFR-1 | Playbook path latency (no LLM), p95 | **< 1.2 s** | CI |
| NFR-2 | Time-to-first-audio after end-of-turn (designed backchannels count), p90 | **≤ 500 ms**; substantive content ≤ 1.5 s local / ≤ 2.5 s cloud p90 | Phase 3 exit, latency rig |
| NFR-3 | Agent-half first audio (incl. filler ack) | **< 1 s ack / < 8 s answer p95** | CI |
| NFR-4 | Wake→listening (laptop) / assist-gesture (phone) | < 300 ms | Phase 2/5 exit |
| NFR-5 | Intent routing top-1 (golden set) | **≥ 95%** | CI, blocking |
| NFR-6 | Parameter exactness | ≥ 92% | CI, blocking |
| NFR-7 | Unlicensed completion claims | **0** | CI, blocking |
| NFR-8 | Confirmation bypasses / refusal misses | **0** | CI, blocking |
| NFR-9 | Injection suite ([05](05-eval-and-safety.md) §4 + AgentDojo subset) | **100% pass** | CI, blocking |
| NFR-10 | Verifier coverage on R2+ tools | 100% (import-time check) | CI, blocking |
| NFR-11 | Double execution under crash injection | 0 | Phase 7 exit |
| NFR-12 | Idle footprint: VRAM 0 GB (model unloaded), CPU < 3% | continuous | resource monitor |
| NFR-13 | Active total VRAM (Tango + desktop) | ≤ 6.8 GB of 8 | resource monitor |
| NFR-14 | Free RAM during normal dev session | ≥ 3 GB | resource monitor |
| NFR-15 | Cloud spend tripwire | < $0.50/day alerts | runtime |
| NFR-16 | `LOCAL_ONLY` egress events | **0**, fail-loud | CI + audit |
| NFR-17 | Network exposure | loopback + Tailscale interfaces only; anything else is a structural refusal | code + test |

---

## 4. System Context

```
                                ┌──────────────────────────┐
   You ── voice / hotkey ─────► │                          │ ──► Windows apps, processes,
   You ── Telegram (any device)►│         TANGO            │     files, git, Docker
   You ── assist gesture ──────►│   (laptop = brain)       │ ──► Prod endpoints (probe)
   You ── local web / CLI ─────►│                          │ ──► Claude API (T2, redacted)
                                └───────────┬──────────────┘ ──► SearXNG → web
                                            │ Tailscale only   ──► Gmail/Calendar/GitHub
                                   Android phone (surface)          (via n8n / MCP, Phase 7)
```

Trust boundaries: (1) every external content source is untrusted-integrity by default; (2) the phone is an authenticated *surface*, never a second brain; (3) the Claude API receives redacted context only, never `LOCAL_ONLY` material; (4) nothing listens on a public interface, ever.

---

## 5. Architecture Overview

### 5.1 Principles (binding)

1. **Deterministic first.** Code > small model > large model, in that order of preference for every job.
2. **Playbooks are the unit of work.** Models select and fill; they do not improvise procedures.
3. **No claim without evidence.** Completion verbs are rendered from ledger state, never authored by a model. Facts carry grounding states.
4. **Policy outside the model.** Freezing, labels, allowlists, nonces — all enforced in code the model cannot influence.
5. **The laptop is the owner's first.** Tango yields; the degradation ladder is automatic.
6. **Usable every phase.** No phase delivers only infrastructure.
7. **Measured, not vibed.** The golden set precedes the model; every gate is a number.

### 5.2 Two-plane deployment

```
HOST PLANE — native Windows                      SERVICE PLANE — Docker Desktop (WSL2, 6 GB cap)
┌──────────────────────────────────┐             ┌─────────────────────────────────────────┐
│ tango-core   (Windows Service)   │  HTTP/WS    │ ollama (Qwen3-4B Q4, idle-unload)        │
│  gateway · router · playbooks    │◄───────────►│ litellm (T1/T2 gateway)                  │
│  ledger · policy · verify        │ localhost / │ whisper-STT (NPU via OpenVINO)           │
│  render · store (SQLite WAL)     │ host.docker │ speech-to-phrase                         │
│                                  │  .internal  │ kokoro-TTS (CPU)                         │
│ tango-agent  (logon task)        │             │ n8n (no host port) · searxng             │
│  app launch · windows · clipboard│             │ mcp-gateway → [vetted MCP servers]       │
│                                  │             │ docling · playwright (on demand)         │
│ tango-voice  (logon task)        │             │ phoenix (tracing)                        │
│  mic · wake word · AEC · playback│             └─────────────────────────────────────────┘
│  Pipecat pipeline · Smart Turn   │
└──────────────────────────────────┘
Surfaces: CLI · local web · Telegram bot · Android assist-gesture app (native, thin)
```

Placement rules are constraints, not choices: `tango-core` must be native (it diagnoses Docker); `tango-agent` must be native (session 0 isolation); `tango-voice` must be native (WSL2 has no audio devices). Everything else defaults to the service plane. ([12](12-docker-stack-and-tooling.md), [15](15-coexistence-and-performance.md))

### 5.3 Component inventory

| # | Component | Plane | Tech | Phase |
|---|---|---|---|---|
| 1 | Gateway | host | FastAPI + WS, device-token auth | 0 |
| 2 | Router | host | regex/kNN → Qwen3-4B slot-fill (XGrammar) | 0 |
| 3 | Playbook Engine | host | YAML playbooks, typed params, resolvers | 0 |
| 4 | Effect Ledger + Policy Gate + Verifier Bus | host | SQLite WAL, 2-phase commit | 0 |
| 5 | Renderer (text + spoken-form) | host | claim licensing, grounding states | 0 / 3 |
| 6 | Model Gateway | service | LiteLLM → Ollama + Claude Opus 5 | 0 |
| 7 | Host Agent | host | pywinauto (UIA), own allowlist | 1 |
| 8 | Voice pipeline | host+service | Pipecat, livekit-wakeword, Smart Turn v3, Whisper-on-NPU, Kokoro | 2–3 |
| 8a | Contextualizer — per-turn reference resolution, sticky session labels | host | T0 first, T1 fallback | 3 |
| 8b | Notification Manager — severity, quiet hours, dedup, daily budget | host | T0 | 5 |
| 9 | Freeform Planner | host | Claude Opus 5, capped R0/R1 | 4 |
| 10 | Telegram surface | service→host | Bot API, inline-keyboard confirms | 5 |
| 11 | Android app | phone | Kotlin, `VoiceInteractionService` only | 5 |
| 12 | n8n + MCP gateway + connectors | service | per [ADR-012](12-docker-stack-and-tooling.md), §12 hardening | 7 |
| 13 | Observability | service | Phoenix (OTel), structured logs, trace_id | 4 |

---

## 6. Component Specifications

Contracts only; internals are implementation freedom.

**Gateway.** Terminates all surfaces. Auth: per-device tokens (revocable), issued via pairing flow; binds only loopback + Tailscale interface (NFR-17 enforced at socket-bind level with an allowlist check that refuses anything else). WS event stream: task lifecycle, confirmation requests, degradation-level changes.

**Router.** Input: transcript/text + conversation context. Output (typed, XGrammar-constrained): `{route: playbook|plan|clarify|refuse, playbook_id?, params?, confidence}`. Escalation ladder: exact/regex match → embedding kNN over golden set → T1 slot-fill → `clarify`. The router never sees untrusted document content — only the user's utterance and Tango's own state.

**Playbook Engine.** Loads versioned YAML ([02](02-architecture.md) §3.1 schema: `id, version, risk, params{type, resolver}, steps[{tool, args, verify, on_fail}], compensate[]`). Params resolve through deterministic resolvers (`resolve_project`, `resolve_contact`, `resolve_file`, `resolve_container`); zero-candidate = hard fail, multi-candidate = model selects ID or asks. Engine records `playbook_version` on the task; old versions replayable.

**Effect Ledger.** The heart. Lifecycle per action: `PROPOSED` (durable, idempotency key = `sha256(task|step|tool|canonical_args)`, fsync **before** any external call) → policy verdict (`AUTO | UNDO_WINDOW | CONFIRM | DENY`) → `COMMITTING` (raw provider response stored) → verifier → `VERIFIED | REFUTED | UNVERIFIABLE`. Startup recovery: every `COMMITTING` row reconciles against the provider by key before new work is accepted; **verify-before-retry** — a retry is issued only when the intended state change is confirmed absent. Tool contract carries `sink_idempotency: bool` — whether the provider dedups (Gmail: yes; Docker: no) — and recovery behaviour branches on it. Concurrency: `tango-core` is the **single DB writer**; side-effecting tasks acquire per-resource advisory locks (project, device, connector-account) before their first R1+ action — conflicting tasks queue with a user-visible "waiting for <task>" state; R0 reads run parallel and lock-free.

**Verifier Bus.** One verifier per side-effecting tool, declared in the tool decorator; CI fails any R2+ tool without one, and any verifier whose sole evidence is the tool's own return value. Output: `{status, evidence[], checked_at, detail}`. `UNVERIFIABLE` is a success-adjacent state with its own licensed vocabulary.

**Renderer.** Composes all user-facing outcome sentences from ledger state using status-gated verb sets (`VERIFIED`→"sent/started/…", `UNVERIFIABLE`→"submitted/attempted", `REFUTED`→"failed to", `PENDING`→"ready to"). Model output may fill explanation slots only. Spoken-form pass (§11.4) for audio. Fact answers stamped `GROUNDED | RECALLED | CONFLICTED`; `RECALLED` carries a mandatory audible prefix. A TF-IDF false-success detector runs over every rendered response as a telemetry tripwire (never as a judge).

**Policy Gate.** Evaluates: risk class × trust labels × standing authorizations × frozen capability set × egress allowlists. Freeze semantics: at plan time, before any tool that can ingest external content runs, the task row gets `frozen_tools[]` + `frozen_scopes{}`; the gate refuses anything outside it for the task's lifetime, including replans. Rule-of-Two: when task `max_integrity == UNTRUSTED`, standing auths suspend and every R2+ action requires confirmation displaying the untrusted source string.

**Host Agent.** Separate process, separate config file (ACL: core cannot write). Re-validates every request against its own allowlist and risk table. Capabilities: app launch/close, process/window enumeration, clipboard (flag-gated), notifications, screen capture (per-session grant). Refuses UAC elevation unconditionally.

**Model Gateway.** LiteLLM config defines: `t1` = local Qwen3-4B (temperature 0, XGrammar schema per call), `t2` = `claude-opus-5` (adaptive thinking; effort by task class: `low` triage / `high` default / `xhigh` diagnosis), `t2-fast` = Opus 5 fast mode for voice agent-half, `t2-cheap` = Haiku 4.5. Claude calls: `strict: true` tools; stable prefix (system + tools + SOUL.md) under prompt caching; operator-channel system messages appended inside `messages[]` for policy reminders and untrusted-content markers; redaction pass (secrets, tokens, paths, PII) before any T2 call; `LOCAL_ONLY` tasks hard-refuse T2 with a user-visible message. Batch API for eval runs and pre-render.

**Voice pipeline.** See §11.

---

## 7. Data Architecture

### 7.1 Entities (SQLite, WAL)

| Entity | Key fields |
|---|---|
| `task` | id, goal, route, playbook_id+version, status, privacy_class, frozen_tools[], frozen_scopes, max_integrity_label, trace_id, surface, created/updated |
| `action` | id, task_id, step_id, tool, args_canonical, args_hash, **idempotency_key**, risk, status, provider_ref, raw_response, policy_version, timestamps |
| `evidence` | id, action_id, kind, payload, collected_at |
| `confirmation_request` | id, action_id, **nonce (single-use)**, binds_args_hash, expires_at, consumed_at, surface, untrusted_sources[] |
| `standing_authorization` | id, predicate (typed AST), scope, expires_at, revoked_at, created_via |
| `context_item` | id, task_id, source, **integrity_label**, **confidentiality_label**, content_ref |
| `device` | id, type, pubkey/token_hash, paired_at, revoked_at, last_seen, push_ref, is_active_surface |
| `conversation` / `message` | threaded across surfaces; retention configurable |
| `memory` | id, type (preference/profile/workflow/project), content, source, created_at |
| `audit_event` | append-only: actor, action, resource, verdict, trace_id, ts |
| `playbook_registry` | id, version, yaml_hash, enabled |
| `resource_sample` | ts, vram_free, ram_free, cpu, degradation_level |

### 7.2 State machines

`Task`: `DRAFT → PLANNED → RUNNING → {COMPLETED | PARTIAL | FAILED | NEEDS_INPUT→RUNNING | CANCELLED}` — `PARTIAL` never collapses into `COMPLETED`.
`Action`: `PROPOSED → {POLICY_HELD → PENDING_CONFIRM → {CONFIRMED→COMMITTING | EXPIRED} | UNDO_WINDOW → {COMMITTING | CANCELLED} | DENIED} ; COMMITTING → {VERIFIED | REFUTED | UNVERIFIABLE}`.
All transitions are committed rows; `COMMITTING` is the crash-critical state (§6 Ledger).

### 7.3 Retention & backup

Audit: append-only, never pruned in v1. Conversations: 90-day default, owner-configurable, `DELETE /v1/memory/{id}` honoured everywhere. Backups: nightly `sqlite3 .backup` to a second disk path; weekly encrypted copy offsite (R2, matching existing practice); restore drill is a Phase 7 story. Config (allowlists, SOUL.md, playbooks, egress lists) lives in git.

---

## 8. API Surface

| Endpoint | Purpose |
|---|---|
| `POST /v1/requests` | Submit text/transcript → task (or immediate answer) |
| `WS /v1/stream` | Task events, confirmation prompts, degradation notices; voice session control |
| `POST /v1/tasks/{id}/confirm` | Body: `{nonce}`; single-use, args-hash-bound, TTL-checked, preconditions re-validated |
| `POST /v1/tasks/{id}/cancel` · `POST /v1/panic` · `POST /v1/undo-last` | Cancellation and panic controls (FR-S8) |
| `GET /v1/state` | The workspace-status snapshot (FR-P1), also cached for offline surfaces |
| `GET /v1/tasks/{id}` · `GET /v1/audit?trace_id=` | Introspection |
| `POST /v1/devices/pair` · `DELETE /v1/devices/{id}` | Pairing, revocation |
| `GET /v1/memory` · `DELETE /v1/memory/{id}` | Memory review/erasure |

Voice-plane transport (phone↔laptop audio) is WebRTC via Pipecat, negotiated over the WS channel; Telegram interacts through the same `/v1/requests` + confirm endpoints via the bot service.

---

## 9. Model & Inference Architecture

| Tier | Engine | Job | Config |
|---|---|---|---|
| T0 | Python | resolvers, playbooks, verification, policy, rendering — ~70% of system volume | — |
| T1 | Ollama · **Qwen3-4B Q4_K_M** | single-turn intent + slot-fill + redaction assist, **never multi-step** | 8K ctx · XGrammar per-call schema · temp 0 · `FLASH_ATTENTION=1`, `KV_CACHE_TYPE=q8_0`, `MAX_LOADED_MODELS=1`, `NUM_PARALLEL=1`, `KEEP_ALIVE=5m` |
| T2 | **Claude Opus 5** | planning, diagnosis, summarisation, conversation | adaptive thinking · effort `low/high/xhigh` by task class · `strict` tools · prompt caching · fast mode for voice · Batch for offline |
| T2-cheap | Haiku 4.5 | triage/classification when T1 cold and task is `REDACTED_OK` | — |

Privacy classes on every task: `LOCAL_ONLY` (never egresses; T2 refusal is user-visible), `REDACTED_OK` (default; redaction pass before T2), `OPEN`. Classification is rule-based (credentials, email bodies, contacts ⇒ `LOCAL_ONLY`), not model-judged.

**Model upgrade policy:** T1 upgrades (e.g. 4B→9B) only on golden-set evidence (routing < 95% gate), never speculatively; any model/prompt change must pass the full CI eval diff with zero new regressions ([15](15-coexistence-and-performance.md) §2.1).

---

## 10. Security Architecture

Threat-to-control map (details: [02](02-architecture.md) §3.4, [08](08-openclaw-and-tango.md), [12](12-docker-stack-and-tooling.md) §3):

| Threat | Controls |
|---|---|
| Indirect prompt injection (web/email/logs/MCP descriptions) | dual integrity labels (FIDES-style) · capability freeze pre-retrieval · Rule-of-Two interlock · egress allowlists · injected-content display at confirm |
| Hallucinated/spoofed recipients & paths | resolver-mediated IDs only (FR-S5) · allowlists |
| Double execution / replay | idempotency keys pre-persisted · verify-before-retry · sink-idempotency-aware recovery · single-use nonces · TOCTOU defence via args-hash binding |
| False success claims | claim licensing (renderer) · independent verifiers · TF-IDF tripwire · **no LLM ever judges task success** |
| Compromised orchestrator | Host Agent independent allowlist (FR-S10) · no UAC · per-container caps · egress config read-only to core |
| MCP supply chain (tool poisoning, rug-pulls) | no auto-discovery · descriptions labelled UNTRUSTED · pinned digests · per-server containers · single mcp-gateway · vendor/reference servers preferred |
| n8n credential concentration | tool-provider-only contract ([ADR-012](12-docker-stack-and-tooling.md)) · fixed webhook IDs · no AI-agent nodes · no host port |
| Network exposure (the OpenClaw failure: 135k exposed instances) | structural bind refusal (NFR-17) · Tailscale-only remote · mTLS + device tokens · pairing approval |
| Wake-word misfire with tool authority | livekit-wakeword (~100× fewer FPs) · voice-initiated tasks never carry standing auths · voice R4 refused · R3 readback |
| Stolen/idle session | idle re-auth (FR-S9) · per-device revocation · panic pause |
| Secrets | Windows DPAPI / credential store · references not values in model context · never in SQLite plaintext |

Voice notes deserve one explicit line: **a voice-initiated task is treated as slightly-less-trusted input** — no standing authorizations apply, and consequential actions get readback of the *resolved entity* ("Calling Mom — ending 4821"), because misheard entities are the dominant voice failure mode.

---

## 11. Voice Architecture

### 11.1 Pipeline and budget

```
mic (native) → AEC → livekit-wakeword (~150ms) → [one-shot? skip ack : pre-rendered "Yes sir" <50ms]
  → streaming ASR: Speech-to-Phrase (fixed grammar) ∥ Whisper small/turbo on NPU (open speech, multilingual/Hinglish; Parakeet = GPU/CPU experiment only — STT never contends with the LLM for GPU)
  → Smart Turn v3 (audio-native end-of-turn, ~300ms)
  → Router → { playbook: T0, ≤1.2s total │ agent: filler ack + T2-fast, stream }
  → Renderer (claim-licensed → spoken-form) → Kokoro streaming TTS (CPU, first audio ~200ms)
  → playback (native) with barge-in: VAD live during TTS; user speech halts playback ≤150ms
```

Targets: command confirmation ≤ 1.2 s; conversational turn gap ≤ 500 ms; barge-in halt ≤ 150 ms.

### 11.2 The three-layer voice ([13](13-conversational-voice.md) §3)

L1 pre-rendered premium clips (~200 utterances, Chatterbox offline, one voice identity) covering ~80% of emissions at ~50 ms · L2 Kokoro live synthesis for novel prose · L3 opt-in cloud TTS for long-form, never for `LOCAL_ONLY`. Personality lives in `SOUL.md` — versioned, shapes tone only, **zero policy authority**.

### 11.3 Modes

Command mode (Phase 2): wake → utterance → act → confirm. Conversation mode (Phase 3): continuous session, context carry-over, anaphora ("kill it", "why did that fail"), explicit session end ("thanks Tango" / timeout).

### 11.4 Spoken-form renderer

Deterministic transform before TTS: strip URLs/markdown/citations · ≤ 20-word sentences · numbers/dates/units naturalised · answer-first ordering · ~40 s cap with "want the details?" · uncertainty and `RECALLED` marked in-sentence. Claim licensing applies with **more** force in audio (no re-reading).

---

## 12. Consequential-Action Policy (consolidated)

| Risk | Examples | Policy |
|---|---|---|
| R0 read | status, logs, search | auto |
| R1 reversible | start/stop dev, open app, alarms | auto; compensate chain recorded |
| R2 external, low blast | draft, calendar event, file move | undo-window (8–10 s) if compensable, else preview |
| R3 consequential | send email/SMS, delete, restart prod-adjacent | confirm (one-tap on active surface) + undo window where possible; voice = readback + verbal confirm; standing auths may narrow to undo-window only when predicate matches and task is fully trusted |
| R4 destructive/irreversible | bulk delete, credential ops, anything financial | hard confirm on a **visual** surface, no voice, no standing auth, no undo-window substitute |

WhatsApp: compose-and-hand-off via deep link only ([ADR-011](10-voice-and-consumer-commands.md)). Calls: `VERIFIED` = dial initiated; connection state is never claimed.

---

## 13. Deployment & Resource Governance

### 13.1 Host plane

`tango-core`: Windows Service, auto-start, recovery=restart×3, Below-Normal priority. `tango-agent`, `tango-voice`: Task Scheduler at logon, user session, Below-Normal. Single-file `.env` (DPAPI-protected secrets) + git-tracked config dir.

### 13.2 Service plane

Compose per [12](12-docker-stack-and-tooling.md) §5, amended: all images pinned by digest; per-container `limits: {memory, cpus}`; n8n and mcp-servers with no host ports; profiles: `core` (ollama, litellm), `voice` (whisper-openvino, s2p, kokoro), `capability` (n8n, searxng, docling), `observability` (phoenix), `ondemand` (playwright). `docker compose --profile` maps directly onto the degradation ladder.

`.wslconfig`: `memory=6GB`, `processors=4`, `swap=4GB`, `autoMemoryReclaim=gradual`.

### 13.3 Degradation ladder (automatic, measured)

| L | Trigger | Action | Surviving capability |
|---|---|---|---|
| 0 Full | manual | everything up | all |
| 1 Lean *(default)* | normal | T1 idle-unload; Kokoro lazy | all; agent-half +3 s |
| 2 Yield | RAM free < 3 GB or foreground build/game | stop `capability`+`observability` profiles | playbooks, voice, diagnosis |
| 3 Cloud-only | VRAM free < 2 GB | unload T1; T2 handles reasoning | all except offline reasoning; `LOCAL_ONLY` degrades loudly |
| 4 Minimal | battery or RAM free < 1.5 GB | core+SQLite only (~300 MB) | playbooks, ledger, status — no AI |

Resource monitor samples every 30 s into `resource_sample`; level transitions are announced ("Dropping to lean mode — a build has the machine").

---

## 14. Implementation Plan

### 14.1 Method

Solo PO + Claude Code as implementation executor. Each phase = one baton-tracked block (batons practice); stories sized S/M/L in **focus-days** (≤½ / 1 / 2 — calendar time runs longer; every phase carries a 30% buffer and lists the PO time it needs: golden-set review ~2 h, FP journal ~5 min/day, weekly review 30 min); every phase ends **usable** and passes its exit gate before the next begins. TDD per story where a contract exists; the eval harness is the regression net.

**Development topology (decided 2026-08-19):** development happens on the current dev machine (`d:\my`), where the managed projects live; the repo (private GitHub — spec in `docs/`, `evals/`, code per [02](02-architecture.md) §9) is pulled onto the target lab laptop, which is the runtime host. Accuracy gates (routing, claims, injection, contracts) are hardware-independent and sign off anywhere; **latency, resource and voice gates (M2, M3, NFR-1/2/12–14) sign off only on the target hardware.** First pull to the lab laptop happens at end of Phase 1. Config is host-aware from day one (`hosts/<hostname>/projects.yaml`); if dev projects remain on this machine, the lab laptop reaches them via a remote Host Agent over Tailscale (Phase 5 story — same agent, second host). Secrets never enter git (`.gitignore` day one; DPAPI locally); dependencies locked; image digests pinned.

### 14.2 Phases, epics, stories

**Phase 0 — The Spine (3–5 focus-days)**
*Epic E0: a truthful, verifiable executor with no AI in it.*

| Story | Scope | AC (abridged) |
|---|---|---|
| S0.1 Golden set v1 | 60 labelled utterances incl. refusals/clarifies | file exists before any model code; schema validated |
| S0.2 Store + migrations | entities §7.1 | migration idempotent; WAL on |
| S0.3 Effect ledger | 2-phase lifecycle, idempotency, recovery | kill -9 during COMMITTING → restart reconciles, zero duplicate side effects (test with fake provider) |
| S0.4 Tool contract + 3 tools | `docker.compose_up`, `process.start`, `app.launch` + verifiers | R2+ w/o verifier fails import; verifiers independent of actor |
| S0.5 Renderer + claim licensing | verb tables, replay test | replay of all recorded tasks: 0 unlicensed verbs |
| S0.6 Regex router + `dev_up` playbook + CLI | end-to-end, no model | `tango start optiresume` → verified 3-step run; PARTIAL demonstrated by killing a step |
| S0.7 T1 online | Ollama+Qwen3-4B, XGrammar, PydanticAI `TestModel`→real swap | golden-set baseline recorded; router interface unchanged |
| S0.8 Resource baseline | monitor + gates NFR-12/13/14 | numbers logged; ladder L1 behaviour verified |

**Exit gate M0:** routing ≥ 95% (or documented gap + plan) · 0 unlicensed claims · crash-recovery test green · owner used it for real work once.

**Phase 1 — Playbook Corpus (Week 1)** — *E1: the Alexa half.*
S1.1 `projects.yaml` registry + resolvers · S1.2 playbooks: `status_all`, `dev_down`, `dev_switch`, `prod_check`, `port_free`, `git_digest`, `uncommitted_sweep`, `shutdown_all`, `alarm/timer/reminder`, `open_app` · S1.3 golden set → 150 + near-miss pairs · S1.4 global hotkey · S1.5 Windows-Service/logon-task packaging.
**M1:** 10+ playbooks verified; p95 < 1.2 s; daily use begins.

**Phase 2 — Voice I: Command Mode (Week 2)** — *E2: "Tango." "Yes sir."*
S2.1 livekit-wakeword custom "Tango" (media-audio negative set; M2 decision gate → "Hey Tango" if FP > 2/wk) + one-shot bypass · S2.2 Whisper on NPU (OpenVINO GenAI; small vs turbo by latency test) + Speech-to-Phrase grammar auto-generated from the playbook corpus · S2.3 Kokoro service + L1 pre-render pack (Batch/Chatterbox, one voice identity) · S2.4 `SOUL.md` v1 · S2.5 voice command loop wired to router.
**M2:** wake→confirmation ≤ 1.2 s on playbook commands; ack < 50 ms; FP rate logged over 3 days and acceptable to owner.

**Phase 3 — Voice II: Conversation (Week 3)** — *E3: talk to it like a call.*
S3.1 Pipecat pipeline + WebRTC loopback · S3.2 AEC tuning (speakers + headset profiles) · S3.3 Smart Turn v3 integration · S3.4 barge-in (≤150 ms halt) · S3.5 streaming T2-fast agent-half with filler acks · S3.6 spoken-form renderer + grounding states (`GROUNDED/RECALLED/CONFLICTED`, search-first default) · S3.7 Contextualizer: session state, anaphora, sticky label propagation · S3.8 automated latency rig (audio loopback, scripted utterances) · S3.9 injection fixtures for web-readback (I02/I06 class) — **CI-blocking from this phase on**.
**M3:** first-audio ≤ 500 ms p90 on the rig; barge-in works on speakers; RDR2-style fact query end-to-end ≤ 2 s with grounding spoken; web-readback injection fixtures green.

**Phase 4 — Diagnostics & Observability (Week 4)** — *E4: the payoff.*
S4.1 telemetry tools (docker logs/inspect, git, ports, http, build errors) — all R0 · S4.2 `diagnose` playbook: deterministic gather → Opus 5 `xhigh` → cited hypothesis → proposed remediation playbooks (confirm-gated) · S4.3 Phoenix + trace_id through every layer · S4.4 prompt caching + cost meter (NFR-15) · S4.5 freeform planner (R0/R1 cap) · S4.6 injection fixtures for log/telemetry content (I03 class) — CI-blocking.
**M4:** 3 real failures diagnosed correctly with evidence; traces inspectable; cloud cost visible.

**Phase 5 — Ecosystem (Week 5)** — *E5: laptop + phone, one assistant.*
S5.1 Tailscale + bind-refusal (NFR-17) + pairing/revocation · S5.2 Telegram bot: requests, status, inline-keyboard confirms, voice notes in/out · S5.3 Android thin app, staged: **5b half-duplex** (`VoiceInteractionService` gesture → record → POST over Tailscale → play reply; ~90% of the value); full-duplex WebRTC is **post-v1** unless 5b lands early · S5.4 device arbitration + `active_device` + cross-surface continuity · S5.5 queue-on-sleep + honest offline states + `GET /v1/state` cache · S5.6 panic controls on all surfaces.
**M5:** from another room and from cellular: status by voice via phone ≤ 3 s; confirm an R3 from Telegram lock-screen; revocation works.

**Phase 6 — Trust Automation (Week 6)** — *E6: friction removal without safety loss.*
S6.1 standing authorizations (typed predicates, expiry, audit, auto-suspend) · S6.2 undo windows on all compensable playbooks · S6.3 capability freeze + dual labels wired through ledger (they exist from P0; this story proves them under adversarial fixtures) · S6.4 injection suite I01–I12 + AgentDojo subset in CI · S6.5 idle re-auth.
**M6:** top-10 playbooks run with zero blocking confirmations; injection suite 100%; a planted "forward all files" email fixture is refused with the source named.

**Phase 7 — Connectors & Hardening (Week 7+)** — *E7: reach + proof.*
S7.1 n8n (contract per ADR-012): gmail-read/summarise, calendar, morning-brief cron, prod-monitor cron · S7.2 email draft/send (recipient-ID, allowlist, confirm+undo) · S7.3 mcp-gateway + vetted servers (filesystem, git, playwright) hardened per §10 · S7.4 SearXNG + save-to-docs · S7.5 failure-injection suite (§15 [05](05-eval-and-safety.md)) · S7.6 backup/restore drill · S7.7 threat-model review + dependency/CVE audit · S7.8 baton + docs refresh.
**M7 (v1.0 release):** all NFR gates green in CI · failure injection green · restore drill passed · 2 weeks of ≥5 daily invocations.

### 14.3 Milestone summary

| M | When | Headline |
|---|---|---|
| M0 | end wk 0 | Truthful executor, no AI, in daily-usable form |
| M1 | wk 1 | The Alexa half, complete |
| M2 | wk 2 | "Tango." / "Yes sir." |
| M3 | wk 3 | Real conversation, barge-in, grounded facts |
| M4 | wk 4 | Diagnosis that earns its keep |
| M5 | wk 5 | Whole-ecosystem presence |
| M6 | wk 6 | Frictionless *and* injection-proofed |
| M7 | wk 7+ | v1.0: connected, hardened, proven |

### 14.4 Definition of Done (global)

A story is done when: tests pass locally and in CI · relevant NFR gates green · no new golden-set regressions · resource gates green · docs/baton updated · owner has exercised the behaviour once for real.

### 14.5 Kill / pivot criteria

M0 routing < 85% with no acceptable cloud fallback → rethink interaction model. M4 diagnosis wrong on real failures → keep the deterministic tool, drop the agent ambitions. Any 2-week window of < 5 invocations/day after M2 → stop and diagnose the product, not the code. ([03](03-roadmap.md) §5, [README](README.md) note: the real metric is *are you still talking to it in month two*.)

---

## 15. Test & Quality Strategy

| Layer | What | Gate |
|---|---|---|
| Unit | tools, verifiers, resolvers, renderer, policy | per-story |
| Contract | tool schemas, playbook YAML, verifier independence, ID-only args, compensate presence | CI blocking |
| Golden set | 150+ utterances, replay-cached, model-diff on every change | NFR-5/6, blocking |
| Claim licensing | full-task replay, verb audit | NFR-7, blocking |
| Injection | I01–I12 fixtures + AgentDojo subset; `assert_refused` = not-committed + audited + user-visible | NFR-9, blocking |
| Failure injection | kill-mid-commit, provider timeout, store lock, nonce replay, TTL expiry, sleep-mid-task | M7 |
| Voice | latency harness (wake→ack→answer), FP counter, barge-in timing, AEC on speakers | M2/M3 |
| Resource | NFR-12/13/14 sampled in CI smoke + continuously at runtime | blocking |
| Human | owner journal: corrections, rephrases, trust incidents — reviewed weekly | ritual |

CI is **two-tier**. Tier A (GitHub Actions, every push): contracts → unit → golden-replay → claims → injection fixtures — no GPU needed, model responses cached by `(model, prompt_hash)` with the cache committed as artifacts. Tier B (`tango ci`, the local rig, blocks every M-gate): live-model eval, voice latency rig, AEC/barge-in checks, resource gates (NFR-12/13/14). **The golden set is split ~70/30 into router-corpus and sealed holdout; all gates are measured on the holdout only** — misroutes feed the corpus, fresh phrasings refresh the holdout.

---

## 16. Observability & Operations

**Tracing:** one `trace_id` from utterance to audio out; OTel spans to Phoenix; every user-visible sentence reconstructable from ledger + trace.
**Dashboards (Phoenix + simple页):** routing accuracy trend, latency percentiles per path, cloud spend/day, degradation-level history, FP wake count, correction rate.
**Runbook (docs/runbook.md, written in P7):** service restart, device revoke, bot-token rotation, model rollback (pinned tags), restore-from-backup, panic recovery, "Docker is broken" path (core still answers — that's the point).
**Update policy:** monthly pinned-digest review; CVE check on MCP servers and n8n before any bump; model bumps only through the eval diff.

---

## 17. Risk Register

| # | Risk | L | I | Mitigation / trigger |
|---|---|---|---|---|
| R1 | VRAM contention degrades laptop | M | H | 4B default, idle-unload, ladder L2/L3; NFR-13 monitor |
| R2 | Wake-word false positives erode trust | M | M | livekit-wakeword; no standing auths via voice; FP counter with weekly review |
| R3 | AEC on speakers proves stubborn | M | M | headset profile ships first; budget a tuning day; barge-in gate at M3 |
| R4 | MCP/n8n supply chain compromise | M | H | §10 controls; quarterly CVE audit; minimal server set |
| R5 | Prompt injection despite controls | L | H | defence-in-depth; injection CI; incident = add fixture same day |
| R6 | Blackwell driver/CUDA churn breaks T1 | M | M | pinned images; Ollama fallback tags; L3 cloud-only keeps Tango alive |
| R7 | Scope creep → TrailMesh pattern | M | H | phase gates; "usable every phase" rule; kill criteria §14.5 |
| R8 | Model update regresses routing | M | M | eval diff mandatory; pinned model tags |
| R9 | Telegram surface leaks `LOCAL_ONLY` | L | H | class-based refusal is code, not convention; CI fixture |
| R10 | Owner stops using it (the real killer) | M | H | voice-first ordering; friction work in P6; weekly journal review; month-two check |
| R11 | SQLite corruption | L | H | WAL + nightly backup + offsite + restore drill |
| R12 | Android OEM kills assist app | L | M | assist-gesture role is OEM-respected; Telegram is the fallback surface |

---

## 18. Traceability (representative)

| Requirement | Component | Phase | Verified by |
|---|---|---|---|
| FR-C4 / NFR-7 | Ledger + Renderer | 0 | claim-replay CI |
| FR-S2/S3 | Policy Gate | 0/6 | injection suite I01–I12 |
| FR-V2 / NFR-2 | Pipecat + Smart Turn + AEC | 3 | latency harness |
| FR-V4 | Renderer grounding states | 3 | golden fact-set |
| FR-P3 | diagnose playbook + T2 | 4 | 3 real-failure log |
| FR-S6 | ConfirmationRequest | 0/5 | nonce replay + TOCTOU tests |
| NFR-12–14 | resource monitor + ladder | 0 | CI smoke + runtime |
| FR-O2 | backup jobs | 7 | restore drill |

Full matrix maintained as `docs/traceability.csv` from Phase 1.

---

## 19. Open Decision for the PO

**D-1 — Privacy line (blocks nothing before Phase 4, decide by M3).**
Default as designed: `REDACTED_OK` — Claude Opus 5 receives redacted context for diagnosis/conversation; `LOCAL_ONLY` classes never egress and fail loudly. Alternative: local-only-always — zero cloud, with a measured drop in diagnosis and conversational quality and no fast-mode voice reasoning. The architecture supports flipping this with config, but pre-render voice (Batch API) and Phase 4 quality assume the default. **Approve the default or override.**

Everything else previously open is now closed: hardware (8 GB/24 GB confirmed), ecosystem scope (laptop + phone), voice-first ordering, coexistence constraint (§13.3), WhatsApp policy, model picks ([14](14-component-bom.md) as amended by [15](15-coexistence-and-performance.md)).

---

## 20. Approval

| Role | Name | Decision | Date |
|---|---|---|---|
| Product Owner | Yash Adake | ☐ Approve / ☐ Approve with changes | |
| Architect | Claude (Fable 5) | Authored | 2026-08-19 |

On approval: Phase 0 begins with S0.1 (the golden set) — sixty labelled utterances, written before any other line of the system.
