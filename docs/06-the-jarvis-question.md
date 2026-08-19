# 06 — The Jarvis Question

> "Will Tango be nearly as good as Jarvis?"

**Short answer: inside the walls you own — yes, genuinely, ~80%. Outside them — no, ~20%, and that gap is not your fault and not closeable by anyone in 2026.**

The long answer is more interesting than the short one, because *which* 20% is missing turns out to be the thing that decides how you should build it.

---

## 1. First, what Jarvis actually is

The films show you the conversation, because conversation is what's cinematic. But if you decompose the character into capabilities, the conversation is the small part.

| # | Capability | What it looks like |
|---|---|---|
| J1 | Speech understanding | Noisy lab, Tony talking over him, half-finished sentences. Never asks "did you mean?" |
| J2 | Reasoning & judgment | Has opinions. Pushes back. Knows when Tony is being an idiot. |
| J3 | Memory & continuity | Years of context. Knows what you were doing last night, and why. |
| J4 | Personality & rapport | Dry, loyal, a character. You'd miss him. |
| J5 | Actuation over owned systems | Lab equipment, fabrication, suit telemetry, house systems. |
| J6 | Actuation over *everything else* | No API keys. No OAuth. No rate limits. No vendor saying no. |
| J7 | Physical sensing | Cameras, sensors, the building, the suit. He **sees**. |
| J8 | Long-horizon autonomy | "Run the simulation overnight." Comes back with a result. |
| J9 | Proactivity with taste | Surfaces what matters. Doesn't nag. |
| J10 | Absolute reliability | No latency. No downtime. Never "I didn't catch that." |

The single most important line in that table is **J6**, and it's the one nobody notices.

---

## 2. The thing about Jarvis nobody says out loud

**Jarvis is roughly 20% AI and 80% infrastructure — and Tony Stark built the infrastructure.**

Every device Jarvis talks to was designed by the same person who deployed Jarvis. There is no vendor in Tony's world who doesn't want to be automated. No app that forbids bots in its ToS. No bank with no public API. No phone manufacturer killing background processes to save battery. No rate limit. No OAuth consent screen. No CAPTCHA.

That is the actual superpower in the fiction, and it's presented as set dressing.

So when you ask "can I build Jarvis" — the honest reframe is: **you can build Jarvis over the parts of your world that you own outright.** That's not a consolation prize. It's a genuinely large territory, and you happen to own more of it than most people do.

---

## 3. Honest scorecard

What's actually reachable, in 2026, on your laptop and phone, built by you.

| Capability | Reachable? | Why |
|---|---|---|
| **J1 Speech understanding** | **90%** | This one has genuinely *arrived*. Whisper-class STT plus a strong model handles messy, interrupted, accented, mid-thought speech better than anything depicted in 2008. Not the bottleneck anymore. |
| **J2 Reasoning & judgment** | **95%** | Current frontier models exceed the Jarvis depiction here. Pushback, opinions, "that's a bad idea because —" is a prompt away. |
| **J3 Memory & continuity** | **85%** | Achievable. And in one respect you beat him: your memory is inspectable and correctable. Jarvis's isn't. |
| **J4 Personality & rapport** | **90%** | A prompt and a voice. Cheap. And more load-bearing than it sounds — see §5. |
| **J5 Actuation, systems you own** | **85%** | Files, git, Docker, deploys, processes, browser, your own services. Full authority, no gatekeeper. **This is your Jarvis territory.** |
| **Diagnosis over your own telemetry** | **80%** | Reading logs and forming a hypothesis is a genuine model strength. |
| **J8 Long-horizon autonomy** | **30%** | Agents still drift over multi-hour unsupervised work. Bounded, verified versions work. "Build me a new suit overnight" does not. |
| **J9 Proactivity with taste** | **25%** | Trivial to build, brutally hard to tune. Jarvis interrupting wrongly three times a day gets muted in a week. This is a *taste* problem, not a tech problem, and taste is slow. |
| **J10 Ambient always-on** | **40%** | Hot mic is technically easy. False wakes, power, and the creep factor currently make push-to-talk *better*, not worse. |
| **J6 Actuation, systems others own** | **10%** | Your bank has no API for you. WhatsApp doesn't want a bot. Your phone's OEM kills background services. **Structurally blocked by other parties' interests, not by difficulty.** |
| **J7 Physical sensing** | **5%** | A hardware project, not a software one. Cameras and a CV pipeline you'd then have to maintain forever. |
| **J10 Absolute reliability** | **40%** | Laptops sleep. Models cold-start. Networks drop. |

**Weighted honestly: ~80% inside your dev environment. ~20% across your whole life.**

And the crucial point: **nobody has better.** Not Google, not OpenAI, not Apple. The missing pieces are locked by other companies' business interests and by physics, not by your skill or your budget. You are not falling short of a bar someone else has cleared.

---

## 4. Where this leads: go deep, not wide

The instinct — and the original spec's instinct — is to go **wide**: desktop, phone, email, calls, calendar, web, Docker, Kubernetes. Breadth *feels* like Jarvis because Jarvis is everywhere.

But look at the scorecard. Breadth drags you straight into J6 territory, where you're a guest in someone else's house and you'll be at 10% no matter how well you engineer. Every hour spent fighting an OEM battery manager or a rate limiter is an hour not spent on the 85% column.

**Depth in the territory you own gets you the Jarvis feeling faster than breadth ever will.**

A Tango that knows your five projects *completely* — every service, every port, every deploy target, every failure mode you've ever hit, every command you run — and answers instantly by voice, with a personality, and never lies to you, will feel more like Jarvis than one that can technically send a WhatsApp message after a confirmation tap.

