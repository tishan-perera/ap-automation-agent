# Screenshots

**Captured.** The main README embeds these five real screenshots of the running app. The payment-matching and reminder images are cropped to the sections described below, so the long posted-payments list and the extra reminder drafts are not in the README.

**Setup for repeatable screenshots:** start the app against a throwaway database so `data/app.db` is not changed, and use a browser window about 1280 px wide:

```bash
DATABASE_URL=sqlite:////tmp/ap-screenshots.db AR_REPORT_AS_OF=2026-11-23 .venv/bin/uvicorn app.main:app
```

| File | Page | What it should show |
|---|---|---|
| `queue.png` | Payables tab: http://127.0.0.1:8000/queue | The approval queue on **Needs review**, with clean rows and rows with exception text visible. |
| `ap-exception.png` | Detail page for **CFS-2021** (Cloudforge Software LLC), opened from the queue | The **MISSING_TOTAL** exception at the top and **Total: —** in the extracted fields (null, not $0). |
| `ar-payment-review.png` | Receivables → Payment matching: http://127.0.0.1:8000/receivables/payments | **BP-0009** (overpayment, "$25.00 has nowhere to go") under Needs review, and **BP-0011** under Unidentified, with no customer guessed. |
| `cash.png` | Cash tab: http://127.0.0.1:8000/cash?as_of=2026-10-15 | Money in and money out, and the **Next 30 days** list with amber rows only for bills waiting for review. (Aging, http://127.0.0.1:8000/receivables/aging, is an acceptable alternative.) |
| `reminder-draft.png` | Receivables → Reminders: http://127.0.0.1:8000/receivables/reminders | A draft **waiting for review** (for example Amberfield Clinics LLC, final tone), and the note that drafts are never emailed. |

The main README embeds each file with an image line such as `![Payables approval queue](docs/screenshots/queue.png)`.

All data in the app is synthetic, so the screenshots contain no real company or personal information.
