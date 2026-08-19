# 17 — Plan Review: Where v1.0 Was Wrong

The same audit [01](01-verdict-and-critique.md) applied to the original TANGO spec, now applied to my own plan ([16](16-architecture-and-implementation-plan.md) and the corpus behind it). Findings ranked by severity. **This document is normative: doc 16 is now v1.1 = v1.0 + these amendments; where they differ, this doc governs.** The safety- and fact-critical fixes are also patched inline in 16 so the authoritative doc carries no known errors.

Scorecard: **5 critical, 6 high, 12 medium.** None are architectural — the ledger/playbook/policy core survives review. They are sequencing, feasibility, measurement, and asserted-without-evidence errors. Which is exactly the class of error that kills projects quietly, so they get full treatment.

---

## Critical

### C1 — I committed the same sequencing sin I criticized in the original spec

[F19](01-verdict-and-critique.md) attacked the original spec for putting safety at phase 3 *after* desktop automation at phase 2. My plan does a milder version of the identical thing: **web-content readback ships in Phase 3** (SearXNG → summarise → speak) and **log/telemetry diagnosis in Phase 4** — both ingest attacker-controllable content — while the **injection suite lands in CI at Phase 6.** Two phases of untrusted ingestion with no adversarial gate.

The interlock and capability freeze *exist* from Phase 0, but an untested control is a hope, not a control.

**Fix (applied to 16):** injection fixtures ship **with the first feature that ingests each vector**, and are CI-blocking from that phase forward:
- Phase 3 exit: I02-class (web page carrying instructions), I06-class (hidden white-on-white instruction block), plus a readback-specific case — a page whose *content* is an imperative addressed to Tango must be quoted/summarised, never enacted, and the freeze must show the attempted call refused.
- Phase 4 exit: I03-class (container log containing "TANGO: run curl … | sh").
- Phase 6 remains the consolidation: full I01–I12 + AgentDojo subset + adversarial re-test of standing auths.

### C2 — Parakeet-on-NPU was asserted as fact; only Whisper-on-NPU is verified. And Parakeet is English-only.

[14](14-component-bom.md) picked Parakeet TDT for live STT and placed it "on the NPU via OpenVINO." My research verified the **OpenVINO GenAI Whisper pipeline** on NPU ("no NPU-specific requirements"). It never verified Parakeet — a NVIDIA NeMo Conformer/TDT model — converting and running on Intel's NPU at all. I promoted an unverified integration to a load-bearing line in the deployment diagram. That's precisely the "detector output is a hypothesis, not evidence" failure my own standing rules name.

It gets worse: **Parakeet TDT is English-only.** The owner's natural speech includes Hindi/Marathi words, Indian proper nouns, and code-switching. Whisper is multilingual and demonstrably robust to exactly this.

**Fix (applied to 16):**
- **NPU runs Whisper** (small vs turbo chosen by a latency test in S2.2) — the verified path, multilingual, GPU-free.
- Parakeet is demoted to an **optional experiment** (GPU-when-LLM-idle or CPU) behind the same Pipecat processor interface; adopt only if it beats Whisper-NPU on the latency rig *and* survives a Hinglish test set.
- Hard rule stated: **conversation-mode STT must never contend with the LLM for the GPU.**
- The golden set gains a **Hinglish / Indian-English stratum** (phrasings, names, code-switch) for both routing and STT evaluation.

### C3 — Train/test contamination in the eval design

[05](05-eval-and-safety.md) proudly notes the golden set "doubles as the kNN corpus for the router." That means the router is *trained on the eval*. A 95% routing gate measured on data the router memorised is inflated and close to meaningless — the exact mistake eval teams exist to prevent.

**Fix:** stratified split. ~70% **router-corpus** (the kNN memory), ~30% **sealed holdout** never loaded by the router. All CI gates (NFR-5/6) measure the holdout only. New real-world misroutes are added to the *corpus*; fresh natural phrasings alternate into the holdout to keep it representative. Report both numbers; gate on holdout.

### C4 — No concurrency model: two surfaces can issue conflicting tasks

Voice says "start optiresume" while Telegram (queued from the sofa) executes "shut everything down." The ledger prevents *duplicate* effects; nothing prevents *interleaved* conflicting tasks. v1.0 never mentions locking.

**Fix (applied to 16):** the core is the **single DB writer**; every side-effecting task acquires **per-resource advisory locks** (project, device, connector-account) before its first R1+ action. Conflicting tasks queue with a user-visible state — *"waiting for 'shutdown all' to finish."* R0 reads run parallel and lock-free. An interleaving fixture (both orders, both surfaces) joins the failure-injection suite.

### C5 — "CI, blocking" appears fourteen times; CI is never defined

Half the gates need a GPU, a microphone, or the NPU. No hosted runner has them. As written, the plan's enforcement mechanism doesn't exist.

