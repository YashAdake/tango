# 10 — Voice, Wake Word & Consumer Commands

> *"Tango"* → *"Yes sir"* → *"call mom" / "WhatsApp Rahul" / "send mail" / "open Chrome" / "set an alarm"* —
> an Alexa-and-agent mixture, always running in the background.

**Short answer: yes to almost all of it. One item — autonomous WhatsApp — is genuinely problematic, and the workaround turns out to be better than the thing you asked for.**

---

## 1. The Alexa-and-agent mixture is exactly the architecture you already have

This framing is right, and worth naming precisely, because it maps onto [ADR-001](04-decisions.md) with no changes.

**Alexa works because it does about fifty things extremely reliably, fast, every time.** It is not smart. It is *dependable*, and dependability is what makes it feel present.

**An agent is the opposite:** open-ended, handles novel requests, reasons — and is slower and less predictable.

You want both, and the split is already built:

| Half | Mechanism | Latency | Reliability |
|---|---|---|---|
| **Alexa half** | Playbooks + regex/kNN router. **No LLM in the path.** | < 500 ms | ~99% |
| **Agent half** | Freeform planner, cloud model, multi-step | 2–8 s | ~85% |

**The critical rule: "set an alarm for 7" must never touch an LLM.** Route → execute → confirm, all local, under half a second. The moment a simple command round-trips to a cloud model, it stops feeling like Alexa and starts feeling like a website.

The router decides which half answers. That's the whole mixture.

---

## 2. Command-by-command truth

| Command | Mechanism | Verifiable? | Score |
|---|---|---|---|
| **"Set an alarm for 7"** | Android `AlarmClock.ACTION_SET_ALARM` (+`EXTRA_SKIP_UI`) · Windows scheduler + TTS | ✓ full | **95%** |
| **"Set a timer for 10 minutes"** | Same, local | ✓ full | **95%** |
| **"Open Chrome / VS Code / Spotify"** | Windows: launch + verify process. Android: `getLaunchIntentForPackage` | ✓ full | **95% / 85%** |
| **"Call mom"** | Resolve contact → ID → Android `ACTION_CALL` (`CALL_PHONE`) | ⚠ partial | **80%** |
| **"Text mom I'll be late"** (SMS) | `SmsManager` (`SEND_SMS`) | ✓ full | **85%** |
| **"Send mail to Rahul about the deploy"** | Gmail API, confirm + undo + allowlist | ✓ full | **85%** |
| **"Telegram Rahul the logs"** | Telegram Bot API | ✓ full | **95%** |
| **"WhatsApp Rahul I'll join at 3"** | **See §4 — the problem child** | — | **40% or 90%** |
| **"What's the weather / news"** | API connector | ✓ | **90%** |
| **"Play music"** | Spotify API or media-key injection | ✓ | **85%** |
| **"Remind me at 6 to push the branch"** | Local scheduler → push notification | ✓ full | **95%** |
| **"Add milk to my shopping list"** | Local list, or Todoist/Notion API | ✓ full | **90%** |

**The pleasant surprise: alarms, timers, reminders and app launching are the *easiest* things in this entire project** — fully local, fully deterministic, fully verifiable, no cloud, no injection surface. They'll be among the first things working and among the most reliable.

### 2.1 "Call mom" — what verification actually means here

Tango can confirm the dial was initiated (call state → `OFFHOOK` via `TelephonyCallback`). It **cannot** confirm anyone answered.

So the honest outputs are:
- *"Calling Mom — dialling."* → `VERIFIED` (the dial happened)
- Never *"I called Mom"* implying a conversation → that's `UNVERIFIABLE` territory

This is precisely the §9.1 case the original spec spotted and never gave a type to. It's now a first-class outcome.

**Also note the hop:** if you say it to the laptop, the flow is laptop → push → phone wakes → dials. Two to four seconds. Say it to the phone and it's instant. Worth knowing which surface you're on.

---

## 3. The wake word: "Tango" → "Yes sir"

### 3.1 It works, and here's the latency budget

