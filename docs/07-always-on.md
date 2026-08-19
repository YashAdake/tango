# 07 — Always-On & Multi-Device Presence

> "Can we make it continuously run in background on all devices to get instructions from me and reply?"

**Yes. But split the question first, because it's actually three questions with very different answers.**

| | What it means | Reachable? | Effort |
|---|---|---|---|
| **A. Always-reachable** | You can issue a command at any moment, from any device, and get a reply | **~95%** | Low |
| **B. Always-invocable** | You can *start talking* instantly — no unlocking, no app switching, no typing | **~85%** | Low–medium |
| **C. Always-listening** | Hot mic, wake word, ambient presence — it hears you without you doing anything | **~50%** | High, and it fights you forever |

Almost everyone building a personal assistant wants (C), starts with (C), and burns out on OEM battery managers and false wake-ups. **(A) + (B) delivers about 95% of the Jarvis feeling for roughly 20% of the effort and 5% of the problems** — because if invocation takes 200 ms, ambient listening stops being the thing you were missing.

Build A, then B. Decide on C afterward, with real usage data about whether you actually want it.

---

## 1. Tier A — Always-reachable

This is the one that genuinely matters and it's mostly solved plumbing.

### 1.1 Windows: split the process, and know which half needs your session

This is the constraint everyone hits and nobody documents:

> **A Windows Service (session 0) cannot launch GUI applications into your desktop.** Session 0 isolation means a service that tries to start VS Code either fails or opens it invisibly.

So the split from [02-architecture.md](02-architecture.md) §3.4 — which existed for security reasons — turns out to be load-bearing for a second reason:

| Process | How it runs | Why |
|---|---|---|
| **Orchestrator** (gateway, router, ledger, store) | **Windows Service**, auto-start, auto-restart on failure | Survives logoff and reboot. Running before you log in. Recovery reconciliation runs on boot. |
| **Host Agent** (app launch, window state, clipboard, notifications) | **Task Scheduler → At logon**, "run only when user is logged on" | Needs the interactive session to touch the desktop |

Configure the service's recovery tab to restart on first, second and subsequent failures. Free crash-resilience.

Docker, git, file and process tools can live either side — put them in the Orchestrator, since they don't need the desktop, and keep the Host Agent's surface as small as possible. Smaller surface, smaller blast radius.

### 1.2 The real obstacle is sleep, not services

Your laptop is asleep most of the day. This — not Android, not architecture — is the #1 practical barrier to "always on," and the original spec never mentions it ([F14](01-verdict-and-critique.md)).

Options, in order of how much I'd recommend them:

**1. Never sleep on AC power.** `powercfg /change standby-timeout-ac 0`, display off separately. When plugged in at your desk, Tango is simply always up. Solves it for most of your actual usage, costs nothing, takes one command. **Start here.**

**2. Queue honestly on battery.** Commands sent while asleep are stored durably and executed on wake, and the UI *says so* — "queued, your laptop is asleep." Cheap, truthful, and far better than a spinner that never resolves. This is the [02](02-architecture.md) §7 availability model.

**3. Wake-on-LAN, if you want it.** Requires: WoL enabled in BIOS/UEFI, the NIC's "Allow this device to wake the computer" + "Only allow a magic packet" in Device Manager, and **Fast Startup disabled** (Fast Startup is a hybrid shutdown and it silently breaks WoL). The catch: **a magic packet has to originate on the same LAN**, so you need something always-on there to send it — a Raspberry Pi, a NAS, or a router that supports it. Note also that laptops on Modern Standby (S0) behave differently from classic S3 sleep; check `powercfg /a` to see which yours does.

Realistically: do (1) and (2). Add (3) only if you find yourself genuinely blocked, which you probably won't.

### 1.3 Transport: Tailscale, and no public ports ever

