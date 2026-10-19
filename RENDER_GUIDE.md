# Render Deployment Guide

A step-by-step, beginner-friendly guide for deploying the **AI Tech Consultant** capstone project to [Render](https://render.com) using Windows and the Render Web Dashboard.

---

## Table of Contents
1. [Deployment Architecture Overview](#1-deployment-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Step 1: Push Repository to GitHub](#3-step-1-push-repository-to-github)
4. [Step 2: Choose Your Deployment Architecture](#4-step-2-choose-your-deployment-architecture)
   - [Option A (Recommended): Unified Single Web Service](#option-a-recommended-unified-single-web-service)
   - [Option B: Decoupled Dual Web Services](#option-b-decoupled-dual-web-services)
5. [Step 3: Exact Render Dashboard Configuration](#5-step-3-exact-render-dashboard-configuration)
6. [Environment Variables Reference](#6-environment-variables-reference)
7. [Secrets Management](#7-secrets-management)
8. [Knowledge Base and Ephemeral Filesystem Strategy](#8-knowledge-base-and-ephemeral-filesystem-strategy)
9. [Build Logs, Monitoring, and Redeployment](#9-build-logs-monitoring-and-redeployment)
10. [Post-Deployment Verification](#10-post-deployment-verification)
11. [Testing All Three Tasks on the Deployed App](#11-testing-all-three-tasks-after-deployment)
12. [Render Free-Tier Limitations & Solutions](#12-render-free-tier-limitations--solutions)
13. [Troubleshooting Common Issues](#13-troubleshooting-common-issues)
14. [Final Submission Readiness Checklist](#14-final-submission-readiness-checklist)

---

## 1. Deployment Architecture Overview

The AI Tech Consultant application consists of two software layers:
1. **Knowledge Brain Backend**: A FastAPI service exposing endpoints (`/health`, `/query`, `/search`, `/escalations`, `/ingestion/run`) with SQLite vector + full-text search over Brilliant Directories documentation.
2. **Consultant Web Interface**: A multi-page Streamlit application (`app.py`) providing:
   - **Task 1 — Knowledge Chat** (`task1_knowledge_brain/ui.py`)
   - **Task 2 — Inbox Review** (`task2_inbox_agent/ui.py`)
   - **Task 3 — Scope Builder** (`task3_meeting_to_scope/ui.py`)

### Architectural Options on Render:

| Architecture | Services Count | Monthly Free Hours Impact | Inter-service Latency | Cold-Start Reliability |
| :--- | :--- | :--- | :--- | :--- |
| **Option A: Unified (Recommended)** | **1 Web Service** | Uses ~720 hrs/mo (**100% Free**) | **0 ms** (same container) | **High** (starts simultaneously) |
| **Option B: Dual Microservices** | **2 Web Services** | Uses ~1,440 hrs/mo (exceeds 750 free hrs if 24/7) | Network HTTP | **Medium** (cold-start sync delays) |

> **Recommendation**: Use **Option A (Unified Service)** for the capstone demo and submission. It runs both FastAPI (internally on `127.0.0.1:8000`) and Streamlit (publicly on Render's `$PORT`) in a single container via `python run.py`. It is free, avoids cold-start race conditions, and gives you a single public URL to share.

---

## 2. Prerequisites

Before starting, ensure you have:
1. A **GitHub account** with your repository (`ai-tech-consultant`).
2. **Git** installed on your Windows machine (`git --version` in PowerShell).
3. A free **Render account** at [https://render.com](https://render.com) (sign up using your GitHub account for 1-click repository linking).
4. A free **Groq API Key** from [https://console.groq.com/keys](https://console.groq.com/keys) (starts with `gsk_...`).
   *(Optional: If you have an Anthropic Claude API key, you can also use `ANTHROPIC_API_KEY`).*

---

## 3. Step 1: Push Repository to GitHub

Open PowerShell in the project directory (`e:\ai-tech-consultant`):

```powershell
# 1. Check current status
git status

# 2. Stage all intended changes
git add .

# 3. Create a clean commit
git commit -m "Prepare application for Render deployment"

# 4. Push to your GitHub repository
git push -u origin main
```

> **Security Check**: Verify that `.env` is NOT tracked. The `.gitignore` file already excludes `.env`, `*.db`, and `data/models/`. Never force-add `.env`.

---

## 4. Step 2: Choose Your Deployment Architecture

### Option A (Recommended): Unified Single Web Service
Runs `python run.py`.
- **FastAPI** starts internally on `127.0.0.1:8000`.
- **Streamlit** binds to Render's assigned `$PORT` on `0.0.0.0`.
- All three tasks communicate locally with zero latency.
- Completely free tier compliant.

### Option B: Decoupled Dual Web Services
If your evaluator specifically demands separate public URLs for FastAPI and Streamlit:
- **Service 1 (API)**: `uvicorn task1_knowledge_brain.app.api:app --host 0.0.0.0 --port $PORT`
- **Service 2 (UI)**: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false`
- Streamlit connects to API via `KB_API_URL=https://<your-api-service>.onrender.com`.

---

## 5. Step 3: Exact Render Dashboard Configuration

### Option A: Creating the Unified Service (Recommended)

1. Log in to the [Render Dashboard](https://dashboard.render.com).
2. Click **New +** (top right) and select **Web Service**.
3. Under **Connect a repository**, select your `ai-tech-consultant` repository.
4. Configure the settings:
   - **Name**: `ai-tech-consultant` (or your preferred name)
   - **Region**: Select the region closest to you (e.g., `Frankfurt (EU Central)` or `Oregon (US West)`)
   - **Branch**: `main`
   - **Root Directory**: *(leave blank — defaults to project root)*
   - **Runtime**: `Python 3`
   - **Build Command**:
     ```bash
     pip install -r requirements.txt
     ```
   - **Start Command**:
     ```bash
     python run.py
     ```
   - **Instance Type**: `Free`
5. Click **Advanced** and add the [Environment Variables](#6-environment-variables-reference) detailed below.
6. Click **Create Web Service**.

---

### Option B: Creating Dual Services (Optional)

If deploying two services:

#### Service 1: `ai-tech-consultant-api`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn task1_knowledge_brain.app.api:app --host 0.0.0.0 --port $PORT`
- **Health Check Path**: `/health`
- **Environment Variables**:
  - `PYTHON_VERSION`: `3.11.9`
  - `GROQ_API_KEY`: *(your secret Groq key)*
  - `LLM_PROVIDER`: `groq`
  - `KB_DAILY_SYNC`: `0`
- Once deployed, copy its URL: e.g. `https://ai-tech-consultant-api.onrender.com`.

#### Service 2: `ai-tech-consultant-ui`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false`
- **Environment Variables**:
  - `PYTHON_VERSION`: `3.11.9`
  - `GROQ_API_KEY`: *(your secret Groq key)*
  - `LLM_PROVIDER`: `groq`
  - `KB_API_URL`: `https://ai-tech-consultant-api.onrender.com` *(use the exact URL from Service 1)*

---

## 6. Environment Variables Reference

Configure these in the Render Dashboard under **Environment** -> **Add Environment Variable**:

| Variable Name | Required? | Recommended Value | Description |
| :--- | :--- | :--- | :--- |
| `PYTHON_VERSION` | **Yes** | `3.11.9` | Ensures a stable Python runtime compatible with onnxruntime and fastembed. |
| `GROQ_API_KEY` | **Yes** | `gsk_...` | Your secret Groq API key from [console.groq.com](https://console.groq.com/keys). |
| `LLM_PROVIDER` | **Yes** | `groq` | Forces the project to use Groq LLMs without requiring Claude. |
| `GROQ_SMALL_MODEL` | No | `openai/gpt-oss-20b` | High-speed routing and lightweight tasks model. |
| `GROQ_LARGE_MODEL` | No | `openai/gpt-oss-120b` | High-reasoning model for scope generation and proposals. |
| `GROQ_VISION_MODEL`| No | `qwen/qwen3.8-27b` | Multimodal model used for image and screenshot analysis. |
| `KB_DAILY_SYNC` | **Yes** | `0` | Disables background re-crawling on boot so memory remains focused on user traffic. |
| `KB_API_URL` | Dual only | `http://127.0.0.1:8000` | In Option A, `run.py` handles this automatically. In Option B, set to your API service URL. |
| `KB_API_KEY` | Optional | *(leave blank)* | Optional API token header (`X-API-Key`) for protecting the API. |
| `ANTHROPIC_API_KEY`| Optional | `sk-ant-...` | Set only if you wish to use Anthropic Claude instead of Groq. |

---

## 7. Secrets Management

> [!CAUTION]
> **NEVER commit API keys to GitHub.**
>
> 1. `GROQ_API_KEY` and `ANTHROPIC_API_KEY` must **only** be added via the Render Web Dashboard (**Environment Variables** section).
> 2. Ensure your local `.env` file is never staged:
>    ```powershell
>    git status
>    ```
>    If `.env` appears under staged files, immediately run `git rm --cached .env`.
> 3. Render encrypts environment variables at rest and injects them securely at container runtime.

---

## 8. Knowledge Base and Ephemeral Filesystem Strategy

### How SQLite and the Index Persist on Render:
1. **Repository Compression**: The complete knowledge base (`data/task1/knowledge.db`, ~74 MB) has been compressed into `data/task1/knowledge.db.gz` (~19.89 MB) and is committed directly into the Git repository.
2. **Auto-Extraction on Startup**: When Render clones and runs the application, `task1_knowledge_brain/db.py` checks if `knowledge.db` exists. If missing, it automatically decompresses `knowledge.db.gz` into `knowledge.db` in ~1.2 seconds.
3. **No External Storage Needed**: All 8,001 active chunks, 510 articles, vector embeddings, and full-text search indexes are ready instantly on container spin-up.
4. **Task 2 and Task 3 Databases**: `task2/inbox.db` and `task3/scope.db` are created on-demand in SQLite with WAL mode enabled.
5. **Ephemeral File System Note**: Render's free tier wipes filesystem state when a container spins down after 15 minutes of inactivity. Because `knowledge.db.gz` is baked into the git deployment, your entire knowledge brain will always be restored in full on every wake-up! For persistent customer inbox reviews across restarts on production, you can attach a Render Persistent Disk (`/data`) or configure an external PostgreSQL database.

---

## 9. Build Logs, Monitoring, and Redeployment

### Reading Build Logs:
1. Go to your Web Service in the Render Dashboard.
2. Click the **Logs** tab on the left sidebar.
3. During deployment, you will observe:
   ```text
   ==> Cloning from https://github.com/blackeagle-25-5/ai-tech-consultant...
   ==> Running build command 'pip install -r requirements.txt'...
   ==> Generating container image...
   ==> Starting service with 'python run.py'...
   Starting Knowledge Brain API on http://127.0.0.1:8000 ...
   API ready (docs: http://127.0.0.1:8000/docs).
   Starting Streamlit app on port 10000 (0.0.0.0) ...
   ==> Your service is live 🎉
   ```

### Triggering a Redeploy:
- **Automatic**: Every time you `git push origin main`, Render detects the new commit and automatically builds and redeploys.
- **Manual**: Click **Manual Deploy** -> **Deploy latest commit** (or **Clear build cache & deploy** if requirements change).

---

## 10. Post-Deployment Verification

Once Render displays **Live**, test your deployment:

### 1. Verify Public Streamlit Interface
Click the public URL provided at the top of your Render service:
`https://<your-service-name>.onrender.com`

- You should see the **AI Tech Consultant** multi-page header with three sidebar pages:
  - 📖 **Knowledge Chat**
  - ✉️ **Inbox Review**
  - 📋 **Scope Builder**

### 2. Verify FastAPI Health (If using Option B Dual Services)
Navigate to:
- Health endpoint: `https://<your-api-service>.onrender.com/health`
  - Expected JSON:
    ```json
    {
      "status": "ok",
      "articles_active": 510,
      "chunks_active": 8001,
      "llm_configured": true,
      "llm_provider": "groq",
      "ingestion_running": false
    }
    ```
- Interactive Swagger docs: `https://<your-api-service>.onrender.com/docs`

---

## 11. Testing All Three Tasks After Deployment

Follow this test script to verify each task on the live Render app:

### Task 1: Knowledge Chat
1. Navigate to the **Knowledge Chat** page.
2. In the query box, enter:
   `How do I set up Stripe for member billing?`
3. Click **Ask**.
4. **Verify**:
   - The response provides clear instructions grounded in Brilliant Directories docs.
   - At least 1-3 official documentation citations/source links are visible.
   - Confidence score and retrieval mode are indicated.
5. In the query box, enter an out-of-scope question:
   `What is the weather forecast for tomorrow in Tokyo?`
6. **Verify**:
   - The system recognizes this is outside Brilliant Directories documentation and logs or offers an escalation.

### Task 2: Inbox Review
1. Navigate to the **Inbox Review** page.
2. Review the simulated incoming client messages.
3. Select an email and click **Draft Response**.
4. **Verify**:
   - The agent categorizes the inquiry accurately.
   - The drafted response uses the Knowledge Brain for technical facts.
   - The human-in-the-loop review interface allows editing and approving the draft before sending.

### Task 3: Scope Builder
1. Navigate to the **Scope Builder** page.
2. Select sample meeting transcript **Meeting A (FitFinder)**.
3. Click **Generate Scope & Proposal**.
4. **Verify**:
   - The agent pipeline extracts functional requirements.
   - Feasibility checks are performed against the Knowledge Brain.
   - Work breakdown estimates (hours and tier) are generated.
   - Senior review checks and a professional client proposal markdown preview are displayed.
   - Click **Approve Scope** to confirm workflow completion.

---

## 12. Render Free-Tier Limitations & Solutions

| Free Tier Limitation | What Happens | Recommended Handling |
| :--- | :--- | :--- |
| **Idle Spin-Down** | Free web services spin down after 15 minutes without requests. | The first request after sleep takes 50-90 seconds to cold boot. Wait for the initial spin-up; once awake, navigation is fast. |
| **750 Instance Hours/Month** | One free service can run 24/7 all month. Two services will exhaust hours by mid-month. | Deploy using **Option A (Unified Service)** to consume only 1 service allowance. |
| **512 MB RAM Ceiling** | Large models loaded in memory can trigger Out-Of-Memory (OOM). | Our project uses `fastembed` ONNX quantized embeddings (`bge-small-en-v1.5-onnx-Q`) consuming under 180 MB RAM, staying safely under 512 MB. |
| **Ephemeral Disk** | Data written at runtime is wiped on restart. | The knowledge base is gzipped into the Git repository (`data/task1/knowledge.db.gz`), ensuring it is restored on every boot. |

---

## 13. Troubleshooting Common Issues

### Issue 1: `Port 8501 is not available` or Service Fails Port Binding
- **Cause**: Streamlit was hardcoded to `8501`, but Render requires binding to `$PORT`.
- **Solution**: Handled automatically in `run.py` via `os.getenv("PORT", "8501")` and `--server.address 0.0.0.0`. Ensure start command is `python run.py`.

### Issue 2: `StreamlitAPIException: Multiple Pages specified with URL pathname`
- **Cause**: Multiple pages in `st.navigation` sharing the same filename `ui.py`.
- **Solution**: Already fixed in `app.py` by specifying distinct URL paths (`url_path="knowledge"`, `url_path="inbox"`, `url_path="scope"`).

### Issue 3: Groq Rate Limit (`429 Too Many Requests: TPM limit exceeded`)
- **Cause**: Groq free tier imposes an 8,000 tokens-per-minute (TPM) limit on `openai/gpt-oss-120b`.
- **Solution**: Handled in `common/groq_client.py` and Task 3 prompts by compacting prompt context and capping completion tokens to 3,500. If rate-limited, wait 60 seconds and retry.

### Issue 4: Missing Database (`sqlite3.OperationalError: no such table`)
- **Cause**: `knowledge.db` was not unpacked.
- **Solution**: Handled automatically in `task1_knowledge_brain/db.py`. If you ever need to manually verify or decompress in a shell:
  ```bash
  python -c "import gzip, shutil; shutil.copyfileobj(gzip.open('data/task1/knowledge.db.gz'), open('data/task1/knowledge.db', 'wb'))"
  ```

### Issue 5: CORS Errors in Browser Console
- **Cause**: Direct cross-origin browser requests to the API.
- **Solution**: `task1_knowledge_brain/app/api.py` includes `CORSMiddleware` configured with `allow_origins=["*"]`.

---

## 14. Final Submission Readiness Checklist

Before submitting your project link:

- [ ] Repository is pushed to GitHub `main` branch with clean history.
- [ ] Render Web Service status shows **Live** with green checkmark.
- [ ] Public Streamlit URL loads properly on desktop and mobile.
- [ ] Sidebar navigation transitions smoothly between all 3 tasks.
- [ ] Task 1 (Knowledge Chat) returns answers with Brilliant Directories source citations.
- [ ] Task 2 (Inbox Review) allows inspecting and approving drafts.
- [ ] Task 3 (Scope Builder) processes meeting transcripts into estimates and proposals.
- [ ] `GROQ_API_KEY` is configured in Render Environment Variables and NOT exposed in Git.
- [ ] `RENDER_GUIDE.md` is visible in the root of the GitHub repository.
