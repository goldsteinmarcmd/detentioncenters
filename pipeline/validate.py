"""Independent checks on the built data.

    python -m pipeline.validate

The build already fails on structural problems — match rate, collisions, unsuppressed
small cells, missing location notes. This module asks a different and harder question:
are the numbers we produce *consistent with numbers nobody in this pipeline computed*?

Every check here compares our output against an independently published figure, or
against a constraint the data must satisfy regardless of how it was assembled. Checks
report rather than fail: a drifting correlation is a prompt to look, not proof of a bug.

What this cannot do: verify that ICE's own published figures are true. If ICE publishes
a wrong population, this pipeline faithfully reproduces a wrong population. Nothing
here, and nothing in the project, can see past the source.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import pandas as pd

from .crosswalk import build_crosswalk, generic_tokens, normalize_tokens
from .geocode import load_gazetteer
from .sources import load_ddp_facilities, load_ice_facilities

AGGREGATES = Path(__file__).parent / "data" / "interim" / "facility_aggregates.json"
GEOJSON = Path(
    os.environ.get(
        "DETENTION_MAP_GEOJSON",
        Path(__file__).parent.parent / "web" / "public" / "data" / "facilities.geojson",
    )
)

# How far outside the spread of a state's county centroids a facility may sit before
# it is worth a look. Generous — this is a gross-error check, not a precision one.
STATE_BBOX_PAD_DEG = 1.5


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else float("nan")


def check_population_agreement(ice: pd.DataFrame, ddp: pd.DataFrame) -> dict:
    """DDP's trailing-year ADP vs ICE's fiscal-year ADP, for the 208 joined facilities.

    Two organisations, two windows, two methods. They should not be equal — but they
    should track each other closely. A weak correlation would be the signature of a
    crosswalk that has joined facilities to the wrong rows.
    """
    joined, _ = build_crosswalk(ice, ddp)
    ddp_by_code = ddp.set_index("detention_facility_code")

    pairs = []
    for _, row in joined.iterrows():
        code = row["detention_facility_code"]
        if not code or code not in ddp_by_code.index:
            continue
        ddp_adp = ddp_by_code.loc[code, "average_daily_population_last_year"]
        ice_adp = sum(
            row.get(c, 0) or 0
            for c in ("Male Crim", "Male Non-Crim", "Female Crim", "Female Non-Crim")
        )
        if pd.notna(ddp_adp) and ice_adp > 0 and ddp_adp > 0:
            pairs.append((float(ddp_adp), float(ice_adp), str(row["Name"]), code))

    r = pearson([p[0] for p in pairs], [p[1] for p in pairs])
    ratios = sorted(
        ((p[1] / p[0], p[2], p[3], p[0], p[1]) for p in pairs), key=lambda t: t[0]
    )
    return {
        "n": len(pairs),
        "correlation": r,
        "median_ratio": ratios[len(ratios) // 2][0] if ratios else float("nan"),
        "most_divergent": ratios[:3] + ratios[-3:],
    }


def check_length_of_stay(ice: pd.DataFrame, ddp: pd.DataFrame) -> dict:
    """Our median stay, computed from individual records, vs ICE's published ALOS.

    These are different statistics — a median against a mean — so they will not match,
    and the mean should generally sit above the median because long stays have no upper
    bound. What matters is that they move together. This is the strongest independent
    check available: the two are derived from entirely separate ICE releases.
    """
    if not AGGREGATES.exists():
        return {"skipped": "run pipeline.aggregate first"}
    aggregates = json.loads(AGGREGATES.read_text())["facilities"]

    joined, _ = build_crosswalk(ice, ddp)
    alos_col = next((c for c in joined.columns if "ALOS" in str(c)), None)
    if not alos_col:
        return {"skipped": "no ALOS column in the ICE sheet"}

    pairs = []
    for _, row in joined.iterrows():
        code = row["detention_facility_code"]
        agg = aggregates.get(code) if code else None
        ours = (agg or {}).get("length_of_stay_days", {}).get("median")
        theirs = row.get(alos_col)
        if ours and pd.notna(theirs) and theirs > 0:
            pairs.append((float(ours), float(theirs), str(row["Name"])))

    if not pairs:
        return {"n": 0}
    r = pearson([p[0] for p in pairs], [p[1] for p in pairs])
    diffs = sorted(((p[1] - p[0], p[2], p[0], p[1]) for p in pairs), key=lambda t: t[0])
    within = sum(1 for o, t, _ in pairs if abs(t - o) <= 0.5 * max(o, t))
    return {
        "n": len(pairs),
        "correlation": r,
        "within_50pct": within,
        "within_50pct_share": within / len(pairs),
        "most_divergent": diffs[:3] + diffs[-3:],
    }


def check_coordinates_in_state(ddp: pd.DataFrame) -> dict:
    """Does every facility sit inside the state it claims?

    Built from the spread of each state's county interior points, padded generously.
    This catches transposed or mis-joined coordinates — the kind of error that puts a
    Texas facility in Ohio and is invisible in any table.
    """
    _, counties = load_gazetteer()
    bounds: dict[str, list[float]] = {}
    for (state, _), (lat, lon) in counties.items():
        b = bounds.setdefault(state, [lat, lat, lon, lon])
        b[0], b[1] = min(b[0], lat), max(b[1], lat)
        b[2], b[3] = min(b[2], lon), max(b[3], lon)

    outside, unknown = [], 0
    for row in ddp.itertuples():
        lat, lon = row.latitude, row.longitude
        if pd.isna(lat) or pd.isna(lon):
            continue
        b = bounds.get(str(row.state).upper())
        if not b:
            unknown += 1  # territories and Guantanamo have no county coverage
            continue
        if not (
            b[0] - STATE_BBOX_PAD_DEG <= lat <= b[1] + STATE_BBOX_PAD_DEG
            and b[2] - STATE_BBOX_PAD_DEG <= lon <= b[3] + STATE_BBOX_PAD_DEG
        ):
            outside.append((row.detention_facility_code, row.name, row.state, lat, lon))
    return {"outside_state": outside, "no_bounds_available": unknown}


def check_crosswalk_name_agreement(ice: pd.DataFrame, ddp: pd.DataFrame) -> dict:
    """How much distinctive name evidence supports each ICE->DDP join?

    A 100% match rate is not the same as 100% correct. The collision assertion catches
    two ICE rows landing on one facility; it cannot catch one row landing on the wrong
    facility. Joins resting on no shared distinctive token are the ones most likely to
    be wrong, so they are listed for a human to read.
    """
    joined, _ = build_crosswalk(ice, ddp)
    ddp_by_code = ddp.set_index("detention_facility_code")
    generic = generic_tokens(ddp["name"].map(lambda n: set(normalize_tokens(n))))

    weak = []
    for _, row in joined.iterrows():
        code = row["detention_facility_code"]
        if not code or code not in ddp_by_code.index:
            continue
        ice_tokens = set(normalize_tokens(str(row["Name"]))) - generic
        ddp_tokens = set(normalize_tokens(str(ddp_by_code.loc[code, "name"]))) - generic
        shared = ice_tokens & ddp_tokens
        if not shared:
            weak.append(
                (
                    str(row["Name"]),
                    str(ddp_by_code.loc[code, "name"]),
                    code,
                    str(row["_match_method"]),
                )
            )
    return {"total": len(joined), "no_shared_distinctive_token": weak}


def check_stale_ddp_population(ice: pd.DataFrame, ddp: pd.DataFrame) -> dict:
    """Facilities where ICE reports more people than DDP ever saw present at once.

    That is not a contradiction to resolve — it is the signature of a facility that came
    into use after DDP's population window closed. Central Valley Annex was in use 53
    days with a DDP maximum of one person, while ICE's FY26 average is 83.7; its sibling
    at the same zip, in use all 365 days, agrees closely.

    It matters because DDP's ADP is the headline figure in the panel and the value the
    map sizes markers by. For these facilities that figure is stale, and showing it
    beside an ICE breakdown of forty people is internally contradictory.
    """
    joined, _ = build_crosswalk(ice, ddp)
    ddp_by_code = ddp.set_index("detention_facility_code")

    stale = []
    for _, row in joined.iterrows():
        code = row["detention_facility_code"]
        if not code or code not in ddp_by_code.index:
            continue
        ice_adp = sum(
            row.get(c, 0) or 0
            for c in ("Male Crim", "Male Non-Crim", "Female Crim", "Female Non-Crim")
        )
        peak = ddp_by_code.loc[code, "max_daily_population_last_year"]
        if ice_adp > 0 and pd.notna(peak) and peak > 0 and ice_adp > peak:
            stale.append(
                (
                    ice_adp / peak,
                    code,
                    str(ddp_by_code.loc[code, "name"]),
                    float(peak),
                    float(ice_adp),
                    ddp_by_code.loc[code, "days_with_detentions_daily_last_year"],
                )
            )
    stale.sort(reverse=True)
    return {"n": len(stale), "facilities": stale}


def check_suppression() -> dict:
    """Re-derive the small-cell guarantee from the shipped file, not from memory."""
    if not GEOJSON.exists():
        return {"skipped": "run pipeline.build first"}
    data = json.loads(GEOJSON.read_text())
    violations = []

    def walk(node, path, threshold):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("stays_in_window", "suppression_threshold", "age_median"):
                    continue
                walk(v, f"{path}.{k}", threshold)
        elif isinstance(node, int) and not isinstance(node, bool):
            if 0 < node < threshold:
                violations.append((path, node))

    checked = 0
    for feat in data["features"]:
        demo = feat["properties"].get("demographics")
        if not demo:
            continue
        checked += 1
        walk(demo, feat["properties"]["code"], demo.get("suppression_threshold") or 5)
    return {"facilities_checked": checked, "violations": violations}


def check_shipped_fidelity(ddp: pd.DataFrame, ice: pd.DataFrame) -> dict:
    """Every displayed population and demographic value matches its pipeline source."""
    if not GEOJSON.exists() or not AGGREGATES.exists():
        return {"skipped": "run pipeline.aggregate and pipeline.build first"}

    data = json.loads(GEOJSON.read_text())
    shipped = {f["properties"]["code"]: f["properties"] for f in data["features"]}
    ddp_by_code = ddp.set_index("detention_facility_code")
    joined, _ = build_crosswalk(ice, ddp)
    ice_by_code = {
        row["detention_facility_code"]: row
        for _, row in joined.iterrows()
        if row["detention_facility_code"]
    }
    aggregates = json.loads(AGGREGATES.read_text())["facilities"]
    population_mismatches = []
    demographic_mismatches = []

    for code, props in shipped.items():
        row = ddp_by_code.loc[code]
        expected_population = {
            "ddp_avg_daily_trailing_year": row["average_daily_population_last_year"],
            "ddp_max_daily_trailing_year": row["max_daily_population_last_year"],
        }
        for field, expected in expected_population.items():
            expected = None if pd.isna(expected) else float(expected)
            if props["population"][field] != expected:
                population_mismatches.append((code, field, expected, props["population"][field]))

        ice_row = ice_by_code.get(code)
        if ice_row is not None:
            ice_fields = {
                "ice_fy_adp_male_criminal": "Male Crim",
                "ice_fy_adp_male_noncriminal": "Male Non-Crim",
                "ice_fy_adp_female_criminal": "Female Crim",
                "ice_fy_adp_female_noncriminal": "Female Non-Crim",
                "ice_fy_adp_mandatory": "Mandatory",
            }
            parts = []
            for field, column in ice_fields.items():
                raw = ice_row[column]
                expected = None if pd.isna(raw) else float(raw)
                if field != "ice_fy_adp_mandatory" and expected is not None:
                    parts.append(expected)
                if props["population"][field] != expected:
                    population_mismatches.append((code, field, expected, props["population"][field]))
            expected_total = round(sum(parts), 2) if parts else None
            if props["population"]["ice_fy_adp_total"] != expected_total:
                population_mismatches.append(
                    (code, "ice_fy_adp_total", expected_total, props["population"]["ice_fy_adp_total"])
                )

        demo = props.get("demographics")
        expected_demo = aggregates.get(code)
        if bool(demo) != bool(expected_demo):
            demographic_mismatches.append((code, "presence"))
        elif demo:
            display_metadata = {
                "measure",
                "measure_note",
                "window_start",
                "window_end",
                "attribution_basis",
                "suppression_threshold",
            }
            displayed_aggregate = {k: v for k, v in demo.items() if k not in display_metadata}
            if displayed_aggregate != expected_demo:
                demographic_mismatches.append((code, "values"))

    return {
        "populations_checked": len(shipped),
        "population_mismatches": population_mismatches,
        "demographics_checked": sum(bool(p.get("demographics")) for p in shipped.values()),
        "demographic_mismatches": demographic_mismatches,
        "aggregate_codes_not_in_registry": sorted(set(aggregates) - set(shipped)),
    }


def main(*, strict: bool = False) -> int:
    ddp, ddp_prov = load_ddp_facilities()
    ice, ice_prov = load_ice_facilities()
    print(f"DDP {ddp_prov.as_of} · ICE {ice_prov.as_of}\n")

    print("1. Population — DDP trailing year vs ICE fiscal year (independent releases)")
    pop = check_population_agreement(ice, ddp)
    print(f"   facilities compared : {pop['n']}")
    print(f"   correlation         : {pop['correlation']:.4f}")
    print(f"   median ICE/DDP ratio: {pop['median_ratio']:.2f}")
    print("   most divergent:")
    for ratio, name, code, d, i in pop["most_divergent"]:
        print(f"     {ratio:6.2f}x  {name[:38]:38s} {code:8s} DDP {d:7.1f}  ICE {i:7.1f}")

    print("\n2. Length of stay — our median from records vs ICE's published mean")
    los = check_length_of_stay(ice, ddp)
    if "skipped" in los:
        print(f"   skipped: {los['skipped']}")
    else:
        print(f"   facilities compared : {los['n']}")
        print(f"   correlation         : {los['correlation']:.4f}")
        print(
            f"   within 50% of each other: {los['within_50pct']}/{los['n']} "
            f"({los['within_50pct_share']:.0%})"
        )
        print("   most divergent (ICE mean − our median, days):")
        for diff, name, ours, theirs in los["most_divergent"]:
            print(f"     {diff:+8.1f}  {name[:38]:38s} ours {ours:6.1f}  ICE {theirs:6.1f}")

    print("\n3. Coordinates fall inside the state each facility claims")
    geo = check_coordinates_in_state(ddp)
    print(f"   outside state bounds: {len(geo['outside_state'])}")
    for code, name, state, lat, lon in geo["outside_state"][:10]:
        print(f"     {code:10s} {name[:36]:36s} {state}  {lat:.3f},{lon:.3f}")
    print(f"   no bounds available : {geo['no_bounds_available']} (territories, GTMO)")

    print("\n4. Crosswalk joins resting on no shared distinctive name token")
    names = check_crosswalk_name_agreement(ice, ddp)
    weak = names["no_shared_distinctive_token"]
    print(f"   {len(weak)} of {names['total']} joins")
    for ice_name, ddp_name, code, method in weak:
        print(f"     {ice_name[:34]:34s} -> {ddp_name[:34]:34s} {code:8s} via {method}")

    print("\n5. Facilities whose DDP population figure is stale (in use after the window)")
    stale = check_stale_ddp_population(ice, ddp)
    print(f"   {stale['n']} of the joined facilities")
    for ratio, code, name, peak, ice_adp, days in stale["facilities"]:
        print(
            f"     {code:8s} {name[:34]:34s} DDP peak {peak:5.0f}  ICE ADP {ice_adp:6.1f}  "
            f"in use {days:.0f}d  ({ratio:.0f}x)"
        )

    print("\n6. Small-cell suppression, re-derived from the shipped GeoJSON")
    sup = check_suppression()
    if "skipped" in sup:
        print(f"   skipped: {sup['skipped']}")
    else:
        print(f"   facilities with demographics: {sup['facilities_checked']}")
        print(f"   cells in the re-identifying range: {len(sup['violations'])}")
        for path, n in sup["violations"][:10]:
            print(f"     {path} = {n}")

    print("\n7. Shipped population and demographic values match their source records")
    fidelity = check_shipped_fidelity(ddp, ice)
    if "skipped" in fidelity:
        print(f"   skipped: {fidelity['skipped']}")
    else:
        print(f"   facility populations checked : {fidelity['populations_checked']}")
        print(f"   population mismatches        : {len(fidelity['population_mismatches'])}")
        print(f"   demographic records checked  : {fidelity['demographics_checked']}")
        print(f"   demographic mismatches       : {len(fidelity['demographic_mismatches'])}")
        orphans = fidelity["aggregate_codes_not_in_registry"]
        print(f"   historical codes not in current registry: {', '.join(orphans) or 'none'}")
    if strict:
        failures = []
        if "skipped" in sup:
            failures.append(f"suppression check skipped: {sup['skipped']}")
        elif sup["violations"]:
            failures.append(f"{len(sup['violations'])} small-cell suppression violations")

        if "skipped" in fidelity:
            failures.append(f"fidelity check skipped: {fidelity['skipped']}")
        else:
            if fidelity["population_mismatches"]:
                failures.append(
                    f"{len(fidelity['population_mismatches'])} population mismatches"
                )
            if fidelity["demographic_mismatches"]:
                failures.append(
                    f"{len(fidelity['demographic_mismatches'])} demographic mismatches"
                )

        if failures:
            print("\nSTRICT VALIDATION FAILED: " + "; ".join(failures))
            return 1
        print("\nStrict publication checks passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero on source-fidelity or privacy-check failures.",
    )
    args = parser.parse_args()
    raise SystemExit(main(strict=args.strict))
