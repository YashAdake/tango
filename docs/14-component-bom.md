# 14 — The Bill of Materials

Every component, chosen on current evidence. **Target: Core Ultra 7 · RTX 5060 8 GB · 24 GB RAM · Docker Desktop on Windows.**

One framing note before the tables, then no more of it: **"best" here means best-for-this-machine-and-this-job, not most.** Several picks below are deliberately *smaller* than the state of the art, because a 5.33% WER model that adds 400 ms to every turn is worse than a 6% WER model that adds 40 ms. Where I've chosen the faster thing over the more accurate thing, the numbers are shown.

---

## 1. The complete BOM

| Layer | **Pick** | Why it wins | Runner-up |
|---|---|---|---|
| **Local LLM** | **Qwen3.5-9B, Q4_K_M** | Best model that fully fits 8 GB — **32K context in 6.96 GB**, entirely in VRAM at every tested context size | Qwen3-8B · Gemma 4 12B (multimodal) |
| **Cloud LLM** | **Claude Opus 5** (`claude-opus-5`) | 1M context, $5/$25 per MTok, adaptive thinking, five effort levels, `strict` tools | Sonnet 5 ($3/$15) for volume · Haiku 4.5 ($1/$5) for triage |
| **Inference runtime** | **Ollama** (llama.cpp under it) | Simplest lifecycle for single-user; llama.cpp gives native GBNF | vLLM 0.17+ (works on Blackwell w/ CUDA 13) if you ever need concurrency |
| **Constrained decoding** | **XGrammar** | **< 40 µs/token**; 3× faster than baseline on JSON Schema, **100× on CFG** | llguidance (OpenAI credited it) · **avoid Outlines** — 40 s–10 min compile on complex schemas |
| **Agent framework** | **PydanticAI (V2)** | Typed validated outputs as a first-class concept, model-agnostic, and **`TestModel`/`FunctionModel`** — the Week-0 "fake brain" trick as a library feature | LangGraph if you ever need durable graph state |
| **STT** | **Parakeet TDT 1.1B** | **RTFx > 2000** — 6.5× faster than Canary-Qwen. On a live voice turn, speed *is* accuracy | Canary-Qwen 2.5B (best English acc., RTFx 418) · Granite Speech 4.1 2B (SOTA **5.33% WER**) · Whisper v3 Turbo (multilingual) |
| **STT accelerator** | **Intel NPU via OpenVINO 2026** | Frees the GPU entirely. NPU otherwise idle. Up to 3× throughput on transformer workloads | CPU |
| **Fixed-command STT** | **Speech-to-Phrase** | Constrained vocabulary → far faster and more accurate on your ~30 playbook commands | — |
| **TTS (runtime)** | **Kokoro-82M** | **4.5 MOS**, 17% CER — beat every proprietary model in its benchmark, at 82M params on **CPU**, zero VRAM | Orpheus 3B |
| **TTS (pre-render)** | **Chatterbox** | **63.75% preference vs ElevenLabs** in blind tests. Offline only — render 200 clips, unload | Higgs Audio V2 (best naturalness, 5.8B — too heavy live) |
| **Wake word** | **livekit-wakeword** | **~100× fewer false positives than openWakeWord.** ONNX, custom training. Directly kills the false-positive tax | openWakeWord · Porcupine (commercial) |
| **Turn detection** | **Smart Turn v3** (Pipecat) | Audio-native — reads the **waveform**, not the transcript, so it doesn't wait for ASR. Open weights + training data | LiveKit Turn Detector v1 (**−39% interruptions**) · TurnSense 1.1 |
| **Voice pipeline** | **Pipecat** | Python, frame-processor pipeline, self-hosted, WebRTC transport. Ships Smart Turn | LiveKit Agents (better if you needed SIP/telephony) |
| **Vision (on demand)** | **Qwen3-VL-4B, Q4** | ~3.5 GB — the 6–8 GB sweet spot. **GUI grounding**: reads a screenshot, identifies buttons and fields | Qwen3-VL-8B (~6 GB, 69.6 MMMU / 96.1 DocVQA) |
| **Model gateway** | **LiteLLM** | One OpenAI-compatible endpoint over Ollama + Claude; budgets, fallbacks, retries as *config* | — |
| **Store** | **SQLite (WAL)** | ACID, no daemon, survives Docker being down | — |
| **Embeddings** *(deferred)* | nomic-embed-text (137M, 274 MB, 8K ctx) | Only if [ADR-005](04-decisions.md) is ever reversed | Qwen3-Embedding-0.6B (**70.7 MTEB**, ~1.5 GB) |
| **Connectors** | **n8n** (tool provider only) | Hundreds of OAuth-handled nodes + cron | — |
| **Tool protocol** | **MCP**, behind a gateway | 9,652 servers. Hardened per [12](12-docker-stack-and-tooling.md) §3 | — |
| **Search** | **SearXNG** | Self-hosted metasearch, no key, no tracking | Claude's `web_search_20260209` server tool |
| **Browser** | **Playwright** | Most-installed MCP server (30k+ ★) | — |
| **Doc parsing** | **Docling** | Best PDF layout handling | Apache Tika |
| **Tracing** | **Arize Phoenix** | 1 container, OTel-native | Langfuse (better, 5 containers) |
| **Transport** | **Tailscale** | Never a public port | — |
| **Async surface** | **Telegram Bot API** | Push, inline keyboards, voice notes, every device | — |
| **Win automation** | **pywinauto** (UIA backend) | Native only — session 0 | FlaUI |

