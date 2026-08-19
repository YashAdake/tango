# 08 — OpenClaw, and What It Means for Tango

> "Do you know about OpenClaw / Clawdbot, how they work, their applications — and what do you think from Tango's POV?"

**Yes. And you should read this before Week 0, because someone already built the spec you handed me, ~60,000 people starred it, and then the security record came in.**

That's not a reason to stop. It's the most useful free lesson this project will ever get.

---

## 1. What it is

**Clawdbot → Moltbot → OpenClaw** — the same project, renamed twice (the first rename under trademark pressure over the Claude-adjacent name). Launched November 2025 by Austrian developer **Peter Steinberger**. It hit **60,000+ GitHub stars in 72 hours**, and the phrase people reached for was "the closest thing to JARVIS we've seen."

Which is exactly what you're building.

**What it does:** an open-source, self-hosted AI agent that runs on your own hardware and **lives inside your chat apps**. You text it on WhatsApp or Telegram; it runs shell commands, drives your browser, reads and writes files, manages your calendar, sends email. Bring your own API key, no subscription.

**How it's built** (from [the official architecture docs](https://docs.openclaw.ai/concepts/architecture)):

| Piece | Design |
|---|---|
| **Gateway** | One long-lived daemon owns every messaging surface — WhatsApp (via Baileys), Telegram (grammY), Slack, Discord, Signal, iMessage, WebChat. One gateway per host. |
| **Control plane** | macOS app, CLI, web UI and automations connect over **WebSocket**, default `127.0.0.1:18789`, typed request/response frames validated against JSON Schema |
| **Nodes** | macOS/iOS/Android/headless clients connect with `role: node` and **declare their capabilities and commands** |
| **Auth** | Shared secret or identity-bearing modes (incl. Tailscale); device identity required; **new device IDs need pairing approval** |
| **Skills** | Modular toolsets enabled individually — shell, web search, files, GitHub, Spotify, third-party APIs |
| **Memory** | Plain **Markdown and YAML** under your workspace and `~/.openclaw` — no database |
| **Heartbeat** | A scheduler wakes the agent on an interval so it can act **unprompted** |
| **Safety** | Three stacked layers: container **sandbox**, **tool policy**, and **exec approvals** (allowlist + optional interactive approval). Approvals can only tighten config-derived policy, never loosen it. |

---

## 2. The uncomfortable part: your spec independently re-derived this

Put the TANGO spec's §5 component table next to OpenClaw's architecture:

| TANGO spec (§5) | OpenClaw |
|---|---|
| TANGO Gateway — authenticated API/WebSocket entry point | Gateway — WebSocket, typed frames, JSON Schema |
| Tool Registry — typed defs, schemas, permissions | Skills — modular toolsets, per-tool enablement |
| Policy Engine — authorization, confirmation, rate limits | Tool policy + exec approvals |
| Android Companion — declares capabilities, pairing/revocation | Nodes — `role: node`, declared capabilities, pairing approval |
| Memory Service | Markdown/YAML memory files |
| Connector Layer — Gmail/calendar/GitHub/web | Skills — GitHub, web, calendar, email |
| Device pairing + short-lived tokens (§16) | Device identity + pairing approval |

Near-identical. Which tells you two useful things:

1. **The shape is right.** Gateway + typed tools + policy + nodes is what this problem converges on. Your spec wasn't naive about structure.
2. **Structure was never the hard part.** OpenClaw *has* all of it — sandbox, tool policy, approvals, pairing, capability declaration — and still produced one of the worst agentic security records of 2026. That's the actual lesson.

---

## 3. What happened next — and why it matters more than the architecture

The record, as of mid-2026:

