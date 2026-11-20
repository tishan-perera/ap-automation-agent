import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.eval_extraction import FIELDS, run_eval, score_field
from tests.conftest import DATA_DIR

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("eval")
    run_eval(DATA_DIR, out_dir)
    return json.loads((out_dir / "eval-report.json").read_text()), out_dir


def test_scores_the_required_fields():
    assert FIELDS == ["vendor_name", "invoice_number", "invoice_date", "total", "po_number"]


def test_null_expected_and_null_extracted_is_a_correct_refusal():
    assert score_field("total", None, None) is True


def test_guessing_zero_for_a_missing_total_is_wrong():
    assert score_field("total", None, Decimal(0)) is False


def test_amounts_compare_as_decimals():
    assert score_field("total", "45.90", Decimal("45.9")) is True
    assert score_field("total", "45.90", Decimal("45.91")) is False


def test_report_has_per_field_accuracy(report):
    data, out_dir = report

    assert set(data["field_accuracy"]) == set(FIELDS)
    for stats in data["field_accuracy"].values():
        assert 0 <= stats["accuracy"] <= 1
        assert stats["total"] == data["invoice_count"]
    markdown = (out_dir / "eval-report.md").read_text()
    accuracy_section = markdown.split("## Per-field accuracy")[1].split("## ")[0]
    for field in FIELDS:
        assert f"| {field} |" in accuracy_section


def test_missing_total_fixture_is_scored_correct(report, ground_truth):
    data, _ = report
    filename = next(f for f, r in ground_truth.items() if r["case_type"] == "missing_total")

    result = next(r for r in data["invoices"] if r["filename"] == filename)

    assert result["fields"]["total"] == {"expected": None, "extracted": None, "correct": True}


def test_report_records_mock_provider_and_zero_tokens(report):
    data, _ = report
    assert data["provider"] == "mock"
    assert data["token_usage"]["total_tokens"] == 0


def test_script_uses_mock_even_if_a_real_provider_is_configured(tmp_path):
    env = {**os.environ, "LLM_PROVIDER": "openai", "OPENAI_API_KEY": "fake-key-never-used"}

    result = subprocess.run(
        [sys.executable, "scripts/eval_extraction.py", "--out-dir", str(tmp_path)],
        check=False,  # we assert on returncode ourselves
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "Per-field accuracy" in result.stdout
    assert json.loads((tmp_path / "eval-report.json").read_text())["provider"] == "mock"
