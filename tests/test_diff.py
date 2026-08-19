from __future__ import annotations

import unittest

from pipeline.diff import compare


def facility(code: str, *, adp=None, rating=None, name="Example Facility", contract="IGSA"):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-97.0, 38.0]},
        "properties": {
            "code": code,
            "name": name,
            "city": "Austin",
            "state": "TX",
            "adp": adp,
            "operator": {"contract_type": contract, "company": None},
            "inspection": {"last_rating": rating, "last_end_date": None},
        },
    }


def release(*features, as_of="2026-08-01"):
    return {
        "metadata": {"sources": [{"source": "DDP", "as_of": as_of}]},
        "features": list(features),
    }


class ReleaseDiffTests(unittest.TestCase):
    def test_reports_added_and_removed_facilities(self) -> None:
        diff = compare(
            release(facility("AAA", adp=100)),
            release(facility("BBB", adp=200)),
        )
        self.assertEqual([c.code for c in diff.added], ["BBB"])
        self.assertEqual([c.code for c in diff.removed], ["AAA"])
        self.assertEqual((diff.before_count, diff.after_count), (1, 1))

    def test_swing_needs_both_an_absolute_and_a_relative_move(self) -> None:
        # 10 people on a base of 20 is a big share but a small number.
        small = compare(release(facility("A", adp=20)), release(facility("A", adp=30)))
        self.assertEqual(small.swings, [])
        # 30 people on a base of 2,000 is a real number but ordinary noise.
        noise = compare(release(facility("A", adp=2000)), release(facility("A", adp=2030)))
        self.assertEqual(noise.swings, [])
        # Both floors cleared.
        real = compare(release(facility("A", adp=200)), release(facility("A", adp=400)))
        self.assertEqual(len(real.swings), 1)
        self.assertIn("up 200", real.swings[0].detail)

    def test_a_number_that_stops_being_published_is_not_a_fall_to_zero(self) -> None:
        diff = compare(release(facility("A", adp=500)), release(facility("A", adp=None)))
        self.assertEqual(diff.swings, [])
        self.assertEqual(len(diff.reporting_stopped), 1)
        self.assertIn("was 500", diff.reporting_stopped[0].detail)

    def test_a_number_that_starts_being_published_is_not_a_rise_from_zero(self) -> None:
        diff = compare(release(facility("A", adp=None)), release(facility("A", adp=500)))
        self.assertEqual(diff.swings, [])
        self.assertEqual(len(diff.reporting_started), 1)

    def test_a_published_zero_is_real_movement_not_an_absence(self) -> None:
        diff = compare(release(facility("A", adp=0)), release(facility("A", adp=90)))
        self.assertEqual(len(diff.swings), 1)
        self.assertEqual(diff.reporting_started, [])

    def test_inspection_and_contract_changes_are_reported(self) -> None:
        diff = compare(
            release(facility("A", adp=100, rating="Pass", contract="IGSA")),
            release(facility("A", adp=100, rating="Fail", contract="CDF")),
        )
        details = sorted(c.detail for c in diff.attributes)
        self.assertEqual(len(details), 2)
        self.assertIn("contract type: IGSA → CDF", details)
        self.assertIn("inspection rating: Pass → Fail", details)

    def test_unchanged_release_reports_nothing(self) -> None:
        same = release(facility("A", adp=100, rating="Pass"))
        diff = compare(same, same)
        payload = diff.to_dict()["changes"]
        self.assertEqual({k: v for k, v in payload.items() if v}, {})

    def test_a_fractional_average_is_never_rounded_to_a_misleading_zero(self) -> None:
        diff = compare(release(facility("A", adp=0.49)), release())
        self.assertIn("0.5", diff.removed[0].detail)
        self.assertNotIn("0 held", diff.removed[0].detail)

    def test_as_of_dates_are_carried_through(self) -> None:
        diff = compare(
            release(facility("A"), as_of="2026-07-20"),
            release(facility("A"), as_of="2026-08-03"),
        )
        self.assertEqual(diff.before_as_of, "2026-07-20")
        self.assertEqual(diff.after_as_of, "2026-08-03")


if __name__ == "__main__":
    unittest.main()