- Tailscale (or equivalent WireGuard mesh) between laptop and phone. Both get stable private addresses that work from anywhere — home, office, mobile data — with no port forwarding, no dynamic DNS, no exposed surface.
- **Never open a port to the internet.** Not with a password, not with a "long random URL." An always-on agent with tool authority behind a public port is the single worst thing you could do with this project.
- mTLS with a pinned cert on top of the tunnel. Per-device tokens, revocable individually.

### 1.4 Phone: push, not a socket

The instinct is a persistent WebSocket from phone to laptop so Tango can reach you. **Don't.** That's exactly what OEM battery managers kill, and it drains battery for the privilege.

Instead:
- **Phone → laptop** is trivial: the phone is awake when you're using it. A plain HTTPS call over Tailscale.
- **Laptop → phone** goes through **Web Push** (installed PWA) or **FCM** (native). High-priority push messages punch through Doze — it's Google's own channel, so OEM battery managers leave it alone. The phone wakes, pulls the payload, shows a notification or speaks.

This is strictly more reliable *and* cheaper than a socket. It's the rare case where the sanctioned path is also the better one.

### 1.5 Multi-device arbitration — the problem you'll hit on day one

With two devices live, "what's the status" needs a rule for who answers. Amazon and Google spent years on this.

For one user, keep it dumb and predictable:
- **The device you spoke into replies.** Always. No exceptions.
- Proactive messages go to a single **`active_device`**, set by most-recent-interaction with a timeout (e.g. 20 min), falling back to the phone.
- One shared task stream — start something on the laptop, ask about it from the phone, get a coherent answer. The ledger already makes this free.
- `POST /v1/devices/{id}/focus` to pin the active device manually.

Add a `Device` presence table: `id, type, last_seen, capabilities[], push_token, is_active`. Small, and it saves you from a category of confusing bugs.

---

## 2. Tier B — Always-invocable

The goal: **from lock screen or anywhere, to talking, in under 300 ms, with no app switching.** Hit that and hot-mic stops feeling necessary.

### 2.1 Windows: a global hotkey

`RegisterHotKey` on something like `Ctrl+Space` held down for push-to-talk. Instant, zero CPU, zero false positives, works over full-screen apps. For a laptop you're already sitting at, this is genuinely **better** than a wake word — no misfires, no "did it hear me," no privacy question.

### 2.2 Android: become the system assistant

This is the good one, and it's underused.

Android lets an app register as the **default digital assistant** via `VoiceInteractionService`. Once set (Settings → Apps → Default apps → Digital assistant app), Tango is launched by the system's **assist gesture** — long-press power, corner swipe, or the dedicated button, depending on your device — **from anywhere, including the lock screen.**

Why this is the right answer:
- **Zero battery cost.** Nothing runs until you invoke it. The OS does the listening for the gesture, not you.
- **OEM-respected.** It's a first-class Android role, so battery managers don't interfere.
- **Sub-300 ms** from gesture to listening.
- No hot mic, no wake word, no false positives, no privacy exposure.

This is the single highest-value native-Android feature for Tango, and it's the one thing I'd break the PWA-first rule ([ADR-006](04-decisions.md)) for. A thin native app that does *only* `VoiceInteractionService` + call initiation + notification listening, with the PWA still handling UI, is a very good split.

Cheaper fallbacks if you don't want native yet: a Quick Settings tile, a home-screen shortcut to the installed PWA, or a lock-screen widget.

### 2.3 iOS, if it's ever in scope

Much worse. No background daemon, period. You get Shortcuts + App Intents ("Hey Siri, ask Tango…") and push notifications. That's the ceiling, and it's Apple's decision, not a gap in your engineering.

---

## 3. Tier C — Always-listening

Now the honest part.

### 3.1 Windows: actually fine

Local VAD plus a wake-word detector (openWakeWord, Porcupine, or a small custom model) running continuously costs a few percent of one core. On a plugged-in laptop this is a non-issue. In a room you're already in, it works.

**Recommendation:** if you want ambient, do it *here first*. Windows is the friendly platform for it and you'll learn the false-positive tax cheaply.

### 3.2 Android: this is the wall

