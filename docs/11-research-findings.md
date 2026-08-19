# 11 — Deep Research Findings (August 2026)

A survey of current documentation, benchmarks, arXiv papers and production write-ups, run against the Tango plan to see what holds, what changes, and what I got wrong.

**Headline: the plan survives, and the two things it was built around are now the two things the 2026 research literature is most worried about.** Three parts of the design get concrete upgrades from published work, one of my claims was overstated, and my latency numbers were optimistic.

---

## Finding 1 — "False success" is measured, and it is the dominant failure mode

This is the most important thing in this entire research pass.

There is a paper — *[From Confident Closing to Silent Failure: Characterizing False Success in LLM Agents](https://arxiv.org/abs/2606.09863)* — on exactly the failure mode [ADR-004](04-decisions.md) exists to prevent: **an agent asserting task completion when environment state shows otherwise.**

The numbers:

| Environment | False success as share of all failures |
|---|---|
| τ²-bench, single-control domains | **45–48%** |
| **AppWorld** (personal-app task suite, self-assessing coding agents) | **75.8%** |
| τ²-bench, **dual-control** telecom (independent state verification available) | **3%** |

Read those first and last rows together. **Where the agent judges its own success: ~46%. Where an independent verifier can check world state: 3%.**

That single comparison is the empirical justification for the entire effect ledger. It is a ~15× reduction in the dominant failure class, and it comes from architecture, not from a better model.

### And LLM judges cannot detect it

| Detector | AUROC |
|---|---|
| LLM judge — 5 models × 5 prompt strategies, full task specs (τ²-bench) | **≤ 0.65** |
| LLM judge on AppWorld API-call traces | **0.54** (barely above chance) |
| Lightweight **TF-IDF detector**, domain-calibrated (τ²-bench) | **0.83** |
| Same, AppWorld | **0.95** |

The mechanism the paper identifies is chilling: **judges anchor on confident closing language as evidence of completion — and false-success trajectories produce exactly that language.** The more confidently an agent lies, the more likely a judge is to believe it.

The TF-IDF detector recovers **4–8× more false successes than the best judge at the same flag rate, at 3,300× lower latency.**

**→ Two hard rules for Tango:**

1. **Never use an LLM to decide whether a task succeeded.** Not as a judge, not as a reviewer, not as a "reflection" step. It's near-chance on the failure mode that matters. State verification is the only signal that works. This upgrades my "verifiers must be independent of the actor" from a design preference to a measured requirement.
2. **Add a TF-IDF false-success detector as a cheap triage signal** over response text. ~50 lines, no model, microseconds, 0.83–0.95 AUROC. A second net under claim licensing, catching cases where a verifier was missing or wrong.

---

## Finding 2 — The verification design is now a research line. I overstated its novelty.

In [08](08-openclaw-and-tango.md) §4 I said nobody was building the honesty layer. **For production tools that's still broadly true; for research it is not.** As of mid-2026 there's an active, converging literature, and it landed on the same design:

| Paper | What it does |
|---|---|
| *[Verified Tool Calls Improve LLM Agent Reliability Under Non-Atomic Failures](https://arxiv.org/abs/2608.02645)* (Aug 2026) | A verification-aware tool wrapper with **postcondition verification, verify-before-retry, and idempotency keys** for server-side dedup. Explicitly targets "timeouts after dispatch, delayed visibility, partial state updates." Reduces duplicate actions at comparable task success. **No model changes required.** |
| *[Atomix: Timely, Transactional Tool Use](https://arxiv.org/pdf/2602.14849)* | Transactional semantics for multi-step state-mutating agent workflows |
| *[Beyond Single-Use Tokens: Durable Authorization State for Replay-Resistant LLM Agent Actions](https://arxiv.org/html/2608.01710)* | Durable ledger state bounding issuance and admission; notes exactly-once physical effects additionally require **sink idempotency** |
| *[From Agent Traces to Trust](https://arxiv.org/pdf/2606.04990)* | Survey of evidence tracing and execution provenance |
| *[AgentLTL](https://arxiv.org/pdf/2607.02599)* | Trace verification for procedural compliance |

The first of those is, essentially, [02-architecture.md](02-architecture.md) §4.2 with an evaluation attached — published two weeks ago.

**→ Correction to the record:** the design is not novel, it is *convergent*. That's better news than novelty — it means independent groups reached it from different directions, and there are papers to read rather than a blank page. The "verify-before-retry" framing in particular is sharper than mine: **only retry when the intended state change is confirmed absent.** Adopt that phrasing directly.

Also adopt the term **sink idempotency** — my design put idempotency keys on Tango's side; the paper's point is that exactly-once physical effects need the *provider* to dedup too. Gmail does. Docker doesn't. That distinction should be a field on the tool contract.

---

## Finding 3 — Prompt injection has converged on out-of-band enforcement, with numbers

The whole field settled on the thing [02](02-architecture.md) §3.4 was reaching for: **don't train the model to resist injection; enforce policy outside the model with a deterministic mediator.**

| System | Mechanism | Result |
|---|---|---|
| **CaMeL** (Google DeepMind) | **Dual-LLM**: a Privileged LLM that never sees untrusted content, and a Quarantined LLM that processes it but **cannot invoke tools**; data provenance through an execution graph | 77% task completion **with provable guarantees** vs 84% undefended — a **7-point utility cost** |
| **FIDES** (Microsoft Research) | **Information-flow control** — confidentiality + integrity labels propagate automatically; tool calls require trusted-integrity data | **Stopped all injection attacks in testing.** With reasoning models, completed **16% MORE tasks than baseline** |
| **MELON** (ICML) | Masked re-execution — compare tool calls with the user's task neutralised; identical calls ⇒ injection | **0.32% ASR at 68.72% utility.** Cost: ~2× API calls |
| **Progent** | Policy mediation | AgentDojo injection success **39.9% → 1.0%**; under adaptive attack 25.8% → 4.2% |
| **LlamaFirewall** (Meta) | PromptGuard 2 + AlignmentCheck + CodeShield | ASR 17.6% → **1.75%** combined |

**FIDES is the standout and it changes my design.** Security structure *improving* task completion by 16% is counterintuitive until you see why: labels force the agent to be explicit about provenance, which reduces the confusion that causes failures anyway. My three-tier `TrustTier` is a coarse version of this. **Upgrade to two propagating labels — confidentiality and integrity — following FIDES.**

### Meta's "Rule of Two" — my trifecta, with an official name

> No agent should simultaneously **process untrusted input**, **access sensitive systems**, and **change external state**. Where all three are required, mandate human approval.

That is [02](02-architecture.md) §3.4's interlock verbatim. **Adopt the name** — shared vocabulary beats a private one.

### Two honest caveats from the literature

- Every one of these is validated on **static benchmarks**. *[Adaptive Evaluation of Out-of-Band Defenses](https://arxiv.org/abs/2606.26479)* cautions that a fixed attack set is not proof of security. Progent held up under a hand-crafted adaptive attack (2.6%); not all did.
- The consensus honest assessment: *"prompt injection cannot be fully solved within current LLM architectures."* Defense-in-depth is the goal, not a solution.

**→ Changes:** upgrade `TrustTier` to FIDES-style dual labels · rename the interlock to the Rule of Two · **add AgentDojo to the eval harness** as a real benchmark alongside my 12 hand-written cases.

---

## Finding 4 — Local models are far worse at agentic tool calling than I assumed. And the architecture already dodges it.

[BFCL v4](https://gorilla.cs.berkeley.edu/leaderboard.html) (April 2026) re-weighted toward holistic agentic evaluation: **Agentic 40%, Multi-Turn 30%**, Live 10%, Non-Live 10%, Hallucination 10%.

| Model | BFCL v4 |
|---|---|
| Qwen3-1.7B | **7.8%** (→20.4% with FISSION-GRPO training) |
| Qwen3-4B | **40.9%** |
| Qwen3-8B | **46.8%** |

And two structural findings that matter more than the absolute numbers:

- **Multi-turn scores drop 5–10 points below single-turn for every model.**
- **If your agent makes 5+ sequential tool calls, effective accuracy compounds the multi-turn score, not the headline number.**

Corroborated at the task level: leading models score **80–90% on single-turn tasks but 18–24% on sustained multi-step workflows crossing applications**.

An 8B model at ~47% cannot be trusted to compose multi-step tool sequences. My [ADR-003](04-decisions.md) was right but *understated*.

**And here is the important part:** BFCL v4 is 70% weighted to agentic + multi-turn — **the exact two things [ADR-001](04-decisions.md) removes from the local model's job.** Playbooks reduce the local model to single-turn intent classification plus slot filling, which is the Live/Non-Live category where small models are strongest.

**The playbook architecture converts a task local models fail (~47%) into one they're good at.** That's the strongest validation in this research pass, and it was accidental — I argued for playbooks on reliability grounds, not benchmark grounds.

Gap between frontier closed and top open-weight is now only 3–4 points on overall BFCL v4, but that's at the top end, not at 8B.

**→ Model picks:** Qwen3 family for T1 (7B/8B on 8GB VRAM via Ollama), **strictly single-turn, constrained decoding, classification and slots only.** Never multi-step. Anything agentic goes to T2.

---

## Finding 5 — "Workflow with agent steps" is now the consensus production pattern

Directly validates [ADR-001](04-decisions.md):

> *"The winning architecture in 2026 combines a deterministic backbone with intelligence deployed at specific steps."*
> *"A middle ground most teams overlook is a workflow with agent steps — orchestration is deterministic, but one or more steps use an LLM to handle ambiguity within a bounded scope. **This is what most production systems actually look like, and it is the pattern you should default to.**"*

Anthropic's *Building Effective Agents* draws the same line: chains first, add routing when inputs are heterogeneous, graduate to agentic loops **only when the task genuinely requires dynamic decision-making**.

Playbooks + router + freeform-planner-as-fallback is exactly this shape. No change needed.

**Context for how often this goes wrong:** Gartner projects **>40% of agentic AI projects will be cancelled by end of 2027**, citing cost and unclear value.

---

## Finding 6 — The local voice stack is solved, with real latency numbers

Home Assistant's pipeline is the mature reference implementation, and it's modular via the **Wyoming protocol**:

```
mic → openWakeWord (:10400) → Wyoming STT (:10300) → intent → LLM (:11434) → Wyoming TTS (:10200) → speaker
```

**Measured end-to-end latency:**

| Hardware | Stack | Latency |
|---|---|---|
| RTX 3060 12GB | Llama 3.3 8B + Whisper small | **1–2 s** |
| Mac Mini M5 24GB | same | **1–1.5 s** |
| Raspberry Pi 5 8GB | Phi-4-mini 3.8B | **5–8 s** |

**→ I was too optimistic in [10](10-voice-and-consumer-commands.md) §3.1.** My ~1.2s budget holds for the **no-LLM playbook path**; anything through a local LLM is realistically **1.5–2s**. Correcting that.

### Three concrete component upgrades

- **Speech-to-Phrase** — Home Assistant's constrained STT for *fixed command sets*. Dramatically faster and more accurate than Whisper when the vocabulary is known in advance. **This is purpose-built for the Alexa half** — your ~30 playbook commands are exactly a fixed command set. Route: Speech-to-Phrase first, fall back to Whisper only when it doesn't match. This is the single best latency win available.
- **Kokoro-82M** (Apache-2.0) — 82M params, ~2–3GB, runs fast on **CPU**, 54 voices, 24kHz. Consensus best lightweight local TTS of 2026. Better pick than Piper for the Jarvis voice.
- **Moonshine** — streaming-oriented STT, pairs well with Kokoro for low-latency speech-to-speech.

**→ Adopt the Wyoming protocol** for STT/TTS/wake-word so every component stays swappable, and you inherit an ecosystem instead of maintaining bindings.

Cloud comparison, for reference: unified voice-agent APIs hit ~1s end-to-end; Cartesia 40ms TTFA, Inworld sub-200ms TTFA. Local is competitive but not faster — the tradeoff is privacy, not speed.

---

## Finding 7 — MCP won. Don't write connectors.

| Metric | Value |
|---|---|
| Governance | Donated by Anthropic to the **Agentic AI Foundation (Linux Foundation)**, Dec 2025 — vendor-neutral |
| Registry | **9,652** servers / 28,959 server-versions (May 2026); 15,926 GitHub repos tagged `mcp-server` |
| SDK downloads | **97M/month** (Mar 2026), up from 100k at launch — 970× in 18 months |
| Adoption | OpenAI, Google DeepMind, Microsoft; Gartner projects 75% of API gateway vendors ship MCP features by end of 2026 |
| Spec | 2026-07-28 release |

**→ Delete the bespoke "Connector Layer" from the plan.** Gmail, GitHub, Calendar, Slack, filesystem, web — MCP servers exist. Tango becomes an **MCP client**.

**But with a wrapper, and this is important:** MCP servers carry **no risk metadata, no verifier, and no idempotency semantics**. An MCP tool is a raw capability. So every MCP tool must be adopted through a Tango-side registration that assigns `risk_class`, a `verifier`, `sink_idempotency`, and an integrity label before it becomes callable. **MCP replaces the connector plumbing, not the policy layer.**

Treat MCP servers themselves as supply chain — pin versions, review before enabling.

---

## Finding 8 — Agent memory is contested. Don't buy a framework.

Mem0, Zep, Graphiti, Letta/MemGPT, LangMem, Supermemory. The field is real but the benchmarks are **openly disputed** — Zep published a rebuttal claiming misconfiguration in Mem0's paper (corrected Zep 75.14% vs reported 65.99% on LOCOMO). LOCOMO and LongMemEval measure different things.

Efficiency spread is enormous: Zep >600,000 tokens per conversation vs Mem0's 1,764. Zep's graph construction is thorough but expensive, with reports of post-ingestion retrieval failing until hours later.

**→ No change to the plan.** [02](02-architecture.md) keeps memory as a table with typed rows and tag retrieval. For one user that's sufficient for a long time, and the field is too unsettled to take a dependency. Revisit when you can name a query that fails.

---

## Finding 9 — Cost is a non-issue

| Setup | Monthly |
|---|---|
| Local free-model | **$3–5** (electricity) |
| Busy agent, paid model + VPS | **$30–80** |
| Light scheduling agent (SaaS) | $15–40 |

Local/cloud crossover is around **5–10M tokens/month**; above that local hardware wins.

**→ The $0.30/day tripwire in [05](05-eval-and-safety.md) is correctly sized** — as a runaway-loop detector, not a budget. Cost should never drive an architecture decision here.

---

## Finding 10 — OpenClaw at scale

~**125,000 GitHub stars** and 125,000+ installs as of August 2026 (reported figures vary by source; it was ~60k in the first 72 hours). Originally published as *Warelay*, November 2025.

Its four core primitives are named as **persistent identity, periodic autonomy, accumulated memory, social context** — and the identity piece is **`SOUL.md`**, a markdown file defining who the agent is, what it values, and its boundaries.

**→ Steal `SOUL.md` wholesale.** It's a clean solution to [06](06-the-jarvis-question.md) §7's Amendment 2 (personality as a feature): a single version-controlled file for voice, tone, forms of address and boundaries, separate from prompts and policy. Costs nothing and it's exactly the right seam.

Note it stays strictly *cosmetic*: `SOUL.md` shapes how Tango talks. It has **zero** authority over claim licensing, risk class, or policy. Personality file, not a policy file.

---

## What changes in the plan

| # | Change | Source |
|---|---|---|
| 1 | **Never use an LLM to judge task success.** Formalise as a rule with a CI check | Finding 1 (AUROC ≤0.65) |
| 2 | **Add a TF-IDF false-success detector** as cheap triage over response text | Finding 1 (0.83–0.95 AUROC, 3300× faster) |
| 3 | Adopt **verify-before-retry** phrasing: retry only when the state change is *confirmed absent* | Finding 2 |
| 4 | Add **`sink_idempotency`** to the tool contract — does the provider dedup, or only Tango? | Finding 2 |
| 5 | Upgrade `TrustTier` → **FIDES-style confidentiality + integrity labels** that propagate | Finding 3 (+16% task completion) |
| 6 | Rename the trifecta interlock to **Meta's "Rule of Two"** | Finding 3 |
| 7 | **Add AgentDojo** to the eval harness | Finding 3 |
| 8 | Local model = **Qwen3 7–8B, single-turn only**, never multi-step | Finding 4 (BFCL 46.8%) |
| 9 | **Speech-to-Phrase for the ~30 playbook commands**, Whisper only as fallback | Finding 6 |
| 10 | **Kokoro-82M** TTS, **Moonshine** streaming STT, **Wyoming protocol** for swappability | Finding 6 |
| 11 | **Delete the connector layer — become an MCP client**, with a Tango-side risk/verifier wrapper | Finding 7 |
| 12 | Adopt **`SOUL.md`** for personality, with zero policy authority | Finding 10 |
| 13 | **Correct the latency budget**: sub-second only on the no-LLM path; 1.5–2s through a local model | Finding 6 |

## What holds unchanged

Playbooks over free-form agency · effect ledger with two-phase commit and idempotency · claim licensing · capability freeze · egress allowlists · resolver-mediated entity IDs · deterministic-first tiering · eval-before-agent · SQLite · Telegram surface · Windows service split.

## What I got wrong

1. **"Nobody built the honesty layer."** Overstated. True of production tools, false of research as of 2026 — see Finding 2. Convergent, not novel.
2. **Latency optimism.** ~1.2s doesn't hold through a local LLM; 1.5–2s is the real number on comparable hardware.
3. **I underestimated how bad small models are at agentic tool use** — which, as it turns out, strengthens rather than weakens the architecture.

---

## The thing worth sitting with

You asked for an assistant that will "do whatever I think, anyhow." The literature has a precise answer to that:

> **80–90% success on single-turn tasks. 18–24% on sustained multi-step workflows crossing applications.**
> **45–75% of agent failures are the agent claiming it succeeded when it didn't.**
> **>40% of agentic AI projects projected cancelled by end of 2027.**

A "does anything I think" free agent lands in the 18–24% band and lies about it roughly half the times it fails. That's not a Jarvis; it's a system you'd stop trusting inside a month.

The same literature is equally clear about what *does* work: a deterministic backbone with intelligence at specific bounded steps, independent state verification, and policy enforced outside the model. That's a system that does a growing set of things at 90%+ and **tells you the truth when it can't**.

Tango can be the second one. The research says the first one isn't available to anybody yet — not to you, not to Google, not to a lab.

---

## Sources

**False success & verification**
[From Confident Closing to Silent Failure (2606.09863)](https://arxiv.org/abs/2606.09863) · [Verified Tool Calls Under Non-Atomic Failures (2608.02645)](https://arxiv.org/abs/2608.02645) · [Atomix (2602.14849)](https://arxiv.org/pdf/2602.14849) · [Durable Authorization State (2608.01710)](https://arxiv.org/html/2608.01710) · [From Agent Traces to Trust (2606.04990)](https://arxiv.org/pdf/2606.04990) · [AgentLTL (2607.02599)](https://arxiv.org/pdf/2607.02599) · [Cleanlab on τ²-Bench](https://cleanlab.ai/blog/tau-bench/)

**Prompt injection**
[Zylos: 2026 State of the Art](https://zylos.ai/research/2026-04-12-indirect-prompt-injection-defenses-agents-untrusted-content/) · [Simon Willison on CaMeL](https://simonwillison.net/2025/Apr/11/camel/) · [CaMeL paper (MIT 6.5660)](https://css.csail.mit.edu/6.5660/2026/readings/camel.pdf) · [CaMeLs Can Use Computers Too (2601.09923)](https://arxiv.org/pdf/2601.09923) · [Adaptive Evaluation of Out-of-Band Defenses (2606.26479)](https://arxiv.org/abs/2606.26479) · [MELON (2502.05174)](https://arxiv.org/pdf/2502.05174) · [Attack & Defense Landscape of Agentic AI (2603.11088)](https://arxiv.org/pdf/2603.11088)

**Tool calling & reliability**
[BFCL v4 Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) · [BFCL paper (PMLR)](https://proceedings.mlr.press/v267/patil25a.html) · [Spheron: BFCL v4 / τ-bench guide](https://www.spheron.network/blog/tool-calling-benchmarks-bfcl-tau-bench-latency-optimization/) · [Fission-GRPO (2601.15625)](https://arxiv.org/pdf/2601.15625) · [Towards a Science of AI Agent Reliability (2602.16666)](https://arxiv.org/pdf/2602.16666) · [On the Reliability of Computer Use Agents (2604.17849)](https://arxiv.org/pdf/2604.17849)

**Architecture patterns**
[Vellum: Agentic Workflows 2026](https://www.vellum.ai/blog/agentic-workflows-emerging-architectures-and-design-patterns) · [Morph: LLM Workflows](https://www.morphllm.com/llm-workflows) · [Agents vs Workflows decision framework](https://dev.to/thedailyagent/agents-vs-workflows-a-decision-framework-for-2026-19ab)

**Voice**
[Home Assistant local AI voice 2026](https://botmonster.com/smart-home/build-private-local-ai-voice-assistant-2026/) · [Whisper + Piper + Ollama stack](https://www.kunalganglani.com/blog/local-ai-voice-assistant-whisper-piper-ollama) · [Kokoro TTS on CPU](https://ariya.io/2026/03/local-cpu-friendly-high-quality-tts-text-to-speech-with-kokoro) · [Moonshine + Kokoro speech2speech](https://rhulha.github.io/Speech2Speech/) · [AssemblyAI: speech-to-speech APIs](https://www.assemblyai.com/blog/best-speech-to-speech-voice-agent-api)

**MCP**
[State of MCP 2026](https://mcp.institute/research/state-of-mcp-2026) · [MCP adoption statistics](https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol) · [MCP spec RC 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/) · [WorkOS: MCP in 2026](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026)

**Memory · cost · OpenClaw**
[Mem0 State of Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026) · [5 memory systems compared](https://medium.com/@wasowski.jarek/i-compared-5-ai-agent-memory-systems-across-6-dimensions-none-wins-6a658335ed0a) · [AI agent cost per task 2026](https://www.kunalganglani.com/blog/ai-agent-cost-per-task-2026) · [OpenClaw and the Programmable Soul](https://duncsand.medium.com/openclaw-and-the-programmable-soul-2546c9c1782c) · [SOUL.md template](https://github.com/openclaw/openclaw/blob/main/docs/reference/templates/SOUL.md)
