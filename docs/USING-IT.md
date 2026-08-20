# Using Tango today

Tango runs on this machine now. Your projects are here, so everything in Phase 0
and Phase 1 works against real state — no laptop, no Ollama, no Docker required
for most of it.

## One-time setup

```powershell
cd d:\my\tango
uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

Then put `d:\my\tango` on your PATH so `tango` works from any directory:

```powershell
[Environment]::SetEnvironmentVariable(
    "Path", $env:Path + ";d:\my\tango", "User")
```

Open a new terminal and `tango status` should work from anywhere.

## The ten things worth trying

```powershell
tango status                          # every project, git state, prod health
tango do why is optiresume down       # evidence, ranked, with the strongest first
tango do what did I ship this week    # cross-repo digest
tango do anything uncommitted         # what is silently rotting
tango do is prod ok                   # real probes, real latency
tango do start myjson                 # verified, step by step
tango running                         # what Tango started that is still alive
tango do shut everything down         # stops it, and proves it stopped
tango do what's hogging port 3000     # the recurring workspace hazard
tango audit                           # everything it has done, with verdicts
```

Refusals are worth trying too, because they are the part most systems get wrong:

```powershell
tango do delete the optiresume database   # refused
tango do take prod down                    # refused
tango do start it                          # asks which project
tango do order me a pizza                  # declines, politely
```

## What to notice

The point is not that it works. The point is **what it says when it doesn't.**

- Start something while Docker is down: it reports the real error and stops,
  rather than claiming your environment is up.
- Ask for status without probing production: it says **"prod not checked"**,
  not "prod fine". Not looking is not the same as looking and finding nothing.
- Diagnose something: it ranks evidence and then says *"that is what the
  evidence points at, not a diagnosis — I have not verified a cause."*

If any of that ever reads as more confident than it should, that is a bug worth
reporting, and a more serious one than a crash.

## The thing I actually need from you

**Every phrasing it does not understand.** When `tango do <something>` answers
*"I don't have a playbook for that yet"*, that utterance is worth more to this
project than anything else you could send me — including Hinglish, half
sentences, and whatever you would have typed at 1am.

Keep them anywhere (a text file, a message). They become rows in
`evals/golden.draft.jsonl`, and the accuracy number only starts meaning
something once it is measured against **your** phrasings rather than my guesses
at them.

Same for anything that reads wrong: an ugly message, a confusing answer, a claim
you did not quite trust.

## When you move to the lab laptop

[SETUP-LAB-LAPTOP.md](SETUP-LAB-LAPTOP.md) — clone, `tango doctor`, fix what it
names, then `python scripts/report.py` and send me the file.

## Where everything is

| | |
|---|---|
| The spec | [16-architecture-and-implementation-plan.md](16-architecture-and-implementation-plan.md) |
| What was checked, and what broke | [VERIFICATION-LOG.md](VERIFICATION-LOG.md) |
| Gates | `python scripts\verify.py` |
| Routing accuracy | `python evals\run.py --all --show-failures` |
