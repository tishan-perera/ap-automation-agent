# CLAUDE.md — AP Automation Agent

You are helping Tishan Perera build a public portfolio project: an AI Accounts Payable web app for AP specialists. It automates the repetitive ~80% of AP work and always keeps a human in the loop for exceptions.

## What this project is
- Ingest synthetic invoice PDFs
- Extract structured JSON with an LLM (or the mock provider)
- Validate schema and math; never invent a missing amount (use null and a flag)
- Detect duplicates
- 2-way and 3-way match against sample purchase orders and receiving records
- Suggest GL account codes with confidence and a reason
- Show an approval queue; a person approves, rejects, or edits
- Write an audit log
- Export approved invoices to a QuickBooks-style CSV

Accounts receivable is out of scope for v1.

## Non-negotiable rules
1. Tests first. Write a failing pytest, run it, then write the minimum code to pass, then run the tests again.
2. Small commits after each working slice. Use the commit message suggested in the current phase.
3. Default LLM provider is mock. Tests must never call a real API or need an API key.
4. Synthetic data only. No real invoices, employer data, vendor data, personal data, or secrets.
5. Never commit .env, API keys, or database files that might hold secrets. Keep .env.example only.
6. Keep files small and focused. One clear responsibility per file. Do not create a thousand-line module.
7. Before you say a task is done: run the tests and the verify commands from the current phase, and report the actual output.
8. If something fails, stop and fix it before starting the next feature.
9. Do not add extra features that are not in the current phase. No AR, no live QuickBooks, no SSO, no email inbox, no cloud deploy.
10. When extraction is uncertain, set the field to null, add a validation flag, and send the invoice to the review queue. Never guess a total.

## Stack
Python 3.11+, FastAPI, Jinja2 + HTMX, SQLite, SQLAlchemy, Pydantic, pytest, ruff, Docker Compose.
LLM providers: mock (default), Gemini, Claude (Anthropic), and OpenAI, selected in settings or .env.

## How to work
- Follow the phase prompt Tishan pastes.
- Prefer simple, readable code over clever code.
- Add or update tests in tests/.
- Use structured logging.
- If you are unsure, ask a short question, then continue with the smallest safe default.
