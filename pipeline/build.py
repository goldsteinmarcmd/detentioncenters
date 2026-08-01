"""Build the map's data files.

    python -m pipeline.build

Emits web/public/data/facilities.geojson — every facility, each carrying a
`location_precision` saying how its coordinates were arrived at — plus
facilities-unplaced.json for anything that could not be placed at all, and a
build-report.json recording provenance and validation results.

709 facilities have a hand-verified address from DDP and are marked `exact`. The
remaining 135 have no published address; they are placed by `geocode.py` at a city or
county centroid and marked accordingly, so an approximate pin can be styled and
explained rather than passing as an address.

Guard counts are carried explicitly as unavailable rather than omitted. See CLAUDE.md
'What the data actually supports'.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .crosswalk import (
    assert_match_rate,
    assert_no_collisions,
    build_crosswalk,
    match_report,
)
from .geocode import (
    load_gazetteer,
    load_location_overrides,
    locate,
    state_centroids,
)
from .sources import load_ddp_facilities, load_ice_facilities

OUT = Path(
    os.environ.get(
        "DETENTION_MAP_OUT_DIR",
        Path(__file__).parent.parent / "web" / "public" / "data",
    )
)
AGGREGATES = Path(__file__).parent / "data" / "interim" / "facility_aggregates.json"

# ICE ADP columns are all fiscal-year-to-date averages of the same vintage, so they can
# be summed with each other — but never with DDP's trailing-year ADP.
ICE_POP_COLS = ["Male Crim", "Male Non-Crim", "Female Crim", "Female Non-Crim"]


def clean(value):
    """NaN/NaT -> None. Null means 'not published'; zero means 'published as zero'."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    if isinstance(value, (pd.Timestamp,)):
        return None if pd.isna(value) else value.date().isoformat()
    if hasattr(value, "item"):
        try:
            return clean(value.item())
        except (ValueError, AttributeError):
            pass
    return value


def _ice_total_adp(row) -> float | None:
    parts = [clean(row.get(c)) for c in ICE_POP_COLS]
    present = [p for p in parts if p is not None]
    return round(sum(present), 2) if present else None


def load_aggregates() -> tuple[dict, dict]:
    """Per-facility demographic rollups from pipeline.aggregate, if they've been run."""
    if not AGGREGATES.exists():
        return {}, {}
    payload = json.loads(AGGREGATES.read_text())
    return payload.get("facilities", {}), payload.get("metadata", {})


