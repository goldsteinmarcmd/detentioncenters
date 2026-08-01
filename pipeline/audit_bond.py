"""Recompute one facility's bond summaries from the protected stay-level source.

    python -m pipeline.audit_bond OKDBACK

The report contains amounts and frequencies only. It never prints stay or person IDs.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

RAW = Path(__file__).parent / "data" / "raw" / "detention-stays-latest.parquet"
WINDOW_DAYS = 365


def amount_report(label: str, values: pd.Series) -> None:
    values = values.dropna().astype(float)
    print(f"\n{label}")
    print(f"  records : {len(values):,}")
    if values.empty:
        print("  median  : not available")
        return
    print(f"  median  : ${values.median():,.2f}")
    print("  source values:")
    for amount, count in values.value_counts().sort_index().items():
        print(f"    ${amount:>10,.2f}  x {count:,}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independently audit a facility's displayed bond medians."
    )
    parser.add_argument("facility_code", help="DDP detention facility code")
    args = parser.parse_args()

    columns = [
        "stay_book_in_date_time",
        "detention_facility_code_longest",
        "initial_bond_set_amount_lowest_seen",
        "bond_posted_amount_lowest_seen",
    ]
    frame = pq.read_table(RAW, columns=columns).to_pandas()
    book_in = pd.to_datetime(frame["stay_book_in_date_time"], utc=True, errors="coerce")
    cutoff = book_in.max()
    window_start = cutoff - timedelta(days=WINDOW_DAYS)
    code = args.facility_code.strip().upper()
    selected = frame[
        (book_in >= window_start)
        & (frame["detention_facility_code_longest"] == code)
    ]

    if selected.empty:
        raise SystemExit(f"No stays found for {code} in the reporting window.")

    print(f"facility : {code}")
    print(f"window   : {window_start.date()} through {cutoff.date()}")
    print(f"stays    : {len(selected):,}")
    amount_report(
        "Lowest initial bond recorded per stay",
        selected["initial_bond_set_amount_lowest_seen"],
    )
    amount_report(
        "Lowest posted amount recorded per stay",
        selected["bond_posted_amount_lowest_seen"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
