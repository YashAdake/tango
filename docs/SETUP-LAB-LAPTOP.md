# Setting up the lab laptop

The runtime host: **Intel Core Ultra 7 · RTX 5060 8 GB · 24 GB RAM · Windows 11.**

Do these in order. **Step 3 tells you what steps 4–6 actually need on your
machine**, so resist installing things before you get there — half of it may
already be present, and guessing at model names and CUDA versions is how a first
run turns into an afternoon.

---

## 1. Prerequisites

Install if missing:

| | Why | Get it |
|---|---|---|
| **Git** | to clone | git-scm.com |
| **Python 3.12+** | Tango core runs native (Docker cannot reach the mic or launch GUI apps) | python.org — **tick "Add to PATH"** |
| **uv** | fast, reproducible venv | `pip install uv` |
| **VS Code** | `app.launch` target | code.visualstudio.com — then <kbd>Ctrl+Shift+P</kbd> → *Shell Command: Install 'code' command in PATH* |

Docker Desktop and Ollama come later — doctor will confirm whether you need them
yet.

---

## 2. Clone and install

```powershell
cd C:\           # or wherever you want it; not inside OneDrive
git clone https://github.com/YashAdake/tango.git
cd tango

uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

If PowerShell blocks activation:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## 3. Run doctor — this is the real instruction step

```powershell
python -m tango.cli doctor
```

It prints one line per check and, for anything not green, **the exact command to
fix it**. Expect `projects` and probably `ollama` to be flagged on a fresh clone.

Read what it says. It is more current than this document.

```
  [ok  ]  python         3.12.x in venv
  [warn]  ollama         not reachable on :11434
           → Install from ollama.com, then: ollama serve
  [warn]  projects       hosts/default (hostname: your-laptop)
           → Create hosts/your-laptop/projects.json with this machine's real paths
```

**`warn` is not `fail`.** Tango runs every playbook without a model — the
deterministic path never needed one. Only fuzzy routing degrades.

---

## 4. Tell Tango about *this* machine

Doctor names your hostname. Create its profile:

```powershell
mkdir hosts\<your-hostname>
copy hosts\default\projects.json hosts\<your-hostname>\projects.json
```

Then edit that file. Every `path` must point at a real directory **on the
laptop**. If your projects still live on the other machine, either clone them
here or leave only the ones that exist — doctor fails on a path that does not,
deliberately, because a silently-wrong path is worse than a missing one.

Re-run doctor until `project paths` is green.

---

## 5. Ollama and the model

```powershell
# Install from ollama.com, then:
ollama serve                  # leave running, or it installs as a service
ollama pull qwen3:4b          # ~2.6 GB
```

**Why 4B and not something bigger.** The local model's entire job is single-turn
intent classification and slot filling — playbooks do the composing, so the
model never has to. That is the part small models are good at, and it keeps
~4 GB of VRAM free for your actual work (docs/15 §2.1). If routing accuracy
comes in under 95% on the golden set, *then* we go up. Not before — that is what
the eval is for.

Then set the environment so Tango's defaults match:

```powershell
$env:TANGO_MODEL = "qwen3:4b"      # optional; this is already the default
$env:TANGO_OLLAMA = "http://localhost:11434"
```

Re-run doctor. `ollama` and `model` should go green.

---

## 6. Docker (only when you want compose-backed projects)

Install Docker Desktop, enable the WSL2 backend, start it. For GPU containers
later you will also want `nvidia-container-toolkit`.

Without Docker, a playbook with a compose step reports `REFUTED` with the real
error and stops — honestly, but it stops. That is expected, not a bug.

---

## 7. First run

```powershell
python -m tango.cli projects        # what it knows about
python -m tango.cli tools           # what it can do, and which are verified
python -m tango.cli do start <one of your projects>
python -m tango.cli audit           # what it actually did
```

Then the gates:

```powershell
python scripts\verify.py            # 7 gates — must be green
python evals\run.py --all           # routing accuracy
```

---

## 8. What to send back

After the first run, paste me:

1. **`python -m tango.cli doctor`** — full output
2. **`python scripts\verify.py`** — full output
3. **`python evals\run.py --all --show-failures`** — full output
4. **What `tango do start <project>` printed**, and whether it matched reality
   (did the dev server actually come up? did the editor open?)
5. **Anything that surprised you** — a wrong answer, a slow moment, an ugly
   message, a phrasing you tried that it did not understand

Point 5 matters most. Points 1–4 tell me whether it *works*; point 5 tells me
whether it is any *good*, and only you can see that.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `'code' is not recognized` | VS Code CLI not on PATH | <kbd>Ctrl+Shift+P</kbd> → *Shell Command: Install 'code' command in PATH*, restart the shell |
| `UnicodeEncodeError` | old console codepage | Use Windows Terminal, or `chcp 65001`. Tango forces UTF-8 on its own streams; a nested tool may not |
| Docker errors on `compose_up` | Docker Desktop not started | Start it, or ignore — the failure is reported honestly |
| Ollama slow on first call | model loading into VRAM | Normal, 2–4 s. Idle unload after 5 min is deliberate (docs/15 §2.3) |
| `no such table` | stale/partial database | `del data\tango.db*` and rerun; the ledger rebuilds |
| Playbook cannot find a path | `hosts/<host>/projects.json` points elsewhere | Fix the path; doctor will confirm |
| Blackwell / CUDA errors | RTX 50-series needs recent builds | Update the NVIDIA driver, then reinstall Ollama |

---

## Where things live

```
docs/16-…-plan.md   the authoritative spec        docs/VERIFICATION-LOG.md  what was checked, what broke
tango/              core (native, this machine)   playbooks/                the recipes
evals/              golden set + harness          hosts/<name>/             per-machine config
scripts/verify.py   the 7-gate completion check   data/tango.db             ledger (gitignored)
```

Nothing here needs the internet at runtime except the optional cloud tier. No
port is opened. The ledger never leaves the machine.
