import json

import pytest

from app.services.gl_coding import suggest_gl
from app.services.ingest import process_invoice
from app.services.seed import seed_reference_data
from tests.conftest import DATA_DIR


@pytest.fixture(scope="module")
def chart():
    return json.loads((DATA_DIR / "chart_of_accounts.json").read_text())


@pytest.fixture(scope="module")
def vendor_map():
    return json.loads((DATA_DIR / "vendor_gl_map.json").read_text())


def test_known_vendor_uses_vendor_map(chart, vendor_map):
    gl = suggest_gl("Cloudforge Software LLC", ["Cloud storage"], chart, vendor_map)

    assert gl.account == "6200"
    assert gl.account_name == "Software and Subscriptions"
    assert gl.confidence >= 0.8
    assert "vendor map" in gl.reason.lower()


def test_vendor_map_lookup_is_normalized(chart, vendor_map):
    gl = suggest_gl("  CLOUDFORGE software, l.l.c. ", [], chart, vendor_map)
    assert gl.account == "6200"


def test_unknown_vendor_falls_back_to_description_keywords(chart, vendor_map):
    gl = suggest_gl("Unknown Vendor Co", ["Annual software license"], chart, vendor_map)

    assert gl.account == "6200"
    assert 0.5 <= gl.confidence < 0.8
    assert "keyword" in gl.reason.lower()
    assert "software" in gl.reason.lower()


def test_unknown_vendor_and_description_gets_conservative_fallback(chart, vendor_map):
    gl = suggest_gl("Unknown Vendor Co", ["Miscellaneous item"], chart, vendor_map)

    assert gl.account is None
    assert gl.confidence < 0.5
    assert "manual" in gl.reason.lower()


@pytest.mark.parametrize(
    ("vendor", "descriptions"),
    [("Cloudforge Software LLC", []), ("Unknown Vendor Co", ["software"]), ("Nobody", [])],
)
def test_credit_side_is_always_accounts_payable(chart, vendor_map, vendor, descriptions):
    gl = suggest_gl(vendor, descriptions, chart, vendor_map)

    assert gl.credit_account == "2000"
    assert gl.credit_account_name == "Accounts Payable"
    assert "credit 2000 accounts payable" in gl.reason.lower()


def test_ingest_fills_gl_fields_from_seeded_map(session, fixture_pdf, vendor_map):
    seed_reference_data(session, DATA_DIR)
    path, expected = fixture_pdf("happy")

    invoice = process_invoice(path, session=session)

    assert invoice.gl_account == vendor_map[expected["vendor_name"]]
    assert invoice.gl_confidence >= 0.8
    assert "Credit 2000 Accounts Payable" in invoice.gl_reason


def test_ingest_without_seed_still_suggests_gl(session, fixture_pdf):
    # Falls back to the data/ JSON files when the database has not been seeded yet.
    path, _ = fixture_pdf("happy")
    invoice = process_invoice(path, session=session)
    assert invoice.gl_account is not None
