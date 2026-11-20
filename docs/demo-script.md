# Demo script (2-3 minutes)

A walkthrough of AP Automation Agent with one success path and one failure path. Everything shown is synthetic data running on the offline mock provider.

**Before you start:** run `docker compose up --build` on a fresh database (delete `data/app.db` if it exists), then open http://localhost:8000. The queue loads with 24 invoices.

---

## 1. The problem (about 15 seconds)

> "AP specialists spend their day re-typing invoices, hunting for duplicates, and matching them to purchase orders. This app does the repetitive part and leaves every decision to a person, and it never invents a number it could not read."

## 2. Happy path (about 60 seconds)

1. On the **Approval queue**, point at the columns: vendor, invoice number, date, total, exceptions, match status, GL code, and status. Most rows say **None** under Exceptions.
2. Open **Northwind Office Supply LLC, NWO-2001, $45.90**.
3. **Extracted fields:** vendor, invoice number, dates, PO-1001, subtotal $42.50, tax $3.40, and total $45.90, all read from the PDF.
4. **PO match:** the panel shows **matched, three-way**. The invoice agrees with PO-1001 on vendor and amount, and the quantity matches the receiving record.
5. **GL suggestion:** **6100 Office Supplies**, confidence 90%. Read out the reason: it comes from the vendor map, and the credit side is always 2000 Accounts Payable.
6. Click **Approve** and confirm. The status changes to **approved**, and the edit form disappears because approved invoices are locked.
7. Scroll to the **Audit log**: "ingested by system", then "approved by ap-specialist", with timestamps.
8. Go back to the queue, click the **Approved** filter, and point at **Export 1 approved to CSV**. It downloads a QuickBooks-style file with one row per line item, and the invoice moves to **Exported**.

## 3. Failure path (about 60 seconds)

**Missing total**

1. Back on **Needs review**, find **Cloudforge Software LLC, CFS-2021**. The Exceptions column says *"Invoice total is missing or unreadable."*
2. Open it. The exception card at the top shows the flag **MISSING_TOTAL**.
3. Point at **Total: —** in the extracted fields. The PDF had no Total line, so the app stored null. It did not guess $0, and it did not add up the lines to fill the gap.
4. Click **Approve**. The page refuses: *"Cannot approve: the total is missing. Edit the invoice first."*
5. Type **105.00** into the Total field and click **Save changes**. The exception clears, the PO panel re-runs to **matched**, and the audit log records **edited** with `total: empty → 105.00`.

**Duplicate (if time allows)**

6. Open the second **NWO-2001** row. It says *"Possible exact duplicate of invoice NWO-2001 (invoice_001.pdf)."* The file was saved and flagged, not silently dropped.
7. Type a reason such as "Duplicate of NWO-2001" and click **Reject**. The audit log shows **rejected** with that reason.

## 4. Close (about 20 seconds)

> "It all runs with one command, `docker compose up --build`, with no API key. There are 118 tests, all on the mock provider, and CI runs them with ruff on every push. To use a real model, set `LLM_PROVIDER` and that provider's key in `.env`."

Optionally, show the terminal:

```bash
.venv/bin/pytest -q
```

---

**Tip:** to run the demo again from a clean queue, stop the app, delete `data/app.db`, and start it again.
