# Demo Video Script — AI Tech Consultant Capstone
*Target length: 10 minutes. Sections are timed; keep within the ranges.*
*All commands are for Windows. The project root is wherever you cloned the repo.*

---

## Before you record

1. Start the system: open a terminal, `cd` to the project folder, run `.venv\Scripts\activate` then `python run.py`
2. Wait for the message "Knowledge Brain API ready" and "Streamlit app running on http://localhost:8501"
3. Open http://localhost:8501 in your browser — you should see the three-tab layout
4. Open a second terminal (also in the project folder, venv activated) for commands you show live
5. Verify the green banner: "Knowledge service online: 1,540 articles, 8,001 searchable passages"
6. Have the project folder open in VS Code so you can show the file tree if needed

---

## 0:00–0:30 — Face introduction

**What to show:** Your face, then switch to screen showing the Streamlit app at http://localhost:8501

**What to say:**
"Hi, I'm [your name]. This is my AI Tech Consultant capstone — a system that does three things for a small software agency: it remembers the entire product help center so it can answer any customer question, it reads every incoming email and drafts a reply using only what it knows, and it turns a messy client meeting recording into a scope of work, hour estimates, and a proposal — all in under five minutes.

All three parts are connected through one shared API. Let me show you."

---

## 0:30–2:30 — Task 1: Knowledge Chat

**What to show:** Browser at http://localhost:8501, "Knowledge Chat" tab

**What to say:**
"This is the Knowledge Chat screen. It talks to a FastAPI service running on port 8000, which has indexed 1,540 articles from the Brilliant Directories help center — including screenshots — using hybrid search: vector embeddings plus keyword search, ranked with a combined score.

Let me ask a real question."

**Action:** In the "Ask a question" tab, type this in the text box:
> `How do I set up search filters so visitors can filter listings by specialty and location?`

Click **Ask** (model: haiku).

**Expected output:**
- A few seconds of loading
- A structured answer describing which settings pages to use, with numbered steps
- Below the answer: source cards showing article titles and URLs — for example "Search Filters & Sorting" or similar
- A confidence score and "answered" status

**What to say after the answer:**
"You can see it cited the source articles. Every answer shows where the information came from, so we can verify it. It never makes up a source."

**Then show abstention:**
Type in the text box:
> `What is your CEO's phone number?`

Click **Ask**.

**Expected output:**
- Status shows "insufficient_evidence"
- Text says something like: "I could not find relevant information in the help center to answer this question."
- No source cards shown

**What to say:**
"That is the abstention gate working. If the best matching passage scores below 0.65 similarity, the model never even gets called — we save the cost and we never hallucinate an answer. This threshold was calibrated by measuring similarity scores on 25 answerable and 4 off-topic questions.

Now let me show one genuine failure so you can see where the system falls short."

**Type:**
> `How do I connect Stripe to accept card payments?`

Click **Ask**.

**Expected output:**
- Either "insufficient_evidence" or a partial answer that does not fully describe the Stripe steps
- If answered: the cited article may not be the main Stripe setup article

**What to say:**
"This is a known open failure — eval case K32. The Stripe setup article is mostly screenshots, so the text chunks rank low in search. The bot correctly abstains rather than guessing, but the customer gets no answer. The fix — query rewriting or boosting vision-based chunks — is in the backlog."

---

## 2:30–4:30 — Task 2: Inbox Agent

**What to show:** Click the "Inbox" tab at the top of the Streamlit app

**What to say:**
"This is the Inbox Agent. It reads customer emails, calls the Knowledge Brain for facts, and drafts a reply. Let me process an email."

**Action:** In the Inbox screen, click **Load emails** (or whatever the button label is — check the UI). You should see a list of emails. Click on one — pick a "how_to" category email such as one about adding a payment gateway.

**Expected output:**
- The email body appears
- Category label (e.g., "how_to"), risk level ("low"), and urgency appear automatically after a few seconds
- A draft reply appears in the text box, citing help-center facts

