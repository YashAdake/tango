# 12 — The Docker Stack & Tool Catalogue

Everything in Docker Desktop, n8n in the mix, and a full survey of what can plug in.

**Verdict up front: yes, containerise the stack — but three things physically cannot go in Docker on Windows, and one of them is the microphone.** The result is a two-plane design: a small native host plane, and a large containerised service plane.

---

## 1. What cannot be containerised on Windows — the honest constraints

| Capability | Docker Desktop on Windows | Why |
|---|---|---|
| **Microphone / audio in** | ❌ **Genuinely blocked** | WSL2 has no native audio device support — no ALSA, no PulseAudio, no sound-card drivers in the kernel. `/dev/snd` passthrough is a Linux-host feature. Workarounds exist (`usbipd` for USB mics) but they're fragile and single-container-only. |
| **Audio out / TTS playback** | ❌ Same reason | Generate audio in a container, play it natively |
| **Launching GUI apps** | ❌ Structurally impossible | Session 0 isolation — a container cannot open VS Code on your desktop |
| **Window state, clipboard, screen capture** | ❌ | Needs the interactive Windows session |
| **Global hotkey** | ❌ | `RegisterHotKey` is a user-session Win32 API |
| **Diagnosing Docker itself** | ⚠️ **Circular** | If Tango lives in Docker, then when Docker is unhealthy Tango cannot report it — [F13](01-verdict-and-critique.md) |
| **GPU / Ollama** | ✅ **Works well** | Docker Desktop 4.29+ has stable `--gpus all` on the WSL2 backend with `nvidia-container-toolkit`. WSL2 CUDA is ~native Linux speed. |
| **Everything else** | ✅ | n8n, STT, TTS, search, browser, MCP servers, observability |

**→ Two planes.** This isn't a compromise; it's the correct shape given the platform.

```
┌─ HOST PLANE (native Windows, ~3 processes) ─────────────────────┐
│  tango-core       Windows Service · gateway, router, playbooks, │
│                   ledger, renderer, SQLite                       │
│                   ⚠ MUST be native: it diagnoses Docker          │
│  tango-agent      user session · app launch, clipboard, windows  │
│                   ⚠ MUST be native: session 0 isolation          │
│  tango-voice      user session · mic, wake word, VAD, playback   │
│                   ⚠ MUST be native: WSL2 has no audio            │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP / Wyoming over host.docker.internal
┌───────────────────────────▼─────────────────────────────────────┐
│  SERVICE PLANE (Docker Desktop)                                  │
│  ollama · litellm · whisper · kokoro · speech-to-phrase          │
│  n8n · searxng · playwright · mcp-gateway · docling              │
│  phoenix (tracing)                                               │
└─────────────────────────────────────────────────────────────────┘
```

`tango-voice` captures audio natively and streams it to the containerised STT over the **Wyoming protocol** — that works perfectly. Only the *device access* has to be native.

---

## 2. Where n8n genuinely fits — and where it must not

n8n 2.0 is a real asset here. It ships **native AI agent nodes**, an **instance-level MCP server** (public preview since April 2026, CE v2.18.4+), and an official **self-hosted AI Starter Kit** compose template. And you already have `d:\my\n8n`.

### What n8n gives Tango, for free

| | |
|---|---|
| **Connector breadth** | Hundreds of pre-built nodes with OAuth handled — Gmail, Calendar, Telegram, GitHub, Notion, Slack, HTTP. This is the single biggest labour saving available. |
| **Scheduling & triggers** | Cron and webhooks — the heartbeat/proactive-monitoring layer from [09](09-capability-catalogue.md) §1.6, no code |
| **Retry / error branches / wait nodes** | Built-in |
| **Visual capability authoring** | Add an integration without writing Python |
| **SQLite by default in CE** | No Postgres dependency for personal use |

### The rule that keeps it safe

> **n8n is a tool provider. It is never a decision maker.**

This matters because n8n has two properties that are dangerous in combination with an LLM:

1. **It holds OAuth credentials for everything.** One credential store, every service. If the model can trigger arbitrary n8n workflows, a successful injection reaches all of them at once — a textbook **Rule of Two** violation ([11](11-research-findings.md) Finding 3) in a single container.
2. **n8n workflows report node success, not postcondition truth.** A node returning 200 is exactly the false-success signal that accounts for [45–76% of agent failures](11-research-findings.md). n8n has no verification layer and isn't trying to have one.

