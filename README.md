# TANGO

Personal AI operating assistant — voice-first, playbook-driven, and structurally incapable of claiming an outcome it hasn't verified.

**Status:** Phases 0 and 1 complete and running. 12 capabilities, 9 CI gates, ~285 tests.
**Runtime target:** dedicated lab laptop — Intel Core Ultra 7 · RTX 5060 8 GB · 24 GB RAM · Windows 11.
**Development host:** the `d:\my` workspace machine (where the managed projects live).

## Start here

| | |
|---|---|
| **Use it today** | [docs/USING-IT.md](docs/USING-IT.md) — it runs on this machine now |
| **The authoritative spec** | [docs/16-architecture-and-implementation-plan.md](docs/16-architecture-and-implementation-plan.md) (v1.1) |
| Its red-team review (governs on conflict) | [docs/17-plan-review-v1.1.md](docs/17-plan-review-v1.1.md) |
| Orientation / summary | [docs/00-SUMMARY.md](docs/00-SUMMARY.md) |
| Full document index | [docs/README.md](docs/README.md) |
| Golden set (S0.1, awaiting owner edit) | [evals/golden.draft.jsonl](evals/golden.draft.jsonl) |

## Layout

```
docs/     the complete specification corpus (00–17)
evals/    golden set + (later) injection fixtures and the eval harness
tango/    (Phase 0) core package — gateway, router, playbooks, ledger, render, store
agent/    (Phase 1) Windows Host Agent — separate process, own allowlist
surfaces/ (Phase 1+) CLI, local web, Telegram bot
hosts/    per-hostname config (projects.yaml) — never secrets
infra/    docker-compose, pinned images
```

Secrets never enter this repo — `.env` is local-only, DPAPI-protected. See `.gitignore`.

## Quickstart (lab laptop, from Phase 1)

```powershell
git clone https://github.com/YashAdake/tango.git; cd tango
python -m tango.doctor        # validates GPU/NPU/Docker/mic/Tailscale before anything runs
docker compose --profile core up -d
python -m tango serve
```
