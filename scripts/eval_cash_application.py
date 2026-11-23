"""Score cash application (payment matching) against the synthetic answer key.

Always uses the offline mock matcher, whatever LLM_PROVIDER says, so it never calls an API.
Payments are matched first; the answer key is read only afterwards, to score the results.
Leaving an unidentified payment unmatched is correct. Guessing a customer or invoice is wrong.

Usage: python scripts/eval_cash_application.py [--ar-dir data/ar] [--out-dir docs]
Writes ar-matching-eval.json and ar-matching-eval.md to the output directory.
"""

import argparse
import json
import sys
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # lets `python scripts/eval_cash_application.py` import the app

from sqlalchemy.orm import Session

from app.db import init_db, make_engine
from app.services.ar.invoicing import create_invoices_from_orders
from app.services.ar.seed import seed_ar_reference
from app.services.cash_application import apply_payments
from app.services.llm.payment_matcher_mock import MockPaymentMatcher


def _apps(rows) -> list[tuple[str, Decimal]]:
    return sorted((number, Decimal(str(amount))) for number, amount in rows)


def score_payment(truth: dict, actual_status: str, actual_apps) -> dict:
    """Status must equal the expected status; applications must match invoices and amounts."""
    want = _apps((a["invoice_number"], a["amount"]) for a in truth["applications"])
    return {
        "status": actual_status == truth["expected_status"],
        "applications": _apps(actual_apps) == want,
    }


def _match_all(ar_dir: Path) -> list[dict]:
    engine = make_engine("sqlite://")  # in memory; nothing touches data/app.db
    init_db(engine)
    with Session(engine) as session, tempfile.TemporaryDirectory() as pdf_dir:
        seed_ar_reference(session, ar_dir)
        create_invoices_from_orders(session, pdf_dir)
        results = [
            {
                "payment_ref": p.payment_ref,
                "status": p.status,
                "confidence": p.confidence,
                "applications": [(a.invoice_number, str(a.amount_applied)) for a in p.applications],
            }
            for p in apply_payments(session, MockPaymentMatcher())
        ]
    engine.dispose()
    return results


def _metric(correct: int, total: int) -> dict:
    return {
        "correct": correct,
        "total": total,
        "accuracy": round(correct / total, 4) if total else 0.0,
    }


def run_eval(ar_dir: Path | str, out_dir: Path | str) -> dict:
    ar_dir, out_dir = Path(ar_dir), Path(out_dir)
    results = _match_all(ar_dir)
    truth = json.loads((ar_dir / "ground_truth.json").read_text())  # read only to score

    rows, by_case = [], defaultdict(Counter)
    for result in results:
        expected = truth[result["payment_ref"]]
        score = score_payment(expected, result["status"], result["applications"])
        rows.append(
            {
                "payment_ref": result["payment_ref"],
                "case_type": expected["case_type"],
                "expected_status": expected["expected_status"],
                "actual_status": result["status"],
                "confidence": result["confidence"],
                "expected_applications": expected["applications"],
                "actual_applications": [
                    {"invoice_number": n, "amount": a} for n, a in result["applications"]
                ],
                "status_correct": score["status"],
                "applications_correct": score["applications"],
            }
        )
        case = by_case[expected["case_type"]]
        case["payments"] += 1
        case["status_correct"] += score["status"]
        case["applications_correct"] += score["applications"]

    report = {
        "matcher": MockPaymentMatcher.name,
        "payment_count": len(rows),
        "status_accuracy": _metric(sum(r["status_correct"] for r in rows), len(rows)),
        "application_accuracy": _metric(sum(r["applications_correct"] for r in rows), len(rows)),
        "auto_posted": sum(r["actual_status"] == "posted" for r in rows),
        "sent_to_review": sum(r["actual_status"] == "needs_review" for r in rows),
        "left_unidentified": sum(r["actual_status"] == "unidentified" for r in rows),
        "by_case_type": {case: dict(counts) for case, counts in sorted(by_case.items())},
        "payments": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ar-matching-eval.json").write_text(json.dumps(report, indent=2) + "\n")
    (out_dir / "ar-matching-eval.md").write_text(render_markdown(report))
    return report


def _pct(metric: dict) -> str:
    return f"{metric['correct']}/{metric['total']} ({metric['accuracy']:.1%})"


def render_markdown(report: dict) -> str:
    perfect = report["status_accuracy"]["accuracy"] == 1.0
    lines = [
        "# AR cash-application (payment matching) eval",
        "",
        "Generated by `scripts/eval_cash_application.py`. Every number below comes from that script.",
        "",
        f"- Matcher: `{report['matcher']}` (offline, deterministic; no API calls)",
        (
            f"- Dataset: {report['payment_count']} synthetic bank payments scored against "
            "`data/ar/ground_truth.json`, which is read only after matching"
        ),
        (
            "- Scoring: status must equal the expected status (posted, needs_review, or "
            "unidentified). Applications must match the expected invoice numbers and amounts "
            "exactly. Leaving an unidentified payment unmatched counts as correct; guessing a "
            "customer or invoice counts as wrong."
        ),
        "",
        "## Results",
        "",
        f"- **Status accuracy:** {_pct(report['status_accuracy'])}",
        f"- **Application accuracy:** {_pct(report['application_accuracy'])}",
        (
            f"- Auto-posted: {report['auto_posted']} · Sent to review: {report['sent_to_review']}"
            f" · Left unidentified: {report['left_unidentified']}"
        ),
        "",
        "## By case type",
        "",
        "| Case type | Payments | Status correct | Applications correct |",
        "|---|---|---|---|",
    ]
    for case, c in report["by_case_type"].items():
        lines.append(
            f"| {case} | {c['payments']} | {c['status_correct']} | {c['applications_correct']} |"
        )
    misses = [
        f"- `{r['payment_ref']}` ({r['case_type']}): expected {r['expected_status']} "
        f"{r['expected_applications']}, got {r['actual_status']} {r['actual_applications']}"
        for r in report["payments"]
        if not (r["status_correct"] and r["applications_correct"])
    ]
    lines += ["", "## Misses", "", *(misses or ["None."]), "", "## Limits", ""]
    if perfect:
        lines.append(
            "- **The mock scored 100%, and that is not a measure of real AI accuracy.** The "
            "mock is a deterministic parser written for these synthetic memo formats, and the "
            "fixtures were designed alongside it. The numbers show the rules, the auto-post "
            "threshold, and the review queue behave as specified."
        )
    else:
        lines.append(
            "- The mock is a deterministic parser written for these synthetic memo formats, "
            "so these numbers are not a measure of real AI accuracy."
        )
    lines += [
        "- Real-provider matching needs an API key and is not run in tests, CI, or this eval.",
        "",
        "## Reproduce",
        "",
        "```bash",
        ".venv/bin/python scripts/eval_cash_application.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ar-dir", default=ROOT / "data" / "ar", type=Path)
    parser.add_argument("--out-dir", default=ROOT / "docs", type=Path)
    args = parser.parse_args(argv)

    report = run_eval(args.ar_dir, args.out_dir)
    print(f"Evaluated {report['payment_count']} bank payments with the {report['matcher']} matcher")
    print(f"  Status accuracy:      {_pct(report['status_accuracy'])}")
    print(f"  Application accuracy: {_pct(report['application_accuracy'])}")
    print(
        f"  Auto-posted {report['auto_posted']}, sent to review {report['sent_to_review']}, "
        f"left unidentified {report['left_unidentified']}"
    )
    print(f"Report written to {args.out_dir / 'ar-matching-eval.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
