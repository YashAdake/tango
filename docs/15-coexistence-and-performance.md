# 15 — Coexistence: Tango Must Not Slow Your Laptop

> "While running this, everything must also run at at least bare minimum speed on the laptop."

**Correct constraint, and the BOM as written in [14](14-component-bom.md) fails it.** Here's the arithmetic, then the fix.

---

## 1. The problem, in numbers

### VRAM — 8 GB total

Your desktop is already using some of it before Tango starts:

| | |
|---|---|
| Windows DWM / desktop compositing | ~0.6 GB |
| Chrome (GPU compositing, 15 tabs) | ~0.5 GB |
| VS Code (GPU rendering) | ~0.2 GB |
| **Desktop floor** | **~1.3 GB** |
| **Available to Tango** | **~6.7 GB** |

Now the BOM's pick:

| Configuration | VRAM | Verdict |
|---|---|---|
| Qwen3.5-9B Q4_K_M **@ 32K ctx** | **6.96 GB** | ❌ **1.3 + 6.96 = 8.26 GB > 8 GB** |
| Qwen3.5-9B Q4_K_M @ 8K ctx | ~5.5 GB | ⚠️ Fits, 1.2 GB spare, no room for vision |
| **Qwen3-4B Q4 @ 8K ctx** | **~2.8 GB** | ✅ **3.9 GB spare** |

**Exceeding VRAM is not a soft failure.** Windows spills to shared memory over PCIe, and everything — the model *and* your desktop — falls off a cliff. Stuttering scroll, laggy typing, 10× slower inference. That's the exact outcome you're asking me to prevent.

**The 32K context figure was the mistake.** It's the number that makes the model look good in a benchmark, and it's wrong for Tango: your requests are one utterance plus a schema plus some tool output. 8K is generous.

### RAM — 24 GB total

Your real working set:

| | |
|---|---|
| Windows baseline | ~4.0 GB |
| Chrome, 15 tabs | ~3.0 GB |
| VS Code + TS server on a real project | ~2.0 GB |
| A Next.js dev server | ~1.0 GB |
| Terminal / Claude Code | ~0.5 GB |
| **Your work** | **~10.5 GB** |

[Doc 12](12-docker-stack-and-tooling.md) allocated **10 GB to WSL2** plus ~4 GB native. That's 24.5 GB of a 24 GB machine before you open a browser tab. **Also over budget.**

---

## 2. The fix — five changes

### 2.1 Start with Qwen3-4B, not 9B. Let the evals decide.

The local model's *only* job is single-turn intent classification and slot filling ([ADR-001](04-decisions.md)). It never composes multi-step tool sequences — that's what playbooks are for, and it's the whole reason the architecture survives [BFCL v4's](11-research-findings.md) small-model numbers.

BFCL's 40.9% (4B) vs 46.8% (9B) gap is measured on **agentic and multi-turn** tasks — 70% of that benchmark's weight — which Tango deliberately doesn't use. On the narrow task it actually runs, the gap is much smaller.

**So: ship the 4B, measure routing accuracy on the golden set, and upgrade to the 9B only if you're below the 95% gate.** That's what the eval harness is *for* ([05](05-eval-and-safety.md)). Guessing high "to be safe" costs 4 GB of a machine that needs it.

### 2.2 8K context, flash attention, quantized KV cache

```bash
OLLAMA_FLASH_ATTENTION=1      # substantially reduces KV cache memory
OLLAMA_KV_CACHE_TYPE=q8_0     # quantized KV cache — big saving, negligible quality loss
OLLAMA_MAX_LOADED_MODELS=1    # never hold two models
OLLAMA_NUM_PARALLEL=1         # single user; parallel slots multiply KV cache
OLLAMA_KEEP_ALIVE=5m          # see 2.3
```

`num_ctx: 8192` per model. These four settings together are worth more than a GB.

### 2.3 Idle unload — and the architecture already makes it free

`OLLAMA_KEEP_ALIVE=5m` unloads the model after five idle minutes. Reload costs 2–4 seconds.

**And you will almost never feel it**, because of a property the design already has:

> **The Alexa half doesn't use the model at all.**

"Start optiresume", "kill everything", "what's the state of everything", "set an alarm", "is prod up" — all playbooks, all regex/kNN routed, all sub-second **with the model completely unloaded**. Only the agent half (diagnosis, novel questions) pays the 2–4 s reload, and that path already shows a pre-rendered *"One moment"*.

So for most of your day, Tango holds **zero VRAM** and your GPU is entirely yours. That's not a compromise — it's the deterministic-first architecture paying a dividend I hadn't counted.

### 2.4 Halve the Docker allocation, and reclaim memory

```ini
# %UserProfile%\.wslconfig
[wsl2]
memory=6GB                    # was 10GB
processors=4                  # leave 4+ threads for foreground work
swap=4GB
autoMemoryReclaim=gradual     # WSL2 returns freed memory to Windows
```

`autoMemoryReclaim` matters most — without it WSL2 holds its high-water mark forever, and Docker Desktop's memory looks like a leak.

6 GB is sufficient because the normal operating set is 2–5 containers, not 13:

| Mode | Containers | RAM |
|---|---|---|
| **Weeks 0–1** | ollama, litellm | ~2 GB |
| **Weeks 2–3 (normal)** | + parakeet, speech-to-phrase | ~3.5 GB |
| **Full (occasional)** | + n8n, searxng, phoenix, docling | ~5.5 GB |
| Playwright / Docling batch | on demand, then stop | +1 GB |

Cap each container so none can run away:

