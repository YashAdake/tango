# 09 — What Tango Can Actually Do

A capability catalogue for Tango built as specified in [02](02-architecture.md)–[08](08-openclaw-and-tango.md).

Everything here is tagged honestly. Nothing is aspirational.

**The organising principle, and it predicts every score below:**

> Tango is excellent wherever it can **act deterministically** *and* **verify independently**.
> It is mediocre where it can do one.
> It is bad where it can do neither.

Tier 1 has both. Tier 4 has neither. That's the whole pattern.

**Legend** — `R0` read-only · `R1` reversible · `R2` external side effect · `R3` consequential · `R4` high risk. `✓verify` = an independent postcondition check exists.

---

## Tier 1 — What it will be genuinely excellent at (85–95%)

All `R0`/`R1`, all independently verifiable, no confirmation friction. **This is Tango's home turf and where 80% of your daily value lives.**

### 1.1 Situational awareness — the flagship

> **"What's the state of everything?"**

```
5 projects · 2 running · 1 needs attention

optiresume    dev up (db✓ api:8000✓ web:3000✓) · 3 uncommitted · dev↑2 ahead of prod
              prod ✓ 218ms · deployed 4d ago from prod@54a8bde
myjson        stopped · clean · main✓ synced
              prod ✓ 94ms
airdraw       stopped · clean · 1 unpushed commit
filesflow     stopped · 47 uncommitted ⚠ · never deployed
portfolio     stopped · clean · V3.0.0.0✓

⚠ port 3000 held by node.exe (pid 18244, optiresume/frontend)
```

**Today this takes you five minutes across terminals and browser tabs. Tango: four seconds.** `R0` · `✓verify` · **95%**

This one capability probably justifies the whole build.

### 1.2 Environment orchestration

| You say | What happens |
|---|---|
| "start optiresume" | Docker db → health check → API → probe `:8000` → VS Code. Reports per-step truth. |
| "kill everything" | All dev servers + containers down, ports freed, verified empty |
| "switch to myjson" | Shuts down what's running, frees `:3000`, pulls latest, starts myjson, opens editor |
| "free port 3000" | Identifies the holder, kills it, confirms the port is actually free |

The port-3000 collision is a documented hazard in your own `CLAUDE.md`. Tango makes it a non-event. `R1` · `✓verify` · **90%**

### 1.3 Failure diagnosis — the highest-leverage thing a model does here

> **"why is the optiresume api down"**

```
The API container is running but unhealthy.

Evidence:
  · container optiresume-api  up 4m, health: unhealthy (3 failed checks)
  · logs: "asyncpg.InvalidPasswordError: password authentication failed"
  · optiresume-db  up 4m, healthy
  · .env changed 12 min ago — POSTGRES_PASSWORD differs from
    docker-compose.yml's POSTGRES_PASSWORD
  · last commit touching .env.example: 2 days ago

Diagnosis: the DB password in .env no longer matches the one the
db container was initialised with. The volume still holds the old
credential.

I can (a) revert .env to the compose value, or (b) drop the db
volume and re-init. (b) destroys local data. Which?
```

That's not a chatbot answer — every line is a tool result. **This is the single best use of an LLM in the whole system.** `R0` read path · `✓verify` · **80–85%**

Covers: container failures, build/type errors, migration failures, port conflicts, env drift, "it worked yesterday" regressions via git.

### 1.4 Cross-repo git intelligence

- **"what did I ship this week"** — digest across all 5 repos, grouped, human-readable
- **"what have I got uncommitted anywhere"** — the thing that silently rots
- **"is prod behind dev anywhere"** — branch divergence across projects
- **"what changed since optiresume last worked"** — commits between a known-good SHA and HEAD
- **"which branches are stale"** — the redesign branches, worktrees, unmerged work

You have several long-lived unmerged branches across projects. Tango keeps that map so you don't have to. `R0` · `✓verify` · **90%**

### 1.5 Deploy & production awareness

- "is prod up" → real HTTP probes to every live domain, with latency
- "what's live on optiresume" → the actual commit SHA serving prod, not what you assume
- "did the last deploy succeed" → Vercel/Render APIs, build logs on failure
- "is prod running the same code as my prod branch" → catches silent deploy failures

