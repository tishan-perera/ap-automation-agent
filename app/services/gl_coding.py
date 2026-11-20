"""Suggest a GL expense account with a confidence score and a written reason.

Order: vendor map (high confidence) -> line-description keywords (medium) -> no suggestion,
so a specialist codes it by hand (low). The credit side is always 2000 Accounts Payable.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppSetting, Invoice
from app.services.normalize import normalize

VENDOR_MAP_CONFIDENCE = 0.9
KEYWORD_CONFIDENCE = 0.6
FALLBACK_CONFIDENCE = 0.0
CREDIT_ACCOUNT = ("2000", "Accounts Payable")
CREDIT_NOTE = "Credit 2000 Accounts Payable."

KEYWORDS = {
    "6100": {"paper", "pens", "office", "supplies", "toner", "stationery"},
    "6200": {"software", "subscription", "seat", "license", "cloud", "storage", "saas"},
    "6300": {"consulting", "review", "workshop", "advisory", "facilitation", "audit"},
    "6400": {"janitorial", "rent", "hvac", "facilities", "maintenance", "lease", "cleaning"},
    "6500": {"travel", "hotel", "airfare", "meals", "mileage", "lodging"},
    "6600": {"electricity", "water", "sewer", "gas", "utility", "utilities"},
    "6700": {"campaign", "marketing", "brochure", "advertising", "social", "promotion"},
}


@dataclass
class GLSuggestion:
    account: str | None
    account_name: str | None
    confidence: float
    reason: str
    credit_account: str = CREDIT_ACCOUNT[0]
    credit_account_name: str = CREDIT_ACCOUNT[1]


def _account_names(chart: dict) -> dict[str, str]:
    return {account["code"]: account["name"] for account in chart["expense_accounts"]}


def _keyword_match(descriptions: list[str]) -> tuple[str, list[str]] | None:
    words = set(re.findall(r"[a-z]+", " ".join(descriptions).lower()))
    best = max(KEYWORDS, key=lambda code: len(KEYWORDS[code] & words))
    hits = sorted(KEYWORDS[best] & words)
    return (best, hits) if hits else None


def suggest_gl(
    vendor_name: str | None, descriptions: list[str], chart: dict, vendor_map: dict[str, str]
) -> GLSuggestion:
    names = _account_names(chart)
    by_vendor = {normalize(vendor): code for vendor, code in vendor_map.items()}

    code = by_vendor.get(normalize(vendor_name))
    if code:
        return GLSuggestion(
            code,
            names.get(code),
            VENDOR_MAP_CONFIDENCE,
            f"Vendor map: {vendor_name} is coded to {code} {names.get(code)}. {CREDIT_NOTE}",
        )

    keyword = _keyword_match([d for d in descriptions if d])
    if keyword:
        code, hits = keyword
        return GLSuggestion(
            code,
            names.get(code),
            KEYWORD_CONFIDENCE,
            f"No vendor map entry for {vendor_name}; line-description keywords "
            f"({', '.join(hits)}) suggest {code} {names.get(code)}. {CREDIT_NOTE}",
        )

    return GLSuggestion(
        None,
        None,
        FALLBACK_CONFIDENCE,
        f"No vendor map entry or keyword match for {vendor_name}; "
        f"needs manual GL coding by a specialist. {CREDIT_NOTE}",
    )


def load_gl_config(session: Session) -> tuple[dict, dict]:
    """Chart and vendor map from app_settings, or from data/ files if not seeded yet."""
    data_dir = Path(get_settings().data_dir)
    config = []
    for key in ("chart_of_accounts", "vendor_gl_map"):
        row = session.get(AppSetting, key)
        raw = row.value if row and row.value else (data_dir / f"{key}.json").read_text()
        config.append(json.loads(raw))
    return config[0], config[1]


def apply_gl(session: Session, invoice: Invoice) -> GLSuggestion:
    chart, vendor_map = load_gl_config(session)
    descriptions = [line.description for line in invoice.line_items]
    gl = suggest_gl(invoice.vendor_name, descriptions, chart, vendor_map)
    invoice.gl_account = gl.account
    invoice.gl_account_name = gl.account_name
    invoice.gl_confidence = gl.confidence
    invoice.gl_reason = gl.reason
    return gl
