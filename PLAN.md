# Implementation plan

Build order is deliberate: the data pipeline is the hard part and the risky part, so it
goes first and gets validated before any map code exists. The map is comparatively
mechanical once the GeoJSON is correct.

Background, sources, and the integrity rules that constrain all of this are in
[CLAUDE.md](CLAUDE.md). Deferred work is in [backlog.md](backlog.md).

## Status — 2026-08-01

| Phase | State |
|---|---|
| 0 — Scaffold | **done** |
| 1 — Sources | **done for 3 of 4** — DDP facilities, ICE xlsx, stays file |
| 2 — Crosswalk | **done, 208/208 (100%)**, both assertions passing |
| 3 — Aggregation | **done** — 400,223 stays → 481 facilities, suppression enforced |
| 4 — Operator enrichment | not started — the last missing field |
| 5 — Build output | **done** — all 844 mapped, 193 KB gzipped |
| 6 — Map | **done** — clusters, click tooltip → panel, deep links, verified |
| 6b — Directions | **done** — both directions, geolocation or typed origin, 3 providers |
| 7 — Search, filters, honesty UI | mostly done; About page outstanding |
| 8 — Ship + keep fresh | **done locally** — publish and worker installation pending GitHub authorization |

```bash
python -m pipeline.aggregate && python -m pipeline.build
cd web && npm run dev
```

Both run green. Operator company is now the only requested field still missing that
could actually be sourced — guard counts genuinely do not exist.

Phase 2 landed better than planned (100% vs. the projected 98.6%) and fixed a
misattribution bug found along the way; see CLAUDE.md 'Distinctive tokens'.

---

## Phase 0 — Scaffold

Repo skeleton, Python venv with `pandas`/`pyarrow`, Vite + TypeScript + MapLibre in
`web/`, `.gitignore` for `pipeline/data/raw/`, `git init`.

**Done when:** `python -m pipeline.build --help` runs and `npm run dev` serves a blank map.

---

## Phase 1 — Source acquisition (`pipeline/sources.py`)

Fetch and cache the four sources, each with its retrieved-at timestamp recorded.

1. **DDP facilities** — download `facilities-latest.parquet` (844 rows). Straight LFS
   pull.
2. **ICE spreadsheet** — scrape `ice.gov/detain/detention-management` for the newest
   `FY##_detentionStats*.xlsx` href, download it, record the filename (it encodes the
   release date). Locate the header row by scanning for the row containing both `Name`
   and `Address` rather than hardcoding row 10.
3. **DDP detention stays** — 110 MB. Cache aggressively; this is the only heavy
   download. Provide a `--skip-stays` flag so the rest of the pipeline can iterate
   without re-pulling it.
4. **DDP historical detention-management** — `facilities-detention-management.parquet`
   for the time series.

All requests need a browser User-Agent. Cache to `pipeline/data/raw/` keyed by source +
date; never re-download unchanged files.

**Done when:** all four cached locally, with a `sources.json` manifest recording URL,
retrieved-at, and byte size for each.

**Risk:** ICE changes its page structure or pulls the file. Mitigation: the pipeline
falls back to the newest cached copy and marks the build stale rather than failing.

---

## Phase 2 — Crosswalk (`pipeline/crosswalk.py`) — **DONE**

Joins the 208 ICE rows onto the 844 DDP facilities. Final: **208/208 (100%)** —
168 zip+distinctive-token, 29 by best token overlap, 6 exact-name, 2 distinctive-token
containment, 1 fuzzy, 2 hand overrides.

Two things came out of building it that weren't in the original design:

- **Any-shared-token matching is unsafe.** It mapped two different ICE facilities onto
  one DDP code, where one's inspection rating and population overwrote the other's.
  Fixed by requiring a *distinctive* token (document frequency ≤ 5%, derived from the
  data). This also lifted the rate and auto-resolved two of the three expected manual
  residuals.
- **A collision assertion is as necessary as the rate assertion.** A 100% match rate
  with a collision in it is worse than a 98% rate without one, because everything looks
  populated. Both now fail the build.

`data/manual/facility_code_overrides.csv` holds the two genuinely ambiguous rows, each
with its evidence and decision date.

---

## Phase 3 — Demographic aggregation (`pipeline/aggregate.py`)

The interesting phase. Roll 1.09M stay records up to per-facility histograms.

- Group by `detention_facility_code_longest` (the facility where the person spent the
  most time — the most defensible attribution when a stay spans facilities). Compute
  `_first` and `_last` variants too; expose which one the UI is showing.
- **Age:** `stay_book_in_date_time.year - birth_year`, bucketed into
  0–17 / 18–24 / 25–34 / 35–44 / 45–54 / 55–64 / 65+. Bands only — `birth_year` has no
  month/day, so exact ages would be false precision.
- **Also roll up:** gender, race/ethnicity, top citizenship countries, length-of-stay
  distribution, release reasons, bond amounts (median + posted rate), criminality at
  book-in.
- **Windowing:** compute over a defined trailing window (12 months) and stamp the
  window on the output. Restrict to stays overlapping the window, not all history.
- **Suppression:** any cell `n < 5` emits `{"suppressed": true}` rather than a count.
  Do not emit age × nationality cross-tabs.
- Read the parquet in row-group batches — do not load 110 MB into a DataFrame at once.

**Done when:** per-facility histograms exist, suppression verified by asserting no
emitted cell has `0 < n < 5`, and totals reconcile against DDP's published ADP within a
stated tolerance.

**Risk:** attribution ambiguity for multi-facility stays. Mitigation: publish the
`_longest` basis, document it in the UI, and keep the alternatives in the output.

