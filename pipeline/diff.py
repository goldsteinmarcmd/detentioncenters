"""What changed between two published releases.

The map answers "what is true now". This answers "what moved" — the facilities that
appeared, the ones that vanished, the populations that swung, and the inspection
results that changed since the last release.

The archive it reads is Git. `pipeline.refresh --publish` commits
`web/public/data/facilities.geojson` on every release, so each of those commits is a
dated snapshot and no separate archive has to exist first.

    python -m pipeline.diff                         # last published release vs. now
    python -m pipeline.diff --before <rev-or-path> --after <path>
    python -m pipeline.diff --json out.json

The reporting rule is the project's rule: a number that stopped being published is
"no longer reported", never a fall to zero, and a number that started being published
is "newly reported", never a rise from zero. Those two cases are counted separately
from real movement, because reading either as a swing would invent a change ICE never
published.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
PUBLISHED = ROOT / "web" / "public" / "data" / "facilities.geojson"

#: A facility has to move by both of these to count as a population swing. The absolute
#: floor keeps small facilities from filling the report with 1-to-3 "200% rises"; the
#: relative floor keeps large ones from reporting ordinary noise.
SWING_MIN_PEOPLE = 25.0
SWING_MIN_SHARE = 0.10

#: Fields whose change is worth reporting on its own, with the label used in the report.
ATTRIBUTE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("inspection", "last_rating", "inspection rating"),
    ("inspection", "last_end_date", "inspection date"),
    ("operator", "contract_type", "contract type"),
    ("operator", "company", "operator"),
)


@dataclass
class FacilityChange:
    code: str
    name: str
    detail: str


@dataclass
class ReleaseDiff:
    before_as_of: str | None
    after_as_of: str | None
    before_count: int
    after_count: int
    added: list[FacilityChange] = field(default_factory=list)
    removed: list[FacilityChange] = field(default_factory=list)
    swings: list[FacilityChange] = field(default_factory=list)
    reporting_started: list[FacilityChange] = field(default_factory=list)
    reporting_stopped: list[FacilityChange] = field(default_factory=list)
    attributes: list[FacilityChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "before": {"as_of": self.before_as_of, "facilities": self.before_count},
            "after": {"as_of": self.after_as_of, "facilities": self.after_count},
            "changes": {
                name: [vars(c) for c in getattr(self, name)]
                for name in (
                    "added",
                    "removed",
                    "swings",
                    "reporting_started",
                    "reporting_stopped",
                    "attributes",
                )
            },
        }


def _load(source: str) -> dict[str, Any]:
    """Read a release from a file path, or from a Git revision of the published file."""
    path = Path(source)
    if path.exists():
        return json.loads(path.read_text())

    spec = source if ":" in source else f"{source}:web/public/data/facilities.geojson"
    try:
        blob = subprocess.run(
            ["git", "show", spec],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Could not read {source!r} as a file or as a Git revision:\n{exc.stderr}"
        ) from exc
    return json.loads(blob)


def previous_published_revision() -> str | None:
    """The commit before the one that last touched the published file."""
    revisions = subprocess.run(
        ["git", "log", "--format=%H", "--", "web/public/data/facilities.geojson"],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.split()
    return revisions[0] if revisions else None


def _by_code(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        feature["properties"]["code"]: feature["properties"]
        for feature in release.get("features", [])
        if feature.get("properties", {}).get("code")
    }


def _as_of(release: dict[str, Any]) -> str | None:
    sources = release.get("metadata", {}).get("sources") or []
    return sources[0].get("as_of") if sources else None


def _label(props: dict[str, Any]) -> str:
    name = props.get("name") or props.get("code") or "Unnamed facility"
    place = ", ".join(p for p in (props.get("city"), props.get("state")) if p)
    return f"{name} ({place})" if place else str(name)


def _people(value: float) -> str:
    """Never round a real non-zero average down to a misleading zero.

    The same rule the frontend applies in `format.ts`: a facility averaging half a
    person held is not a facility holding nobody, and a change report is exactly where
    that distinction gets lost.
    """
    magnitude = abs(value)
    if magnitude == 0:
        return "0"
    if magnitude < 0.1:
        return f"{value:,.3f}"
    if magnitude < 10:
        return f"{value:,.1f}"
    return f"{value:,.0f}"


def _population(props: dict[str, Any]) -> float | None:
    value = props.get("adp")
    return float(value) if isinstance(value, (int, float)) else None


def compare(before: dict[str, Any], after: dict[str, Any]) -> ReleaseDiff:
    old = _by_code(before)
    new = _by_code(after)
    result = ReleaseDiff(
        before_as_of=_as_of(before),
        after_as_of=_as_of(after),
        before_count=len(old),
        after_count=len(new),
    )

    for code in sorted(new.keys() - old.keys()):
        props = new[code]
        population = _population(props)
        detail = (
            f"new facility, {_people(population)} average daily population"
            if population is not None
            else "new facility, population not reported"
        )
        result.added.append(FacilityChange(code, _label(props), detail))

    for code in sorted(old.keys() - new.keys()):
        props = old[code]
        population = _population(props)
        detail = (
            f"no longer in the release, last reported {_people(population)} held"
            if population is not None
            else "no longer in the release, population was not reported"
        )
        result.removed.append(FacilityChange(code, _label(props), detail))

    for code in sorted(old.keys() & new.keys()):
        was, now = old[code], new[code]
        label = _label(now)
        old_population, new_population = _population(was), _population(now)

        if old_population is None and new_population is not None:
            result.reporting_started.append(
                FacilityChange(
                    code, label, f"population now reported as {_people(new_population)}"
                )
            )
        elif old_population is not None and new_population is None:
            result.reporting_stopped.append(
                FacilityChange(
                    code,
                    label,
                    f"population no longer reported, was {_people(old_population)}",
                )
            )
        elif old_population is not None and new_population is not None:
            change = new_population - old_population
            # A facility that was published as zero has no base to take a share of, so
            # the absolute floor is the only test that can apply to it.
            share = abs(change) / old_population if old_population else None
            big_enough = abs(change) >= SWING_MIN_PEOPLE and (
                share is None or share >= SWING_MIN_SHARE
            )
            if big_enough:
                direction = "up" if change > 0 else "down"
                percent = f" ({share:.0%})" if share is not None else ""
                result.swings.append(
                    FacilityChange(
                        code,
                        label,
                        f"{direction} {_people(abs(change))}{percent}, "
                        f"{_people(old_population)} to {_people(new_population)}",
                    )
                )

        for section, key, description in ATTRIBUTE_FIELDS:
            old_value = (was.get(section) or {}).get(key)
            new_value = (now.get(section) or {}).get(key)
            if old_value == new_value:
                continue
            result.attributes.append(
                FacilityChange(
                    code,
                    label,
                    f"{description}: {old_value or 'not published'} → "
                    f"{new_value or 'not published'}",
                )
            )

    return result


def render(diff: ReleaseDiff) -> str:
    lines = [
        f"Facility data {diff.before_as_of or 'unknown'} → {diff.after_as_of or 'unknown'}",
        f"{diff.before_count:,} facilities → {diff.after_count:,}",
    ]
    sections = (
        ("New facilities", diff.added),
        ("Facilities no longer in the release", diff.removed),
        ("Population swings", diff.swings),
        ("Population reported for the first time", diff.reporting_started),
        ("Population no longer reported", diff.reporting_stopped),
        ("Attribute changes", diff.attributes),
    )
    for title, changes in sections:
        lines.append("")
        lines.append(f"{title}: {len(changes)}")
        for change in changes:
            lines.append(f"  {change.name} [{change.code}] — {change.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--before",
        help="Earlier release: a file path, a Git revision, or rev:path. "
        "Defaults to the last commit that published the dataset.",
    )
    parser.add_argument(
        "--after",
        default=str(PUBLISHED),
        help="Later release; defaults to the currently published file.",
    )
    parser.add_argument("--json", dest="json_out", help="Also write the diff as JSON here.")
    args = parser.parse_args(argv)

    before_source = args.before or previous_published_revision()
    if not before_source:
        print(
            "No earlier release found: the dataset has never been committed, so there "
            "is nothing to compare against yet.",
            file=sys.stderr,
        )
        return 1

    diff = compare(_load(before_source), _load(args.after))
    print(render(diff))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(diff.to_dict(), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