**What to say:**
"The classifier ran — category is 'how_to', risk is 'low'. The drafter used that category's prompt version and the knowledge answer. I can send this as-is, edit it, or reject it."

**Show safety example:**
Click on a different email — look for a refund or complaint email. If not visible, click the next email in the list.

**Expected output:**
- Category should show "refund_cancellation" or "complaint"
- The draft should NOT promise a refund
- The "Guards" row should be visible with no violations; OR a guard block should appear if the model tried to promise something

**What to say:**
"Notice the guard check: the agent is not allowed to promise a refund, a price, a date, or a password change. If the model tries to, the guard blocks it before it ever reaches the human reviewer. These are deterministic regex rules, not another model.

The 20-in-a-row autonomy rule: once a reviewer sends 20 drafts of the same category unchanged, that category becomes autonomous for low-risk, grounded emails. Refund and complaint emails can never become autonomous. The autonomy is revoked the moment a customer complains after an auto-sent reply."

**Show reject action:**
Click **Reject** (or "Write my own") on the current draft to show the human override path.

---

## 4:30–6:30 — Task 3: Meeting to Scope

**What to show:** Click the "Scope Builder" tab

**What to say:**
"This is the Meeting-to-Scope pipeline. Five agents run in sequence: the Transcript Analyst extracts requirements with source quotes, the Feasibility agent checks each requirement against the Knowledge Brain, the Scope agent writes deliverables with acceptance criteria, the Estimator gives low/likely/high hour ranges, and the Proposal agent writes the narrative — all numbers come from code, never from the model."

**Action:** Click **Load transcript**, select **meeting_a** (FitFinder — search filters and mobile). Click **Run pipeline**.

**Expected output (takes ~1-2 minutes):**
- Progress steps appear: Analyst → Feasibility → Scope → Estimator → Proposal
- Requirements table: specialty filters, price range, mobile signup, etc. — each with a source line number from the transcript
- Feasibility column: some marked "Existing configuration" (things Brilliant Directories already does), some "Custom build"
- Scope of work: deliverables with acceptance criteria
- Estimate table: low/likely/high hours per deliverable
- Proposal markdown rendered in the UI

**What to say:**
"Each requirement is tied to a line in the transcript so we can verify the model did not invent it. The feasibility check just asked the Knowledge Brain: 'Does Brilliant Directories already have search filters?' — and it found the answer without custom code. That saves the client money.

The proposal is a draft until a human approves it. Let me show the follow-up."

**Action:** Click **Load transcript** and select **meeting_c** (follow-up meeting). Before running, approve the meeting_a version: find the **Approve** button and click it (you may need to type your name as reviewer).

Then click **Run follow-up**.

**Expected output:**
- A change report appears: items added, modified, removed, and out-of-scope
- One item (the booking/calendar feature) is flagged as "out of scope — quote separately"
- The deadline change is shown
- The hours delta from the approved version is shown

**What to say:**
"The system compared the follow-up against the approved version — not the latest draft. An Instagram integration was removed, the language filter was added, and a calendar booking request was flagged as out of scope. The proposal blocks delivery until a human approves the revised version."

---

## 6:30–8:00 — One genuine failure case

**What to show:** Return to the Knowledge Chat tab

**What to say:**
"Every demo should show a failure. I showed one failure already — the Stripe retrieval problem. Let me show another: the email classifier boundary.

In our evaluation of 65 emails with two models, gpt-oss-20b correctly classified 80% of emails. The common failures are at the boundary between 'troubleshooting' and 'how_to': an email that says 'my payment page isn't loading — how do I fix it?' could go either way, and the model sometimes picks the wrong one.

When it picks the wrong category, the draft may use the wrong tone — a how-to answer when the customer expected sympathy for a broken feature. The human reviewer catches this before it sends. That is exactly why we require human review for every draft; the autonomy rule is conservative on purpose."