def build_feature_properties(
    ddp_row, ice_row, ice_as_of, ddp_as_of, agg=None, agg_meta=None
) -> dict:
    """One facility's display payload, with every value's origin attached."""
    has_ice = ice_row is not None
    agg_meta = agg_meta or {}

    def ice(col):
        return clean(ice_row.get(col)) if has_ice else None

    # Flat duplicates of nested values, purely for MapLibre styling: cluster
    # accumulators and paint expressions cannot reach into nested property objects.
    # Canonical values stay in the nested blocks below; these are display plumbing.
    adp = clean(ddp_row["average_daily_population_last_year"])

    props = {
        "adp": adp,
        "rating": clean(ice_row.get("Last Final Rating")) if has_ice else None,
        "contract": clean(ice_row.get("Type Detailed")) if has_ice else None,
        "code": ddp_row["detention_facility_code"],
        "name": clean(ddp_row["name"]),
        "address": clean(ddp_row["address_full"]) or clean(ddp_row["address"]),
        "city": clean(ddp_row["city"]),
        "county": clean(ddp_row["county"]),
        "state": clean(ddp_row["state"]),
        "zip": clean(ddp_row["zip"]),
        "field_office": clean(ddp_row["field_office"]),
        "federal_court_district": clean(
            ddp_row["federal_court_district_of_confinement"]
        ),
        "federal_court_circuit": clean(
            ddp_row["federal_court_circuit_of_confinement"]
        ),
        # --- population: two different vintages, deliberately not blended ---
        "population": {
            "ddp_avg_daily_trailing_year": clean(
                ddp_row["average_daily_population_last_year"]
            ),
            "ddp_max_daily_trailing_year": clean(
                ddp_row["max_daily_population_last_year"]
            ),
            "ice_fy_adp_total": _ice_total_adp(ice_row) if has_ice else None,
            "ice_fy_adp_male_criminal": ice("Male Crim"),
            "ice_fy_adp_male_noncriminal": ice("Male Non-Crim"),
            "ice_fy_adp_female_criminal": ice("Female Crim"),
            "ice_fy_adp_female_noncriminal": ice("Female Non-Crim"),
            "ice_fy_adp_mandatory": ice("Mandatory"),
            # Set when ICE reports a higher average than DDP ever observed present at
            # once — the signature of a facility that came into use after DDP's window
            # closed. The DDP figure is then stale rather than wrong, and must not be
            # presented as the current population.
            "ddp_figure_stale": None,
        },
        "classification_adp": {
            "level_a": ice("Level A"),
            "level_b": ice("Level B"),
            "level_c": ice("Level C"),
            "level_d": ice("Level D"),
        },
        "operator": {
            # Contract *structure*, which ICE publishes. The operating *company* is a
            # separate, hand-cited crosswalk and is deliberately null until sourced.
            "contract_type": ice("Type Detailed"),
            "company": None,
            "company_status": "not_yet_researched",
        },
        "inspection": {
            "last_type": ice("Last Inspection Type"),
            "last_end_date": ice("Last Inspection End Date"),
            "last_standard": ice("Last Inspection Standard"),
            "last_rating": ice("Last Final Rating"),
        },
        "avg_length_of_stay_days": next(
            (ice(c) for c in ice_row.index if "ALOS" in str(c)), None
        )
        if has_ice
        else None,
        "guaranteed_minimum_beds": ice("Guaranteed Minimum"),
        "sex_designation": ice("Male/Female"),
        # --- who is held here: derived from individual-level stays, aggregated ---
        # A flow measure over its own window, on its own cutoff. Deliberately kept in
        # a separate block from `population` so the two can never be read as one number.
        "demographics": (
            {
                "measure": agg_meta.get("measure"),
                "measure_note": agg_meta.get("measure_note"),
                "window_start": agg_meta.get("window_start"),
                "window_end": agg_meta.get("data_cutoff"),
                "attribution_basis": agg_meta.get("attribution_basis"),
                "suppression_threshold": agg_meta.get("suppression_threshold"),
                **agg,
            }
            if agg
            else None
        ),
        # --- fields that do not exist in public data ---
        "not_publicly_reported": {
            "guard_count": (
                "ICE does not publish facility staffing levels, and they do not appear "
                "in FOIA releases. Closest published proxy: guaranteed minimum beds."
            ),
        },
        "sources": {
            "location": {
                "source": "Deportation Data Project — ICE detention facilities",
                "as_of": ddp_as_of,
            },
            "population.ddp_*": {
                "source": "Deportation Data Project (trailing year)",
                "url": (
                    "https://github.com/deportationdata/ice-detention-facilities/raw/"
                    "refs/heads/main/data/facilities-latest.parquet"
                ),
                "as_of": ddp_as_of,
            },
            "population.ice_*, classification_adp, inspection, operator.contract_type, "
            "guaranteed_minimum_beds, avg_length_of_stay_days": {
                "source": "ICE Detention Statistics (fiscal year to date)",
                "url": "https://www.ice.gov/detain/detention-management",
                "as_of": ice_as_of,
            }
            if has_ice
            else None,
            "demographics": {
                "source": "Deportation Data Project — individual-level detention stays",
                "url": (
                    "https://github.com/deportationdata/ice/raw/refs/heads/main/data/"
                    "detention-stays-latest.parquet"
                ),
                "as_of": agg_meta.get("data_cutoff"),
                "note": (
                    "Aggregated in the pipeline; no individual records are published. "
                    "Cutoff is earlier than the ICE and facility data above — these "
                    "numbers describe a different period and must not be combined."
                ),
            }
            if agg_meta
            else None,
        },
        "has_ice_attributes": has_ice,
    }

    pop = props["population"]
    peak = pop["ddp_max_daily_trailing_year"]
    ice_total = pop["ice_fy_adp_total"]
    if ice_total and peak is not None and peak > 0 and ice_total > peak:
        pop["ddp_figure_stale"] = (
            f"This facility appears to have come into use after the trailing-year window "
            f"closed. Over that window it held at most {peak:,.0f}, so the trailing-year "
            f"average below understates it badly. ICE's fiscal-year figures are the "
            f"current ones for this facility."
        )

    lat, lon = clean(ddp_row["latitude"]), clean(ddp_row["longitude"])
    if lat is not None and lon is not None:
        # Only an exact address earns a directions link. Sending someone driving to a
        # city centroid would be worse than offering nothing.
        props["directions_url"] = f"geo:{lat},{lon}?q={lat},{lon}"
    return props


