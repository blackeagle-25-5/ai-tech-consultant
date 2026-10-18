# AI Tech Consultant

One system that knows a software product, drafts customer email replies, and turns client meetings into
a scope of work, estimate and proposal. It uses the Brilliant Directories help center as its knowledge
and keeps Claude at the centre.

| Task | Folder | What it does |
|---|---|---|
| 1. Knowledge Brain | `task1_knowledge_brain/` | Crawls the help center politely, understands screenshots, answers with citations, escalates to humans, syncs daily. FastAPI service. |
| 2. Inbox Agent | `task2_inbox_agent/` | Classifies emails, drafts grounded replies via the Task 1 API, human review, learns from edits, earns autonomy after 20 unchanged sends. |
| 3. Meeting to Scope | `task3_meeting_to_scope/` | Five agents: transcript → requirements → feasibility (via Task 1 API) → scope → estimate → proposal. Compares follow-ups against the approved baseline. |

All three tasks are Python (the brief allows Python for Task 2). Tasks 2 and 3 reach Task 1 only through
its HTTP API (`common/kb_client.py`). Email sending and proposal delivery are **simulated**; nothing leaves the machine.

## Setup (Windows, macOS or Linux; Python 3.11+)
```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env, then put your ANTHROPIC_API_KEY in .env
```

## First-time knowledge ingestion (about 1 hour, resumable)
```bash
python -m task1_knowledge_brain.ingestion            # crawl ~1,540 articles + describe screenshots
python -m task1_knowledge_brain.ingestion --limit 20 # quick trial
```
Stop it any time with Ctrl+C. Running it again continues where it stopped.

## Run everything (one command)
```bash
python run.py
```
- App: http://localhost:8501 (Knowledge Chat, Inbox Review, Scope Builder)
- Knowledge Brain API: http://127.0.0.1:8000/docs (contract in `task1_knowledge_brain/API.md`)

## Tests and evaluations
```bash
pytest                                                    # offline, no API cost
python -m task1_knowledge_brain.evals.run_evals --part retrieval
python -m task1_knowledge_brain.evals.run_evals --part answer
python -m task2_inbox_agent.evals.run_evals --part all    # needs run.py running
python -m task2_inbox_agent.evals.run_learning
python -m task3_meeting_to_scope.evals.run_evals
```
Results go to `evals_results/`. See `EVALS.md` for numbers, `DESIGN.md` for architecture and decisions,
and `AI_LOG.md` for how Claude Code was used.

## Configuration (`.env`)
`ANTHROPIC_API_KEY` (required for answers and drafts) · `GROQ_API_KEY` (temporary free fallback for development only, used when no Claude key is set; results are labelled `groq:` and are not the final Claude results) · `KB_API_KEY` (optional API protection) ·
`CRAWL_CONCURRENCY` / `CRAWL_DELAY_S` (politeness) · `MAX_IMAGES` (vision budget per run) ·
`KB_ANSWER_MODEL`, `SCOPE_*_MODEL` (model routing). Pricing assumptions for proposals: `task3_meeting_to_scope/pricing.yaml`.
