# TANGO — Review & Revised Plan

Review of `TANGO_Comprehensive_Project_Specification.md` (v1.0), and a replacement plan.

**Status:** specification complete — see [16-architecture-and-implementation-plan.md](16-architecture-and-implementation-plan.md) (authoritative). **Target machine: Core Ultra 7 · RTX 5060 8 GB · 24 GB RAM.**
**Date:** 2026-08-18

---

## The 60-second verdict

The spec is **unusually good at knowing what matters and unusually bad at being buildable.**

Its core insight — *separate intelligence from execution; never treat a model's "done" as evidence* — is the single most important idea in agent engineering, and most specs three times its length never reach it. Sections 9.2, 17 and 29 are better than what most funded teams write.

But it is a **platform specification for a product that has never been validated**, and it will not survive contact with a solo developer on a Windows laptop:

- 15 components, 10 roadmap phases, 27 backlog items — for one user.
- The "narrow MVP" in §23 is roadmap phases 0 through 5. That's ~3 months before the first day of real use.
- It never asks what job TANGO does *daily*. Every example scenario ("open my dev environment", "email Rahul the PDF") is something you can already do in under 30 seconds.
- The riskiest technical bet in the document — a local SLM doing reliable multi-tool selection on a consumer laptop — is stated as a given and never examined.
- The safety machinery it correctly identifies as essential is scheduled for **phase 3**, after tool execution is already built.

(A process note, not a product comparison: a comprehensive "Implementation Ready" architecture doc in this workspace recently produced five infrastructure packages and no working thing. That pattern is about *build order* and is indifferent to what the project is.)

**Recommendation:** don't build TANGO as specified. Build the part that has a real daily job, on a spine that makes the honesty guarantee real from commit #1, in six weeks. Details in [03-roadmap.md](03-roadmap.md), as amended by [06-the-jarvis-question.md](06-the-jarvis-question.md) §7.

**Scope note:** TANGO is a personal system with exactly one user. Nothing here is argued from markets, competitors or differentiation — those arguments don't apply and have been removed.

---

## Documents

| # | Document | What's in it |
|---|---|---|
| **00** | **[SUMMARY](00-SUMMARY.md)** | **Start here** — what Tango is, the hardware split, the 6-week arc, and what you can do with it |
| 01 | [Verdict & Critique](01-verdict-and-critique.md) | What the spec gets right, and 19 findings ranked by severity with fixes |
| 02 | [Revised Architecture](02-architecture.md) | Five inversions, contracts, state machines, data model, model routing |
| 03 | [Roadmap](03-roadmap.md) | Six weeks, usable at every step. Week 0 is day-level. Plus the cut list |
| 04 | [Decisions (ADRs)](04-decisions.md) | The ten contested calls, with the losing options and why they lost |
| 05 | [Eval & Safety Harness](05-eval-and-safety.md) | Golden set, metrics, injection suite, claim-licensing tests, CI gates |
| 06 | [The Jarvis Question](06-the-jarvis-question.md) | Honest scorecard vs Jarvis, what makes it *feel* like Jarvis, and two amendments to the plan |
| 07 | [Always-On & Multi-Device](07-always-on.md) | Background service, sleep, Tailscale, push, wake words — per-platform reality and what to build |
| 08 | [OpenClaw & Tango](08-openclaw-and-tango.md) | Prior art: what OpenClaw is, its 2026 security record, and the four lessons + one idea to steal |
| 09 | [Capability Catalogue](09-capability-catalogue.md) | What Tango can actually do, tiered by achievability, with a day-in-the-life |
| 10 | [Voice & Consumer Commands](10-voice-and-consumer-commands.md) | Wake word, "yes sir", latency budget, and the per-command truth table (call/WhatsApp/mail/alarm) |
| 11 | [Research Findings (Aug 2026)](11-research-findings.md) | 14 searches + primary papers: false-success data, CaMeL/FIDES, BFCL v4, MCP, voice stack. 13 plan changes |
| 12 | [Docker Stack & Tooling](12-docker-stack-and-tooling.md) | Two-plane design, n8n's exact role, MCP supply-chain hardening, compose skeleton, full tool menu |
| 13 | [Conversational Voice](13-conversational-voice.md) | Turn detection, barge-in, AEC, the 3-layer voice strategy, spoken-form rendering, hardware assignment |
| 14 | [Component BOM](14-component-bom.md) | **The definitive component list** — every pick with evidence, Claude Opus 5 config, VRAM plan, exclusions |
| 15 | [Coexistence & Performance](15-coexistence-and-performance.md) | Real VRAM/RAM arithmetic, the degradation ladder, and why the BOM needed correcting |
| **16** | **[Architecture & Implementation Plan](16-architecture-and-implementation-plan.md)** | **The authoritative spec** — requirements, contracts, security, phased plan with gates, risks. Supersedes 00–15 on conflict |
| **17** | **[Plan Review v1.1](17-plan-review-v1.1.md)** | Red-team of doc 16 — 23 findings (5 critical), amendments applied; where 16 and 17 differ, 17 governs |
| — | [evals/golden.draft.jsonl](../evals/golden.draft.jsonl) | **S0.1 draft, live** — 61 utterances awaiting the owner edit pass ([evals/README](../evals/README.md)) |

---

## The one-paragraph version of the new plan

TANGO stops being "an assistant that can do anything" and becomes **a workspace operations copilot with a verifiable honesty guarantee**: it knows the live state of your five projects, runs your multi-step routines deterministically, diagnoses what's broken from real telemetry, and is reachable from your phone — and it is structurally incapable of claiming an action succeeded when it cannot prove it. The unit of work is a **tested, versioned Playbook**, not a model improvising over a tool registry. Side effects go through an **effect ledger** with two-phase commit and idempotency keys. The user-facing sentence is **rendered from the ledger**, not written by the model. And an **eval set of real utterances exists before the agent does**, so every model, prompt and routing change is measured rather than vibed.

---

## Open questions

1. **Privacy line.** Is "redacted context to a cloud model with no-retention" acceptable, or is local-only a hard requirement? This single answer changes the architecture more than anything else here — see [04-decisions.md](04-decisions.md) ADR-003. Settle it before Week 0.
2. **The daily-job list.** Twenty things you actually did last week on this laptop that were tedious. Not hypothetical — actual. This picks the first ten playbooks. (For a personal Jarvis it is no longer a go/no-go gate — see below.)
3. **Voice, latency, and how much you care.** [06](06-the-jarvis-question.md) §5 argues sub-second latency and voice matter more to the Jarvis feeling than any capability on the roadmap. If you agree, voice moves to Week 2 and the ordering in [03](03-roadmap.md) §2 changes accordingly.

**On kill criteria:** [03-roadmap.md](03-roadmap.md) §5 says "if fewer than 8 jobs score positive, don't build it." That was utility-maximising reasoning and it's too harsh for a system whose point is partly the having of it. The criterion that actually matters for a personal assistant is simpler: **are you still talking to it in month two?** Nothing downstream fixes a no.