# How precise a pin is, in words the panel can show verbatim.
PRECISION_LABELS = {
    "exact": None,
    "city_centroid": "No address is published for this facility, so it is placed at the "
    "centre of {place} — somewhere in this city, not at this point.",
    "manual_city_centroid": "Placed by hand at the centre of {place}; no address is "
    "published. Somewhere in this city, not at this point.",
    "county_centroid": "No address or city is published, so it is placed at the centre "
    "of {place} — somewhere in this county.",
    "manual_county_seat": "Placed by hand near {place}; no address is published for "
    "this facility.",
    "manual_office_code": "The facility is identified only by an ICE office code, read "
    "here as {place}. Inferred from the code, not from a published address.",
    "manual_facility_area": "Placed by hand in the area of {place}; no street address "
    "is published.",
    "state_centroid": "Nothing beyond the state is published for this facility, so it "
    "sits at the centre of {place} as a placeholder. Do not read the position as a "
    "location at all.",
}


def apply_location(props: dict, ddp_row, located) -> tuple[float, float] | None:
    """Attach coordinates and say plainly how precise they are.

    A real coordinate is never overwritten. Where one is derived, the precision, the
    place it was matched to, and the source all travel with it so the map can style it
    differently and the panel can explain it.
    """
    lat, lon = clean(ddp_row["latitude"]), clean(ddp_row["longitude"])
    if lat is not None and lon is not None:
        props["location_precision"] = "exact"
        props["location_note"] = None
        props["approx"] = 0
        props["sources"]["location"] = {
            "source": "Deportation Data Project — geocoded and hand-verified",
            "as_of": props["sources"]["location"]["as_of"],
        }
        return lat, lon

    if located is None:
        props["location_precision"] = "none"
        props["location_note"] = (
            "No location is published for this facility beyond its state, and it could "
            "not be placed."
        )
        props["approx"] = 1
        return None

    template = PRECISION_LABELS.get(located.precision) or PRECISION_LABELS["city_centroid"]
    props["location_precision"] = located.precision
    props["location_matched_to"] = located.matched_to
    props["location_note"] = template.format(place=located.matched_to)
    props["approx"] = 1
    props["sources"]["location"] = {
        "source": located.source,
        "as_of": None,
        "note": "Derived location — not a published facility address.",
    }
    return located.latitude, located.longitude


