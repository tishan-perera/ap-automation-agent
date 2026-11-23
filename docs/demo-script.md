# Demo script (3-4 minutes)

A walkthrough of the AP and AR finance-ops agent: payables, receivables, and cash. Everything shown is synthetic data running on the offline mock provider, and nothing is ever emailed.

**Before you start (for repeatable numbers):** delete `data/app.db` if it exists, then start the app with a fixed report date:

```bash
AR_REPORT_AS_OF=2026-11-23 .venv/bin/uvicorn app.main:app
```

Open http://127.0.0.1:8000. Startup loads:
- 24 vendor invoices into Payables
- 24 customer invoices
- 15 bank payments, with confident ones posted
- 7 reminder drafts

With `docker compose up --build` instead, the AR pages use today's date, so aging and reminder figures will differ from the ones quoted below.

---

## 1. The problem (about 15 seconds)

> "A small finance team spends its day re-typing vendor invoices, matching customer payments to invoices, and chasing late payers. This app does the repetitive part on both sides and leaves every judgement call to a person. It never invents an amount and never guesses which invoice a payment is for."

## 2. Payables (about 60 seconds)

**Happy path**

1. On the **Payables** tab, point at the queue columns: vendor, invoice number, date, total, exceptions, match status, GL code, and status. Most rows say **None** under Exceptions.
2. Open **Northwind Office Supply LLC, NWO-2001, $45.90**. Show the extracted fields, the PO panel (**matched, three-way**), and the GL suggestion (**6100 Office Supplies**, with its reason).
3. Click **Approve** and confirm. The status becomes **approved**, and the audit log shows "approved by ap-specialist".

**One exception: missing total**

4. Back on the queue, open **Cloudforge Software LLC, CFS-2021**. The exception card says *"Invoice total is missing or unreadable."*, and **Total** shows **—**. The PDF had no Total line, so the app stored null. It did not guess $0.
5. Click **Approve**. The app refuses: *"Cannot approve: the total is missing. Edit the invoice first."*
6. Type **105.00** into Total and save. The exception clears, the PO match re-runs to **matched**, and the audit log records `total: empty → 105.00`.

## 3. Receivables (about 60 seconds)

1. Click the **Receivables** tab. The overview counts open customer invoices, payments waiting for review, reminder drafts waiting, and high-risk customers. Amber marks only the counts that need a person.
2. Open **Customer invoices**, then the PDF for **AR-1001** (Amberfield Clinics LLC, $6,600.00, Net 30, due 2026-09-02). It was created from sales order SO-5001. Its status is **paid**, because the matcher posted a payment against it.
3. Open **Payment matching** and scroll to **Posted**. **BP-0001**, memo *"Payment for AR-1001"*, auto-posted: the memo named the invoice clearly and the amount matched it exactly, so confidence was at least 90% and the payment was complete.
4. Go to **Needs review**. **BP-0009** pays AR-1012 plus **$25.00** extra. It says *"The payment is more than the open balance; $25.00 has nowhere to go."*, so it waits for a person.
5. Go to **Unidentified**. **BP-0011** ($517.38) has an empty memo and no payer. The app did not guess a customer, and a person can assign it with **Save applications**.

## 4. Aging, risk, and a reminder (about 45 seconds)

1. Open **Aging**. As of 2026-11-23 there are **14** open invoices totaling **$88,590.00**:
   - 1-30 days: 3 invoices, $23,680.00
   - 31-60 days: 10 invoices, $57,710.00
   - 61-90 days: 1 invoice, $7,200.00

   Point out **Download CSV**, and say that viewing or downloading changes nothing.
2. Open **Risk**. **Amberfield Clinics LLC** is **high** with score **60**. The reason reads: *"The oldest open invoice is 61 days past due, and 2 invoices are 30 or more days past due."* The score comes from fixed rules; the wording only restates them.
3. Open **Reminders**. Amberfield has a **final**-tone draft listing AR-1009 and AR-1017 with their due dates and balances. Click **Approve wording**. The banner says nothing was emailed: there is no send button, and the app can't store a "sent" status.

## 5. Cash (about 30 seconds)

1. Click the **Cash** tab and set **As of** to **2026-10-15**.
2. **Money in:** $49,460.00 from 9 posted payments, plus $39,775.00 in 6 open customer invoices due by 2026-11-14.
3. **Money out:** $27,510.62 in 14 AP bills still in review due in the same window. Approved or exported bills shows whatever you approved earlier in the demo, $45.90 for NWO-2001.
4. Point at the **Next 30 days** list, sorted by due date. Amber rows are bills waiting for a person. There's also an **Undated** section: items with no due date are never given a guessed date.

## 6. Close (about 15 seconds)

> "It all runs with one command, `docker compose up --build`, with no API key. There are 267 tests, all on the mock provider, and CI runs them with ruff on every push. The eval reports say plainly that the mock's 100% is not real AI accuracy."

```bash
.venv/bin/pytest -q
```

---

**Tip:** to run the demo again from the start, stop the app, delete `data/app.db`, and start it again.