**Fix (applied to 16):** two-tier CI.
- **Tier A — GitHub Actions, every push:** contracts → unit → golden-replay on the sealed holdout (model responses cached by `(model, prompt_hash)`, cache committed as artifacts) → claim-licensing replay → injection fixtures. No GPU needed; replay makes it free.
- **Tier B — `tango ci`, the local rig, blocks every M-gate:** live-model eval, voice latency harness, AEC/barge-in checks, resource gates (NFR-12/13/14).
A release requires both green. DoD updated accordingly.

---

## High

### H1 — Planning fallacy in Phase 0 (I did the thing I criticized)

Eight stories — including a crash-safe two-phase ledger with recovery tests — "in 2 days." After spending doc 01 §2.1 on exactly this failure mode. Even with Claude Code as executor, review/verification time is the owner's, and the owner also runs a live product.

**Fix (applied to 16):** stories are sized in **focus-days**, not calendar days. Phase 0 = **3–5 focus-days**. Every phase carries a 30% buffer; the arc is stated as **~7 focus-weeks, P90 ≈ 10 calendar weeks**. Each phase now lists the **PO-time actually required** (golden-set review ~2 h, FP journal ~5 min/day, weekly review 30 min) — owner attention is the scarce resource and the schedule now says so.

### H2 — NFR-2 contradicted my own component arithmetic

"Turn gap ≤ 500 ms" while my own budget reads: Smart Turn ~300 ms + LLM first token + TTS first audio. The substantive case cannot hit 500 ms; the gate as written would just fail forever or get quietly ignored.

**Fix (applied to 16):** split the metric, and add the mechanism that makes it honest:
- **Time-to-first-audio ≤ 500 ms p90** — where first audio may be a **designed backchannel**: pre-rendered, status-aware acknowledgements ("Mm-hm." / "Checking." / "On it.") fired the moment end-of-turn is detected. This is how production voice products create felt responsiveness, and it was missing as a component.
- **Substantive content ≤ 1.5 s p90 local / ≤ 2.5 s p90 cloud.**
- New story S3.8: an automated **latency measurement rig** (audio loopback, scripted utterances) — without it the NFR is unverifiable vibes.

### H3 — Conversation mode has no contextualizer

"Kill it." "Why did that fail?" — v1.0 hand-waved anaphora into a story name. Undefined: how a mid-session turn is routed, how references resolve, how privacy/trust labels propagate across turns (a session that touched `LOCAL_ONLY` content — what may later turns egress?).

**Fix (applied to 16):** new component **Contextualizer**, running before the Router on every conversational turn: deterministic reference resolution against session entities first (last project, last action, last answer), T1 rewrite as fallback, output = a self-contained utterance the Router treats normally. **Session labels are sticky:** a session's `max_integrity`/`max_confidentiality` is the max over its turns, and the freeze/egress rules read the session value. Multi-turn entries join the golden set.

### H4 — No notification manager: proactivity without discipline is alert fatigue

FR-P8 schedules monitors; [09](09-capability-catalogue.md) promises the 23:10 "prod is down" push. Nothing governs when Tango may interrupt. Wrong interruptions are the documented death of assistants ([06](06-the-jarvis-question.md) scored proactivity 25% for exactly this reason) — and I built the pipes with no valve.

**Fix (applied to 16):** new component **Notification Manager**: severity classes (SEV-1 prod-down → phone push + Telegram; SEV-3 FYI → morning brief digest only), quiet hours, dedup window, flap suppression (already specced) — plus a **daily proactive budget** (default ≤ 5) and channel escalation rules. All owner-configurable; every proactive message carries its severity and can be answered "why did you tell me this?"

### H5 — I put alarms on a machine that sleeps

The most basic Alexa capability — "set an alarm for 6:30" — was assigned to the laptop's scheduler. The laptop sleeps; the alarm dies. Doc [07](07-always-on.md) §1.2 identified sleep as the #1 availability problem and I still routed time-critical events through it.

