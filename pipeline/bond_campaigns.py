"""Build the public, anonymous bond-campaign feed.

The input is intentionally a publishable export, not a case-management database.
Names, A-Numbers, birth dates, documents, and contact details do not belong here.

    python -m pipeline.bond_campaigns
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent.parent
SOURCE = Path(__file__).parent / "data" / "manual" / "bond_campaigns.csv"
OUT = Path(
    os.environ.get("DETENTION_MAP_OUT_DIR", ROOT / "web" / "public" / "data")
)

FIELDS = (
    "case_id",
    "region",
    "bond_amount",
    "amount_raised",
    "booking_category",
    "verified_on",
    "verification_expires_on",
    "public_summary",
    "contribution_url",
    "contribution_recipient",
    "terms_url",
    "status",
    "consent_confirmed",
)

BOOKING_CATEGORIES = {
    "conviction_reported": {
        "label": "Conviction reported",
        "note": "A conviction was reported in the case record reviewed by the partner.",
    },
    "pending_charge_reported": {
        "label": "Pending charge reported",
        "note": "A pending charge was reported; a charge is not a conviction.",
    },
    "immigration_only": {
        "label": "Immigration-only classification",
        "note": "No criminal charge or conviction was reported in the reviewed booking record.",
    },
    "not_reported": {
        "label": "Not reported",
        "note": "The partner did not publish a criminal-history classification for this case.",
    },
}

STATUSES = {"accepting", "funded", "bond_posted", "released", "paused"}
CASE_ID = re.compile(r"^BF-[A-Z0-9]{6,16}$")
A_NUMBER = re.compile(r"\bA[- ]?\d{8,9}\b", re.IGNORECASE)


def _fail(row_number: int, message: str) -> None:
    raise ValueError(f"bond campaign row {row_number}: {message}")


def _money(value: str, row_number: int, field: str) -> int:
    try:
        amount = int(value)
    except ValueError:
        _fail(row_number, f"{field} must be a whole-dollar amount")
    if amount < 0:
        _fail(row_number, f"{field} cannot be negative")
    return amount


def _date(value: str, row_number: int, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(row_number, f"{field} must be YYYY-MM-DD")


def _https_url(value: str, row_number: int, field: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        _fail(row_number, f"{field} must be an HTTPS URL")
    if parsed.username or parsed.password:
        _fail(row_number, f"{field} cannot contain credentials")
    return value


def build_cases(source: Path = SOURCE, *, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    cases = []
    seen_ids: set[str] = set()

    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(
                "bond campaign columns must exactly match: " + ", ".join(FIELDS)
            )

        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            row = {key: (value or "").strip() for key, value in row.items()}
            case_id = row["case_id"]
            if not CASE_ID.fullmatch(case_id):
                _fail(row_number, "case_id must be BF- followed by 6-16 letters or digits")
            if case_id in seen_ids:
                _fail(row_number, f"duplicate case_id {case_id}")
            seen_ids.add(case_id)

            if row["consent_confirmed"].upper() != "TRUE":
                _fail(row_number, "consent_confirmed must be TRUE before publication")
            if A_NUMBER.search(row["public_summary"]):
                _fail(row_number, "public_summary appears to contain an A-Number")
            if len(row["public_summary"]) > 360:
                _fail(row_number, "public_summary must be 360 characters or fewer")

            bond_amount = _money(row["bond_amount"], row_number, "bond_amount")
            amount_raised = _money(row["amount_raised"], row_number, "amount_raised")
            if bond_amount <= 0:
                _fail(row_number, "bond_amount must be greater than zero")

            category = row["booking_category"]
            if category not in BOOKING_CATEGORIES:
                _fail(
                    row_number,
                    "booking_category must be one of "
                    + ", ".join(sorted(BOOKING_CATEGORIES)),
                )

            status = row["status"]
            if status not in STATUSES:
                _fail(row_number, "status must be one of " + ", ".join(sorted(STATUSES)))

            verified_on = _date(row["verified_on"], row_number, "verified_on")
            expires_on = _date(
                row["verification_expires_on"], row_number, "verification_expires_on"
            )
            if verified_on > today:
                _fail(row_number, "verified_on cannot be in the future")
            if expires_on < verified_on:
                _fail(row_number, "verification_expires_on cannot precede verified_on")

            contribution_url = _https_url(
                row["contribution_url"], row_number, "contribution_url"
            )
            terms_url = _https_url(row["terms_url"], row_number, "terms_url")
            if not row["contribution_recipient"]:
                _fail(row_number, "contribution_recipient is required")

            public_status = status
            if status == "accepting" and expires_on < today:
                public_status = "verification_expired"

            cases.append(
                {
                    "case_id": case_id,
                    "region": row["region"] or None,
                    "bond_amount": bond_amount,
                    "amount_raised": amount_raised,
                    "booking_category": {
                        "code": category,
                        **BOOKING_CATEGORIES[category],
                    },
                    "verified_on": verified_on.isoformat(),
                    "verification_expires_on": expires_on.isoformat(),
                    "public_summary": row["public_summary"] or None,
                    "contribution_url": (
                        contribution_url if public_status == "accepting" else None
                    ),
                    "contribution_recipient": row["contribution_recipient"],
                    "terms_url": terms_url,
                    "status": public_status,
                }
            )

    cases.sort(key=lambda item: (item["status"] != "accepting", item["case_id"]))
    return {
        "metadata": {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "privacy": (
                "Anonymous, consented campaign records only. No names, A-Numbers, "
                "birth dates, contact details, or case documents are published."
            ),
        },
        "cases": cases,
    }


def main() -> int:
    payload = build_cases()
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / "bond-cases.json"
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {destination} ({len(payload['cases'])} campaigns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