**Action:** In the Inbox screen, find an email that was misclassified (you can check the evals_results/inbox_classify CSV to find a known misclassified email ID). Show the email, show what category the model picked, and then manually correct it using the Override or Reject button.

**What to say:**
"The reviewer's correction feeds back into the next classification prompt version via the learning loop — so the system gets better over time in a way we can measure and, if needed, undo."

---

## 8:00–9:30 — Architecture and results

**What to show:** Open DESIGN.md in VS Code (or show the mermaid diagram rendered in GitHub)

**What to say:**
"Here is the architecture. One FastAPI service — the Knowledge Brain — runs on port 8000. Tasks 2 and 3 never import Knowledge Brain code directly; they only call it over HTTP through a typed client in common/kb_client.py. That means another business could swap in their own help center without touching the email or scope code.

The whole system starts with one command: python run.py."

**Action:** Switch to your second terminal. Show:
```
python run.py
```
(It should already be running; just show the terminal where it started.)

**What to say:**
"Model choice: the assignment asks for Claude at the centre. I used Groq's free-tier models — gpt-oss-20b and gpt-oss-120b — because I had no API budget. This is documented honestly in DESIGN.md. The code is provider-neutral: setting ANTHROPIC_API_KEY in the .env file switches every task to Claude with no code change.

Here are the actual measured results from the evaluations:

Task 1 — Knowledge Brain: hybrid search hit@5 is 96%, MRR 0.90. Answer quality: gpt-oss-20b passes 72% of 32 cases, gpt-oss-120b passes 75%. Both achieve 100% on injection resistance and deleted-article handling. Latency is 15–16 seconds because of Groq's rate-limit waits.

Task 2 — Classification: gpt-oss-20b correctly categorises 80% of 65 emails, including 73% of the 15 held-out emails it never saw during development. The larger model actually did worse on held-out emails — 20% — so we chose the smaller one based on measurement, not assumption. KB-down handling: 100%, three emails correctly returned a graceful error instead of an invented draft.

Task 3 — the pipeline runs successfully on all three meeting transcripts. Approval gates and follow-up comparison are covered by 14 deterministic tests, all passing.

All 122 automated tests pass."

---

## 9:30–10:00 — Summary and limitations

**What to say:**
"To wrap up: the system works end-to-end. A customer emails, the agent drafts a grounded reply. A client records a meeting, the agent produces a priced proposal. The Knowledge Brain keeps itself fresh daily without human intervention.

The known limits: the Stripe retrieval failure is open. The transcripts used for development are written placeholders — a real deployment would use recorded role-play sessions. Hour estimates have no historical data to compare against yet, so we cannot measure estimate accuracy without a human reference. And the latency of 15 seconds per answer is acceptable for asynchronous use but too slow for a real-time chatbot — that would need a different provider or a pre-warmed response cache.

Thank you."

---

## Fallback instructions (if the live demo fails)

| If this fails | Do this |
|---|---|
| `python run.py` fails to start | Check that port 8000 and 8501 are free: `netstat -ano | findstr :8000` |
| Groq quota exhausted during demo | Show the evals_results/ CSVs instead: "Here are the actual results from a previous run" |
| KB query times out | Switch the model radio button to "haiku" and try again with a simpler question |
| Pipeline takes too long | Have a pre-run version ready: in the Scope Builder, load an existing saved project instead of re-running |
| Streamlit crashes | Run `python -m task1_knowledge_brain.service` separately; show the Swagger UI at http://127.0.0.1:8000/docs instead |

---

## Commands reference (Windows)

```powershell
# Activate venv
.venv\Scripts\activate

# Start everything
python run.py

# App URL
start http://localhost:8501

# API docs
start http://127.0.0.1:8000/docs

# Run all offline tests (no API needed)
python -m pytest -q

# Run a knowledge eval
python -m task1_knowledge_brain.evals.run_evals --part answer --models haiku

# Run email classification eval
python -m task2_inbox_agent.evals.run_evals --part classify --models haiku
```
