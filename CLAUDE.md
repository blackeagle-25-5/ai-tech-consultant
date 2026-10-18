# AI Tech Consultant — Project Instructions

## Project Goal

Build the AI Tech Consultant capstone assignment according to the official assignment and reference materials.

## Development Rules

- Use Python for all three tasks because the developer is a beginner. The assignment permits Python for Task 2 when TypeScript is unfamiliar.
- Use Claude Code as the primary AI coding assistant.
- Implement in small stages. Test each stage before proceeding.
- Explain commands and errors in simple English.
- Never claim a feature or test works without verifying it.
- Never fabricate evaluation scores, test results, meeting recordings, or completed features.
- Keep secrets in environment variables. Never commit `.env` files or API keys.
- Write readable, typed Python with sensible error handling and automated tests.

## Task 1: Knowledge Brain

- Crawl the Brilliant Directories Solution Center sitemap: https://support.brilliantdirectories.com/support/sitemap.xml
- Extract articles, metadata, and associated images or screenshots.
- Store and retrieve knowledge with source URLs.
- Provide a FastAPI interface for questions, ingestion status, health checks, and human escalations.
- Answer with evidence and citations; abstain when evidence is insufficient.
- Support resumable crawling, incremental updates, and removal of deleted articles.
- Protect against prompt injection in retrieved content.

## Task 2: Email Assistant

- Process at least 50 realistic emails.
- Classify emails and draft replies using the Task 1 API.
- Support human review, editing, rejection, and feedback.
- Implement and test the required autonomy rule: 20 consecutive unchanged sends of the same kind, with safeguards and a way to revoke autonomy.
- Do not send real emails without explicit authorization.

## Task 3: Meeting-to-Scope

- Analyze at least three role-played client meeting transcripts.
- Use the Task 1 API to check existing product features.
- Generate a scope of work, task list, hour estimates, and proposal.
- Compare follow-up meetings against an approved baseline.
- Flag additions, removals, and out-of-scope requests.
- Require human approval before client-facing delivery.

## Architecture

- Keep each task in its own folder.
- Tasks 2 and 3 must call Task 1 through its API.
- Do not duplicate Task 1's implementation in other tasks.
- Use clear API contracts and validate inputs and outputs.
- The whole project must eventually run with one documented command.

## Testing and Submission

- Test each task independently and test the integrated workflow.
- Evaluate quality, cost, latency, reliability, and safety using actual results.
- Compare model configurations with measured evidence where feasible.
- Maintain README.md, DESIGN.md, EVALS.md, and AI_LOG.md within the assignment's length limits.
- Retain the Claude Code conversation history and meaningful Git commits.
- Prioritize a working end-to-end system before cosmetic improvements.

## Working Procedure

1. Inspect the repository and available assignment materials.
2. Create a requirements checklist and implementation plan.
3. Implement Task 1 and verify it.
4. Implement and test Task 2.
5. Implement and test Task 3.
6. Integrate, evaluate, document, and prepare the demo.
7. After each stage, report files changed, tests actually run, failures, and the next action.

Never generate the entire application in one unverified pass.