---

## 2. The picks that changed from earlier docs

Six upgrades, each with a reason worth stating.

### 2.1 Qwen3.5-9B replaces Qwen3-8B
Benchmarks on exactly your VRAM tier put Qwen3.5-9B at Q4_K_M as the best 8 GB model "by a significant margin" — and critically, **it's the only one that stays fully in VRAM at 32K context (6.96 GB)**. Partial CPU offload is what makes local models feel slow; avoiding it matters more than parameter count.

### 2.2 Parakeet TDT replaces Whisper on the live path
Parakeet TDT 1.1B runs at **RTFx > 2000** — 6.5× faster than Canary-Qwen, and it ranks 23rd on accuracy rather than 1st. **Take that trade.** On a live conversational turn, a 60 ms transcription that's 94% accurate beats a 400 ms transcription that's 95% accurate, because the model corrects small ASR errors from context and the human notices latency far more than a fixed typo.

Keep **Whisper v3 Turbo** available for the accuracy path — dictation, long-form, anything non-conversational — and **Speech-to-Phrase** in front of both for the ~30 fixed commands.

### 2.3 livekit-wakeword replaces openWakeWord
**~100× fewer false positives.** [Doc 07](07-always-on.md) §3.3 argued that the false-positive tax is what gets assistants muted — and it matters more here than for Alexa, because a misfire lands on a system with tool authority. A 100× reduction is the single largest reliability improvement available in the voice stack.

### 2.4 Smart Turn v3 — a component I'd only named generically
It's **audio-native**: it judges turn completion from the raw waveform rather than the transcript, so it doesn't wait on ASR at all. Open weights and training data, and it ships with Pipecat, which we'd already picked. LiveKit's transformer-based detector is the alternative and reports **39% fewer interruptions**.

### 2.5 XGrammar — not just "constrained decoding"
The libraries are not interchangeable. **XGrammar: < 40 µs/token, 3× on JSON Schema, 100× on CFG.** **Outlines pioneered the approach but takes 40 seconds to 10+ minutes to compile complex schemas** — which, in a voice loop, is fatal. Specify XGrammar explicitly.

### 2.6 PydanticAI — and it hands you the Week-0 trick for free

This is the best find of the sweep. PydanticAI is model-agnostic with validated structured output built into the framework, and its testing story is `pytest` + **`TestModel`** + **`FunctionModel`** + `Agent.override`.

`TestModel` is *the "build the spine with a fake brain" pattern as a library feature.* [Week 0](03-roadmap.md) — build and prove the ledger, verification and claim licensing with a deterministic stand-in, then swap the real model in behind an identical interface — is a first-class supported workflow, not something to hand-roll.

**Use PydanticAI as the typed model-call layer only.** It does not own the ledger, the policy gate, the router, or the renderer. Those stay Tango's.

---

## 3. Claude tier — the details that matter

Verified against the current API reference, not memory.

**Model:** `claude-opus-5` — 1M context, **$5 / $25 per MTok**.

| Feature | How to use it in Tango |
|---|---|
| **Adaptive thinking** | `thinking: {type: "adaptive"}`. **`budget_tokens` is rejected with a 400** on Opus 5 — if you recall that parameter, it's stale |
| **Effort levels** | `output_config: {effort: ...}` — `low` for routing/triage, `high` for normal work, **`xhigh` for diagnosis** (best for agentic tasks), `max` when correctness beats cost |
| **Strict tools** | `strict: true` on the tool definition + `additionalProperties: false`. **Guarantees `tool_use.input` validates exactly** — the cloud-tier equivalent of XGrammar |
| **Prompt caching** | Tango's system prompt + tool list + `SOUL.md` is a stable prefix. Cache it. Order is `tools` → `system` → `messages`; keep volatile content after the last breakpoint. Verify with `usage.cache_read_input_tokens` |
| **Mid-conversation system messages** | Append `{"role": "system", ...}` to `messages[]` (Opus 5, no beta). The API reference describes this as **the prompt-injection-safe operator channel** — see §3.1 |
| **Fast mode** | `speed: "fast"` + beta `fast-mode-2026-02-01`. **Up to 2.5× output tokens/sec** at $10/$50. For voice, worth it on the agent half |
| **Server web search** | `web_search_20260209` with dynamic filtering — an alternative to SearXNG when you want citations handled |
| **Batch API** | **50% cost**, async. Use for eval runs and offline pre-render passes |
| **Haiku 4.5** | $1/$5 — the cheap tier for triage and classification when the local model is cold |