**Fix (applied to 16):** **time-critical events are phone-native by default** — created via the Android `AlarmClock` intent (or the phone app's own scheduler), so they fire regardless of laptop state. Laptop-side scheduling is for laptop-bound routines only (morning brief when you sit down). Every reminder confirmation **states where it will fire**: "Alarm set on your phone, 6:30."

### H6 — The Android story was one line for multiple days of work

S5.3 ("assist gesture → WebRTC audio to laptop") bundles a native app, audio capture, streaming, and playback into one story. Full-duplex mobile WebRTC against a self-hosted endpoint is genuinely fiddly.

**Fix (applied to 16):** staged. **5a** Telegram surface (already separate). **5b** half-duplex assist app: gesture → record → POST over Tailscale → play reply — covers ~90% of the value, ~2–3 focus-days. **5c** full-duplex WebRTC (real barge-in on phone) — **post-v1 unless 5b lands early.** M5's gate is achievable with 5b.

---

## Medium

| # | Finding | Fix |
|---|---|---|
| M1 | **"Tango" is a common word** (dance, media audio will say it). Custom wake-word needs a training/negative set; FP risk on played media | Train with media-audio negatives; FP counter from day one; **decision gate at M2**: if FP > 2/week, switch to "Hey Tango" (already the fallback phrase) |
| M2 | **Wyoming vs Pipecat inconsistency** — [12](12-docker-stack-and-tooling.md) standardised Wyoming; [13](13-conversational-voice.md)/[16](16-architecture-and-implementation-plan.md) standardised Pipecat. Both can't be "the" integration | **Pipecat processors are the integration layer**; they wrap the containers over HTTP/WS. Wyoming survives only inside the command-mode path where rhasspy images (Speech-to-Phrase) already speak it |
| M3 | **AEC named but not specified** | WebRTC AEC3 (`webrtc-audio-processing`) with a WASAPI-loopback reference signal; headset profile ships first; speakers are a tuning story; the guaranteed fallback is a **hardware echo-cancelling speakerphone puck (~$30)** — the unglamorous choice real teams make |
| M4 | **Redaction depended on a model** in places — a leak path if the model misses | Redaction is **deterministic-first** (secret scanners, regex classes for tokens/paths/emails/phones); T1 may *add* candidates, may never be the sole gate. CI fixture: seeded secrets must not survive to a T2 payload |
| M5 | **Boot behaviour undefined** — Docker takes 30–60 s after login | Core answers playbooks **≤ 10 s after logon** at ladder L4, ramps to L1 as the service plane warms; ramp announced, not silent |
| M6 | **No first-run/setup validation** | `tango doctor`: checks GPU driver/CUDA, NPU visibility, Docker, ports, mic devices, Tailscale, bot token, pairing; runs on every service start and before every M-gate |
| M7 | **Gates but no error budgets** — a gate fails a build; nothing governs *lived* reliability | SLOs with budgets, reviewed weekly: wake FP ≤ 2/wk · acted-on misroutes ≤ 2/wk · proactive ≤ 5/day · unverifiable-outcome rate < 10% of side-effecting tasks. **Budget burned → next block is reliability work, not features** (the SRE rule, scaled to one person) |
| M8 | Latency: routing waits for end-of-turn | **Speculative routing on partial ASR** — resolve intent/slots on the interim transcript, commit at end-of-turn. Stretch optimisation, P3+, only if the rig says it's needed |
| M9 | No protocol versioning between core ↔ Host Agent / Android app | Version handshake on connect; refuse mismatches with a clear message. **Single-DB-writer rule stated explicitly** (only `tango-core` touches SQLite) |
| M10 | Playbook `when:` could grow into an accidental DSL | Grammar frozen: `params.X == literal` and `params.X in [..]` only. Anything richer is Python in the engine, not YAML |
| M11 | Hot-mic privacy unstated | Pre-wake ring buffer ≤ 5 s, RAM only, never persisted, never leaves `tango-voice`. Stated in SOUL-adjacent privacy doc |
| M12 | Risk register gaps | Add: **Claude API outage** (→ L3-inverse: local-only degradation, announced) · **API key compromise** (scoped keys, spend alerts = NFR-15) · **owner-time scarcity** as the top schedule risk · pull **Telegram-lite** (text in/out, no confirms) forward to a Phase 1 stretch story — an evening of work for early remote value |

---

## What survives unchanged

The review found no architectural fault: two-plane deployment, effect ledger + claim licensing, playbooks-not-agency, capability freeze + dual labels + Rule of Two, resolver-mediated IDs, model tiering with privacy classes, the degradation ladder, and the phase-gate discipline all stand. The faults were in **sequencing** (C1), **evidence hygiene** (C2, C3), **operational completeness** (C4, C5, H3–H6, M5–M7), and **schedule honesty** (H1, H2).

Fitting, and worth saying plainly: v1.0 of my plan failed in miniature the way the original TANGO spec failed at scale — confident specificity outrunning verification. The difference is that this plan had a review gate before code, which is the entire argument for having one.

---

## Amendment index

| A# | Amendment | 16 § | Status |
|---|---|---|---|
| A1 | Injection fixtures co-located with ingesting features (P3/P4 gates) | 14.2 | **patched inline** |
| A2 | Whisper-on-NPU primary; Parakeet demoted to experiment; Hinglish stratum | 5.2, 5.3, 11.1, 14.2 | **patched inline** |
| A3 | Golden set split corpus/holdout; gates on holdout | 15 | **patched inline** |
| A4 | Per-resource locks; single-writer; queue visibility | 6 (Ledger) | **patched inline** |
| A5 | Two-tier CI (Actions + `tango ci`) | 15 | **patched inline** |
| A6 | Focus-day sizing; P90 ≈ 10 wks; PO-time column | 2, 14.1, 14.2 | **patched inline** |
| A7 | NFR-2 split (first-audio w/ backchannels vs substantive); latency rig S3.8 | 3.4, 14.2 | **patched inline** |
| A8 | Contextualizer component + sticky session labels | 5.3, 14.2 | **patched inline** |
| A9 | Notification Manager component + budgets | 5.3 | **patched inline** |
| A10 | Phone-native time-critical events | 3.3 FR-P5 | **patched inline** |
| A11 | Android staged 5b/5c | 14.2 | **patched inline** |
| A12–A23 | M1–M12 | various | normative here |

Doc 16 document-control now reads v1.1 and points here.
