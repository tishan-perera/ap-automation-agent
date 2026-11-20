# Screenshots

Not captured yet. Save these three PNG files in this folder with exactly these names, so the README can link to them. Use a fresh demo database (delete `data/app.db`, then start the app) and a browser window about 1280 px wide.

| File | Page | What it should show |
|---|---|---|
| `queue.png` | http://localhost:8000/queue | The approval queue on the **Needs review** filter, with clean rows and rows with exception text visible. |
| `invoice-happy.png` | Detail page for **NWO-2001** (Northwind Office Supply LLC, $45.90) | "No exceptions found", the PO panel showing **matched, three-way**, and the GL suggestion **6100 Office Supplies**. |
| `invoice-exception.png` | Detail page for **CFS-2021** (Cloudforge Software LLC) | The **MISSING_TOTAL** exception at the top and **Total: —** in the extracted fields (null, not $0). |

All data in the app is synthetic, so the screenshots contain no real company or personal information.
