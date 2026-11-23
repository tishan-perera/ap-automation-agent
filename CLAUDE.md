# CLAUDE.md — AP Automation Agent

You are helping Tishan Perera build a public portfolio project: an AI finance-ops web app for AP and AR specialists. It automates the repetitive ~80% of payables and receivables work and always keeps a human in the loop for exceptions. AP is built. AR is being added in phases.

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

Accounts receivable (being added in phases; the source of truth is docs/specs/2026-11-21-ar-design.md):
- Synthetic customers, sales orders, and a bank payment feed
- Customer invoices from sales orders (JSON and PDF, sequential AR-1001 numbers, Net 30)
- AI cash application that auto-posts only confident, complete, balanced matches
- AR aging with CSV export, and rule-based late-payment risk flags
- AI-drafted collection reminders that wait for approval and are never sent
- Payables / Receivables / Cash tabs and a combined Cash dashboard

## Non-negotiable rules
1. Tests first. Write a failing pytest, run it, then write the minimum code to pass, then run the tests again.
2. Small commits after each working slice. Use the commit message suggested in the current phase.
3. Default LLM provider is mock. Tests must never call a real API or need an API key.
4. Synthetic data only. No real invoices, employer data, vendor data, personal data, or secrets.
5. Never commit .env, API keys, or database files that might hold secrets. Keep .env.example only.
6. Keep files small and focused. One clear responsibility per file. Do not create a thousand-line module.
7. Before you say a task is done: run the tests and the verify commands from the current phase, and report the actual output.
8. If something fails, stop and fix it before starting the next feature.
9. Do not add extra features that are not in the current phase. No live QuickBooks, no live bank feeds, no SSO, no email inbox, never send email (no SMTP or mail API), no cloud deploy.
10. When extraction is uncertain, set the field to null, add a validation flag, and send the invoice to the review queue. Never guess a total.
11. Never guess a payment match or invent a customer, invoice, or amount. Uncertain cash applications go to the approval queue.

## Stack
Python 3.11+, FastAPI, Jinja2 + HTMX, SQLite, SQLAlchemy, Pydantic, pytest, ruff, Docker Compose.
LLM providers: mock (default), Gemini, Claude (Anthropic), and OpenAI, selected in settings or .env.

## Brand
- Colors: navy #0B1F33 for text, teal #14B8A6 as the automation accent (lines, fills, active states; never text on light backgrounds), amber #F59E0B only for items that need a human (needs review, pending approval), paper #F8FAFC background.
- Type: Inter for UI text and headings; JetBrains Mono with tabular numbers for amounts, IDs, and invoice numbers.
- Light and calm: white cards, thin borders, 12px radius, 8px spacing grid.
- Full guide: docs/brand/dashboard-style.md. Fonts are self-hosted in app/web/static/fonts/. Never reference brand files outside this repo.

## How to work
- Follow the phase prompt Tishan pastes.
- Prefer simple, readable code over clever code.
- Add or update tests in tests/.
- Use structured logging.
- If you are unsure, ask a short question, then continue with the smallest safe default.