---

## Phase 4 — Operator enrichment (`pipeline/enrich.py`)

The weakest-evidence field, so it gets the most explicit provenance.

- Derive what's mechanical: `Type Detailed` → contract structure
  (private CDF/DIGSA vs. county IGSA vs. federal BOP/SPC vs. USMS).
- Build `data/manual/operator_crosswalk.csv` — one row per facility:
  `detention_facility_code, operator_name, operator_type, source_url, as_of, confidence`.
  Seed from NIJC's Transparency Project, cross-check against USAspending.gov recipients.
- Start with the ~50 highest-population facilities. Coverage will be partial and that's
  fine — uncovered facilities show contract type only, and say so.
- **Never infer a company from a facility name.** "CCA, Florence Correctional Center"
  implying CoreCivic is a reasonable guess and still needs a citation before it ships.

**Done when:** every facility has a contract type; top-50 by population have a cited
operator; the rest render as "Operator not independently verified."

---

## Phase 5 — Build output (`pipeline/build.py`)

Orchestrate 1→4 into `web/public/data/facilities.geojson`.

- One `Feature` per geocoded facility (709 of 844). The 135 without coordinates go to a
  separate `facilities-ungeocoded.json` so they're listed and searchable but not
  silently dropped.
- Feature properties carry the display fields plus a `sources` block mapping each field
  group to `{source, url, as_of}`.
- Derive the directions URL from lat/lon at build time — a platform-neutral geo link,
  with Google/Apple Maps fallbacks chosen client-side.
- Expect ~2 MB raw, ~400 KB gzipped. Single file keeps client-side search and filtering
  instant. If it passes ~5 MB, split to a light map file plus per-facility detail JSON.
- Validate before writing: schema check, no null geometry, no `0 < n < 5`, match rate
  ≥95%, every non-null value has a source entry.

**Done when:** `python -m pipeline.build` produces validated GeoJSON from a cold cache
in one command.

---

## Phase 6 — Map (`web/`)

- MapLibre GL JS, GeoJSON source with `cluster: true`. Cluster color/radius by summed
  ADP so the visual weight reflects people held, not facility count.
- Uncolored-by-default markers; a legend-driven color mode (contract type, inspection
  rating, population band).

**Tooltip vs. panel.** You asked for tooltips carrying name, address, population, age
breakdown, operator, directions. That is far too much for a hover tooltip — an age
histogram in a hover state is unreadable and unreachable on touch. Split it:

- **Hover:** name, city/state, current ADP. Three lines.
- **Click → side panel:** everything else — full address with directions button,
  population breakdowns (crim/non-crim, M/F, classification levels), age histogram,
  demographics, operator + contract type, inspection history, ALOS, guaranteed minimum,
  federal court district, and a provenance footer with per-field sources and dates.

The panel is deep-linkable (`?facility=CODE`) so a specific facility can be shared —
which matters directly for the Reddit port.

**Done when:** all 709 facilities render, cluster, and open a populated panel.

---

## Phase 7 — Search, filter, honesty UI

- Text search across name / city / county / state.
- Filters: state, contract type, operator, inspection rating, population range.
- "Locate me" → nearest facilities.
- **Missing-data treatment is a first-class feature, not an afterthought.** Guard counts
  render as "Not publicly reported — ICE does not release facility staffing levels,"
  with the guaranteed-minimum and contract-value proxies beside it. Same pattern for any
  unverified operator.
- An About page covering sources, methodology, the age-band and suppression rules, the
  stock-vs-flow distinction, and known gaps.

**Done when:** a visitor can tell, for any number on screen, where it came from and when.

---

## Phase 8 — Ship + keep fresh

- Deploy the static build to GitHub Pages with the official Pages Actions.
- Run `pipeline.refresh --publish` daily from a dedicated worker clone via a macOS
  LaunchAgent. It fetches official sources, skips unchanged fingerprints, stages the
  aggregate outputs, runs strict source-fidelity and privacy validation, and promotes
  and pushes only after the whole build passes.
- Track source URLs, retrieval times, checksums, publication dates, and upstream
  versions in the local source manifest. Display source names and as-of dates at the
  bottom of the relevant population and demographics sections.
- Keep the individual-level stays file ignored and local. Only the three validated
  facility-level aggregate files may be committed by the publisher.

**Done when:** data refreshes daily with no manual step, a bad upstream release blocks
publication, and a successful aggregate commit automatically deploys to GitHub Pages.

---

## Sequencing notes

Phases 1–3 are the real work; 5–6 are mostly mechanical once the GeoJSON is right.
Phase 4 is open-ended manual research — cap it at the top-50 facilities for v1 and let
coverage grow over time rather than blocking the launch on it.

One thing left to decide before Phase 6:

- Whether v1 shows the trailing-12-month age window only, or a time slider (the
  historical detention-management parquet supports the slider, but it's scope).

The ungeocoded question is now answered by the data. The 135 are almost entirely ICE
hold rooms and field-office holding areas totalling **781 ADP, 1.2% of the national
62,498**. They ship in `facilities-ungeocoded.json` and belong in the list and search
from day one — they are real facilities holding real people — but they do not need map
treatment in v1, because there is no defensible coordinate to place them at.

## What v1 will not have

Stated up front so it isn't discovered late: guard counts (don't exist), real-time
population (not published — everything is averaged over a period), complete operator
coverage (manual research, grows over time), ORR/unaccompanied-minor facilities (out of
scope), and per-facility incident/death records (a separate ICE data release, in
[backlog.md](backlog.md)).