def main() -> int:
    ddp, ddp_prov = load_ddp_facilities()
    ice, ice_prov = load_ice_facilities()
    print(f"DDP facilities : {ddp_prov.rows:>4}  as of {ddp_prov.as_of}")
    print(f"ICE facilities : {ice_prov.rows:>4}  as of {ice_prov.as_of}")

    joined, results = build_crosswalk(ice, ddp)
    report = match_report(results)
    print(
        f"crosswalk      : {report['matched']}/{report['total']} "
        f"({report['rate']:.1%})  collisions={len(report['collisions'])}"
    )
    assert_match_rate(report)
    assert_no_collisions(report)

    ice_by_code = {
        row["detention_facility_code"]: row
        for _, row in joined.iterrows()
        if row["detention_facility_code"]
    }

    aggregates, agg_meta = load_aggregates()
    if aggregates:
        print(
            f"demographics   : {len(aggregates)} facilities, window "
            f"{agg_meta.get('window_start')} -> {agg_meta.get('data_cutoff')}"
        )
    else:
        print("demographics   : none (run `python -m pipeline.aggregate` first)")

    places, counties = load_gazetteer()
    states = state_centroids(counties)
    overrides = load_location_overrides()

    features, unplaced = [], []
    precision_counts: dict[str, int] = {}
    for _, row in ddp.iterrows():
        code = row["detention_facility_code"]
        ice_row = ice_by_code.get(code)
        props = build_feature_properties(
            row,
            ice_row,
            ice_prov.as_of,
            ddp_prov.as_of,
            agg=aggregates.get(code),
            agg_meta=agg_meta,
        )

        located = None
        if clean(row["latitude"]) is None or clean(row["longitude"]) is None:
            located = locate(
                str(row["name"]), str(row["state"]), places, counties, states, overrides
            )
        coords = apply_location(props, row, located)
        precision_counts[props["location_precision"]] = (
            precision_counts.get(props["location_precision"], 0) + 1
        )

        if coords is None:
            unplaced.append(props)
            continue
        lat, lon = coords
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            }
        )

    build_meta = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "sources": [ddp_prov.to_dict(), ice_prov.to_dict()],
        "crosswalk": {
            "matched": report["matched"],
            "total": report["total"],
            "rate": round(report["rate"], 4),
            "methods": report["methods"],
        },
        "counts": {
            "facilities_total": len(ddp),
            "mapped": len(features),
            "unplaced": len(unplaced),
            "exact_location": precision_counts.get("exact", 0),
            "approximate_location": sum(
                v for k, v in precision_counts.items() if k not in ("exact", "none")
            ),
            "with_ice_attributes": sum(
                1 for f in features if f["properties"]["has_ice_attributes"]
            ),
            "with_demographics": sum(
                1 for f in features if f["properties"]["demographics"]
            ),
        },
        "location_precision": dict(
            sorted(precision_counts.items(), key=lambda kv: -kv[1])
        ),
        "demographics_window": {
            "start": agg_meta.get("window_start"),
            "end": agg_meta.get("data_cutoff"),
            "measure": agg_meta.get("measure"),
        }
        if agg_meta
        else None,
    }

    validate(features, unplaced, ddp)

    OUT.mkdir(parents=True, exist_ok=True)
    geojson = {
        "type": "FeatureCollection",
        "metadata": build_meta,
        "features": features,
    }
    _write(OUT / "facilities.geojson", geojson)
    _write(
        OUT / "facilities-unplaced.json",
        {"metadata": build_meta, "facilities": unplaced},
    )
    _write(OUT / "build-report.json", {**build_meta, "unmatched": report["unmatched"]})

    print(
        f"mapped         : {len(features)}  "
        f"({build_meta['counts']['with_ice_attributes']} with ICE attributes)"
    )
    for precision, n in build_meta["location_precision"].items():
        print(f"  {precision:24s} {n:4d}")
    if unplaced:
        print(f"unplaced       : {len(unplaced)}")
    for name in ("facilities.geojson", "facilities-unplaced.json"):
        kb = (OUT / name).stat().st_size / 1024
        print(f"  {name:32s} {kb:8.1f} KB")
    return 0


def validate(features: list[dict], unplaced: list[dict], ddp: pd.DataFrame) -> None:
    """Fail the build rather than ship something quietly wrong."""
    if len(features) + len(unplaced) != len(ddp):
        raise SystemExit(
            f"facility count mismatch: {len(features)}+{len(unplaced)} != {len(ddp)}"
        )

    codes = [f["properties"]["code"] for f in features] + [
        u["code"] for u in unplaced
    ]
    if len(set(codes)) != len(codes):
        raise SystemExit("duplicate facility codes in output")

    for f in features:
        lon, lat = f["geometry"]["coordinates"]
        if lat is None or lon is None:
            raise SystemExit(f"null geometry survived for {f['properties']['code']}")
        # Guantanamo (~19.9N, -75.1W) is in scope, so bounds are world-valid, not CONUS.
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise SystemExit(f"impossible coordinates for {f['properties']['code']}")

    for f in features:
        if f["properties"]["operator"]["company"] is not None:
            raise SystemExit(
                f"{f['properties']['code']} has an operator company but no citation; "
                "operator enrichment must carry a source (CLAUDE.md integrity rule 2)"
            )

    # Nothing re-identifying may reach the client, even if aggregate.py changes.
    # An approximate pin must always carry its explanation, or it reads as an address.
    for f in features:
        props = f["properties"]
        if props.get("approx") and not props.get("location_note"):
            raise SystemExit(
                f"{props['code']} is placed approximately but carries no explanation"
            )

    for f in list(features) + list(unplaced):
        props = f["properties"] if "properties" in f else f
        _assert_suppressed(props.get("demographics"), props["code"])


def _assert_suppressed(demographics, code: str) -> None:
    """Re-check small-cell suppression at the last point before it ships."""
    if not demographics:
        return
    threshold = demographics.get("suppression_threshold") or 5
    skip = {"stays_in_window", "suppression_threshold", "age_median"}

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in skip:
                    continue
                walk(v, f"{path}.{k}")
        elif isinstance(node, int) and not isinstance(node, bool):
            if 0 < node < threshold:
                raise SystemExit(
                    f"unsuppressed cell n={node} at {code}{path} — "
                    "violates CLAUDE.md integrity rule 4"
                )

    walk(demographics, "")


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=None, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
