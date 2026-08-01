"""Approximate coordinates for facilities DDP could not geocode.

135 of the 844 facilities have no published street address — they are ICE hold rooms
and field-office holding areas, and DDP leaves their coordinates null. They also have
no city or county field: 133 of the 135 carry nothing but a name and a state.

So the only location signal available is the place name inside the facility name
("Albany Hold Room" in NY). This module strips the role words off a facility name,
matches what is left against the US Census Gazetteer for that state, and returns the
Census interior point for the matched place.

This is an inference, and it is labelled as one. Every coordinate produced here carries
a `location_precision` other than `exact` plus the place it was matched to, so the map
can render it differently and the panel can say plainly how the point was derived. A
pin from this module means "somewhere in this city", never "at this address".

Nothing here overwrites a real coordinate — it only fills nulls.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

RAW = Path(__file__).parent / "data" / "raw"
MANUAL = Path(__file__).parent / "data" / "manual"

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
    "2024_Gaz_place_national.zip"
)

# Suffixes the Census appends to place names — not part of the name itself.
# Lowercase: they are matched against a lowercased name.
LSAD_SUFFIXES = (
    "city and borough", "consolidated government", "metropolitan government",
    "unified government", "urban county", "municipality", "corporation",
    "borough", "village", "township", "town", "city", "cdp", "comunidad",
    "zona urbana", "plantation", "gore", "grant", "location", "reservation",
)

# Words describing what the facility *is*. Whatever survives should be the place.
ROLE_WORDS = (
    "detention and removal operations", "command center", "district office",
    "field office", "sub office", "suboffice", "processing center", "service center",
    "holding facility", "detention center", "detention facility", "staging facility",
    "hold room", "holding room", "hold rooms", "holdroom", "staging area",
    "county jail", "medical center", "hospital", "airport", "annex",
    "hold", "holding", "jail", "prison", "facility", "office", "staging",
    "ero", "ice", "ins", "usms", "dro", "fo", "spc", "cdf",
)


@dataclass(frozen=True)
class Located:
    latitude: float
    longitude: float
    precision: str
    matched_to: str
    source: str


def _normalize(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _strip_lsad(name: str) -> str:
    """Peel Census designators off the end, repeatedly.

    "Indianapolis city (balance)" arrives here as "Indianapolis city" once the
    parenthetical is gone, and needs the "city" taken off too.
    """
    changed = True
    while changed:
        changed = False
        low = name.lower().rstrip()
        for suffix in LSAD_SUFFIXES:
            if low.endswith(" " + suffix):
                name = name[: len(low) - len(suffix) - 1].rstrip()
                changed = True
                break
    return name


def _place_keys(raw_name: str) -> list[str]:
    """Lookup keys for one Census place: the real name first, then aliases.

    Aliases exist because the Census name is often not what anyone calls the place:
    "Nashville-Davidson metropolitan government (balance)" is Nashville, and
    "Boise City" is Boise. Aliases are only ever used to fill a key nothing else
    claimed, so a real place always beats an alias.
    """
    name = re.sub(r"\(.*?\)", " ", raw_name)  # drop "(balance)" and friends
    base = _strip_lsad(name)
    keys = [_normalize(base)]

    if "-" in base:
        keys.append(_normalize(_strip_lsad(base.split("-")[0])))

    # "Boise City" -> also answer to "Boise".
    norm = _normalize(base)
    if norm.endswith(" city"):
        keys.append(norm[: -len(" city")])

    # "Town of Pecos City" -> "pecos". The Census uses a leading form for some places,
    # which the trailing-suffix strip above cannot reach.
    for prefix in ("town of ", "city of ", "village of ", "borough of "):
        for candidate in (norm, *keys):
            if candidate.startswith(prefix):
                stem = candidate[len(prefix) :]
                keys.append(stem)
                if stem.endswith(" city"):
                    keys.append(stem[: -len(" city")])

    return [k for k in dict.fromkeys(keys) if k]


def load_gazetteer() -> tuple[dict, dict]:
    """(state, place) -> coordinates, plus (state, county) -> coordinates.

    Where a state has several places with the same name, the one with the largest land
    area wins — the Census lists both "Albany city" and small CDPs of the same name.
    """
    places: dict[tuple[str, str], tuple[float, float, float]] = {}
    counties: dict[tuple[str, str], tuple[float, float]] = {}

    place_zip = RAW / "gaz_place.zip"
    county_zip = RAW / "gaz_counties.zip"
    if not place_zip.exists():
        raise FileNotFoundError(
            f"{place_zip} not found. Download it from {GAZETTEER_URL}"
        )

    zf = zipfile.ZipFile(place_zip)
    df = pd.read_csv(
        io.BytesIO(zf.read(zf.namelist()[0])), sep="\t", dtype=str, engine="python"
    )
    df.columns = [c.strip() for c in df.columns]

    aliases: dict[tuple[str, str], tuple[float, float, float]] = {}
    for row in df.itertuples():
        try:
            lat = float(str(row.INTPTLAT).strip())
            lon = float(str(row.INTPTLONG).strip())
            area = float(str(row.ALAND).strip() or 0)
        except (ValueError, AttributeError):
            continue
        state = str(row.USPS).strip().upper()
        keys = _place_keys(str(row.NAME))
        if not keys:
            continue
        # Primary name wins outright; ties broken by land area, so "Springfield city"
        # beats a tiny "Springfield CDP" elsewhere in the same state.
        primary = (state, keys[0])
        prev = places.get(primary)
        if prev is None or area > prev[2]:
            places[primary] = (lat, lon, area)
        for alias in keys[1:]:
            key = (state, alias)
            prev = aliases.get(key)
            if prev is None or area > prev[2]:
                aliases[key] = (lat, lon, area)

    for key, value in aliases.items():
        places.setdefault(key, value)

    if county_zip.exists():
        zf = zipfile.ZipFile(county_zip)
        cdf = pd.read_csv(
            io.BytesIO(zf.read(zf.namelist()[0])), sep="\t", dtype=str, engine="python"
        )
        cdf.columns = [c.strip() for c in cdf.columns]
        for row in cdf.itertuples():
            try:
                lat = float(str(row.INTPTLAT).strip())
                lon = float(str(row.INTPTLONG).strip())
            except (ValueError, AttributeError):
                continue
            name = _normalize(str(row.NAME).replace(" County", ""))
            counties[(str(row.USPS).strip().upper(), name)] = (lat, lon)

    return {k: (v[0], v[1]) for k, v in places.items()}, counties


def state_centroids(counties: dict) -> dict[str, tuple[float, float]]:
    """Mean of a state's county interior points — a last-resort, state-level marker."""
    acc: dict[str, list[tuple[float, float]]] = {}
    for (state, _), (lat, lon) in counties.items():
        acc.setdefault(state, []).append((lat, lon))
    return {
        state: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        for state, pts in acc.items()
    }


