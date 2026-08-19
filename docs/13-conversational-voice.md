# 13 — Conversational Voice & The Tango Voice

> *"Voice chatting with my assistant, like calling him. Search on Chrome and read the output out loud. And a good lifelike voice — like Iron Man."*

This is a real shift, not a feature request. **Voice stops being an input method and becomes the interface.** Everything below is designed for your machine: **RTX 5060 8 GB · Core Ultra 7 NPU · 24 GB RAM.**

---

## 1. What actually changes: command mode → conversation mode

| | Command mode (docs 10) | **Conversation mode (this doc)** |
|---|---|---|
| Shape | Wake word → one utterance → act → short reply | Continuous session, multi-turn, like a phone call |
| Turn-taking | Silence timer | **Semantic turn detection** |
| Interruption | Not possible | **Barge-in** — you cut it off mid-sentence, it stops instantly |
| Pipeline | Batch: record → transcribe → think → speak | **Streaming, all four stages, overlapping** |
| Echo | Not an issue | **AEC required** — or it hears its own voice and interrupts itself |
| Latency target | < 1.2 s to confirmation | **< 500 ms turn gap** |

Both stay. `"Tango, set an alarm for 7"` should never enter conversation mode — it's one shot, sub-second, no LLM. But `"Tango, why is the API down?"` opens a session you can talk inside.

---

## 2. The latency physics — the number that decides whether it feels alive

From the 2026 voice-agent literature:

| | |
|---|---|
| **Human conversational turn gap** | **200–300 ms** |
| Most voice agents in production | **800–1500 ms** (they wait for VAD silence) |
| **Semantic turn detection (audio + text)** | **~300 ms**, without cutting you off mid-thought |

That gap between 300 ms and 1500 ms is the entire difference between "talking to Tango" and "issuing commands to a machine that lags."

**Silence-based VAD cannot get there.** If you wait 700 ms of silence to be sure you've finished, you've already lost. If you wait 200 ms, it interrupts every time you pause to think. **Semantic turn detection** — a small model that judges from both audio prosody *and* partial transcript whether the thought is complete — is what closes it. This is now a solved, available component.

### The four things that must be streaming

1. **Streaming ASR** — incremental transcription while you're still speaking, not after
2. **Streaming LLM** — first tokens out before the whole answer is formed
3. **Streaming TTS** — start speaking on the first clause, not the last
4. **Continuous VAD** — listening *even while Tango is talking*, which is what makes barge-in possible

### AEC is not optional

Echo cancellation removes Tango's own outbound audio from the mic signal. Without it, on laptop speakers, Tango hears itself, VAD fires, and it interrupts its own sentence. **This is the single most common reason home-built voice assistants feel broken**, and it's invisible until you use speakers instead of headphones.

---

## 3. The Tango voice

You asked for lifelike. Here's the honest 2026 state, and it's better news than you'd expect.

| Model | Quality | Size / cost | License |
|---|---|---|---|
| **Chatterbox** (Resemble AI) | **63.75% preference vs ElevenLabs in blind tests** — it wins | GPU, ~2–3 GB | Permissive · English-only · watermarked |
| **Kokoro-82M** | **4.5 MOS**, 17% CER — beat every proprietary model in that benchmark | **82M, CPU-fast, ~2–3 GB RAM** | Apache-2.0 · 54 preset voices · no cloning |
| **Higgs Audio V2** | Best overall naturalness | ~5.8B — too heavy here | Apache-2.0 |
| Orpheus 3B | Very natural, emotion tags | ~2 GB @ Q4 | Permissive |
| XTTS-v2 | 17 languages, clone from ~3 s | ~2 GB | CPML — non-commercial |
| ElevenLabs / Cartesia | Premium, 40–150 ms TTFA | Cloud, paid | — |

**The community's dominant 2026 pattern is stacking, not picking.** Take that seriously — it's exactly right for 8 GB of VRAM.

### The three-layer voice strategy