Given OptiResume is live with real users, this is not a convenience. `R0` · `✓verify` · **85%**

### 1.6 Bounded proactive monitoring

Heartbeat-driven, pushed to Telegram. **The safe kind of proactivity — verifiable facts, not judgement calls.**

- Prod health every N minutes; alert on 2 consecutive failures (never on 1 — flap suppression)
- SSL cert and domain expiry warnings
- "tell me if the optiresume API 5xx rate rises"
- Disk space, Docker volume growth
- Deploy completion notifications

`R0` · `✓verify` · **85%** — and note it's high *only* because these are objectively checkable. Judgement-based proactivity ("you should work on X") stays at ~25% forever.

### 1.7 Personal knowledge retrieval

Your `Docs/`, `batons/`, per-project `docs/`, the `NewProds/` research set, `marketing/`.

- "what did baton 011 say about the scoring engine"
- "what's my Turnstile gotcha again"
- "what did I decide about CloudJiffy vs Render"

You have a *lot* of documented decisions. Retrieval is `grep` + `git log` + a model reading the hits — no RAG needed ([ADR-005](04-decisions.md)). `R0` · **85%**

---

## Tier 2 — Genuinely useful, minor friction (70–85%)

### 2.1 Baton automation — fits your existing practice exactly

You already run per-project checkpoints in `d:\my\batons\` with a `_STATE.md` pointer. Tango can:

- **Morning brief:** read `_STATE.md`, summarise where you left off, show what changed overnight
- **End-of-day:** generate a draft baton from the day's actual git activity, commands run, and tasks completed — you edit rather than write from scratch
- Keep `_STATE.md` current automatically

This is a rare case where Tango slots into a workflow you *already have*, so adoption cost is zero. `R1`/`R2` · **85%**

### 2.2 Verification & build gates

- "typecheck everything" → `tsc --noEmit` across all Next projects, one consolidated report
- "lint myjson" · "run optiresume tests" → parsed output, only failures surfaced
- "is anything broken right now" → build + typecheck + test sweep across all 5

Uses the exact commands from each project's `CLAUDE.md`. `R1` · `✓verify` · **85%**

### 2.3 Claude Code orchestration

Genuinely interesting: Tango prepares and launches Claude Code sessions with context preloaded.

- "start a Claude session on the optiresume scoring bug" → correct directory, relevant baton and files gathered, session opened
- "what's Claude Code working on right now" → running sessions across projects

Tango becomes the layer *above* your AI coding tools rather than competing with them. `R1` · **75%**

### 2.4 Content & marketing ops

You run a LinkedIn programme with documented rules (short posts, 500–900 chars, load-bearing image, OptiResume excluded until ~Nov 2026).

- "draft this week's post from what I shipped" → git digest → draft respecting your documented constraints → preview, never auto-post
- "what have I not written about yet" → shipped work vs published posts

`R2` preview-only · **75%** — quality depends on the model, and it's a draft you edit, never a send.

### 2.5 File operations

Find, move, organise, archive — within allowed paths, `resolve_file` IDs only, never free paths ([ADR-009](04-decisions.md)). "where's that Turnstile screenshot", "archive last month's scratch files". `R1`/`R2` · `✓verify` · **80%**

### 2.6 Research & capture

Web search → read → summarise → save into `Docs/` with source URLs. All retrieved content is `UNTRUSTED`, so the trifecta interlock applies. `R0`/`R1` · **75%**

### 2.7 Scheduled routines

Morning brief · end-of-day shutdown + baton · Monday weekly digest · pre-deploy checklist. Same playbook engine, cron-triggered. `R1` · **85%**

### 2.8 Cross-device handoff

Via Telegram: send a file, log excerpt, screenshot or link from laptop to phone and back. Solves a genuine daily annoyance for near-zero cost. `R1` · **85%**

---

## Tier 3 — Works, but friction or real risk (50–70%)

| Capability | Why it's capped |
|---|---|
| **Email triage & draft** (`R2`) | Good value. But email bodies are `UNTRUSTED` — interlock suspends standing auths for the whole task |
| **Email send** (`R3`) | Works with confirm + undo + recipient allowlist. Modest time saved; the peak of injection risk |
| **Calendar create/modify** (`R2`/`R3`) | Fine once a connector exists. `✓verify` via returned event ID |
| **Remediation actions** (`R2`/`R3`) | "restart the API", "redeploy", "roll back" — real value, but always propose-then-confirm, never auto |
| **Phone actions via Android** (`R3`) | Call initiation and SMS intents work. `UNVERIFIABLE` outcome — Tango can confirm the request was accepted, never that anyone answered |
| **Browser automation** (`R2`) | Brittle. Sites change, logins break. Use official APIs wherever one exists |
| **Screen capture / vision** (`R1`) | Works, but per-session explicit grant. Useful for "what's this error on screen" |

---

## Tier 4 — Don't build these

| | Why |
|---|---|
| Banking, payments, financial actions | No API for you, and `R4` should never be automated in a personal system |
| WhatsApp automation | Unofficial libraries only; risks your number ([08](08-openclaw-and-tango.md) §5) |
| Long autonomous coding | Agents still drift over multi-hour unsupervised work |
| Physical-world sensing/control | A hardware project, not this one |
| Anything behind a login that forbids bots | You'd be maintaining a breakage forever |
| Judgement-based proactivity | "You should work on X" — ~25% and the false positives get it muted |

---

## A day, concretely

**08:40** — Assist gesture on the phone: *"morning brief."*
> *Nothing broke overnight. All 4 prod sites healthy. You left off in optiresume on the scoring-engine baton — 3 uncommitted files in `app/services/scoring/`. Note: myjson's SSL cert renews in 9 days.*

**09:15** — At the desk, hotkey: *"start optiresume."*
> *DB up. API responding on :8000. Web on :3000. VS Code open.* — 3 seconds, every claim checked.

**11:30** — *"why is the api throwing 500s"* → the diagnosis in §1.3. Two minutes of reading logs replaced by six seconds.

**14:00** — *"typecheck everything"* → *3 clean, filesflow has 2 errors in `src/lib/pdf.ts`.*

**16:20** — Telegram, from a café: *"is prod ok?"*
> *All 4 healthy. optiresume 218ms, myjson 94ms, airdraw 140ms, portfolio 88ms. Last deploy 4d ago, prod@54a8bde.*

**18:45** — *"what did I ship today"* → digest across repos → draft baton for review.

**19:00** — *"shut it down."* → containers down, servers stopped, ports free, verified. *Done — 4 uncommitted files in optiresume, unpushed.*

**23:10** — Push, unprompted: *⚠ optiresume API failed 2 consecutive health checks. 502 from Render. Last deploy 4d ago, so this isn't a deploy. Want the Render logs?*

That last one is the moment Tango stops being a tool.

---

## Why these specifically, and not a longer list

Three properties of the architecture produce this shape:

**Playbooks make capability #31 cost an afternoon.** ([ADR-001](04-decisions.md)) Not a rewrite — a YAML file and a test. The catalogue *grows* rather than being fixed at launch, and adding one can't break another.

**Verification is why the answers are worth trusting.** ([02](02-architecture.md) §3.2–3.3) "Prod is healthy" means Tango probed it. "Started" means a process was independently observed. Without that, every item above is a guess wearing a confident voice — and you'd go back to checking manually, which is [OpenClaw's gap](08-openclaw-and-tango.md) §4.

**The trifecta interlock is why Tier 2's web and email items are safe at all.** ([02](02-architecture.md) §3.4) Without it, "summarise this page" becomes an attack surface with shell access behind it.

---

## The honest ceiling

**Inside your development environment: ~85%.** Genuinely Jarvis-like. Everything in Tier 1 works, works fast, and works the hundredth time exactly like the first.

**Across your whole digital life: ~40%.** Email and calendar work with friction. Phone actions are thin. Most consumer services don't want to be automated.

**In the physical world: ~5%.** Not this project.

Roughly **25 capabilities at 85%+**, growing by one per afternoon once the spine exists. Which is why [03-roadmap.md](03-roadmap.md) spends Week 0 on the spine and not on features — the spine is what makes every subsequent capability cheap *and* trustworthy.