**So the integration contract is:**

| Rule | |
|---|---|
| Each n8n workflow = **one Tango tool**, registered Tango-side with `risk_class`, `verifier`, `sink_idempotency`, integrity label | Verification stays in Tango's ledger |
| Tango calls workflows by **fixed webhook ID with typed inputs** — never by name the model chose | An injection can't select a workflow |
| **Playbook steps invoke n8n. The model never does.** | Capability freeze holds |
| **Do not use n8n's AI Agent nodes** for Tango's own reasoning | That's the unbounded agency [ADR-001](04-decisions.md) exists to avoid — two competing brains, neither verified |
| n8n never calls back into `tango-core`'s privileged API | One-way dependency |
| Bind n8n to the Docker network only — **no host port** | Reachable by Tango, not by your browser from outside |

Used this way n8n is excellent: it's the **connector and scheduler layer**, and Tango keeps the brain, the policy and the truth.

---

## 3. MCP — enormous value, genuinely alarming supply chain

[Doc 11](11-research-findings.md) Finding 7 said become an MCP client. That still holds, but the security picture is worse than I implied and needs its own handling.

**The data:**
- **30+ CVEs** filed against popular MCP servers in **January–February 2026 alone**
- Systematic scans found security findings in roughly **66% of popular servers**
- April 2026: OX Security disclosed an **RCE in an MCP server with 150M+ downloads**
- OWASP codified **tool poisoning as MCP03:2025**

**And the mechanism that matters most:**

> A malicious server can hide instructions in **tool descriptions** — invisible in the UI, fully visible to the model. **A poisoned tool does not need to be called. Its description alone can direct the model to exfiltrate keys or config.**

That is a direct hit on the capability-freeze design: MCP's discovery protocol **auto-merges third-party metadata into the model's context**.

### How Tango runs MCP safely

| Control | |
|---|---|
| **MCP tool descriptions are `UNTRUSTED` integrity by definition** | They're third-party authored text entering model context. Label them per [FIDES](11-research-findings.md) and let the labels propagate. |
| **No auto-discovery, ever.** Servers are enabled one at a time by explicit config | The discovery protocol is the attack surface |
| **Read every tool description before enabling** | It's the payload location |
| **Pin image digests, not tags** | Rug-pull defence |
| **Run each MCP server in its own container**, no host mount beyond an explicit allowlist path, no host network | Blast radius |
| **A single `mcp-gateway` is the only thing that talks to them** | One control point, one owner |
| **Tango-side registration required** — every MCP tool gets a risk class, verifier and idempotency flag before it's callable | MCP carries none of this |
| Prefer **vendor-maintained servers** (GitHub, Microsoft Playwright, Supabase, Stripe, Notion, Sentry, Cloudflare) and the Anthropic reference set | Smaller supply-chain surface |

**Anthropic reference servers** (`modelcontextprotocol/servers`): Everything, Fetch, Filesystem, Git, Memory, Sequential Thinking, Time. Start there.

**MCP replaces connector plumbing. It does not replace the policy layer.**

---

## 4. The stack, service by service

### 4.1 Core — Week 0/1 (start here, two containers)

| Service | Image | Port | Why |
|---|---|---|---|
| **ollama** | `ollama/ollama` | 11434 | Local models. GPU via `nvidia-container-toolkit`. Qwen3 7–8B. |
| **litellm** | `ghcr.io/berriai/litellm` | 4000 | **One OpenAI-compatible endpoint** for Ollama + Claude + anything else. Key management, fallbacks, per-model budgets, retries. Implements [ADR-003](04-decisions.md)'s T1/T2 routing as config instead of code. |

That's it for Week 0. Two containers.

### 4.2 Voice — Week 2

| Service | Image | Port | Why |
|---|---|---|---|
| **wyoming-whisper** | `rhasspy/wyoming-whisper` | 10300 | STT fallback (faster-whisper) |
| **speech-to-phrase** | `rhasspy/wyoming-speech-to-phrase` | 10301 | **Constrained STT for your ~30 playbook commands.** Much faster and more accurate on a known vocabulary — the biggest latency win available ([11](11-research-findings.md) Finding 6) |
| **kokoro** | `ghcr.io/remsky/kokoro-fastapi` | 8880 | TTS. 82M params, Apache-2.0, CPU-fast, 54 voices. The Jarvis voice. |

