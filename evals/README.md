# Golden set — S0.1 draft

`golden.draft.jsonl` — 61 rows, drafted by Claude, **awaiting the owner edit pass** (the ~2 h of PO time doc 16 §14.1 budgets).

Row schema: `id · utterance · expect (playbook+params | route:plan | clarify | decline | refuse) · strata (canonical/natural/ambiguous/context/oos/refuse/near-miss/fact) · lang (en | hi-en) · author · note? · context? · phase?`

**The owner pass (what makes this real):**
1. Rewrite `natural` rows into phrasings you *actually say* — the drafts are guesses. Change `author` to `owner` on every row you touch.
2. Add ~20 rows of real phrasings as you use Tango in week 1 — especially Hinglish and half-sentences.
3. Decide the flagged judgement calls (g016, g038, g050, g060 — see `note`).
4. Rows with `phase` tags are future capabilities: the harness expects an honest "not available yet" until that phase ships.

**Rules from [17](../docs/17-plan-review-v1.1.md) C3:** this file later splits ~70/30 into router-corpus and a **sealed holdout** — owner-authored rows are preferred for the holdout. Gates are measured on the holdout only. Every real-world misroute becomes a corpus row the same day it happens.
