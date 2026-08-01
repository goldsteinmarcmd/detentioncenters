"""Roll the individual-level stays file up to per-facility aggregates.

    python -m pipeline.aggregate

Input is 1.09M rows, one per detention stay, carrying birth year, nationality, and
release reason. Output is counts. No row from this file ever reaches the client — see
CLAUDE.md integrity rule 3.

Two things this module is careful about:

**Flow, not stock.** Every number here counts *people booked in during a window*, which
is a different quantity from average daily population. A facility with a 3-day average
stay churns thousands of people through a handful of beds. The output labels itself
`measure: "flow"` so the UI cannot present it as a snapshot of who is there now.

**Small cells.** Any band with fewer than 5 people emits `{"suppressed": true}` instead
of a count, because a count of 1 at a small facility is re-identifying when combined
with anything else. Age is not cross-tabulated with nationality for the same reason.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

RAW = Path(__file__).parent / "data" / "raw"
OUT = Path(__file__).parent / "data" / "interim"

SUPPRESSION_THRESHOLD = 5
WINDOW_MONTHS = 12
TOP_COUNTRIES = 5

# birth_year has no month or day, so an exact age is off by up to a year. Bands absorb
# that; point ages would be false precision.
AGE_BANDS: list[tuple[int, int, str]] = [
    (0, 17, "0-17"),
    (18, 24, "18-24"),
    (25, 34, "25-34"),
    (35, 44, "35-44"),
    (45, 54, "45-54"),
    (55, 64, "55-64"),
    (65, 200, "65+"),
]

# The facility where the person spent the most time — the most defensible attribution
# when a stay spans several. `_first` and `_last` are computed too so the UI can say
# which basis it is showing.
BASIS_COLUMN = {
    "longest": "detention_facility_code_longest",
    "first": "detention_facility_code_first",
    "last": "detention_facility_code_last",
}

COLUMNS = [
    "detention_facility_code_first",
    "detention_facility_code_longest",
    "detention_facility_code_last",
    "stay_book_in_date_time",
    "stay_book_out_date_time",
    "birth_year",
    "gender",
    "citizenship_country",
    "stay_release_reason",
    "book_in_criminality",
    "detainee_classification",
    "initial_bond_set_amount_lowest_seen",
    "bond_posted_amount_lowest_seen",
]


def _suppress(count: int) -> dict | int:
    """Counts below the threshold never leave the pipeline as exact numbers."""
    if 0 < count < SUPPRESSION_THRESHOLD:
        return {"suppressed": True, "lt": SUPPRESSION_THRESHOLD}
    return int(count)


def _band(age: float) -> str | None:
    if pd.isna(age):
        return None
    for lo, hi, label in AGE_BANDS:
        if lo <= age <= hi:
            return label
    return None


def _counts(series: pd.Series, limit: int | None = None) -> dict:
    vc = series.dropna().value_counts()
    if limit:
        vc = vc.head(limit)
    return {str(k): _suppress(int(v)) for k, v in vc.items()}


def load_stays(path: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Read only the columns we aggregate — 13 of 70 keeps this to a few hundred MB."""
    path = path or (RAW / "detention-stays-latest.parquet")
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. It is a 110 MB Git LFS download; see CLAUDE.md."
        )
    df = pq.read_table(path, columns=COLUMNS).to_pandas()

    book_in = pd.to_datetime(df["stay_book_in_date_time"], utc=True, errors="coerce")
    df["book_in"] = book_in
    cutoff = book_in.max()
    window_start = cutoff - timedelta(days=365 * WINDOW_MONTHS // 12)

    meta = {
        "source": "Deportation Data Project — ICE detention stays (individual-level)",
        "rows_total": int(len(df)),
        "data_cutoff": cutoff.date().isoformat() if pd.notna(cutoff) else None,
        "window_start": window_start.date().isoformat() if pd.notna(cutoff) else None,
        "window_months": WINDOW_MONTHS,
        "measure": "flow",
        "measure_note": (
            "Counts people booked in during the window, not people held on any given "
            "day. Not comparable to average daily population."
        ),
        "attribution_basis": "longest",
        "attribution_note": (
            "A stay spanning several facilities is attributed to the one where the "
            "person spent the most time."
        ),
        "suppression_threshold": SUPPRESSION_THRESHOLD,
    }
    return df, meta


def aggregate(df: pd.DataFrame, meta: dict, basis: str = "longest") -> dict:
    window_start = pd.Timestamp(meta["window_start"], tz="UTC")
    win = df[df["book_in"] >= window_start].copy()

    win["age"] = win["book_in"].dt.year - win["birth_year"]
    win.loc[(win["age"] < 0) | (win["age"] > 120), "age"] = pd.NA
    win["age_band"] = win["age"].map(_band)

    book_out = pd.to_datetime(win["stay_book_out_date_time"], utc=True, errors="coerce")
    win["los_days"] = (book_out - win["book_in"]).dt.total_seconds() / 86400

    code_col = BASIS_COLUMN[basis]
    out: dict[str, dict] = {}

    for code, grp in win.groupby(code_col):
        if not code or pd.isna(code):
            continue
        n = len(grp)
        ages = grp["age"].dropna()
        los = grp["los_days"].dropna()
        bond_set = grp["initial_bond_set_amount_lowest_seen"].dropna()
        bond_posted = grp["bond_posted_amount_lowest_seen"].dropna()

        out[str(code)] = {
            "stays_in_window": n,
            "age_bands": {
                label: _suppress(int((grp["age_band"] == label).sum()))
                for _, _, label in AGE_BANDS
                if (grp["age_band"] == label).sum() > 0
            },
            "age_known": _suppress(len(ages)),
            "age_median": round(float(ages.median()), 1) if len(ages) >= SUPPRESSION_THRESHOLD else None,
            "gender": _counts(grp["gender"]),
            "top_citizenship": _counts(grp["citizenship_country"], TOP_COUNTRIES),
            "release_reason": _counts(grp["stay_release_reason"]),
            "book_in_criminality": _counts(grp["book_in_criminality"]),
            "classification": _counts(grp["detainee_classification"]),
            "length_of_stay_days": {
                "median": round(float(los.median()), 1) if len(los) >= SUPPRESSION_THRESHOLD else None,
                "p90": round(float(los.quantile(0.9)), 1) if len(los) >= SUPPRESSION_THRESHOLD else None,
                "n_completed": _suppress(len(los)),
            },
            "bond": {
                "median_set": round(float(bond_set.median()), 2) if len(bond_set) >= SUPPRESSION_THRESHOLD else None,
                "median_posted": round(float(bond_posted.median()), 2) if len(bond_posted) >= SUPPRESSION_THRESHOLD else None,
                "n_set": _suppress(len(bond_set)),
                "n_posted": _suppress(len(bond_posted)),
            },
        }
    return out


def validate(aggregates: dict) -> None:
    """No emitted count may sit in the re-identifying range."""
    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, int) and not isinstance(node, bool):
            if 0 < node < SUPPRESSION_THRESHOLD and not path.endswith("stays_in_window"):
                raise SystemExit(f"unsuppressed small cell at {path}: {node}")

    for code, agg in aggregates.items():
        walk(agg, code)
        bond = agg["bond"]
        for median_field, count_field in (
            ("median_set", "n_set"),
            ("median_posted", "n_posted"),
        ):
            median = bond[median_field]
            count = bond[count_field]
            if median is not None and median < 0:
                raise SystemExit(f"negative bond median at {code}.{median_field}: {median}")
            if median is not None and (
                not isinstance(count, int) or count < SUPPRESSION_THRESHOLD
            ):
                raise SystemExit(
                    f"bond median without a publishable sample at {code}.{median_field}"
                )


def main() -> int:
    df, meta = load_stays()
    print(f"stays          : {meta['rows_total']:,} rows")
    print(f"data cutoff    : {meta['data_cutoff']}")
    print(f"window         : {meta['window_start']} -> {meta['data_cutoff']}")

    aggregates = aggregate(df, meta)
    validate(aggregates)

    in_window = sum(a["stays_in_window"] for a in aggregates.values())
    print(f"facilities     : {len(aggregates)} with stays in window")
    print(f"stays in window: {in_window:,}")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "facility_aggregates.json"
    path.write_text(json.dumps({"metadata": meta, "facilities": aggregates}))
    print(f"wrote          : {path.relative_to(Path.cwd())} "
          f"({path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
