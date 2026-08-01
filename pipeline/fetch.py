"""Fetch and validate every upstream file needed by the public-data build.

Downloads are written to a temporary file, checked for the expected format and
schema, and only then moved into the raw cache. The manifest is local-only: it records
retrieval metadata without putting individual-level source data in Git.

    python -m pipeline.fetch
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .sources import (
    DDP_FACILITIES_URL,
    ICE_DETENTION_PAGE,
    RAW,
    SOURCE_MANIFEST,
    _find_header_row,
)

DDP_STAYS_URL = (
    "https://github.com/deportationdata/ice/raw/refs/heads/main/data/"
    "detention-stays-latest.parquet"
)
DDP_FACILITIES_COMMIT_API = (
    "https://api.github.com/repos/deportationdata/ice-detention-facilities/commits"
    "?path=data/facilities-latest.parquet&per_page=1"
)
DDP_STAYS_COMMIT_API = (
    "https://api.github.com/repos/deportationdata/ice/commits"
    "?path=data/detention-stays-latest.parquet&per_page=1"
)
CENSUS_PLACE_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
    "2024_Gaz_place_national.zip"
)
CENSUS_COUNTY_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
    "2024_Gaz_counties_national.zip"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

FACILITY_COLUMNS = {
    "detention_facility_code",
    "name",
    "state",
    "latitude",
    "longitude",
    "average_daily_population_last_year",
}
STAY_COLUMNS = {
    "detention_facility_code_longest",
    "stay_book_in_date_time",
    "birth_year",
    "gender",
    "citizenship_country",
}


@dataclass(frozen=True)
class FetchResult:
    key: str
    path: Path
    changed: bool
    bytes: int
    sha256: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_manifest() -> dict:
    if not SOURCE_MANIFEST.exists():
        return {"version": 1, "sources": {}}
    try:
        payload = json.loads(SOURCE_MANIFEST.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Cannot read {SOURCE_MANIFEST}: {exc}") from exc
    payload.setdefault("version", 1)
    payload.setdefault("sources", {})
    return payload


def _save_manifest(manifest: dict) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    manifest["checked_at"] = _now()
    temp = SOURCE_MANIFEST.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.replace(temp, SOURCE_MANIFEST)


def _request(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    method: str = "GET",
):
    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }
    merged.update(headers or {})
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=merged, method=method), timeout=180
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _github_version(
    api_url: str,
    previous_date: str | None,
    previous_commit: str | None,
) -> tuple[str | None, str | None]:
    try:
        with _request(api_url, {"Accept": "application/vnd.github+json"}) as response:
            payload = json.load(response)
        stamp = payload[0]["commit"]["committer"]["date"]
        return str(stamp)[:10], payload[0]["sha"]
    except (KeyError, IndexError, TypeError, ValueError, urllib.error.URLError):
        return previous_date, previous_commit


def _reuse_current_version(
    key: str,
    target: Path,
    manifest: dict,
    version_id: str | None,
) -> FetchResult | None:
    previous = manifest["sources"].get(key, {})
    if not version_id or previous.get("version_id") != version_id or not target.exists():
        return None
    digest = _sha256(target)
    if previous.get("sha256") and digest != previous["sha256"]:
        return None
    previous["checked_at"] = _now()
    manifest["sources"][key] = previous
    _save_manifest(manifest)
    return FetchResult(key, target, False, target.stat().st_size, digest)


def _download(
    key: str,
    url: str,
    target: Path,
    manifest: dict,
    validator,
    *,
    published_at: str | None = None,
    version_id: str | None = None,
) -> FetchResult:
    if urllib.parse.urlparse(url).hostname == "www.ice.gov":
        return _download_ice_with_curl(
            key,
            url,
            target,
            manifest,
            validator,
            published_at=published_at,
            version_id=version_id,
        )

    previous = manifest["sources"].get(key, {})
    headers: dict[str, str] = {}
    if previous.get("url") == url:
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]

    RAW.mkdir(parents=True, exist_ok=True)
    try:
        response = _request(url, headers)
    except urllib.error.HTTPError as exc:
        if exc.code != 304 or not target.exists():
            raise RuntimeError(f"Download failed for {url}: HTTP {exc.code}") from exc
        current_sha = previous.get("sha256") or _sha256(target)
        previous["checked_at"] = _now()
        if published_at:
            previous["published_at"] = published_at
        if version_id:
            previous["version_id"] = version_id
        manifest["sources"][key] = previous
        _save_manifest(manifest)
        return FetchResult(key, target, False, target.stat().st_size, current_sha)

    suffix = target.suffix or ".download"
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=suffix, dir=RAW)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle, response:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
        validator(temp)
        digest = _sha256(temp)
        changed = not target.exists() or digest != _sha256(target)
        if changed:
            os.replace(temp, target)
        else:
            temp.unlink()

        retrieved_at = _now()
        entry = {
            "url": url,
            "local_file": target.name,
            "bytes": target.stat().st_size,
            "sha256": digest,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "retrieved_at": retrieved_at,
            "checked_at": retrieved_at,
            "published_at": published_at or previous.get("published_at"),
            "version_id": version_id or previous.get("version_id"),
        }
        manifest["sources"][key] = entry
        _save_manifest(manifest)
        return FetchResult(key, target, changed, entry["bytes"], digest)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _download_ice_with_curl(
    key: str,
    url: str,
    target: Path,
    manifest: dict,
    validator,
    *,
    published_at: str | None,
    version_id: str | None,
) -> FetchResult:
    """Use curl for ICE, whose Akamai rules reject urllib even on public files."""
    previous = manifest["sources"].get(key, {})
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=target.suffix, dir=RAW
    )
    os.close(fd)
    temp = Path(temp_name)
    try:
        subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--location",
                "--max-time",
                "180",
                "--user-agent",
                USER_AGENT,
                "--output",
                str(temp),
                url,
            ],
            check=True,
        )
        validator(temp)
        digest = _sha256(temp)
        changed = not target.exists() or digest != _sha256(target)
        if changed:
            os.replace(temp, target)
        else:
            temp.unlink()

        retrieved_at = _now()
        entry = {
            "url": url,
            "local_file": target.name,
            "bytes": target.stat().st_size,
            "sha256": digest,
            "etag": None,
            "last_modified": None,
            "retrieved_at": retrieved_at,
            "checked_at": retrieved_at,
            "published_at": published_at or previous.get("published_at"),
            "version_id": version_id or previous.get("version_id"),
        }
        manifest["sources"][key] = entry
        _save_manifest(manifest)
        return FetchResult(key, target, changed, entry["bytes"], digest)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _validate_facilities(path: Path) -> None:
    parquet = pq.ParquetFile(path)
    columns = set(parquet.schema_arrow.names)
    missing = FACILITY_COLUMNS - columns
    if missing or parquet.metadata.num_rows < 700:
        raise ValueError(
            f"Invalid facilities parquet: rows={parquet.metadata.num_rows}, "
            f"missing columns={sorted(missing)}"
        )


def _validate_stays(path: Path) -> None:
    parquet = pq.ParquetFile(path)
    columns = set(parquet.schema_arrow.names)
    missing = STAY_COLUMNS - columns
    if missing or parquet.metadata.num_rows < 500_000:
        raise ValueError(
            f"Invalid stays parquet: rows={parquet.metadata.num_rows}, "
            f"missing columns={sorted(missing)}"
        )


def _validate_ice(path: Path) -> None:
    workbook = pd.ExcelFile(path)
    sheet = next(
        (name for name in workbook.sheet_names if name.lower().startswith("facilities")),
        None,
    )
    if not sheet:
        raise ValueError(f"No facilities sheet in {path.name}")
    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=25)
    header = _find_header_row(probe)
    data = pd.read_excel(path, sheet_name=sheet, header=header, usecols=["Name"])
    if data["Name"].notna().sum() < 100:
        raise ValueError(f"ICE spreadsheet has too few facility rows: {len(data)}")


def _validate_zip(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise ValueError(f"Not a valid ZIP archive: {path}")
    with zipfile.ZipFile(path) as archive:
        if not any(name.lower().endswith(".txt") for name in archive.namelist()):
            raise ValueError(f"Gazetteer ZIP contains no text file: {path}")


def _ice_url_from_dated_documents() -> str:
    """Find the newest official spreadsheet when ICE blocks its landing page.

    The document server returns a clean 404 for dates with no release. Probe backward
    from today only as far as the newest cached release (or 45 days on a cold worker),
    then validate the first real workbook through the normal download path.
    """
    cached_dates = []
    for path in RAW.glob("FY*_detentionStats*.xlsx"):
        match = re.search(r"detentionStats(\d{2})(\d{2})(\d{4})\.xlsx", path.name, re.I)
        if match:
            month, day, year = map(int, match.groups())
            try:
                cached_dates.append(date(year, month, day))
            except ValueError:
                pass

    today = datetime.now(timezone.utc).date()
    earliest = max(cached_dates) if cached_dates else today - timedelta(days=45)
    cursor = today
    while cursor >= earliest:
        fiscal_year = cursor.year + 1 if cursor.month >= 10 else cursor.year
        filename = (
            f"FY{fiscal_year % 100:02d}_detentionStats"
            f"{cursor.month:02d}{cursor.day:02d}{cursor.year:04d}.xlsx"
        )
        url = f"https://www.ice.gov/doclib/detention/{filename}"
        probe = subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--location",
                "--head",
                "--max-time",
                "30",
                "--user-agent",
                USER_AGENT,
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                url,
            ],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        if probe.stdout.strip() == "200":
            return url
        if probe.stdout.strip() != "404":
            raise RuntimeError(
                f"ICE document probe failed for {url}: HTTP {probe.stdout.strip()}"
            )
        cursor -= timedelta(days=1)
    raise RuntimeError(
        "Could not locate an ICE detention spreadsheet on the landing page or the "
        "official dated document endpoint"
    )


def _ice_url() -> str:
    try:
        with _request(ICE_DETENTION_PAGE, {"Accept": "text/html"}) as response:
            page = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError:
        curl_page = subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--location",
                "--max-time",
                "60",
                "--user-agent",
                USER_AGENT,
                ICE_DETENTION_PAGE,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if curl_page.returncode:
            print("ICE landing page unavailable; probing its official dated documents.")
            return _ice_url_from_dated_documents()
        page = curl_page.stdout

    hrefs = re.findall(
        r'''href=["']([^"']*FY\d{2}_detentionStats\d{8}\.xlsx(?:\?[^"']*)?)["']''',
        page,
        flags=re.IGNORECASE,
    )
    if not hrefs:
        print("ICE landing page has no spreadsheet link; probing official documents.")
        return _ice_url_from_dated_documents()

    urls = [urllib.parse.urljoin(ICE_DETENTION_PAGE, html.unescape(href)) for href in hrefs]

    def release_date(url: str) -> str:
        match = re.search(r"detentionStats(\d{2})(\d{2})(\d{4})\.xlsx", url, re.I)
        if not match:
            return "0000-00-00"
        month, day, year = match.groups()
        return f"{year}-{month}-{day}"

    return max(urls, key=release_date)


def fetch_all(*, skip_stays: bool = False) -> list[FetchResult]:
    manifest = _load_manifest()
    sources = manifest["sources"]

    facilities_date, facilities_version = _github_version(
        DDP_FACILITIES_COMMIT_API,
        sources.get("ddp_facilities", {}).get("published_at"),
        sources.get("ddp_facilities", {}).get("version_id"),
    )
    facilities_target = RAW / "facilities-latest.parquet"
    facilities_cached = _reuse_current_version(
        "ddp_facilities", facilities_target, manifest, facilities_version
    )
    results = [
        facilities_cached
        or _download(
            "ddp_facilities",
            DDP_FACILITIES_URL,
            facilities_target,
            manifest,
            _validate_facilities,
            published_at=facilities_date,
            version_id=facilities_version,
        )
    ]

    if not skip_stays:
        stays_date, stays_version = _github_version(
            DDP_STAYS_COMMIT_API,
            sources.get("ddp_stays", {}).get("published_at"),
            sources.get("ddp_stays", {}).get("version_id"),
        )
        stays_target = RAW / "detention-stays-latest.parquet"
        stays_cached = _reuse_current_version(
            "ddp_stays", stays_target, manifest, stays_version
        )
        results.append(
            stays_cached
            or _download(
                "ddp_stays",
                DDP_STAYS_URL,
                stays_target,
                manifest,
                _validate_stays,
                published_at=stays_date,
                version_id=stays_version,
            )
        )

    ice_url = _ice_url()
    ice_name = Path(urllib.parse.urlparse(ice_url).path).name
    results.append(
        _download(
            "ice_facilities",
            ice_url,
            RAW / ice_name,
            manifest,
            _validate_ice,
            published_at=re.sub(
                r".*detentionStats(\d{2})(\d{2})(\d{4})\.xlsx",
                r"\3-\1-\2",
                ice_name,
                flags=re.IGNORECASE,
            ),
        )
    )
    results.extend(
        [
            _download(
                "census_places",
                CENSUS_PLACE_URL,
                RAW / "gaz_place.zip",
                manifest,
                _validate_zip,
                published_at="2024",
            ),
            _download(
                "census_counties",
                CENSUS_COUNTY_URL,
                RAW / "gaz_counties.zip",
                manifest,
                _validate_zip,
                published_at="2024",
            ),
        ]
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-stays",
        action="store_true",
        help="Do not check the large detention-stays source on this run.",
    )
    args = parser.parse_args()

    for result in fetch_all(skip_stays=args.skip_stays):
        state = "updated" if result.changed else "unchanged"
        print(f"{result.key:18s} {state:9s} {result.bytes / 1024 / 1024:7.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