Wake word (`openWakeWord`) and mic capture run **natively** in `tango-voice`. Routing: Speech-to-Phrase first → Whisper only on no-match.

### 4.3 Capability — Weeks 3–5

| Service | Image | Why |
|---|---|---|
| **n8n** | `n8nio/n8n` | Connectors + scheduling, per §2. SQLite backend, no host port. |
| **searxng** | `searxng/searxng` | Private metasearch. No API key, no tracking, no rate limit. The `search_web` tool. |
| **playwright** | `mcr.microsoft.com/playwright` | Headless browser for fetch/render. Microsoft's Playwright MCP is the most-installed MCP server (30k+ stars). |
| **mcp-gateway** | custom thin service | Single controlled entry to all MCP servers, per §3 |
| **docling** | `ghcr.io/docling-project/docling-serve` | PDF/docx/pptx → structured text. Better than Tika for layout-heavy PDFs. |

### 4.4 Observability — Week 4+

| Option | Weight | Verdict |
|---|---|---|
| **Arize Phoenix** | **1 container**, OTel-native | ✅ **Pick this.** Right size for one user. Elastic License 2.0 (source-available, not OSI). |
| **Langfuse** | **4–5 containers** (ClickHouse + Postgres + Redis + MinIO), ~3–4 GB RAM | MIT, more capable — prompt management, datasets, evals, scoring. Acquired by ClickHouse Jan 2026; self-hosting unchanged. **Overkill for one user; revisit if the eval harness outgrows files.** |
| Grafana + Prometheus + Loki | 3 containers | ❌ Skip. Structured logs plus one `trace_id` is enough at this scale. |

### 4.5 Deferred — add only on evidence

| Service | Add when |
|---|---|
| **qdrant** / pgvector | You can name a query that grep + git + agentic search fails ([ADR-005](04-decisions.md)) |
| **postgres** | Something actually needs it. n8n CE and Tango both use SQLite. |
| **redis** | You have a queue problem you can measure |
| **home-assistant** | You do smart-home. Also the reference voice pipeline if you want to crib it. |
| **open-webui** | Handy for eyeballing models during Week 0–1; delete after |
| **minio** | Offsite backup target beyond a file copy |
| **traefik / caddy** | Tailscale Serve already terminates TLS. Skip. |
| **infisical / vault** | Windows DPAPI + a `.env` outside the repo is right for one user |

### 4.6 Explicitly rejected

| | Why |
|---|---|
| **Dify** | RAG/app builder — duplicates Tango's job with a competing, unverified brain |
| **Agent Zero / AutoGPT-likes** | Unbounded agency; the 18–24% band |
| **n8n AI Agent nodes for Tango's reasoning** | Second unverified brain, per §2 |
| **ComfyUI** | Image generation isn't a Tango capability |
| **Postgres for Tango's ledger** | [ADR-002](04-decisions.md) — circular dependency |

---

## 5. Compose skeleton

