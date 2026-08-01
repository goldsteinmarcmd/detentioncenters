"""Refresh, validate, smoke-build, and optionally publish the public map data.

This is the worker entry point. It promotes generated files only after every privacy,
source-fidelity, and frontend check passes.

    python -m pipeline.refresh
    python -m pipeline.refresh --publish
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .fetch import fetch_all
from .sources import RAW, SOURCE_MANIFEST

ROOT = Path(__file__).parent.parent
PUBLIC_DATA = ROOT / "web" / "public" / "data"
AGGREGATES = ROOT / "pipeline" / "data" / "interim" / "facility_aggregates.json"
STATE_FILE = RAW / "refresh-state.json"
PUBLIC_FILES = (
    "facilities.geojson",
    "facilities-unplaced.json",
    "build-report.json",
    "bond-cases.json",
)


def _run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _output(command: list[str]) -> str:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _source_fingerprint() -> str:
    if SOURCE_MANIFEST.exists():
        manifest = json.loads(SOURCE_MANIFEST.read_text())
        stable = {
            key: {
                "url": value.get("url"),
                "sha256": value.get("sha256"),
                "published_at": value.get("published_at"),
            }
            for key, value in sorted(manifest.get("sources", {}).items())
        }
    else:
        paths = [
            *RAW.glob("facilities-latest*.parquet"),
            *RAW.glob("FY*_detentionStats*.xlsx"),
            RAW / "detention-stays-latest.parquet",
            RAW / "gaz_place.zip",
            RAW / "gaz_counties.zip",
        ]
        stable = {
            path.name: _digest(path)
            for path in sorted(set(paths))
            if path.exists()
        }
    build_inputs = [
        *sorted((ROOT / "pipeline").glob("*.py")),
        *sorted((ROOT / "pipeline" / "data" / "manual").glob("*")),
        ROOT / "requirements.txt",
    ]
    stable["_build_inputs"] = {
        str(path.relative_to(ROOT)): _digest(path)
        for path in build_inputs
        if path.is_file()
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _last_successful_fingerprint() -> str | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text()).get("source_fingerprint")
    except (json.JSONDecodeError, OSError):
        return None


def _save_success(fingerprint: str) -> None:
    STATE_FILE.write_text(
        json.dumps(
            {
                "source_fingerprint": fingerprint,
                "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n"
    )


def _prepare_git() -> None:
    try:
        _output(["git", "rev-parse", "--is-inside-work-tree"])
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("--publish requires this project to be a Git repository") from exc

    dirty = _output(["git", "status", "--porcelain", "--untracked-files=all"])
    if dirty:
        raise RuntimeError(
            "Worker clone has uncommitted files; refusing to mix them into an automated "
            "data refresh:\n" + dirty
        )
    _run(["git", "pull", "--ff-only", "origin", "main"])


def _promote(staged_data: Path) -> None:
    PUBLIC_DATA.mkdir(parents=True, exist_ok=True)
    for name in PUBLIC_FILES:
        source = staged_data / name
        if not source.exists():
            raise RuntimeError(f"Build did not produce {source}")
        temporary = PUBLIC_DATA / f".{name}.tmp"
        shutil.copy2(source, temporary)
        os.replace(temporary, PUBLIC_DATA / name)


def _publish() -> bool:
    paths = [str((PUBLIC_DATA / name).relative_to(ROOT)) for name in PUBLIC_FILES]
    _run(["git", "add", "--", *paths])
    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", *paths], cwd=ROOT
    ).returncode != 0
    if not changed:
        print("Validated output is identical to the published data; nothing to push.")
        return False

    day = datetime.now(timezone.utc).date().isoformat()
    _run(["git", "commit", "-m", f"data: refresh sources {day}", "--", *paths])
    _run(["git", "push", "origin", "HEAD:main"])
    return True


def refresh(*, publish: bool, force: bool, skip_fetch: bool, skip_stays: bool) -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    lock_path = RAW / "refresh.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another data refresh is already running") from exc

        if publish:
            _prepare_git()

        fetch_results = [] if skip_fetch else fetch_all(skip_stays=skip_stays)
        for result in fetch_results:
            status = "updated" if result.changed else "unchanged"
            print(f"{result.key:18s} {status}")

        fingerprint = _source_fingerprint()
        outputs_missing = any(not (PUBLIC_DATA / name).exists() for name in PUBLIC_FILES)
        need_build = (
            force
            or outputs_missing
            or not AGGREGATES.exists()
            or fingerprint != _last_successful_fingerprint()
        )
        if not need_build:
            print("All upstream source fingerprints are unchanged; refresh is a no-op.")
            return 0

        stays_changed = any(
            result.key == "ddp_stays" and result.changed for result in fetch_results
        )
        if (
            force
            or not AGGREGATES.exists()
            or stays_changed
            or fingerprint != _last_successful_fingerprint()
        ):
            _run([sys.executable, "-m", "pipeline.aggregate"])

        with tempfile.TemporaryDirectory(prefix="detention-map-build-", dir=ROOT / "pipeline" / "data") as temp:
            staged_public = Path(temp) / "public"
            staged_data = staged_public / "data"
            staged_data.mkdir(parents=True)

            env = os.environ.copy()
            env["DETENTION_MAP_OUT_DIR"] = str(staged_data)
            _run([sys.executable, "-m", "pipeline.build"], env=env)
            _run([sys.executable, "-m", "pipeline.bond_campaigns"], env=env)

            validate_env = env.copy()
            validate_env["DETENTION_MAP_GEOJSON"] = str(
                staged_data / "facilities.geojson"
            )
            _run(
                [sys.executable, "-m", "pipeline.validate", "--strict"],
                env=validate_env,
            )

            if not (ROOT / "web" / "node_modules").exists():
                _run(["npm", "ci"], cwd=ROOT / "web")
            web_env = os.environ.copy()
            web_env["VITE_PUBLIC_DIR"] = str(staged_public)
            _run(["npm", "run", "build"], cwd=ROOT / "web", env=web_env)

            _promote(staged_data)

        pushed = _publish() if publish else False
        _save_success(fingerprint)
        print("Refresh completed" + (" and pushed." if pushed else "."))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Commit validated aggregate outputs and push them to origin/main.",
    )
    parser.add_argument("--force", action="store_true", help="Rebuild even if inputs match.")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Use the existing raw cache; useful for local testing.",
    )
    parser.add_argument(
        "--skip-stays",
        action="store_true",
        help="Do not check the large DDP stays file on this run.",
    )
    args = parser.parse_args()
    return refresh(
        publish=args.publish,
        force=args.force,
        skip_fetch=args.skip_fetch,
        skip_stays=args.skip_stays,
    )


if __name__ == "__main__":
    raise SystemExit(main())
