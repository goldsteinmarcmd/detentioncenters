"""Load the raw sources.

Everything here is read-only with respect to upstream: files land in data/raw/ and are
never rewritten in place. Each loader returns a (DataFrame, provenance) pair so the
as-of date travels with the data instead of being reattached later and guessed at.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd

RAW = Path(__file__).parent / "data" / "raw"
MANUAL = Path(__file__).parent / "data" / "manual"

DDP_FACILITIES_URL = (
    "https://github.com/deportationdata/ice-detention-facilities/raw/"
    "refs/heads/main/data/facilities-latest.parquet"
)
ICE_DETENTION_PAGE = "https://www.ice.gov/detain/detention-management"
SOURCE_MANIFEST = RAW / "source-manifest.json"


@dataclass(frozen=True)
class Provenance:
    """Where a table came from and what date it speaks to."""

    source: str
    url: str
    as_of: str | None
    retrieved_at: str | None
    sha256: str | None
    local_file: str
    rows: int

    def to_dict(self) -> dict:
        return asdict(self)


def _newest_raw(pattern: str) -> Path:
    """Newest file in data/raw/ matching a glob, by name (ICE names sort by date)."""
    matches = sorted(RAW.glob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"No file matching {pattern!r} in {RAW}. "
            f"Fetch it first (see CLAUDE.md 'Data sources')."
        )
    return matches[-1]


def _newest_ice_raw() -> Path:
    matches = list(RAW.glob("FY*_detentionStats*.xlsx"))
    dated = [(d, p) for p in matches if (d := _ice_release_date(p.name))]
    if not dated:
        raise FileNotFoundError(
            f"No ICE detention statistics spreadsheet in {RAW}. Fetch it first."
        )
    return max(dated, key=lambda item: item[0])[1]


def _manifest_entry(key: str) -> dict:
    if not SOURCE_MANIFEST.exists():
        return {}
    try:
        payload = json.loads(SOURCE_MANIFEST.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return payload.get("sources", {}).get(key, {})


def load_ddp_facilities() -> tuple[pd.DataFrame, Provenance]:
    """DDP facility registry — the spine. 844 rows, one per facility.

    Accepts either the plain parquet or the `-sf` (simple-features) variant; the sf
    file carries an extra WKB `geometry` column that duplicates latitude/longitude,
    so it is dropped rather than carried through the pipeline.
    """
    path = _newest_raw("facilities-latest*.parquet")
    df = pd.read_parquet(path)
    df = df.drop(columns=[c for c in ("geometry",) if c in df.columns])

    # 2 of 844 rows have no facility code. They still represent real facilities, so
    # they get a synthetic key rather than being dropped (CLAUDE.md, Conventions).
    missing = df["detention_facility_code"].isna()
    if missing.any():
        slug = (
            df.loc[missing, "name"]
            .fillna("unknown")
            .str.upper()
            .str.replace(r"[^A-Z0-9]+", "-", regex=True)
            .str.strip("-")
            .str.slice(0, 40)
        )
        df.loc[missing, "detention_facility_code"] = "SYNTH-" + slug

    dupes = df["detention_facility_code"].duplicated().sum()
    if dupes:
        raise ValueError(f"{dupes} duplicate detention_facility_code in DDP facilities")

    source_meta = _manifest_entry("ddp_facilities")
    prov = Provenance(
        source="Deportation Data Project — ICE detention facilities",
        url=DDP_FACILITIES_URL,
        as_of=source_meta.get("published_at") or _file_date(path),
        retrieved_at=source_meta.get("retrieved_at"),
        sha256=source_meta.get("sha256"),
        local_file=path.name,
        rows=len(df),
    )
    return df, prov


def load_ice_facilities() -> tuple[pd.DataFrame, Provenance]:
    """ICE detention statistics — the `Facilities FY##` sheet.

    Only lists facilities with a population count greater than zero, which is why this
    is ~208 rows against DDP's 844. The header row has moved between fiscal years, so
    it is located by scanning for the row carrying both `Name` and `Address` rather
    than hardcoded.
    """
    path = _newest_ice_raw()
    sheet = _facilities_sheet_name(path)

    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=25)
    header_row = _find_header_row(probe)
    df = pd.read_excel(path, sheet_name=sheet, header=header_row)
    df = df[df["Name"].notna()].reset_index(drop=True)

    source_meta = _manifest_entry("ice_facilities")
    prov = Provenance(
        source=f"ICE Detention Statistics — sheet {sheet!r}",
        url=f"https://www.ice.gov/doclib/detention/{path.name}",
        as_of=_ice_release_date(path.name),
        retrieved_at=source_meta.get("retrieved_at"),
        sha256=source_meta.get("sha256"),
        local_file=path.name,
        rows=len(df),
    )
    return df, prov


def _facilities_sheet_name(path: Path) -> str:
    """Find the facilities sheet. Its name carries the fiscal year, e.g. 'Facilities FY26'."""
    names = pd.ExcelFile(path).sheet_names
    for name in names:
        if name.strip().lower().startswith("facilities"):
            return name
    raise ValueError(f"No 'Facilities *' sheet in {path.name}; found {names}")


def _find_header_row(probe: pd.DataFrame) -> int:
    """Locate the header by content, not position — it sat on row 10 in FY26."""
    for idx, row in probe.iterrows():
        cells = {str(v).strip().lower() for v in row if pd.notna(v)}
        if {"name", "address"} <= cells:
            return int(idx)
    raise ValueError("Could not find a header row containing both 'Name' and 'Address'")


def _ice_release_date(filename: str) -> str | None:
    """FY26_detentionStats07202026.xlsx -> 2026-07-20."""
    m = re.search(r"(\d{2})(\d{2})(\d{4})\.xlsx$", filename)
    if not m:
        return None
    month, day, year = m.groups()
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _file_date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