*Also note: assistant prefill returns a 400 on Opus 5. Use `output_config.format` for structured output instead.*

### 3.1 The security find

Mid-conversation system messages are documented as **the prompt-injection-safe operator channel** — and that maps exactly onto Tango's trust boundary. Policy reminders, capability-freeze declarations and untrusted-content markers go in as `{"role": "system"}` entries *inside* `messages[]`, rather than being concatenated into a user turn where injected content sits at the same level.

It also **preserves the cached prefix**, so the security property is free rather than costing a cache invalidation on every policy update. Adopt it for the T2 path.

---

## 4. Resource allocation

### VRAM — 8 GB, the binding constraint

| Mode | Allocation |
|---|---|
| **Normal** | Qwen3.5-9B Q4_K_M @ 32K ctx — **6.96 GB** · ~1 GB free |
| **Vision** (on demand) | Unload the 9B → Qwen3-VL-4B Q4 (~3.5 GB) → reload. Or run the 4B text model permanently and keep 4 GB free |
| **STT** | **NPU** — 0 GB VRAM |
| **TTS** | **CPU** — 0 GB VRAM |
| **Pre-render** (offline) | Chatterbox, LLM unloaded |

**The whole design exists to keep one model resident.** Model swapping costs 3–8 seconds, which destroys a conversation. NPU for hearing and CPU for speaking is what makes that possible.

### RAM — 24 GB

| | |
|---|---|
| Windows + browser + editor | ~7 GB |
| **WSL2 / Docker cap** | **10 GB** — set explicitly |
| Native (core + agent + voice + Kokoro) | ~4 GB |
| Headroom | ~3 GB |

```ini
# %UserProfile%\.wslconfig
[wsl2]
memory=10GB
processors=8
```

---

## 5. Deliberately excluded

Naming these matters as much as the picks — each was considered and rejected for a stated reason.

| Excluded | Why |
|---|---|
| **TensorRT-LLM** | Fastest on NVIDIA, but the complexity is for multi-user serving. Ollama gets ~85% of it at a fraction of the ops |
| **vLLM** | Works on Blackwell now (0.17+, CUDA 13), but PagedAttention and continuous batching solve *concurrency*, which you don't have |
| **Outlines** | 40 s–10 min schema compile. Disqualifying in a voice loop |
| **Granite Speech 4.1 / Canary-Qwen** | SOTA accuracy, ~6.5× slower. Wrong trade for live conversation; keep as a batch-transcription option |
| **LangGraph / CrewAI / AutoGen** | Durable graph state and multi-agent orchestration solve problems Tango doesn't have. Playbooks *are* the graph |
| **Qwen3-Embedding / any vector DB** | [ADR-005](04-decisions.md) — no RAG until a query demonstrably fails grep + git |
| **Langfuse** | Better than Phoenix, needs 5 containers and ~3–4 GB. Not at 24 GB |
| **Dify / Agent Zero / n8n AI Agent nodes** | Competing unverified brains |
| **XTTS-v2** | CPML licence, and Chatterbox beats it on quality |

---

## 6. Where each piece lands

| Week | Added |
|---|---|
| **0** | SQLite ledger · PydanticAI + `TestModel` · Ollama + Qwen3.5-9B · LiteLLM · Claude Opus 5 (`low`/`high` effort) · XGrammar |
| **1** | Playbook engine · resolvers · eval harness |
| **2** | livekit-wakeword · Speech-to-Phrase · Parakeet on NPU · Kokoro · Chatterbox pre-render · `SOUL.md` |
| **3** | Pipecat · Smart Turn v3 · AEC · streaming · spoken-form renderer |
| **4** | Diagnostics (Opus 5 @ `xhigh`) · Phoenix · prompt caching |
| **5** | Telegram · Tailscale · Android assist-gesture app |
| **6** | n8n · SearXNG · Docling · MCP gateway |
| **7+** | Playwright · Qwen3-VL-4B · AgentDojo · hardening |

---

## 7. The two numbers to keep in view

**6.96 GB** — Qwen3.5-9B at 32K context. Everything else in this document is arranged so that number never has to move.

**< 40 µs/token** — XGrammar's constrained-decoding overhead. That's what makes "the local model's output is always schema-valid" a *guarantee* rather than a retry loop, which is what lets the Alexa half stay under a second.

Every other pick here is replaceable. Those two are load-bearing.
