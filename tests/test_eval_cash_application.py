import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.eval_cash_application import run_eval, score_payment
from tests.conftest import DATA_DIR

ROOT = Path(__file__).resolve().parents[1]
AR_DIR = DATA_DIR / "ar"


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("cash-eval")
    run_eval(AR_DIR, out_dir)
    return json.loads((out_dir / "ar-matching-eval.json").read_text()), out_dir


def expected(status, apps, case="exact_invoice_number"):
    return {
        "case_type": case,
        "expected_status": status,
        "applications": [{"invoice_number": n, "amount": a} for n, a in apps],
    }


def test_leaving_an_unidentified_payment_unmatched_is_correct():
    truth = expected("unidentified", [], "unidentified")
    assert score_payment(truth, "unidentified", []) == {"status": True, "applications": True}


def test_guessing_an_invoice_for_an_unidentified_payment_is_wrong():
    truth = expected("unidentified", [], "unidentified")
    score = score_payment(truth, "needs_review", [("AR-1001", "100.00")])
    assert score == {"status": False, "applications": False}


def test_applications_must_match_invoices_and_amounts():
    truth = expected("posted", [("AR-1001", "6600.00")])
    assert score_payment(truth, "posted", [("AR-1001", "6600.00")])["applications"] is True
    assert score_payment(truth, "posted", [("AR-1001", "6500.00")])["applications"] is False
    assert score_payment(truth, "posted", [("AR-1002", "6600.00")])["applications"] is False


def test_report_has_status_and_application_accuracy(report):
    data, out_dir = report

    assert data["payment_count"] == len(json.loads((AR_DIR / "ground_truth.json").read_text()))
    for metric in ("status_accuracy", "application_accuracy"):
        stats = data[metric]
        assert 0 <= stats["accuracy"] <= 1
        assert stats["total"] == data["payment_count"]
    assert set(data["by_case_type"]) >= {"unidentified", "partial", "overpayment"}
    markdown = (out_dir / "ar-matching-eval.md").read_text()
    assert "Status accuracy" in markdown and "Application accuracy" in markdown


def test_unidentified_fixtures_are_scored_correct(report):
    data, _ = report
    unidentified = [r for r in data["payments"] if r["case_type"] == "unidentified"]

    assert unidentified
    for row in unidentified:
        assert row["actual_status"] == "unidentified"
        assert row["actual_applications"] == []
        assert row["status_correct"] and row["applications_correct"]


def test_report_names_the_mock_and_says_it_is_not_real_ai_accuracy(report):
    data, out_dir = report

    assert data["matcher"] == "mock"
    markdown = (out_dir / "ar-matching-eval.md").read_text()
    if data["status_accuracy"]["accuracy"] == 1.0:
        assert "deterministic" in markdown.lower()
        assert "not a measure of real ai accuracy" in markdown.lower()


def test_script_uses_mock_even_if_a_real_provider_is_configured(tmp_path):
    env = {**os.environ, "LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "fake-key-never-used"}

    result = subprocess.run(
        [sys.executable, "scripts/eval_cash_application.py", "--out-dir", str(tmp_path)],
        check=False,  # we assert on returncode ourselves
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "Status accuracy" in result.stdout
    assert json.loads((tmp_path / "ar-matching-eval.json").read_text())["matcher"] == "mock"
