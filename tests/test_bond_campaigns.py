from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pipeline.bond_campaigns import FIELDS, build_cases


class BondCampaignTests(unittest.TestCase):
    def build(self, **overrides) -> dict:
        row = {
            "case_id": "BF-7K2M9Q",
            "region": "California",
            "bond_amount": "7500",
            "amount_raised": "1250",
            "booking_category": "pending_charge_reported",
            "verified_on": "2026-08-01",
            "verification_expires_on": "2026-08-08",
            "public_summary": "Consent-based anonymous campaign.",
            "contribution_url": "https://fund.example/cases/BF-7K2M9Q",
            "contribution_recipient": "Example Bond Fund",
            "terms_url": "https://fund.example/terms",
            "status": "accepting",
            "consent_confirmed": "TRUE",
            **overrides,
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "campaigns.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerow(row)
            return build_cases(source, today=date(2026, 8, 1))

    def test_publishes_only_anonymous_campaign_fields(self) -> None:
        campaign = self.build()["cases"][0]
        self.assertEqual(campaign["bond_amount"], 7500)
        self.assertEqual(campaign["booking_category"]["code"], "pending_charge_reported")
        self.assertNotIn("name", campaign)
        self.assertNotIn("a_number", campaign)

    def test_expired_verification_disables_contributions(self) -> None:
        campaign = self.build(
            verified_on="2026-07-24", verification_expires_on="2026-07-31"
        )["cases"][0]
        self.assertEqual(campaign["status"], "verification_expired")
        self.assertIsNone(campaign["contribution_url"])

    def test_requires_consent(self) -> None:
        with self.assertRaisesRegex(ValueError, "consent_confirmed"):
            self.build(consent_confirmed="FALSE")

    def test_rejects_a_number_in_public_summary(self) -> None:
        with self.assertRaisesRegex(ValueError, "A-Number"):
            self.build(public_summary="Case A123456789 needs support.")


if __name__ == "__main__":
    unittest.main()