```yaml
# d:\my\tango\infra\docker-compose.yml
name: tango

x-restart: &restart
  restart: unless-stopped

networks:
  tango: {driver: bridge}

services:
  # ── core ──────────────────────────────────────────────
  ollama:
    <<: *restart
    image: ollama/ollama:0.12.6            # pin, don't use :latest
    networks: [tango]
    ports: ["11434:11434"]                  # host-visible: tango-core needs it
    volumes: [ollama:/root/.ollama]
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: all, capabilities: [gpu]}]

  litellm:
    <<: *restart
    image: ghcr.io/berriai/litellm:main-v1.80.0
    networks: [tango]
    ports: ["4000:4000"]
    volumes: ["./litellm.yaml:/app/config.yaml:ro"]
    command: ["--config", "/app/config.yaml"]
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}

  # ── voice (device access stays native; these are network services) ──
  whisper:
    <<: *restart
    image: rhasspy/wyoming-whisper:2.5.0
    networks: [tango]
    ports: ["10300:10300"]
    command: ["--model", "small", "--language", "en"]
    volumes: [whisper:/data]

  speech-to-phrase:
    <<: *restart
    image: rhasspy/wyoming-speech-to-phrase:1.1.0
    networks: [tango]
    ports: ["10301:10300"]
    volumes: ["./sentences:/config/sentences:ro"]   # your ~30 playbook commands

  kokoro:
    <<: *restart
    image: ghcr.io/remsky/kokoro-fastapi-cpu:v0.3.0
    networks: [tango]
    ports: ["8880:8880"]

  # ── capability ────────────────────────────────────────
  n8n:
    <<: *restart
    image: n8nio/n8n:2.18.4
    networks: [tango]                       # NO host port — Tango reaches it in-network
    volumes: [n8n:/home/node/.n8n]
    environment:
      N8N_SECURE_COOKIE: "false"
      N8N_DIAGNOSTICS_ENABLED: "false"
      N8N_PERSONALIZATION_ENABLED: "false"
      GENERIC_TIMEZONE: Asia/Kolkata

  searxng:
    <<: *restart
    image: searxng/searxng:2026.7.1
    networks: [tango]
    volumes: ["./searxng:/etc/searxng:rw"]

  docling:
    <<: *restart
    image: ghcr.io/docling-project/docling-serve:v1.4.0
    networks: [tango]

  # ── observability ─────────────────────────────────────
  phoenix:
    <<: *restart
    image: arizephoenix/phoenix:11.0.0
    networks: [tango]
    ports: ["6006:6006"]
    volumes: [phoenix:/mnt/data]

volumes: {ollama: , whisper: , n8n: , phoenix: }
```

**Notes that matter:**
- **Pin every tag.** `:latest` on an MCP-adjacent stack is how rug-pulls land.
- `n8n` has **no host port** — only `tango-core` reaches it, on the Docker network.
- `tango-core` runs natively and reaches containers via `localhost:<port>`; containers reach the host via `host.docker.internal`.
- Nothing binds to `0.0.0.0` on a routable interface. Tailscale is the only remote path.
- **GPU prerequisite:** Docker Desktop 4.29+, `nvidia-container-toolkit` installed, NVIDIA runtime registered — the "could not select device driver" error is a config problem, not hardware.

---

## 6. Full tool menu, by capability

Everything worth knowing about, so you can pick deliberately rather than discover later.

| Capability | Options | Pick |
|---|---|---|
| **Local LLM runtime** | Ollama · llama.cpp · vLLM · LM Studio | **Ollama** — simplest lifecycle; vLLM only if you need throughput |
| **Model gateway** | LiteLLM · OpenRouter · direct SDKs | **LiteLLM** — one endpoint, budgets, fallbacks |
| **STT** | faster-whisper · **Speech-to-Phrase** · Moonshine · Parakeet-TDT · whisper.cpp | **Speech-to-Phrase + faster-whisper** |
| **TTS** | **Kokoro-82M** · Piper · XTTS · Chatterbox | **Kokoro** — best lightweight of 2026 |
| **Wake word** | openWakeWord · Porcupine · microWakeWord | **openWakeWord** (native, custom "Tango") |
| **Voice transport** | **Wyoming** · gRPC · REST | **Wyoming** — swappable, HA ecosystem |
| **Workflow / connectors** | **n8n** · Node-RED · Windmill · Temporal | **n8n** — connector breadth wins |
| **Web search** | **SearXNG** · Brave API · Tavily · Exa | **SearXNG** — self-hosted, no key |
| **Browser** | **Playwright** · browserless · Steel · Puppeteer | **Playwright** (+ its MCP server) |
| **Doc parsing** | **Docling** · Apache Tika · unstructured · MarkItDown | **Docling** — best PDF layout handling |
| **Tool protocol** | **MCP** · OpenAPI · bespoke | **MCP**, behind a gateway (§3) |
| **Vector store** *(deferred)* | Qdrant · pgvector · **sqlite-vec** · Chroma | **sqlite-vec** if ever needed — no new service |
| **Memory** *(deferred)* | Mem0 · Zep · Letta · Graphiti | **None** — a table. Field is benchmark-disputed ([11](11-research-findings.md) Finding 8) |
| **Tracing** | **Phoenix** · Langfuse · OTel raw | **Phoenix** — 1 container |
| **Secrets** | **Windows DPAPI** · Infisical · Vaultwarden | **DPAPI** — one user, no service |
| **Remote access** | **Tailscale** · WireGuard · Cloudflare Tunnel | **Tailscale** — never a public port |
| **Messaging surface** | **Telegram Bot API** · Discord · Signal · WhatsApp | **Telegram** ([08](08-openclaw-and-tango.md) §5) |
| **Windows automation** | **pywinauto** (UIA backend) · FlaUI · AutoHotkey v2 | **pywinauto** — native only |
| **Scheduling** | n8n cron · Windows Task Scheduler · APScheduler | **n8n cron** for connector jobs, **APScheduler** in-core for playbooks |

