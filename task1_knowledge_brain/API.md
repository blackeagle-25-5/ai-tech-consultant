# Knowledge Brain API

Base URL: `http://127.0.0.1:8000` (started by `python run.py`). Interactive docs: `/docs` (OpenAPI).
All bodies are JSON. If the server has `KB_API_KEY` set, send it as the `X-API-Key` header on every
endpoint except `/health`. Every response carries an `X-Request-ID` header (send your own to correlate logs).

## POST /query: answer a question with citations

```json
{ "question": "How do I connect my own domain?", "top_k": 8, "requester": "inbox-agent",
  "escalate": true, "model": "haiku" }
```
| Field | Rules | Meaning |
|---|---|---|
| `question` | 3–2000 chars, required | The user's question. Treated as data, never as instructions. |
| `top_k` | 1–20, default 8 | Passages retrieved as evidence. |
| `requester` | optional, ≤100 chars | Who asks; stored on escalations. |
| `escalate` | default `true` | Create a human task when the answer is not supported. |
| `model` | `haiku` \| `sonnet`, optional | Override the answer model (default `KB_ANSWER_MODEL`). |

Response `200`:
```json
{ "request_id": "3f2a9c1b7d10", "status": "answered",
  "answer": "Go to Settings > Domain Manager ...",
  "sources": [{ "title": "How to Connect a Domain ...", "url": "https://support.brilliantdirectories.com/...",
                "section": "Step 1", "source_type": "article", "relevance": 0.84, "image_urls": [] }],
  "confidence": "high", "needs_human": false, "escalation_id": null,
  "model": "claude-haiku-5-5", "prompt_version": "answer@v1", "latency_ms": 2140, "cost_usd": 0.00041 }
```
- `status`: `answered`, or `insufficient_evidence` (the help center does not support an answer; `answer` says what is missing).
- `sources`: only passages the model actually cited and that were really retrieved. `source_type` is
  `article` (text), `image` (AI description of a screenshot; `image_urls` links the original) or `expert` (approved human answer).
- `relevance`: cosine similarity of question and passage (0–1). It is not a probability.
- `confidence`: the model's own rating of how directly the evidence answers. EVALS.md reports how well it tracks accuracy.
- `needs_human`: `true` whenever `status` is not `answered`. `escalation_id` is set if a human task was created.

Errors: `422` invalid input · `401` bad/missing API key · `503` Claude not configured · `502` model call failed ·
`504` answer took longer than 60 s · `500` unexpected (details in server log).

## POST /search: retrieval only (no model call, no cost)
`{ "query": "stripe payment gateway", "k": 8, "mode": "hybrid" }` with `mode` = `hybrid` | `vector` | `keyword`.
Returns a list of `{chunk_id, title, url, section, source_type, text, image_urls, similarity}`.

## Escalations (human expert loop)
- `GET /escalations?status=open|resolved` lists tasks (question, reason, closest passages found, latest answer).
- `POST /escalations` with `{ "question": "...", "reason": "...", "requester": "..." }` creates a task manually (`201`).
- `POST /escalations/{id}/resolve` with
  `{ "answer": "...", "expert": "Asha", "scope": "general", "source_url": null, "expires_at": "2027-01-01" }`.
  A `general` answer becomes searchable immediately as an `expert` source. A `customer_specific` answer is stored
  but never reused. Resolving again creates a new version that replaces the old one in search.
  After `expires_at` the answer stops being used. The question is also appended to the eval set. `404` if unknown.

## Ingestion
- `POST /ingestion/run` with `{ "mode": "incremental", "limit": null, "describe_images": true }` returns `202`
  and runs in the background. `409` if a run is already in progress. `incremental` fetches only new, changed
  (sitemap `lastmod`) or previously failed pages; `full` re-fetches everything.
- `GET /ingestion/status` returns page counts by status, active articles and passages, screenshot counts, and the
  last run report (`added, updated, removed, unchanged, failed, images_described`).
- A daily incremental run happens at 03:00 UTC (`KB_SYNC_HOUR_UTC`; disable with `KB_DAILY_SYNC=0`).

## GET /health
`{ "status": "ok", "articles_active": 1540, "chunks_active": 7400, "llm_configured": true, "ingestion_running": false }`

## Python client
Tasks 2 and 3 use `common/kb_client.py` (`KnowledgeClient.query/search/escalate/...`). It retries network
errors and 5xx/429 errors, and raises `KnowledgeUnavailable` instead of returning a made-up answer.
