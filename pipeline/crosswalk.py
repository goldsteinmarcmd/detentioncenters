"""Join ICE's facility attributes onto the DDP facility spine.

The ICE spreadsheet carries no facility code, so the join is by name and place. Naive
normalized-name + state matching gets ~56%, which would silently strip attributes from
nearly half the facilities. The staged strategy below is the validated one; see
CLAUDE.md 'Joining ICE <-> DDP'.

Order matters: cheap and precise first, fuzzy last, hand-curated overrides ahead of
everything so a human decision is never overruled by an algorithm.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

MANUAL = Path(__file__).parent / "data" / "manual"
OVERRIDES = MANUAL / "facility_code_overrides.csv"

FUZZY_THRESHOLD = 0.84
MIN_MATCH_RATE = 0.95

# Expanded so that "CTR" and "CENTER" compare equal. Applied per token, after the
# name is split on non-alphanumerics.
ABBREVIATIONS = {
    "CTR": "CENTER",
    "CO": "COUNTY",
    "DET": "DETENTION",
    "FAC": "FACILITY",
    "CORR": "CORRECTIONAL",
    "INST": "INSTITUTION",
    "FED": "FEDERAL",
    "PROC": "PROCESSING",
}

# Dropped entirely — present in some names, absent in others, and never distinguishing.
STOPWORDS = {"THE", "OF", "AND", "DEPT"}

# A token shared by more than this fraction of facility names carries no identifying
# information. "CENTER" appears in hundreds of names; "WEBB" appears in two. Requiring
# a *distinctive* shared token is what keeps ICE's WEBB COUNTY DETENTION CENTER from
# matching CORECIVIC LAREDO PROCESSING CENTER on the strength of "CENTER" alone —
# which it otherwise does, because ICE lists Webb County under the wrong zip.
GENERIC_TOKEN_MAX_DF = 0.05


@dataclass
class MatchResult:
    ice_index: int
    ice_name: str
    state: str
    detention_facility_code: str | None
    method: str
    score: float | None


def normalize_tokens(name: str) -> list[str]:
    """Uppercase, split on non-alphanumerics, expand abbreviations, drop stopwords."""
    if not isinstance(name, str):
        return []
    raw = "".join(ch if ch.isalnum() else " " for ch in name.upper()).split()
    out = [ABBREVIATIONS.get(tok, tok) for tok in raw]
    return [tok for tok in out if tok not in STOPWORDS]


def normalize_name(name: str) -> str:
    return " ".join(normalize_tokens(name))


def _zip5(value) -> str | None:
    """ICE stores zips as text, DDP sometimes as ZIP+4. Compare the first 5 digits."""
    if pd.isna(value):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:5].zfill(5) if digits else None


def load_overrides() -> dict[str, str]:
    """Hand-maintained ICE-name -> facility-code map. Human-owned; never rewritten."""
    if not OVERRIDES.exists():
        return {}
    with OVERRIDES.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {
        f"{r['ice_name'].strip().upper()}|{r['state'].strip().upper()}": r[
            "detention_facility_code"
        ].strip()
        for r in rows
        if r.get("detention_facility_code", "").strip()
    }


def build_crosswalk(
    ice: pd.DataFrame, ddp: pd.DataFrame
) -> tuple[pd.DataFrame, list[MatchResult]]:
    """Resolve each ICE row to a DDP detention_facility_code.

    Returns the ICE frame with a `detention_facility_code` column plus the per-row
    match record, so an unmatched row can be reported by name rather than by index.
    """
    overrides = load_overrides()

    ddp = ddp.copy()
    ddp["norm_name"] = ddp["name"].map(normalize_name)
    ddp["name_tokens"] = ddp["name"].map(lambda n: set(normalize_tokens(n)))
    ddp["zip5"] = ddp["zip"].map(_zip5)
    generic = generic_tokens(ddp["name_tokens"])
    ddp["distinctive"] = ddp["name_tokens"].map(lambda t: t - generic)
    by_state: dict[str, pd.DataFrame] = {
        state: grp for state, grp in ddp.groupby(ddp["state"].str.upper())
    }

    results: list[MatchResult] = []
    for idx, row in ice.iterrows():
        ice_name = str(row["Name"])
        state = str(row["State"]).strip().upper()
        results.append(
            _match_one(
                idx=int(idx),
                ice_name=ice_name,
                state=state,
                ice_zip=_zip5(row.get("Zip")),
                candidates=by_state.get(state),
                overrides=overrides,
                generic=generic,
            )
        )

    out = ice.copy()
    out["detention_facility_code"] = [r.detention_facility_code for r in results]
    out["_match_method"] = [r.method for r in results]
    return out, results


def generic_tokens(token_sets: pd.Series) -> set[str]:
    """Tokens too common across facility names to identify anything.

    Derived from the data rather than hardcoded, so it stays correct as the registry
    grows. With 844 facilities this selects the expected set: CENTER, COUNTY,
    DETENTION, CORRECTIONAL, FACILITY, JAIL, PROCESSING, and similar.
    """
    n = len(token_sets)
    if not n:
        return set()
    df: dict[str, int] = {}
    for toks in token_sets:
        for tok in toks:
            df[tok] = df.get(tok, 0) + 1
    return {tok for tok, count in df.items() if count / n > GENERIC_TOKEN_MAX_DF}


def _match_one(
    idx: int,
    ice_name: str,
    state: str,
    ice_zip: str | None,
    candidates: pd.DataFrame | None,
    overrides: dict[str, str],
    generic: set[str],
) -> MatchResult:
    def result(code, method, score=None):
        return MatchResult(idx, ice_name, state, code, method, score)

    override = overrides.get(f"{ice_name.strip().upper()}|{state}")
    if override:
        return result(override, "override")

    if candidates is None or candidates.empty:
        return result(None, "unmatched:no-candidates-in-state")

    tokens = set(normalize_tokens(ice_name))
    distinctive = tokens - generic
    norm = normalize_name(ice_name)
    ambiguous_at_zip = False

    # 1. Same zip AND at least one shared *distinctive* token. Zip alone is not enough
    #    — a county jail and an ICE hold room share one. A shared generic token is not
    #    enough either; that is what mis-assigned Webb County to Laredo.
    if ice_zip:
        zip_hits = candidates[candidates["zip5"] == ice_zip]
        shared = zip_hits[
            zip_hits["distinctive"].map(lambda t: bool(t & distinctive))
        ]
        if len(shared) == 1:
            return result(shared.iloc[0]["detention_facility_code"], "zip+token")
        if len(shared) > 1:
            # Several facilities at one zip sharing a distinctive token. Rank by token
            # overlap only, and require a strict winner.
            #
            # String similarity is deliberately *not* used to break a tie here. Three
            # Karnes County facilities share zip 78118 and the single token "Karnes";
            # what separates them is "Immigration Processing" vs "Correctional" vs
            # "Residential", which similarity scoring reads as noise and ranks wrongly.
            # It picked a facility holding 0.2 people for an ICE row reporting 1,076.
            # When the name cannot decide, nothing in this file can: refuse, and let a
            # human resolve it with evidence.
            scored = sorted(
                (
                    (
                        len(c.distinctive & distinctive) / max(len(distinctive), 1),
                        c.detention_facility_code,
                    )
                    for c in shared.itertuples()
                ),
                reverse=True,
            )
            if len(scored) == 1 or scored[0][0] > scored[1][0]:
                return result(scored[0][1], "zip+token:multi", round(scored[0][0], 3))
            # Ambiguous here, but not necessarily ambiguous overall: an exact name
            # match is stronger evidence than sharing a zip, so fall through rather
            # than refuse. Only the end of the chain gets to give up.
            ambiguous_at_zip = True

    # 2. Exact normalized-name equality within the state.
    exact = candidates[candidates["norm_name"] == norm]
    if len(exact) >= 1:
        return result(exact.iloc[0]["detention_facility_code"], "exact-name")

    # 3. Distinctive-token containment. Catches "WEBB COUNTY DETENTION CENTER (CCA)"
    #    against "Corecivic Webb County Detention Center" — same facility, different
    #    operator prefix, and a zip ICE has wrong. Requires an unambiguous winner.
    if distinctive:
        scored = [
            (c.detention_facility_code, len(c.distinctive & distinctive) / len(distinctive))
            for c in candidates.itertuples()
        ]
        top = max(scored, key=lambda kv: kv[1])
        if top[1] >= 1.0 and sum(1 for _, s in scored if s >= 1.0) == 1:
            return result(top[0], "distinctive-token", round(top[1], 3))

    # 4. Fuzzy, deliberately last and deliberately tight — and never on its own.
    #
    #    String similarity alone is actively dangerous here, because facility names
    #    differ precisely where they matter: "LUBBOCK COUNTY DETENTION CENTER" and
    #    "Brooks County Detention Center" score 0.87, differing only in the county —
    #    the one token that identifies the place. Requiring a shared distinctive token
    #    as well means a high score can corroborate a match but never create one.
    best_code, best_score = None, 0.0
    for cand in candidates.itertuples():
        if distinctive and not (cand.distinctive & distinctive):
            continue
        score = SequenceMatcher(None, norm, cand.norm_name).ratio()
        if score > best_score:
            best_code, best_score = cand.detention_facility_code, score
    if best_score >= FUZZY_THRESHOLD:
        return result(best_code, "fuzzy", round(best_score, 3))

    if ambiguous_at_zip:
        return result(None, "unmatched:ambiguous-at-zip", None)
    return result(None, "unmatched", round(best_score, 3) if best_code else None)


def match_report(results: list[MatchResult]) -> dict:
    matched = [r for r in results if r.detention_facility_code]
    unmatched = [r for r in results if not r.detention_facility_code]
    methods: dict[str, int] = {}
    for r in matched:
        methods[r.method] = methods.get(r.method, 0) + 1

    by_code: dict[str, list[MatchResult]] = {}
    for r in matched:
        by_code.setdefault(r.detention_facility_code, []).append(r)

    return {
        "total": len(results),
        "matched": len(matched),
        "rate": len(matched) / len(results) if results else 0.0,
        "methods": dict(sorted(methods.items(), key=lambda kv: -kv[1])),
        "unmatched": [
            {"name": r.ice_name, "state": r.state, "best_score": r.score}
            for r in unmatched
        ],
        "collisions": [
            {
                "detention_facility_code": code,
                "ice_names": [r.ice_name for r in rows],
                "methods": [r.method for r in rows],
            }
            for code, rows in by_code.items()
            if len(rows) > 1
        ],
    }


def assert_match_rate(report: dict) -> None:
    """A silent drop here means facilities quietly lose their attributes. Fail loudly."""
    if report["rate"] < MIN_MATCH_RATE:
        names = ", ".join(f"{u['name']} ({u['state']})" for u in report["unmatched"])
        raise SystemExit(
            f"ICE<->DDP match rate {report['rate']:.1%} below the {MIN_MATCH_RATE:.0%} "
            f"floor ({report['matched']}/{report['total']}).\n"
            f"Add codes to {OVERRIDES} for: {names}"
        )


def assert_no_collisions(report: dict) -> None:
    """Two ICE facilities may never resolve to one DDP code.

    A collision is worse than an unmatched row: the second write silently overwrites
    the first, so one facility shows another's inspection rating and population while
    both look populated. This is unrecoverable downstream, so it fails the build.
    """
    if report["collisions"]:
        detail = "\n".join(
            f"  {c['detention_facility_code']}: " + " + ".join(c["ice_names"])
            for c in report["collisions"]
        )
        raise SystemExit(
            f"{len(report['collisions'])} ICE facility pair(s) resolved to one DDP "
            f"code — attributes would overwrite each other:\n{detail}\n"
            f"Resolve in {OVERRIDES}."
        )