---

## 7. Resource budget

On a 16 GB Windows laptop with a consumer GPU:

| Service | RAM | VRAM |
|---|---|---|
| Ollama (Qwen3 8B, q4) | ~1 GB | **~6 GB** |
| faster-whisper small | ~1 GB | ~1 GB (or CPU) |
| Speech-to-Phrase | ~300 MB | — |
| Kokoro (CPU) | ~2–3 GB | — |
| n8n | ~500 MB | — |
| SearXNG | ~200 MB | — |
| Docling | ~800 MB | — |
| Phoenix | ~400 MB | — |
| Playwright *(when running)* | ~1 GB | — |
| **Total steady** | **~7 GB** | **~7 GB** |

Comfortable at 16 GB. **Add full Langfuse (+3–4 GB across 4 containers) and it stops being comfortable** — another reason for Phoenix.

Docker Desktop's WSL2 memory can balloon; cap it in `.wslconfig`:
```ini
[wsl2]
memory=10GB
processors=6
```

---

## 8. The trap to avoid

You now have a menu of ~20 services. The failure mode is booting fifteen of them in week 0, spending a fortnight on compose files and OAuth, and never writing a playbook — which is [`TrailMesh_Failed/packages/*`](01-verdict-and-critique.md) with a different file extension.

**Add a container only when a playbook needs it, that week:**

| Week | Containers | Running total |
|---|---|---|
| 0 | `ollama`, `litellm` | 2 |
| 1 | — | 2 |
| 2 | `whisper`, `speech-to-phrase`, `kokoro` | 5 |
| 3 | `n8n`, `searxng` | 7 |
| 4 | `phoenix` | 8 |
| 5 | `mcp-gateway` + 2–3 vetted MCP servers | ~11 |
| 6 | `playwright`, `docling` | 13 |

**Week 0 is still two containers and one native service.** The stack is the reward for having playbooks, not the prerequisite.

---

## 9. Amendments this creates

| # | Change | Doc |
|---|---|---|
| 14 | **Two-plane architecture** — host plane native (core/agent/voice), service plane in Docker | [02](02-architecture.md) §2 |
| 15 | **`tango-core` and SQLite stay native.** It diagnoses Docker; it cannot live there | [ADR-002](04-decisions.md) reinforced |
| 16 | **Voice device access is native.** WSL2 has no audio — hard platform constraint | [10](10-voice-and-consumer-commands.md) |
| 17 | **LiteLLM implements the T0/T1/T2 routing** as config, not code | [ADR-003](04-decisions.md) |
| 18 | **n8n = tool provider, never decision maker.** Fixed webhook IDs, typed inputs, Tango-side risk/verifier. No AI Agent nodes. | new — ADR-012 |
| 19 | **MCP tool descriptions are `UNTRUSTED` integrity.** No auto-discovery, pinned digests, per-server containers, read descriptions before enabling | [11](11-research-findings.md) Finding 7, hardened |
| 20 | **Phoenix over Langfuse** for one user — 1 container vs 5 | new |
| 21 | **Speech-to-Phrase for playbook commands**, Whisper as fallback only | [11](11-research-findings.md) Finding 6 |

### ADR-012 — n8n is a tool provider, never a decision maker

**Status:** Accepted

**Decision.** n8n runs in the stack and supplies connectors and scheduling. Each workflow is registered as one Tango tool with a fixed webhook ID, typed inputs, a Tango-side risk class, verifier and idempotency flag. Only playbook steps invoke it. Tango does not use n8n's AI Agent nodes.

**Why.** n8n's connector breadth is the largest single labour saving available. But it holds every OAuth credential in one place, and node-level success is precisely the false-success signal that accounts for [45–76% of agent failures](11-research-findings.md). Letting a model choose freely among credential-bearing workflows is a **Rule of Two** violation in a single container.

**Rejected:** n8n as the orchestrator with Tango as a node. That inverts the trust model — verification and policy would sit downstream of the thing they're meant to constrain.