```yaml
  n8n:
    deploy:
      resources:
        limits: {memory: 512M, cpus: '1.0'}
```

### 2.5 Tango yields to the foreground — as a rule, not a tuning pass

| Control | |
|---|---|
| **Below Normal priority** on `tango-core`, `tango-voice`, and Ollama | Foreground apps always win the CPU |
| **Lazy-load Kokoro** — load on first speech, unload after 10 min idle | Saves ~2.5 GB when you're not talking |
| **Wake word only** is permanently resident | livekit-wakeword ONNX: ~1–2% of one core |
| **Pause the stack** — `docker compose stop` on the non-core services | One command frees ~3 GB when you need it |

---

## 3. The corrected budget

### VRAM

| State | Tango | Desktop | Total of 8 GB |
|---|---|---|---|
| **Idle (most of the day)** | **0 GB** — model unloaded | 1.3 GB | **1.3 GB** ✅ |
| Active, agent half | 2.8 GB (4B @ 8K) | 1.3 GB | **4.1 GB** ✅ |
| Vision on demand | 3.5 GB (VL-4B, text model unloaded) | 1.3 GB | 4.8 GB ✅ |
| If evals force the 9B | 5.5 GB (@ 8K) | 1.3 GB | 6.8 GB ⚠️ workable |

### RAM

| | |
|---|---|
| Your work (Chrome, VS Code, dev server, terminal) | ~10.5 GB |
| Windows | ~4.0 GB |
| WSL2 / Docker | ~6.0 GB cap (typically 3.5 GB used) |
| Native Tango (core + agent + voice; Kokoro lazy) | ~1.5 GB |
| **Total typical** | **~19.5 GB** |
| **Free** | **~4.5 GB** ✅ |

Speaking adds ~2.5 GB briefly (Kokoro). Your own project's Docker adds ~1.5 GB. Both fit.

---

## 4. The degradation ladder

Tango should *measure* pressure and step down automatically, not wait to be told.

| Level | Trigger | What it gives up | Still works |
|---|---|---|---|
| **0 — Full** | Plenty free | — | Everything |
| **1 — Lean** *(default)* | Normal | LLM unloaded when idle; Kokoro lazy | All playbooks instant; agent half +3 s |
| **2 — Yield** | RAM < 3 GB free, or a game/build is foreground | Stop n8n, searxng, phoenix, docling | Playbooks, voice, diagnostics |
| **3 — Cloud-only** | VRAM < 2 GB free | Unload local model entirely; route reasoning to Claude | Everything except offline reasoning |
| **4 — Minimal** | On battery, or RAM < 1.5 GB | Everything down but `tango-core` + SQLite (~300 MB) | Playbooks, ledger, status. No AI. |

**Level 4 is the important one.** At ~300 MB Tango still answers *"what's the state of everything?"* and still starts your dev environment — because those were never AI features. **A Tango that degrades to a very fast deterministic tool is far more useful than one that degrades to nothing.**

Level 3 is worth noting too: cloud fallback means low VRAM costs you *privacy scope*, not *capability* — and `LOCAL_ONLY` tasks still fail loudly rather than escaping ([ADR-003](04-decisions.md)).

---

## 5. Verify it, don't assume it

Week 0 should establish a baseline and Week 2 should re-check it. Cheap, and it's the difference between "I think it's fine" and knowing.

```powershell
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv -l 5
docker stats --no-stream
Get-Process | Sort-Object WS -Descending | Select-Object -First 15 Name,@{N='MB';E={[int]($_.WS/1MB)}}
```

Three numbers to gate on:

| Metric | Gate |
|---|---|
| **VRAM free with Tango active** | **≥ 1.5 GB** |
| **RAM free during a normal dev session** | **≥ 3 GB** |
| **Tango CPU while idle** | **< 3%** of total |

Any breach → drop a degradation level and re-measure. Put these in the Week-0 eval harness alongside the routing gates — a performance regression should fail the build the same way a correctness regression does.

---

## 6. Amendments to the BOM

| # | Change | Was |
|---|---|---|
| 22 | **Qwen3-4B Q4 is the default local model.** Upgrade to 9B only if golden-set routing falls below 95% | Qwen3.5-9B |
| 23 | **8K context, not 32K** | 32K (6.96 GB) |
| 24 | **Flash attention + q8_0 KV cache + single loaded model + single parallel slot** | unspecified |
| 25 | **`OLLAMA_KEEP_ALIVE=5m`** — idle unload; free because playbooks need no model | always resident |
| 26 | **WSL2 capped at 6 GB with `autoMemoryReclaim=gradual`** | 10 GB |
| 27 | **Per-container memory and CPU limits** | none |
| 28 | **Below Normal process priority; Kokoro lazy-loaded** | none |
| 29 | **Five-level degradation ladder, measured and automatic** | none |
| 30 | **Resource gates in CI** alongside correctness gates | none |

---

## 7. The short answer

**Yes — and the fix was mostly to stop over-provisioning.**

Three things do the work:

1. **The 4B model instead of the 9B**, because the local model's job is narrow and the eval harness will tell you if that's wrong rather than you guessing.
2. **8K context instead of 32K**, because the 32K figure was benchmark-shaped, not Tango-shaped, and it was the single line item that broke the VRAM budget.
3. **Idle unload**, which is nearly free precisely because the most-used half of Tango was designed never to touch a model.

Typical steady state: **1.3 GB VRAM, ~19.5 GB of 24 GB RAM, under 3% CPU.** Your laptop stays yours, and Tango is still instant on everything you actually do all day.