**Layer 1 — Pre-rendered, premium quality, zero runtime cost.**
Render your ~200 most common utterances **once, offline**, with the best engine available (Chatterbox, or a paid API — it's a one-time cost, not per-use). Ship them as WAVs.

> "Yes sir." · "Done." · "One moment." · "Starting optiresume." · "All four sites are healthy." · "I couldn't confirm that." · "Alarm set."

This covers roughly **80% of everything Tango ever says**, at **premium quality and ~50 ms latency**. It's the single highest-leverage decision in the whole voice design — you get a better-than-ElevenLabs voice on most interactions while spending zero VRAM and zero milliseconds.

**Layer 2 — Kokoro on CPU, for novel prose in real time.**
Diagnoses, search readbacks, anything generated. 4.5 MOS is genuinely good, it costs **zero VRAM** (leaving all 8 GB for the model), and it streams.

**Layer 3 — Premium cloud TTS, optional, for long-form.**
When you ask it to read an article and quality matters more than privacy. Explicitly opt-in, never for `LOCAL_ONLY` content.

Layers 1 and 2 use the *same voice identity* so it sounds like one assistant — pick a Kokoro preset first, then render Layer 1 with a Chatterbox clone of that same preset's output. Consistency matters more than peak quality.

### On "like Iron Man"

Two different asks hiding in one phrase:

- **The character** — calm, measured, unhurried, faintly dry, British-adjacent, never eager. **Fully achievable** and mostly *not* a TTS problem: it's `SOUL.md` ([08](08-openclaw-and-tango.md) §5) plus voice selection plus never rushing the delivery. This is where the Jarvis feeling actually lives.
- **The specific actor's voice** — that's cloning a real person, which I'd steer away from: it's the one part of this with genuine legal and ethical exposure, and it makes Tango a knockoff instead of a character.

Better: build **Tango's own voice** with that character. Pick a Kokoro British male preset, tune pacing and pitch, clone it into Chatterbox for the premium layer, and put the personality in `SOUL.md`. You'll end up with something more distinctive than an impression — and it'll be *yours*.

---

## 4. Reading things aloud — the part everyone forgets

> *"Search on Chrome and read the output out loud."*

Two separate problems.

### 4.1 The search itself — don't drive Chrome

For *search and read back*, driving a real browser is the wrong tool: slow, brittle, and unnecessary. Use **SearXNG → fetch → Docling** — faster, cleaner, verifiable, and already in the stack ([12](12-docker-stack-and-tooling.md)).

**Real Chrome via CDP earns its place only for tasks needing your logged-in session** — something behind your own auth. And that's the highest-risk path in the whole system: untrusted web content plus your live sessions is the **Rule of Two** violation in its purest form. Hard-gated, week 6+, never from a voice-initiated task.

### 4.2 The readback — web text is not speakable

This is the component people skip and then wonder why their assistant sounds terrible. A web page read verbatim is unlistenable: URLs, markdown, citations, parentheticals, 40-word sentences, "1,234,567".

Tango needs a **spoken-form renderer** — a deterministic transform between text and TTS:

| Rule | |
|---|---|
| Strip URLs, markdown, footnotes, brackets | Never speak `[3]` or `https://` |
| Split into ≤ 20-word sentences | Long sentences are unfollowable by ear |
| Expand numbers, dates, units naturally | "one point two million", not "1,200,000" |
| **Lead with the answer**, then detail | You can't skim audio |
| **Cap at ~40 seconds**, then offer more | "That's the gist — want the details?" |
| Mark uncertainty aloud | "I couldn't confirm this one" |

**And it stays claim-licensed** ([ADR-004](04-decisions.md)). Spoken output has *more* authority than text, not less — you can't re-read it, you can't see the hedge. The verb rules apply harder here.

---

### 4.3 Facts are not actions — and they need a different honesty rule

A question like *"what's RDR2's protagonist called?"* exposes a real gap in the architecture as written.

Everything in [02](02-architecture.md) verifies **actions**: did the container start, did the message get an ID, does the process exist. Every one has a postcondition you can independently check.

**A fact has no postcondition.** There is no world state to probe for "who is the protagonist." So claim licensing, as specified, simply doesn't apply — and that's exactly the gap where a model quietly makes something up.

**The analogue for facts is citation.** Every factual answer must fall into one of three states, and Tango says which:

| State | What it means | How it's spoken |
|---|---|---|
| **`GROUNDED`** | Answer came from a retrieved source Tango can name | "Arthur Morgan." *(source available on request)* |
| **`RECALLED`** | Answer came from the model's own weights, unretrieved | **"From memory — Arthur Morgan. Want me to check?"** |
| **`CONFLICTED`** | Sources disagree, or the model disagrees with the source | "Sources differ — here's what each says." |

**The rule:** `RECALLED` answers must be *audibly* marked. Not a footnote, not a hedge in a UI you're not looking at — a spoken prefix. In audio you cannot see a citation, so the marking has to be in the sentence itself.

**Default routing for factual questions: search first.** It costs ~1 second and converts `RECALLED` into `GROUNDED`. Speed is not worth being confidently wrong — that's the same trade the whole ledger exists to make, applied to knowledge instead of effects.

Two refinements worth having:
- **Snippet-first.** Search results usually contain the answer in the snippet. Answer from it, skip fetching the page, save ~600 ms.
- **`CONFLICTED` is a first-class outcome**, exactly like `UNVERIFIABLE`. "I found two different answers" is a complete, honest response.

---

## 5. Your hardware, assigned

**RTX 5060 8 GB · Core Ultra 7 NPU · 24 GB RAM.**

### VRAM (8 GB — the binding constraint)

| | |
|---|---|
| Qwen3 8B, Q4_K_M | ~5.0 GB |
| KV cache @ 8k context | ~1.0 GB |
| **Subtotal** | **~6.0 GB** |
| Headroom | ~2.0 GB |

**Whisper goes on the NPU, not the GPU.** OpenVINO 2026's GenAI Whisper pipeline runs on NPU with no special requirements, and Intel reports up to 3× throughput on transformer workloads when the NPU is used well. Your NPU is otherwise **completely idle** — this is free capability, and it's the reason the 8 GB budget works.

**Kokoro goes on CPU.** 82M params, designed for it, ~2–3 GB RAM, zero VRAM.

**Chatterbox never runs at runtime** — it's the offline pre-render tool for Layer 1. Load it, render 200 clips, unload it.

Result: **GPU does one job (the model), NPU does hearing, CPU does speaking.** Nothing contends.

### RAM (24 GB)

| | |
|---|---|
| Windows + browser + editor | ~7 GB |
| WSL2 / Docker cap | **10 GB** ← set this explicitly |
| Native Tango (core + agent + voice + Kokoro) | ~4 GB |
| Headroom | ~3 GB |

```ini
# %UserProfile%\.wslconfig
[wsl2]
memory=10GB
processors=8
```

24 GB works, with one caveat: **don't run the full 13-container stack and heavy dev work simultaneously.** Weeks 0–3 only need 2–7 containers, which is comfortable.

---

## 6. Laptop + phone as one ecosystem

One brain on the laptop. Multiple surfaces. Shared conversation state.

| | Laptop | Phone |
|---|---|---|
| **Invoke** | Wake word (hot mic, plugged in) or hotkey | **Assist gesture** — long-press power, < 300 ms, lock screen, zero battery ([07](07-always-on.md) §2.2) |
| **Audio** | Native capture, AEC, speakers | Native capture, **streamed to laptop over WebRTC** |
| **Where thinking happens** | Local | Laptop, over Tailscale |
| **Async / text** | Local UI | Telegram |
| **Latency added** | — | ~10–30 ms on LAN, ~50–100 ms on cellular — both fine |

**Continuity is the thing that makes it feel like one assistant:** start a conversation at the desk, walk away, pick it up on the phone mid-thread. The ledger and conversation state already live in one SQLite file, so this is nearly free — it just needs the device arbitration from [07](07-always-on.md) §1.5 (*the device you spoke into replies; proactive messages go to `active_device`*).

---

## 7. The framework: Pipecat

Don't hand-roll the streaming pipeline. Two serious open-source options:

| | Verdict |
|---|---|
| **Pipecat** (Daily.co) | ✅ **Pick this.** Python. Models a voice agent as a pipeline of frame processors — STT → LLM → TTS → tools → memory. Fine-grained control over conversation flow, wide provider plugins, fully self-hosted. Matches how Tango is already built. |
| **LiveKit Agents** | WebRTC media server + Agents SDK, Apache-2.0, room model, native SIP/telephony. Infrastructure-first — better if you needed multi-participant or real phone numbers. |

**Use Pipecat for the pipeline, WebRTC for phone↔laptop transport.** Pipecat supports WebRTC transports, so the phone joins as a participant and you inherit AEC, jitter buffering and network adaptation instead of writing them.

**Critical integration rule:** Pipecat's "LLM" stage is **not** a raw model — it's **Tango's router**. The pipeline hands over a transcript; Tango decides Alexa-half or agent-half, runs playbooks through the ledger, verifies, and returns claim-licensed text for TTS. Pipecat handles *audio*. It never handles *authority*.

---

## 8. Revised plan

Voice was Week 2 as a feature. It's now the interface, and it spans two weeks.

| Week | Was | Now |
|---|---|---|
| 0 | Spine (regex router, ledger, verification) | **unchanged** — still first, still no AI |
| 1 | Ten playbooks + eval | **unchanged** |
| 2 | Voice | **Voice I — command mode.** Wake word, Speech-to-Phrase, Whisper on NPU, Kokoro, **Layer-1 pre-rendered pack**, `SOUL.md` |
| 3 | Diagnostics | **Voice II — conversation mode.** Pipecat, streaming ASR/LLM/TTS, **AEC**, semantic turn detection, barge-in, spoken-form renderer |
| 4 | Remote / PWA | **Diagnostics** (now with voice, which is where it shines) |
| 5 | Standing auths | **Phone ecosystem** — assist-gesture app, WebRTC audio, Telegram, device arbitration |
| 6 | Connectors | **Standing auths + undo**, then connectors |
| 7+ | — | Hardening, MCP, Chrome-with-session (hard-gated) |

Seven weeks instead of six. Voice conversation is worth the extra week — it's the thing you actually asked for.

---

## 9. Honest limits

| | |
|---|---|
| **~300–500 ms turn gap** | Achievable with semantic turn detection and streaming. Not 200 ms — that's still ahead of the field. |
| **Agent-half responses take 1.5–3 s** | Real tool calls take real time. Covered by a pre-rendered *"One moment"* — which is why Layer 1 matters more than it looks. |
| **Barge-in needs AEC, and AEC needs tuning** | Expect a day of fiddling with mic gain and speaker placement. Headphones work perfectly from day one; speakers need work. |
| **Chatterbox is English-only and watermarked** | Fine for pre-rendering. Note it. |
| **Hot mic is desktop-only** | Android background mic is still the wall ([07](07-always-on.md) §3.2). The assist gesture is the answer, and at 300 ms you won't feel the difference. |
| **Long readbacks will still feel long** | Audio has no skim. That's why the 40-second cap and "want the details?" exist. |

---

## 10. What this actually gets you

```
You:    "Tango."
Tango:  "Yes sir."                            ← pre-rendered, ~50 ms, premium voice
You:    "What's Anthropic's latest on prompt injection?"
Tango:  "One moment."                          ← pre-rendered, covers the search
        [ SearXNG → fetch → Docling → summarise ]
        "They published on capability-based defences — the idea is the
         agent's permitted tools get frozen before untrusted content is
         read. Two other groups report near-total blocking on the standard
         benchmark. Want the details, or shall I save it to your docs?"
You:    "Save it—"                             ← you interrupt; it stops mid-word
Tango:  "Saved to Docs, with sources."
```

No wake word repeated. No app opened. You interrupted it and it stopped. It read you a summary, not a web page. And it didn't say "saved" until the file existed.

**That's the thing you asked for.**