Jarvis-in-the-lab is achievable. Jarvis-in-the-world isn't, for anyone, yet. **Build the lab.**

---

## 5. What actually makes it *feel* like Jarvis

This is the part the original spec has nothing on, and it matters more than most of the architecture.

The Jarvis feeling doesn't come from capability. It comes from five things:

**1. Latency.** Jarvis answers *instantly*. This is the big one and it's badly underrated. A 4-second pause doesn't make him a slower Jarvis — it makes him not-Jarvis. The illusion of presence dies somewhere around 1.5 seconds. **Sub-second on routine things is worth more than any feature on the roadmap.** This is an argument for deterministic playbooks and a warm local model, and against a cloud round-trip on the common path.

**2. Voice, as the medium — not as a feature.** Jarvis is fundamentally a voice you talk to. A text Tango will never feel like Jarvis regardless of how capable it gets. I had voice at week 6 in the roadmap. **That was wrong for your objective** — see §6.

**3. Continuity of address.** No wake word every sentence, no "command mode", no starting over. You say "start optiresume", then "actually kill it", then "why did it fail" — and it follows. Anaphora and short-term context are cheap to build and enormously load-bearing for the feeling.

**4. Personality.** He's dry. He's a bit sardonic. He calls you "sir." My §8 response rules — *"lead with outcome, one line, never narrate"* — are correct for a **tool** and actively hostile to a **presence**. Second thing I got wrong. See §6.

**5. Absolute trust.** And this is the one I want to dwell on.

---

## 6. The most Jarvis-like thing in the whole plan is the boring part

Watch what Tony does when Jarvis says *"the reactor is at 400% capacity."*

He acts. Immediately. He doesn't check. He doesn't ask "are you sure?" He never once verifies Jarvis against a second source.

**That total, unhesitating trust is the entire relationship.** It's what separates a Jarvis from a search box with a nice voice. And it exists because Jarvis has never, in the history of their working together, said something was true when it wasn't.

Now — that's exactly what the ledger and claim-licensing layer in [02-architecture.md](02-architecture.md) §3.2–3.3 is for.

I originally pitched it as engineering discipline, which made it sound like compliance paperwork. It isn't. **It's the mechanism that makes the Jarvis relationship possible.** An assistant that says "started your dev environment" when the DB actually failed doesn't just have a bug — it permanently converts you into someone who double-checks. And an assistant you double-check is a slower way of doing the task yourself. That's the death, and it's usually irreversible: trust is lost in one incident and rebuilt over months.

So the least glamorous part of the plan turns out to be the part that decides whether Tango feels like Jarvis or like a voice-activated shell script. Keep it. It's not overhead. **It's the character.**

*(As a footnote: ADR-010 argued for extracting that layer as a reusable library. That argument was about audience and doesn't apply — ignore it. Build it exactly the same way internally; just don't do it for anyone else's benefit.)*

---

## 7. Amendments to the plan

Two things I got wrong once the objective is "personal Jarvis" rather than "maximum utility per hour."

### Amendment 1 — Voice moves from Week 6 to Week 2

Voice isn't a feature of Tango; it's the medium Tango exists in. Shipping it last means spending five weeks building something that will need rethinking once it can talk.

This also *strengthens* an argument already in the critique — [F19](01-verdict-and-critique.md) flagged that voice retroactively changes the whole confirmation design, because you can't show a preview in a voice-only flow. Better to hit that constraint in week 2 with four playbooks than in week 6 with thirty.

**Revised order:**

| Week | Was | Now |
|---|---|---|
| 0 | Spine (regex router, ledger, verification) | **unchanged** — still right, still first |
| 1 | Ten playbooks + eval loop | **unchanged** |
| 2 | Diagnostics | **Voice + personality** — push-to-talk, local STT/TTS, continuity of address |
| 3 | Remote / PWA | **Diagnostics** |
| 4 | Standing auths + undo | **Remote / PWA** (now a voice remote, which is the Jarvis form) |
| 5 | Email connector | **Standing auths + undo windows** — removing friction *is* Jarvis-ness |
| 6 | Voice + hardening | **Presence & proactivity**, then hardening |

Week 0 stays exactly as it is. The regex-router-first trick is *more* important here, not less — it's what lets you reach voice in week 2 standing on a spine that's already proven.

### Amendment 2 — Personality is a feature, not noise

Strike the "one line, never narrate, minimal prose" rule from [02-architecture.md](02-architecture.md) §8. Replace with:

- **Outcome verbs stay claim-licensed.** Non-negotiable — that's §6 above, and it's the whole relationship.
- **Everything around them is yours to shape.** Tone, wit, forms of address, how it reacts to a failure, whether it's amused when you break the same thing twice.
- **Brevity by default, personality in the seams.** Jarvis is brief on routine confirmations and expansive when something is interesting or wrong. That's the same lumpiness the good version of this system should have anyway.

The constraint is narrow and precise: **Tango can say anything it likes, in any voice it likes, as long as it never claims an outcome it hasn't verified.**

---

## 8. So — will it be nearly as good as Jarvis?

Over your development environment, your projects, your machine, your files: **yes, and closer than you'd guess.** The gap between "good personal agent in 2026" and "Jarvis in the lab" is mostly latency, voice, taste and trust — all of which are within reach for one person over a couple of months.

Over your bank, your phone's OS internals, your physical surroundings, and anything owned by a company that doesn't want to be automated: **no, ~10%, permanently, for now.** Not because of you. Because those doors are held shut from the other side.

The real risk to this project isn't capability and it isn't ambition. It's that **you build the 20% that's blocked instead of the 80% that isn't**, run out of momentum in the locked corridors, and never reach the part that would have felt like Jarvis.

Build the lab. The lab is very good.