To hold a mic open in the background you need:
- A **foreground service** with a persistent notification, declaring `foregroundServiceType="microphone"` and holding `FOREGROUND_SERVICE_MICROPHONE` (Android 14+ requirements).
- A **battery optimisation exemption** (fine to request for a personal sideloaded app).
- **Per-OEM autostart whitelisting** — and this is the killer. MIUI autostart, ColorOS's background permissions, Samsung's "put unused apps to sleep," OnePlus's aggressive dozing. These kill *foreground services*, which are supposed to be unkillable. `dontkillmyapp.com` exists entirely because of this.
- Android 14+ also restricts *starting* a foreground service from the background, so restart-after-kill is itself constrained.

Net: works acceptably on Pixel/stock, fights you continuously on MIUI/ColorOS/OneUI, and costs measurable battery all day. **Every day you leave it on, you're paying a battery tax for a capability the assist gesture already gives you in 300 ms.**

### 3.3 The false-positive tax — the real reason to think twice

A wake word that misfires a few times a day, in a room with a TV, during a call, or when someone says a similar-sounding word, is *the* thing that gets assistants muted and then uninstalled. And a misfire isn't neutral here: Tango has tool authority. A false wake that lands on a plausible-sounding phrase is a different class of problem than Alexa playing the wrong song.

Two hard rules if you build Tier C:
1. **A wake-word-initiated request can never carry standing authorizations.** It gets the same treatment as untrusted-context tasks ([02](02-architecture.md) §3.4) — R2+ requires explicit confirmation, always.
2. **Voice can never reach R4.** Already in the [Week 6 plan](03-roadmap.md); it matters much more with a hot mic.

### 3.4 The security shift nobody mentions

Always-on changes your posture in a way worth stating plainly: a persistently-running, persistently-authenticated agent with tool authority means **any compromise is now permanent rather than session-scoped.** The mitigations already in the plan (Tailscale-only, mTLS, per-device revocable tokens, the Host Agent's independent allowlist, capability freeze) were sized for an occasional-use system. They're still correct, but the value of the panic controls in [02](02-architecture.md) §7 — global pause, `undo last` — goes up a lot.

Add one more: **an idle re-auth.** After N hours with no interaction, R3+ requires re-authentication (Windows Hello / phone biometric) even if the device is still paired.

---

## 4. What I'd actually build

Slotting into the [amended roadmap](06-the-jarvis-question.md) §7:

| Week | Addition | Gets you |
|---|---|---|
| **0** | Orchestrator as a Windows Service; Host Agent at logon | Survives reboot from day one. Costs ~30 min if done now, half a day if retrofitted. |
| **2** (voice) | Global hotkey push-to-talk on Windows | Instant invocation where you spend most of your time |
| **3** | `powercfg` never-sleep-on-AC + honest queue-on-sleep | Effectively always-up at your desk |
| **4** (remote) | Tailscale + PWA + Web Push + device presence table + arbitration | **Tier A complete** — reachable from anywhere, replies anywhere |
| **5** | Thin native Android app: `VoiceInteractionService` only | **Tier B complete** — assist gesture, lock screen, 300 ms, zero battery |
| **later** | Windows wake word, if you still want it | Tier C on the friendly platform, measured |
| **maybe never** | Android hot mic | Only if Windows Tier C proves it's worth the tax |

The two lines that matter most in that table are the Week 0 service split (cheap now, painful later) and the Week 5 assist-gesture app — that one is what makes Tango feel omnipresent on your phone without a single background process running.

---

## 5. The honest summary

**"Always reachable, always one gesture away, replies on whatever device you used"** — yes, ~95%, and most of it is configuration rather than engineering.

**"Always listening, everywhere, ambiently"** — yes on Windows, partially and grudgingly on Android, and it's the part where you'll spend the most time for the least return.

And the reframe worth holding onto: **Jarvis feels omnipresent because he answers instantly, not because he's always listening.** A 200 ms gesture-to-listening path is indistinguishable from ambient presence in daily use — and it costs no battery, produces no false wakes, and never has to be explained to someone else in the room.