def candidate_place(name: str, state: str = "") -> list[str]:
    """Place-name candidates from a facility name, most specific first.

    "Austin Detention and Removal Operations Hold Room"     -> austin
    "Athens, TX Hold Room"                                  -> athens
    "ERO Hold Room (Springfield MO)"                        -> springfield
    "South Texas/Pearsall Hold Room"                        -> ... pearsall
    """
    fragments: list[str] = []
    # A parenthetical usually *is* the location, so it goes first.
    fragments.extend(re.findall(r"\((.*?)\)", name))
    fragments.append(re.sub(r"\(.*?\)", " ", name))

    out: list[str] = []
    for fragment in fragments:
        text = _normalize(fragment)
        for role in sorted(ROLE_WORDS, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(role)}\b", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue

        tokens = text.split()
        # "charleston wv" -> drop the trailing state code.
        if state and len(tokens) > 1 and tokens[-1] == state.strip().lower():
            tokens = tokens[:-1]
        if not tokens:
            continue

        # Longest phrases first — "salt lake city" must beat "salt". Leading windows
        # before trailing ones, so "south texas pearsall" still reaches "pearsall".
        for size in range(len(tokens), 0, -1):
            for start in range(0, len(tokens) - size + 1):
                out.append(" ".join(tokens[start : start + size]))

    return list(dict.fromkeys(out))


def load_location_overrides() -> dict[tuple[str, str], Located]:
    """Hand-placed facilities, each with its reasoning recorded in the CSV.

    Covers what no gazetteer can resolve: US territories and Guantanamo, which are not
    in the Census place file; facility names that are not place names (Krome); a
    misspelling in the source (Bakerfield); and ICE office codes (PHI, SFR, HEL).
    Human-owned — the build reads this and never writes it.
    """
    import csv

    path = MANUAL / "location_overrides.csv"
    if not path.exists():
        return {}
    out: dict[tuple[str, str], Located] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (KeyError, ValueError):
                continue
            key = (row["name"].strip().upper(), row["state"].strip().upper())
            out[key] = Located(
                latitude=lat,
                longitude=lon,
                precision=row.get("precision") or "manual",
                matched_to=row.get("matched_to") or "",
                source=f"Hand-placed: {row.get('evidence', '').strip()}",
            )
    return out


def locate(
    name: str,
    state: str,
    places: dict,
    counties: dict,
    states: dict,
    overrides: dict[tuple[str, str], Located] | None = None,
) -> Located | None:
    """Best available approximate point for a facility, or None."""
    st = (state or "").strip().upper()
    if not st:
        return None

    if overrides:
        hit = overrides.get((name.strip().upper(), st))
        if hit:
            return hit

    candidates = candidate_place(name, st)

    for cand in candidates:
        hit = places.get((st, cand))
        if hit:
            return Located(
                latitude=hit[0],
                longitude=hit[1],
                precision="city_centroid",
                matched_to=f"{cand.title()}, {st}",
                source="US Census Bureau 2024 Gazetteer (place interior point)",
            )

    # County fallback only when the facility name actually says "county". Without that
    # guard, "Boise Hold Room" lands in rural Boise County, 60 miles from Boise.
    if re.search(r"\bcounty\b", name, re.I):
        for cand in candidates:
            hit = counties.get((st, cand))
            if hit:
                return Located(
                    latitude=hit[0],
                    longitude=hit[1],
                    precision="county_centroid",
                    matched_to=f"{cand.title()} County, {st}",
                    source="US Census Bureau 2024 Gazetteer (county interior point)",
                )

    hit = states.get(st)
    if hit:
        return Located(
            latitude=hit[0],
            longitude=hit[1],
            precision="state_centroid",
            matched_to=st,
            source="Mean of county interior points, US Census Bureau 2024 Gazetteer",
        )
    return None
