# 00 — Tango: The Summary

*Read this first. Docs [01](01-verdict-and-critique.md)–[12](12-docker-stack-and-tooling.md) are the working detail behind it.*

---

## What Tango is

**A personal AI assistant that runs on your own hardware, that you talk to, that does real work on your machine and your services — and that is structurally incapable of telling you something worked when it can't prove it.**

Two halves, one voice:

- **The Alexa half** — ~30 things it does *dependably*, in under a second, with no AI in the path. Start a project, kill everything, set an alarm, open an app, check prod.
- **The agent half** — open-ended reasoning for novel requests. Why is this broken. What changed. What did I ship.

The router decides which half answers. You never have to know which.

---

## Your hardware, and what it buys

**Intel Core Ultra 7 + RTX 5060 (8 GB) laptop.**

This is meaningfully better than what the earlier docs assumed, and it lets us use all three compute units instead of fighting over one:

| Unit | Runs | Why |
|---|---|---|
| **RTX 5060 · 8 GB** | **Qwen3 8B, Q4/Q5** (~5 GB) — the local brain | Leaves ~3 GB headroom for context and a second model |
| **Intel NPU (AI Boost)** | **Whisper STT** via OpenVINO | Frees the GPU entirely. The NPU is idle otherwise — free capability. |
| **CPU** | Kokoro TTS (82M), Speech-to-Phrase, wake word, everything else | Kokoro is CPU-fast by design |

**What this means concretely:** the local model is genuinely good enough for intent classification and slot-filling — the only two jobs [ADR-003](04-decisions.md) gives it — and voice never has to compete with it for VRAM. Expect **sub-second on the playbook path, ~1–1.5 s through the local model**, comparable to or better than the RTX 3060 reference numbers in [11](11-research-findings.md).

*Gotcha to plan for:* Blackwell (RTX 50-series) needs recent CUDA/driver builds. Pin current Ollama/llama.cpp images rather than whatever's cached.

---

## What we will be doing

Six weeks. Something usable at the end of every one of them.

| Week | Build | You get |
|---|---|---|
| **0** *(2 days)* | The spine — eval set, SQLite ledger, verification, claim licensing. **Router is regex. Zero AI.** Then swap the model in behind the same interface. | `tango start optiresume` → *"DB up. API on :8000. Editor open."* Every claim independently checked. |
| **1** | Ten playbooks + project registry + resolvers. Golden set to 150 utterances. | Daily use begins |
| **2** | **Voice** — wake word, Speech-to-Phrase, Whisper on NPU, Kokoro voice, `SOUL.md` personality | *"Tango." "Yes sir."* |
| **3** | **Diagnostics** — logs, containers, git, build failures → evidence-cited hypotheses. Plus Telegram as your remote surface. | The week it earns its keep |
| **4** | Standing authorizations + undo windows + tracing | Friction disappears |
| **5** | MCP gateway + n8n connectors — mail, calendar, contacts, calls | Reaches beyond the machine |
| **6** | Hardening — injection suite, failure injection, AgentDojo | Safe for daily life |

**Week 0 is two Docker containers and one native Windows service.** The stack grows to ~13 containers by week 6 — but only as playbooks need them.

**Why the spine comes first:** building the safety machinery with a *fake brain* means that when something breaks on day 2, the model is the only suspect. Everything else was green yesterday.

---

## What we will accomplish

Four guarantees. These are the actual product — everything else is features.

**1. It will not lie to you.**
Every side-effecting action goes through a durable ledger: propose → policy → commit → verify. The sentence you read is *rendered from ledger state*, not written by the model. It can say "sent" only when a message ID exists.

Why this matters more than it sounds: research this year measured **45–76% of all agent failures are the agent claiming success when it failed** — and where independent state verification exists, that drops to **3%**. LLM judges can't catch it (AUROC ≤0.65) because they anchor on confident-sounding language, which is exactly what false success produces.

**2. It will be dependable on the things you do daily.**
Playbooks are tested code, not model improvisation. The 100th run is the 1st run. Adding capability #31 cannot break #7.

**3. Untrusted content cannot make it act.**
Tool permissions are frozen *before* web pages, emails or logs enter context. Recipients and paths are checked against allowlists the model can't write. It follows **Meta's Rule of Two** — never untrusted input + sensitive access + external change in one task without you in the loop.

**4. It stays yours.**
Local models on your GPU, your data in a SQLite file, no public ports, reachable only over Tailscale. Cloud models used deliberately and redacted, never silently — and `LOCAL_ONLY` tasks fail loudly rather than escaping to the internet.

---

## What you'll be able to do

### Excellent (85–95%) — verifiable, fast, no friction

- **"What's the state of everything?"** — every project, dev servers, containers, uncommitted work, branch divergence, prod health, last deploy. Five minutes of tab-hopping → **four seconds.**
- **"Start optiresume" / "kill everything" / "switch to myjson" / "free port 3000"**
- **"Why is the API down?"** — real logs, containers, git history, env diffs → a diagnosis with evidence cited, and a proposed fix it won't apply without asking
- **"What did I ship this week?"** — digest across every repo
- **"Is prod ok?"** — real probes, the actual commit serving prod
- **Alarms, timers, reminders, opening apps** — the easiest and most reliable things in the system
- **Unprompted:** *"⚠ API failed 2 health checks. 502 from Render. Last deploy was 4 days ago, so this isn't a deploy."*
- **Morning brief / end-of-day baton** — reads your existing checkpoint files, drafts the next one from real git activity

### Good (70–85%) — small friction

- Typecheck/lint/test sweeps across all projects · research and capture · file operations · content drafts from shipped work · cross-device handoff · calendar · **email draft and send** (confirm + undo + recipient allowlist)

### Works, with caveats (50–80%)

- **Calls** — dials via your phone; it can confirm the dial started, never that anyone answered
- **SMS and Telegram** — fully automatic, fully verified
- **WhatsApp** — opens the right chat with the message written; you tap send. That tap is the safety gate anyway
- **Remediation** — restart, redeploy, roll back: proposed, never automatic

### Won't do

Banking · anything behind a login that forbids bots · physical world · multi-hour unsupervised work · judgement-based nagging.

---

## The honest ceiling

**~85% inside your development environment.** Genuinely Jarvis-like — and that's the domain you own outright, where nobody's business model is working against you.

**~40% across the rest of your digital life.** Email and calendar with friction; phone thin; most consumer services don't want automating.

**~5% physical.** Not this project.

The gap isn't skill or budget. Jarvis works because Tony Stark built every device Jarvis talks to. You own your dev environment completely — **so build the lab. The lab is very good.**

---

## Before I write the blueprint — three things

1. **Confirm the machine.** RTX 5060 Laptop at **8 GB** VRAM, and how much system RAM (16 or 32)? 32 makes the container stack comfortable; 16 means trimming.
2. **Does Tango manage *this* laptop too, or only the new one?** If your projects stay on `d:\my` here, Tango needs a remote host agent — that's a real design branch, not a detail.
3. **Privacy line.** Redacted context to a cloud model for the hard reasoning, or local-only always? Local-only is viable on this hardware for classification and slots, but diagnosis quality drops noticeably. **This one changes the architecture more than anything else.**