**Credential exposure.** Giskard researchers exploited a deployment in January 2026 and found OpenClaw had already leaked API keys and credentials. Patched in `2026.1.29` (30 Jan 2026). ([Giskard](https://www.giskard.ai/knowledge/openclaw-security-vulnerabilities-include-data-leakage-and-prompt-injection-risks))

**A CVE run:**

| CVE | Class |
|---|---|
| **CVE-2026-25253** | CWE-669 *Incorrect Resource Transfer Between Spheres*, **CVSS 8.8** |
| CVE-2026-24763 | Command injection |
| CVE-2026-26322 | SSRF |
| CVE-2026-26329 | Path traversal → local file read |
| **CVE-2026-30741** | **Prompt-injection-driven code execution** |

**"ClawJacked"** (Oasis Security) — malicious *websites* could brute-force and hijack locally running instances, then silently exfiltrate data **by abusing the agent's own built-in autonomy**. Note the mechanism: the exploit didn't break the agent's permissions. It *used* them.

**And the number that should stop you cold** — SecurityScorecard, February 2026: **40,214 internet-exposed instances**, 35.4% flagged vulnerable. By 9 February: **135,000+ unique IPs across 82 countries, 12,812 exploitable via RCE.**

One hundred and thirty-five thousand people put a shell-capable AI agent on the open internet.

---

## 4. The four lessons, in order of value to you

### Lesson 1 — Defaults *are* the security model

Those 135,000 people are not idiots. They're developers who installed a personal assistant, and "personal assistant" simply does not trigger threat-modelling in anyone's head. The bind default is `127.0.0.1`, which is correct — but the moment someone wants it from their phone, the obvious move is to change the bind address, and nothing in that moment shouts *"you are now publishing a remote shell."*

**For Tango:** [07-always-on.md](07-always-on.md) already says never open a port, Tailscale only. Elevate that from advice to a **structural refusal** — Tango refuses to bind to anything but loopback or a Tailscale interface, and overriding requires a config-file edit containing an explicit acknowledgement string. Not a warning. A refusal.

### Lesson 2 — CVE-2026-30741 and ClawJacked are the lethal trifecta, in production

[02-architecture.md](02-architecture.md) §3.4 named the exploit condition: **private data + attacker-controlled content + an outbound channel, in one task.** OpenClaw shipped all three composable by default, and CVE-2026-30741 plus ClawJacked are the invoice.

Note especially: ClawJacked exfiltrated data **by abusing autonomy, not by escalating privilege.** The agent did exactly what it was permitted to do. A sandbox doesn't stop that. An allowlist doesn't stop that. An approval prompt barely stops it, because —

### Lesson 3 — Approval prompts decay into rubber stamps

OpenClaw's exec approvals are real and well-designed. But an approval you see thirty times a day stops being a decision and becomes a reflex — which is [F6](01-verdict-and-critique.md) restated as an empirical result rather than a prediction.

**For Tango:** this is the argument for [ADR-007](04-decisions.md) (undo windows + narrow typed standing authorizations instead of confirm-everything) and for the **capability freeze** — computing the permitted tool set *before* untrusted content arrives, so injected instructions cannot widen the aperture whatever the model decides. Freezing is structural; approving is behavioural. Behaviour decays.

### Lesson 4 — Nobody built the honesty layer

Conspicuously absent from OpenClaw's architecture docs, and I went looking: **no verification of outcomes, no confirmation that an action did what it claimed, no distinction between "submitted" and "confirmed."**

Its three safety layers — sandbox, tool policy, approvals — are all **perimeter** controls. They constrain what the agent may *attempt*. Not one constrains what the agent may *claim*.

So OpenClaw can tell you it sent the email when the send failed. That isn't a bug in OpenClaw; it's an entire axis nobody in this space is building on.

**That axis is Tango's.** The effect ledger, the four-valued verification status, and claim licensing ([02](02-architecture.md) §3.2–3.3) are orthogonal to everything OpenClaw does. Tango isn't a worse OpenClaw — it's a different property. And per [06](06-the-jarvis-question.md) §6, it's the property the Jarvis relationship is actually made of.

---

## 5. The genuinely great idea you should steal

Forget the architecture. **OpenClaw's best decision is that it lives in your chat apps** — and it collapses most of [07-always-on.md](07-always-on.md) into nothing.

What a Telegram bot gives you, free, today:

| Doc 07 problem | Telegram's answer |
|---|---|
| Always-reachable from any device | Already true — phone, desktop, web, tablet |
| Push notifications through Doze | Telegram's own push, battle-tested, OEM-proof |
| No persistent socket, no battery drain | Long-polling or webhook, server-side |
| Cross-device conversation continuity | Built in |
| Voice input | Native voice messages → your STT |
| Rich confirmations | **Inline keyboards** — one-tap approve/cancel with callback data |
| File transfer both ways | Built in |
| Auth | Bot token + your user-ID allowlist |
| Message history / audit surface | Free |

That is **Week 3 and Week 4 of the roadmap, deleted** — no PWA, no Web Push infrastructure, no device presence table, no cert pinning, no Tailscale on the common path. And an inline keyboard on your lock screen is a better confirm affordance than anything I sketched.

**Caveats, both real:**

- **Use Telegram, not WhatsApp.** OpenClaw drives WhatsApp through **Baileys**, an unofficial reverse-engineered library — it breaks on protocol changes and risks your number being banned. Telegram has a real, supported Bot API. Not a close call.
- **Messages leave your machine.** Telegram's servers see everything you send Tango, which directly contradicts the `LOCAL_ONLY` privacy class in [ADR-003](04-decisions.md). Clean compromise: Telegram for routine R0/R1; local surfaces (hotkey, CLI, Android assist gesture) for anything classified `LOCAL_ONLY`; and Tango **refuses to discuss `LOCAL_ONLY` material over Telegram at all**. Enforceable rule, not a vague preference.

The assist-gesture app from [07](07-always-on.md) §2.2 is still worth building — it's the low-latency, fully-local path. But Telegram gets you always-on presence **in an evening**.

---

## 6. So: build, fork, or use?

**A — Just run OpenClaw, hardened.** Fastest path to something Jarvis-shaped; you could have it this weekend. Costs: you inherit its CVE stream, you don't own the trust model, and the one property you specifically care about — that it never lies to you — isn't there and can't easily be added from outside.

**B — Fork or wrap it.** Best raw value per hour. Take the channel layer and skills ecosystem, add the ledger and claim licensing on top. Costs: you're maintaining a fork of a fast-moving, security-eventful codebase, and the ledger wants to sit *underneath* tool execution — surgery on the part that changes most.

**C — Build Tango clean, steal the ideas.** Six weeks. You own the trust model completely, you understand every line, the honesty layer is native rather than bolted on. Costs: you rebuild channel plumbing that already exists.

**My read, given this is explicitly your own system and the point is partly the having of it: C, with two amendments.**

1. **Telegram is your primary surface from Week 3.** Not a PWA. Steal the single best idea and skip plumbing you'd have rebuilt for no reason.
2. **Read OpenClaw's source before Week 0** — specifically its exec-approvals and tool-policy code. It's the most mature open implementation of the layer your spec described, written by people who then got the security results in public. A day of reading beats a week of designing.

Treat those 135,000 exposed instances as a permanent design constraint. It's the difference between *"I know about prompt injection"* and *"I have seen what happens at scale when a personal assistant meets the open internet."*

---

## 7. One-line answer

**OpenClaw is your spec, shipped, at scale — and its security record is the empirical proof of the arguments in [01](01-verdict-and-critique.md) and [02](02-architecture.md).** It got everything right except the two things Tango exists for: **structural containment of untrusted content**, and **never claiming an outcome it hasn't verified.** Build those, steal its chat-app surface, skip the rest.

---

## Sources

- [OpenClaw — Gateway architecture (official docs)](https://docs.openclaw.ai/concepts/architecture)
- [OpenClaw — Exec approvals (official docs)](https://docs.openclaw.ai/tools/exec-approvals)
- [Wikipedia — OpenClaw](https://en.wikipedia.org/wiki/OpenClaw)
- [CNBC — From Clawdbot to Moltbot to OpenClaw](https://www.cnbc.com/2026/02/02/openclaw-open-source-ai-agent-rise-controversy-clawdbot-moltbot-moltbook.html)
- [Giskard — Data leakage & prompt injection risks](https://www.giskard.ai/knowledge/openclaw-security-vulnerabilities-include-data-leakage-and-prompt-injection-risks)
- [IBM X-Force — What OpenClaw reveals about agentic AI security risks](https://www.ibm.com/think/x-force/what-openclaw-reveals-about-agentic-ai-security-risks)
- [Sangfor — From vulnerabilities to supply chain abuse](https://www.sangfor.com/blog/cybersecurity/openclaw-ai-agent-security-risks-2026)
- [DigitalOcean — What is OpenClaw](https://www.digitalocean.com/resources/articles/what-is-openclaw) · [7 security challenges](https://www.digitalocean.com/resources/articles/openclaw-security-challenges)
- [Nebius — Architecture and hardening guide](https://nebius.com/blog/posts/openclaw-security)
