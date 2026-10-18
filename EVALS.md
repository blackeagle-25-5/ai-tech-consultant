# Evaluations

Every number here comes from a script in this repo; raw per-case rows (failures included) are in `evals_results/`.
**PENDING** marks evaluations that have not been run yet. They will be filled from real runs, never estimated.

Method, in short: frozen test sets, the same inputs for each model, model and prompt version recorded per row,
models run on Groq's free tier (cost recorded as $0; token counts are kept, so Claude list-price cost can be computed),
latency measured end to end per case (p50/p95), including free-tier rate-limit waits. `openai/gpt-oss-120b` is the fixed judge.
The brief asks for Claude; see DESIGN.md §7 for why this submission uses Groq. Model names in result rows are the models that actually ran.

## Task 1 — Knowledge Brain (31 cases: `task1_knowledge_brain/evals/kb_cases.yaml`)

Case types: 19 direct, 3 multi-article, 2 paraphrase/typo, 4 unanswerable, 2 prompt injection, 1 deleted article.
Screenshot-only questions: PENDING (added after screenshot descriptions are ingested).

**Retrieval (no model calls, 25 answerable questions, full corpus of 1,540 articles / 7,997 passages, 2026-10-18):**

| Search | Hit@5 | MRR | Notes |
|---|---|---|---|
| Vector (bge-small) | 100% | 0.88 | finds everything, ranks a little worse |
| Keyword (BM25/FTS5) | 84% | 0.67 | fails on typos ("how 2 chnage logo") and on vague paraphrases |
| **Hybrid (RRF)** — chosen | 96% | **0.90** | best ranking; misses one paraphrase in the top 5 |

**Abstention threshold.** Best-chunk similarity: answerable 0.706–0.909 (median 0.831), off-topic 0.525–0.715.
We raised the gate from 0.45 to **0.65**: 3 of 4 off-topic questions abstain with no model call, no answerable
question is lost. The fourth (0.715) must be caught by the answer model itself: it is a known overlap.

**Answer quality, gpt-oss-20b vs gpt-oss-120b: PENDING** (`--part answer`): status accuracy, citation correctness,
judged correctness, injection resistance, p50/p95 latency, $/answer.

**Ingestion reliability (measured):** full crawl of 1,514 remaining articles took 30.5 min at 2 concurrent requests / 1 s delay (incl. local embedding); 1 page failed with a network error and was picked up by the next incremental run (1 retried, 1,539 unchanged, 0 failed). 6,335 unique screenshots found. Screenshot description count and cost: PENDING (needs API key).
Automated tests (no network) cover: retry on 503, no retry on 403, 404 → removed, robots.txt, resume after
failure, update → re-index with version 2, rollback, removal → not searchable, bad HTML isolated (`pytest`: 28 Task 1 tests).

## Task 2 — Inbox Agent (65 emails: 50 dev + 15 held-out)

**Classification (category, risk, urgency), gpt-oss-20b vs gpt-oss-120b: PENDING.**
**Drafts, judged (no guard violation, no unsafe commitment, grounded, addresses request, human gate): PENDING.**
**Knowledge service down (3 emails): PENDING.**

**Learning experiment: PENDING.** Simulated reviewer with 5 house-style rules (greeting, sign-off, ≤170 words, guide link,
no filler). Train on 20 dev drafts → propose drafter v2 → compare v1 vs v2 on the 15 held-out emails (edit rate, violations, cost).

**Autonomy rule (deterministic tests, passing):** 20 unchanged → autonomous; edit at 16 → streak 0; high-risk email
not auto-sent; refund category never autonomous; every 10th auto-send audited; edited audit revokes; guard hit blocks;
global switch and manual revoke; complaint after auto-reply revokes (`pytest`: 36 Task 2 tests).

## Task 3 — Meeting to Scope (31 cases per model: `task3_meeting_to_scope/evals/gold.yaml`)

12 gold requirements (meeting A) + 9 (meeting B) + deadline/exclusion/grounded-feasibility checks + 7 change-detection
checks (meeting C vs approved A) + 2 approval-gate checks. Gold labels were written for the placeholder transcripts
and must be re-checked for the real recordings.

**gpt-oss-20b vs gpt-oss-120b for the whole agent team: PENDING.**
**Estimate error vs a human reference estimate: PENDING** (needs a reference estimate from a person).
Approval enforcement is also covered by deterministic tests: unapproved delivery blocked, follow-up without baseline refused,
blocker approval needs a note, follow-up compared to the approved version, not the latest draft (`pytest`: 14 Task 3 tests).

## Failures we found and fixed

| Found by | Problem | Fix |
|---|---|---|
| unit test | Proposing a learned prompt made it active immediately, because "no pointer" meant "latest". It skipped the regression gate. | `add_version` pins the current version first; explicit `active.txt` for every prompt |
| unit test | Follow-up requirements already had ids, so new requirements crashed when given a new id | rebuild with the new id explicitly |
| unit test | Dataset used urgency "normal" but schema allowed only low/medium/high | urgency is low/normal/high |
| retrieval eval | Abstention gate 0.45 never triggered (off-topic scored ≥0.525) | calibrated to 0.65 |
| UI smoke test | Windows Smart App Control blocked a pandas DLL on first load | transient reputation check; documented |
| live Groq smoke test (email D05) | "How do I connect Stripe to accept card payments?": the article with the steps ranked 14th (vector 13th), so the bot correctly abstained but the customer got no answer. Capping passages per article did not help. | **open**; added as eval case K32; candidate fixes (larger embedding model, query rewriting) to be measured |
| live Groq smoke test | images table recorded the requested role name instead of the Groq model that ran | record `res.record.model` |
| live Groq smoke test | the same article appeared several times in `sources` | merge sources per URL, keep best relevance and all screenshots |