| Step | Budget | How |
|---|---|---|
| Wake word detection | 100–200 ms | openWakeWord or Porcupine, custom "Tango" keyword, local, ~2% of one core |
| **Acknowledgement** | **< 50 ms** | **Pre-rendered WAV. Do not synthesise it.** |
| Command capture + endpoint | speech + ~300 ms | VAD silence detection |
| STT | 200–500 ms | faster-whisper small/distil, local |
| Route (Alexa half) | < 10 ms | regex/kNN, no model |
| Execute + verify | 100 ms–3 s | playbook |
| Response TTS | 50–400 ms | pre-rendered templates for common replies, Piper otherwise |

**"Tango … set an alarm for 7am" → confirmation in roughly 1.2 seconds.** That's Alexa-class.

**The single highest-value trick: pre-render your acknowledgements and your common responses as audio files.** "Yes sir." "Done." "Starting optiresume." Synthesising those on demand adds 300–500 ms to *every single interaction* and it's the difference between present and sluggish. The ack should fire directly from the wake-word detector, before anything else in the system has even woken up.

### 3.2 One design improvement over what you described

The two-turn pattern — *"Tango"* → *"Yes sir"* → *"call mom"* — is lovely, and it is **slower than one-shot**.

Support both, exactly as Alexa does:

- **"Tango, call mom"** — speech continues after the wake word → **skip the ack entirely**, just do it. Fastest path.
- **"Tango."** *(silence)* → *"Yes sir."* → listen. For when you haven't decided yet.

The detector just checks whether audio continues within ~400 ms of the wake word. Ten lines of code, and it means you're never forced through a two-turn handshake for a command you already knew.

### 3.3 Where it can run

| Platform | Always-listening | Verdict |
|---|---|---|
| **Windows (plugged in)** | ✅ Genuinely fine | Few % of one core. **Do it here first.** |
| **Windows (battery)** | ⚠️ Measurable drain | Gate it on AC power |
| **Android** | ❌ The wall | Foreground service + `microphone` type + battery exemption + per-OEM autostart whitelisting — and MIUI/ColorOS kill foreground services anyway |
| **Android alternative** | ✅ Assist gesture | Long-press power → Tango listening in < 300 ms, **zero battery**, OEM-proof ([07](07-always-on.md) §2.2) |

So the realistic setup: **hot mic on the desktop, assist gesture on the phone.** In practice that covers everything, because the phone is in your hand when you're using it.

### 3.4 The false-positive tax, and why it matters more here

A wake word that misfires while a video is playing, during a call, or on a similar-sounding word is what gets assistants muted and then uninstalled. And Tango isn't Alexa — a misfire lands on a system with tool authority.

Two hard rules, from [02](02-architecture.md) §3.4 and [03](03-roadmap.md):

1. **Wake-word-initiated requests never carry standing authorizations.** Same treatment as untrusted context — R2+ requires explicit confirmation.
2. **Voice never reaches R4.** Ever. It must move to a visual surface.

And for R3 by voice, **readback is mandatory**, because misheard entities are the real danger:

> *"Calling Mom — mobile, ending 4821. Say yes."*

"Call mom" heard as "call Tom" is annoying. A misheard email recipient is a different category. This is why [ADR-009](04-decisions.md) matters most in the voice path — the model never authors a phone number or an address, it only selects a resolved ID, and the readback names the resolved entity so you can catch it.

---

## 4. WhatsApp — the honest problem, and the better answer

This is the one thing on your list that doesn't have a clean solution. Four options:

| Option | How it works | Verdict |
|---|---|---|
| **WhatsApp Business / Cloud API** | Official Meta API | ❌ Built for businesses messaging customers — separate number, template approval for business-initiated messages. Not for texting your friends from your own number. |
| **Baileys / whatsapp-web.js** | Reverse-engineered protocol. What OpenClaw uses. | ⚠️ Fully autonomous send — but **unofficial, breaks on protocol changes, and risks your number being banned.** ~40% and it's a maintenance liability forever. |
| **Deep link** — `https://wa.me/<number>?text=<msg>` | Opens WhatsApp with the message pre-filled. **You tap send.** | ✅ **90%.** Official, stable, zero risk. |
| **Accessibility service auto-tap** | Robot taps the send button | ❌ Fragile across OEM skins, Android 13+ Restricted Settings for sideloaded apps. Don't. |

### The reframe

The deep-link option looks like a limitation. Look at it again:

> **Sending a message on your behalf is an R3 action. It needs a confirmation gate. WhatsApp is handing you a perfect one.**

The flow is: *"Tango, WhatsApp Rahul I'll join at 3"* → contact resolved deterministically → WhatsApp opens on your phone, correct chat, message composed → **you glance and tap.** Total time about three seconds, and you have visually confirmed both the recipient and the text — which is exactly what [ADR-007](04-decisions.md) says an R3 action needs.

Compare against Baileys: fully autonomous, no gate, no verification of what actually went out, on a library that can get your number banned and breaks whenever Meta changes something.

**The "worse" option is genuinely the better engineering.** And for messages where you truly want zero taps, use Telegram or SMS — both fully automatable, both verifiable.

---

## 4.1 Full UI automation — "open WhatsApp, find the name, send it, no stopping"

If you want the last tap gone too, there is exactly one way on Android: an **AccessibilityService**. It's worth understanding properly rather than being waved away, so here it is.

### How it actually works

Your native app registers an `AccessibilityService`, which gives it the live UI tree (`AccessibilityNodeInfo`) of whatever's on screen plus the ability to act on it:

```
1. launch WhatsApp                       (intent)
2. find search  → ACTION_CLICK           (view-id: com.whatsapp:id/menuitem_search)
3. set contact name → ACTION_SET_TEXT
4. wait for results, click first row     ← ambiguity risk lives here
5. find message box → ACTION_SET_TEXT    (view-id: com.whatsapp:id/entry)
6. find send → ACTION_CLICK              (view-id: com.whatsapp:id/send)
7. read back for the ✓✓ to confirm       ← more scraping, equally fragile
```

Those view-ids are illustrative — **they change**, which is the first problem.

### The four costs, in ascending order of seriousness

**1. It breaks constantly.** WhatsApp ships updates every couple of weeks. Every layout change can invalidate your selectors, silently. You are now maintaining a scraper against a hostile-to-scraping app, forever. This is the normal experience of Android UI automation against third-party apps, not bad luck.

**2. It can't be verified properly.** How does Tango know it sent? By scraping the UI for delivery ticks — more of the same fragility. Under [02](02-architecture.md) §4.2 this action is `UNVERIFIABLE`, which means **Tango still isn't allowed to tell you "sent."** You automated the tap and gained no certainty.

**3. Step 4 is a real correctness hazard.** WhatsApp's search returns fuzzy matches. Two Rahuls, a group with "Rahul" in the name, "Message yourself" pinned at the top. A robot clicking "the first row" will eventually send your message to the wrong person, and you won't be watching.

**4. And the serious one — AccessibilityService is total device capture.**

It reads *every screen in every app*: your banking app, your password manager, your OTPs, your private chats, everything you type. It's the most powerful permission on Android, which is why Android 13+ puts it behind Restricted Settings for sideloaded apps and why Google polices it hard.

Now recall what Tango is: a system with an LLM in it that reads untrusted content — web pages, emails, logs. Doc [08](08-openclaw-and-tango.md) documented what happened when 135,000 people combined agent autonomy with a wide blast radius, and ClawJacked exfiltrated data **by using the agent's own permissions, not by escalating them.**

Granting device-wide screen capture to that system is the single highest-risk thing available in this entire project.

### And the punchline

**The deep link already does steps 1–5.**

`wa.me/<number>?text=...` opens WhatsApp, in the correct chat, with the message typed. You never search. You never type. Contact resolution happened deterministically on Tango's side ([ADR-009](04-decisions.md)) — which is *better* than WhatsApp's fuzzy search, because it can't pick the wrong Rahul.

So the entire accessibility route buys you **step 6. One tap.**

You'd be taking on permanent breakage, unverifiable outcomes, a wrong-recipient failure mode, and device-wide screen capture on a system that reads untrusted content — to save one tap on a message you're already looking at.

### If you still want it: the contained version

Not a refusal — it's your phone. But build it with the blast radius closed:

| Constraint | Why |
|---|---|
| **Package-locked** — the service refuses to act unless the foreground package is exactly `com.whatsapp` | Kills the device-capture concern in practice |
| **Never a model-callable tool** — it is a *playbook step*, not something the LLM can invoke | An injected instruction can't reach it |
| **Requires a pre-resolved `ContactId` and pre-composed text** — both fixed before the step runs | No free-text recipient, ever |
| **Blocked when `max_trust_tier == UNTRUSTED`** | The trifecta interlock ([02](02-architecture.md) §3.4) |
| **Never claims "sent"** — outcome is `UNVERIFIABLE` unless a tick is scraped, and even then say "appears delivered" | [ADR-004](04-decisions.md) |
| **Off by default; single toggle to disable** | You'll want it when a WhatsApp update breaks it at 2am |

Do this in **Week 7+, after everything else works.** It's the most fragile thing you'd own and the least valuable per hour spent.

### The other reading — "without any app at all"

If you meant *can this work without building a native Android app*: no. UI automation requires an `AccessibilityService`, which requires a native app. A PWA cannot do it, and neither can anything running on the laptop.

The zero-app options are the ones already in the table: **Telegram and SMS**, both fully autonomous, both verifiable, no tap, no native app, no fragility. If "no stopping" matters more than "must be WhatsApp", that's your answer — and it's available in Week 3 rather than Week 7.

---

## 5. What this changes in the plan

Small amendments, not a redesign.

| Change | Where |
|---|---|
| **Wake word moves into Week 2** with voice, Windows-only | [03](03-roadmap.md) / [06](06-the-jarvis-question.md) §7 |
| **Pre-rendered ack + response audio** — treat as a hard requirement, not an optimisation | Week 2 |
| **One-shot bypass** — no forced two-turn handshake | Week 2 |
| **Alarms, timers, reminders, app launch** — add to the Week 1 playbook set. Easiest, most reliable, most Alexa-like things you can build | Week 1 |
| **Voice readback for all R3** | Week 5 |
| **Contact resolver + Android call/SMS bridge** | Week 5, with the assist-gesture app |
| **WhatsApp = deep link only.** No Baileys. Recorded as a decision. | ADR-011 below |

### ADR-011 — WhatsApp via deep link, never via unofficial libraries

**Status:** Accepted

**Decision.** Tango composes WhatsApp messages via `wa.me` deep links and hands the send action to you. It does not use Baileys, whatsapp-web.js, or any reverse-engineered client.

**Why.** Ban risk to your personal number, permanent breakage exposure on someone else's protocol changes, and no verification of what was actually delivered. The deep link is stable, official, and supplies the R3 confirmation gate that architecture demands anyway. Telegram and SMS cover the cases where a fully autonomous send genuinely matters.

**On full UI automation:** an AccessibilityService can remove the final tap (§4.1). It is not forbidden — it's your phone — but it is deferred to Week 7+ and, if built, must be package-locked to `com.whatsapp`, invocable only as a playbook step (never as a model-callable tool), gated on a pre-resolved `ContactId`, blocked under `UNTRUSTED` context, and never permitted to claim "sent". The deep link already covers everything except the tap itself.

**Reconsider if:** Meta ships a personal-account API. Unlikely.

---

## 6. So, concretely, what your voice loop looks like

**At the desk, hot mic:**
```
You:    "Tango."
Tango:  "Yes sir."                                    (pre-rendered, ~50 ms)
You:    "Set an alarm for 6:30."
Tango:  "Alarm set, 6:30 AM."                         (~800 ms, no LLM involved)
```

**One-shot, no handshake:**
```
You:    "Tango, start optiresume."
Tango:  "DB up. API on 8000. Editor open."            (~3 s, verified per step)
```

**Consequential, with readback:**
```
You:    "Tango, call mom."
Tango:  "Calling Mom — mobile, ending 4821. Say yes."
You:    "Yes."
Tango:  "Dialling."                                    (phone rings ~2 s later)
```

**The agent half, same wake word:**
```
You:    "Tango, why is the optiresume API throwing 500s?"
Tango:  "One moment."                                  (pre-rendered — covers the model latency)
        [ ~6 s of real tool calls ]
        "Database password mismatch. The .env changed 12 minutes ago and
         no longer matches what the db volume was initialised with.
         I can revert the .env, or drop the volume — that destroys local data."
```

Same wake word, same voice. The router silently picked the Alexa half three times and the agent half once, and you never had to know which.

**That's the mixture.**
