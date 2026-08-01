# CLAUDE.md

Project context for Claude Code. Read this before touching the pipeline or the map.

## What this is

An interactive map of every US immigration detention facility, where clicking a
facility shows who is held there, how many, who runs it, and how the facility has
performed on federal inspections — each fact carrying a source and an as-of date.

Public-accountability project built entirely on public and FOIA-released federal
data. Facility-level only. Eventually also a Reddit app (see [backlog.md](backlog.md)).

## Scope

"Detention centers" here means **civil immigration detention** — facilities where ICE
holds people. That includes dedicated ICE facilities, county jails under
intergovernmental agreements, private contractor-run facilities, Bureau of Prisons and
US Marshals facilities holding ICE detainees, CBP short-term holding, ICE hold rooms
and staging areas, and hospitals where ICE has held people.

It does **not** include the general US jail/prison system, criminal-immigration-only
facilities, or ORR shelters for unaccompanied minors (different agency, different data,
deliberately out of scope).

## Data sources (all verified live 2026-07-28)

### 1. Deportation Data Project — facility registry + geocodes (primary)

Berkeley/UCLA academic project. CC-0. Published as Git LFS parquet on GitHub.

- `https://github.com/deportationdata/ice-detention-facilities/raw/refs/heads/main/data/facilities-latest.parquet`
- **844 facilities, 709 with hand-verified lat/lon.** 26 columns.
- Key columns: `detention_facility_code` (the universal join key), `name`, `address`,
  `city`, `county`, `county_fips_code`, `state`, `zip`, `address_full`, `latitude`,
  `longitude`, `field_office`, `federal_court_district_of_confinement`,
  `federal_court_circuit_of_confinement`, `average_daily_population_last_year`,
  `average_midnight_population_last_year`, `max_daily_population_last_year`,
  `max_midnight_population_last_year`, `days_with_detentions_daily_last_year`.
- Codebook: `https://deportationdata.org/docs/ice/codebook-facilities.html`
- Note: `facilities-latest.xlsx` is linked from the site but **404s**. Use the parquet.
- The site 403s `WebFetch`; fetch with `curl` + a browser User-Agent.

### 2. ICE detention statistics spreadsheet — facility attributes (primary)

ICE's own biweekly release. Authoritative for contract type and inspections.

- `https://www.ice.gov/detain/detention-management` → current file
  `https://www.ice.gov/doclib/detention/FY26_detentionStats07202026.xlsx`
- **The filename changes every release.** Scrape the page for the newest
  `FY##_detentionStats*.xlsx` link; never hardcode.
- Sheet `Facilities FY26`: **header on row 10, data from row 11**, 208 facilities.
- Columns: `Name`, `Address`, `City`, `State`, `Zip`, `AOR`, `Type Detailed`,
  `Male/Female`, `FY26 ALOS`, `Level A`–`Level D`, `Male Crim`, `Male Non-Crim`,
  `Female Crim`, `Female Non-Crim`, `ICE Threat Level 1`–`3`, `No ICE Threat Level`,
  `Mandatory`, `Guaranteed Minimum`, `Last Inspection Type`, `Last Inspection End Date`,
  `Last Inspection Standard`, `Last Final Rating`.
- **Only lists facilities with population > 0**, so 208 ≪ 844. This is why DDP is the
  spine and ICE is the enrichment, not the reverse.
- `Type Detailed` is a *contract* code, not a company: `USMS IGA` (104), `IGSA` (38),
  `DIGSA` (26), `CDF` (20), `BOP` (7), `SPC` (5), `STAGING` (3), `USMS CDF` (3),
  `STATE` (1), `FAMILY` (1).
- DDP also mirrors this parsed across historical releases at
  `ice-detention-facilities/.../facilities-detention-management.parquet` (664 rows,
  55 columns, multiple fiscal years) — use for time series.

### 3. DDP individual-level detention stays — demographics (primary)

- `https://github.com/deportationdata/ice/raw/refs/heads/main/data/detention-stays-latest.parquet`
- **1,087,417 rows, 110 MB, 70 columns.** One row per detention stay.
- Carries `birth_year`, `gender`, `race`, `ethnicity`, `citizenship_country`,
  `birth_country`, `detention_facility_code_first` / `_longest` / `_last`,
  book-in/book-out timestamps, bond amounts, `stay_release_reason`,
  `detainee_classification`, `book_in_criminality`.
- **This is what makes the age breakdown possible** — aggregate in the pipeline, never
  ship individual rows.
- To inspect the schema without downloading 110 MB, read the parquet footer over HTTP
  range requests (see `pipeline/sources.py`).

### 4. US Census Gazetteer — approximate placement (derived locations only)

`https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip`
(and `..._counties_national.zip`). 32,333 places and 3,222 counties with authoritative
interior-point coordinates. Used **only** to place the 135 facilities that have no
published address — never to adjust a real geocode. See 'Location precision'.

Census names are not the names people use, so the loader indexes aliases: it strips
`(balance)` (`Indianapolis city (balance)`), takes the first hyphen segment
(`Nashville-Davidson metropolitan government` → Nashville), handles a trailing `city`
(`Boise City` → Boise) and a leading one (`Town of Pecos City` → Pecos). An alias never
displaces a real place name.

### 5. NIJC Transparency Project — operator company (enrichment)

`https://immigrantjustice.org/exposing-ice-detention-contracts-and-inspections-transparency-project/`
Owner/operator relationships and contracting documents. No clean API — this feeds a
hand-curated, per-row-cited crosswalk. Cross-check against USAspending.gov recipient
names.

## What the data actually supports

You asked for a specific tooltip field list. Verified reality:

| Requested field | Status | Source |
|---|---|---|
| Name | Yes | DDP + ICE |
| Address | Yes, geocoded | DDP |
| Number of people | Yes — ADP, max, and crim/non-crim × M/F splits | DDP + ICE |
| Directions | Yes — derived from lat/lon | computed |
| **Age breakdown** | **Yes — derived** from `birth_year`, as bands | DDP stays |
| **Which company runs it** | **Partial** — contract *type* published; company needs a curated crosswalk | ICE + NIJC |
| **How many guards** | **No — not published anywhere** | — |

**Guard counts do not exist in public data.** Not in ICE's releases, not in FOIA sets,
not in DDP. The field stays in the schema as `null`, rendered as "Not publicly
reported," with the closest honest proxies shown instead: `Guaranteed Minimum` (the
contractual bed floor ICE pays for regardless of occupancy), contract value from
USAspending, and any staffing findings in ODO/OIG inspection reports. Do not estimate
it from a staffing ratio and present it as data.

Age is a **flow** measure (people booked in during a window), not a snapshot of who is
there today. `birth_year` has no month/day, so age is ±1 year — publish bands
(0–17, 18–24, 25–34, 35–44, 45–54, 55–64, 65+), never point ages.

Bonus fields worth surfacing that weren't asked for: last inspection rating and date,
average length of stay, guaranteed minimum, mandatory-detention count, federal court
district (habeas venue), ICE field office, bond amounts, release reasons.

## Joining ICE ↔ DDP

The ICE spreadsheet has **no facility code**, so it must be matched by name/place.
Naive normalized-name + state matching gets **55.8%**. The strategy implemented in
`pipeline/crosswalk.py` gets **100% (208/208)**, verified against the FY26 07/20/2026
release:

1. Hand-maintained overrides first — a human decision is never overruled by a heuristic.
2. Filter DDP candidates to same `state`.
3. 5-digit zip **and** ≥1 shared **distinctive** name token → 184 (168 unique,
   16 resolved by a strict winner on token overlap).
4. Exact normalized-name equality → 13.
5. Distinctive-token containment, requiring a unique candidate → 2.
6. `difflib` ratio ≥ 0.84 **and** ≥1 shared distinctive token → 2.
7. Remainder to the override file → 7.

Stages 3–6 are ordered by strength of evidence, not by cost, and a stage that cannot
decide falls through instead of refusing. An exact name match outranks sharing a zip:
several facilities were left unmatched when an ambiguous zip short-circuited the chain
before the name stage ever ran.

Name normalization: uppercase, strip non-alphanumerics, expand `CTR`→`CENTER`,
`CO`→`COUNTY`, `DET`→`DETENTION`, `FAC`→`FACILITY`, `CORR`→`CORRECTIONAL`,
`INST`→`INSTITUTION`, `FED`→`FEDERAL`, `PROC`→`PROCESSING`, drop `THE|OF|AND|DEPT`.

### Distinctive tokens — why "a shared token" is not enough

Requiring merely *a* shared token silently mis-assigns facilities. ICE lists
`WEBB COUNTY DETENTION CENTER (CCA)` under zip 78041 — which is the zip of a *different*
facility, Corecivic Laredo Processing Center (`LRDICDF`). The two share the token
`CENTER`, so both ICE rows resolved to the same DDP code and one facility's inspection
rating and population silently overwrote the other's.

A token appearing in more than **5%** of facility names carries no identifying
information and is excluded from the match test. The set is derived from the data rather
than hardcoded; at 844 facilities it selects `CENTER, CORRECTIONAL, COUNTY, DETENTION,
FACILITY, HOLD, HOSPITAL, JAIL, MEDICAL, ROOM`.

This also auto-resolved two of the three previously manual residuals
(`BURLEIGH COUNTY (ND)` → `BURLEND`, `SWEETWATER COUNTY JAIL (WY)` → `SWEETWY`), each
the sole candidate in its state.

### Two failure modes a high match rate hides

Both were found by `pipeline/validate.py`, not by the build, and both looked like clean
matches:

- **String similarity differs exactly where it matters.** `LUBBOCK COUNTY DETENTION
  CENTER` scored 0.87 against `Brooks County Detention Center` — the names differ only
  in the county, which is the one token identifying the place. Fuzzy matching now
  requires a shared distinctive token as well, so a high score can *corroborate* a
  match but never *create* one.
- **Arbitrary tie-breaking is silent.** Three Karnes County facilities share zip 78118
  and the single token `Karnes`; what separates them is "Immigration Processing" vs
  "Correctional" vs "Residential". Ranking by token overlap tied all three, and `max()`
  took the first — attributing ICE's 1,076 people to a facility holding 0.24. Ties now
  require a strict winner or fall through. String similarity is deliberately *not* used
  to break these ties: it ranked "Correctional" above "Residential", also wrongly.

### Remaining manual overrides

Seven rows are genuinely ambiguous by name and resolved by hand, each with its evidence
recorded per row in `pipeline/data/manual/facility_code_overrides.csv`. The interesting
ones:

- `KARNES COUNTY IMMIGRATION PROCESSING CENTER (TX)` → `KRNRCTX`. Resolved on
  population: DDP reports 1082.3 against ICE's 1075.6; the alternatives hold 0.24 and
  2.14. Karnes was converted from a family residential centre, hence the name drift.
- `LUBBOCK COUNTY DETENTION CENTER (TX)` → `LUBBOTX` (`Lubbock County Jail`). DDP has
  no facility under ICE's name.
- `CCA, FLORENCE CORRECTIONAL CENTER (AZ)` → `CCAFLAZ`, by elimination once the SPC and
  Staging Facility rows claim `FLO` and `FSF`.
- `WEBB COUNTY DETENTION CENTER (CCA) (TX)` → `WEBDCTX`, distinguished from `WEBCOTX`
  (Webb County *Jail*, ADP 0.003) by type and population.

### Build-time assertions

Both fail the build rather than ship something quietly wrong:

- **Match rate ≥ 95%** — below that, facilities silently lose their attributes.
- **Zero collisions** — two ICE rows may never resolve to one DDP code. A collision is
  worse than a miss: the second write overwrites the first, so one facility displays
  another's rating and population while both still look populated.

## Integrity rules

These are the point of the project. Do not relax them for UI convenience.

1. **Never fabricate, estimate, or interpolate a value to fill a gap.** A missing
   number renders as "Not publicly reported" with a note on why. Two of the originally
   requested fields genuinely do not exist; the map says so rather than guessing.
2. **Every displayed value carries a source and an as-of date.** Different fields have
   different vintages — ICE ADP is fiscal-year-to-date, DDP ADP is trailing-year, stays
   data has its own cutoff. Never blend them into one undated number.
3. **Aggregate only. No detainee-level data ever reaches the client.** The stays file
   is individual-level; the pipeline emits counts.
4. **Small-cell suppression.** Any age/demographic cell with `n < 5` renders as "<5",
   not the true count. Do not cross-tabulate age × nationality × facility — that
   combination is re-identifying at small facilities.
5. **No staff PII.** Facilities, companies, and agencies are in scope. Named
   individuals — officers, guards, detainees — are not.
6. **Distinguish stock from flow.** ADP and "people booked in during period X" are
   different quantities. Label which one is on screen.
7. **Preserve provenance through refactors.** If a field loses its source metadata, it
   comes off the map until the source is restored.
8. **A wrong attribution is worse than a missing one.** Never let two source rows
   resolve to one facility — the second silently overwrites the first and both still
   look populated. When a match is ambiguous, leave it unmatched and record it, or
   resolve it by hand with cited evidence. Enforced by the collision assertion.
9. **A derived location must always announce itself.** Coordinates computed from a city
   or county centroid are not an address, and a solid pin claims otherwise. Anything
   not `location_precision: exact` renders hollow, opens with a banner explaining how
   the point was derived, and gets no directions link. Enforced at build time: an
   `approx` feature without a `location_note` fails the build.

## Stack

- **Pipeline:** Python 3.12, `pandas` 2.3 + `pyarrow` 22 (both already installed).
  Emits static JSON. No server, no database.
- **Frontend:** Vite + TypeScript + **MapLibre GL JS**. No UI framework.
  MapLibre chosen over Leaflet because its GeoJSON source does clustering natively at
  844 points, and swapping the basemap to self-hosted Protomaps `.pmtiles` later is a
  style-config change rather than a rewrite — which matters for the Reddit/Devvit port,
  where external domains must be allowlisted.
- **Basemap:** free raster tiles to start; Protomaps pmtiles when Devvit needs it.
- **Hosting:** GitHub Pages, deployed by `.github/workflows/pages.yml` whenever `main`
  changes. A dedicated worker clone runs `pipeline.refresh --publish` daily through a
  macOS LaunchAgent; strict validation and an atomic staging build must pass before the
  three public aggregate files can be committed and pushed.

## Layout

```
pipeline/
  sources.py      fetch + cache raw sources (ICE link discovery, LFS parquet, range reads)
  crosswalk.py    ICE ↔ DDP facility matching + override file
  aggregate.py    age/demographic rollups from the 1.09M-row stays file
  enrich.py       operator-company crosswalk
  build.py        orchestrates → data/out/facilities.geojson
  data/raw/       cached downloads (gitignored)
  data/manual/    hand-curated, human-owned, never auto-overwritten
web/
  src/            map, detail panel, filters, formatting
  public/data/    built geojson
```

`pipeline/data/manual/` is hand-curated and cited. The build reads it and never
rewrites it.

## Conventions

- `detention_facility_code` is the primary key everywhere. Facilities without one
  (a handful in DDP) get a synthetic `SYNTH-<slug>` key.
- Null means "not published." Zero means "published as zero." Never conflate them.
- ADP values are fractional averages, not people counts — round only at render time.
- The ICE spreadsheet's header row is 10, not 1. It has moved between fiscal years;
  detect it by scanning for the row containing `Name` and `Address`.

## Gotchas

- `deportationdata.org` returns 403 to `WebFetch`; use `curl` with a browser UA.
- DDP data files are Git LFS. The `github.com/.../raw/refs/heads/main/...` URL resolves
  LFS content correctly; `api.github.com` reports pointer sizes (`0.00 MB`), not real ones.
- The ICE xlsx filename embeds its release date and changes every time.
- ICE's `Over72HourFacilities.xlsx` has only name/city/state/type and is adults-only —
  not worth joining.
- The `Vulnerable & Special Population` sheet is about segregation placements, not age.
  It also still carries FY2022 data in the FY26 workbook. Check `fy` before using it.
- **ICE zips are not authoritative.** Several are mailing addresses or simply wrong:
  Webb County is listed under `78041` (the Laredo Processing Center's zip, physically
  `78046`), Burleigh County under a PO-box `58502` (physically `58504`), Florence
  Correctional under `85232` (Florence AZ is `85132`). Match on zip *plus* a distinctive
  name token; never treat an ICE zip as ground truth. DDP's geocodes are hand-verified
  and win on conflict.
- DDP publishes a `-shp.zip` shapefile variant alongside the parquet. It holds the same
  844 rows but the DBF format truncates every column name to 8 characters
  (`dtntn_f_`, `avrg____`, `avrg_____1`). Build on the parquet; the shapefile is only
  useful for handing to GIS software.
- `facilities-latest-sf.parquet` is the same data plus a WKB `geometry` column that
  duplicates `latitude`/`longitude`. `sources.py` drops it rather than carrying it.

## Build status

Verified working end to end as of 2026-07-29. Two commands, in order — `build` reads
whatever `aggregate` last wrote and runs without it, just with no demographics:

```bash
python -m pipeline.aggregate && python -m pipeline.build
```

| Output | Count | Notes |
|---|---|---|
| `facilities.geojson` | **844 mapped** | 2.2 MB raw, **193 KB gzipped** |
| `facilities-unplaced.json` | 0 | every facility now has coordinates |
| with ICE attributes | 208 | all 208 ICE rows are geocoded |
| with demographics | 481 | of 844 |
| inspection ratings present | 166 | 158 Pass, **5 Fail**, 3 pending |

The stays rollup covers **400,223 stays** beginning between 2025-03-11 and 2026-03-11,
out of 1,087,417 rows total.

### Location precision — not every pin means the same thing

Only 709 facilities have a published street address. The other 135 are ICE hold rooms
and field-office holding areas, and DDP leaves their coordinates null; they carry no
city or county field either (city: 2/135, county: 0/135). They are placed by
`pipeline/geocode.py` and every facility carries a `location_precision`:

| `location_precision` | Count | What the pin means |
|---|---|---|
| `exact` | 709 | DDP's hand-verified geocode of a published address |
| `city_centroid` | 121 | Census centroid of the city named in the facility name |
| `manual_city_centroid` | 5 | hand-placed city, cited in the override file |
| `manual_office_code` | 3 | read from an ICE office code (PHI, SFR, HEL) |
| `manual_facility_area` | 3 | Guantanamo and Krome — areas, not addresses |
| `manual_county_seat` | 1 | Etowah County, AL |
| `county_centroid` | 1 | Pike County, PA |
| `state_centroid` | 1 | `Pdn Hold Room`, TX — "PDN" is unidentified |

Rules this must keep:

- **A derived point never passes as an address.** Approximate pins render hollow, the
  panel opens with a banner saying how the point was arrived at, and the build asserts
  that any `approx` feature carries a `location_note`.
- **No directions for an approximate pin.** The panel's "Getting there" section is
  replaced by an explanation of why routing is not offered. Sending someone to a city
  centroid implies a precision the data does not have.
- **A real coordinate is never overwritten** — `geocode.py` only fills nulls.
- The city is parsed from the *facility name* ("Albany Hold Room" → Albany, NY), which
  is published data, then matched against the Census gazetteer within the same state.
  Ambiguous matches are refused rather than guessed; they go to
  `data/manual/location_overrides.csv` with their evidence.

County fallback fires only when the name literally contains "county". Without that
guard "Boise Hold Room" lands in rural Boise County, 60 miles from Boise city.

### The three vintages do not line up

This is the single most important thing to preserve in the UI:

| Data | As of |
|---|---|
| DDP facility registry + ADP | 2026-07-28 |
| ICE attributes, inspections, contract type | 2026-07-20 |
| Demographics, age, bond, release reason | **2026-03-11** |

The stays file lags the other two by roughly four and a half months. Age bands describe
a *different period* from the population figures next to them, and they are a flow
measure besides. The panel labels each block with its own window and carries a
provenance table; never merge them into one "as of" line.

## Validation (`pipeline/validate.py`)

```bash
python -m pipeline.validate
```

The build's assertions catch structural faults. This module asks the harder question:
do our numbers agree with numbers *nobody in this pipeline computed*? Every check
compares against an independently published figure or a constraint the data must
satisfy however it was assembled. Checks report rather than fail — drift is a prompt to
look, not proof of a bug.

Current results, 2026-07-29:

| Check | Result | Reading |
|---|---|---|
| DDP ADP vs ICE ADP, 205 facilities | **r = 0.96**, median ratio 0.99 | two organisations, two windows, two methods — they track closely |
| Our median stay vs ICE's published ALOS, 199 facilities | **r = 0.79**, 75% within 50% | agreement, but qualified — see below |
| Coordinates inside their stated state, 844 | **2 outside** | an upstream DDP contradiction, below |
| Joins with no shared distinctive token | **0 of 208** | every join rests on real name evidence |
| DDP population figure stale | **2 facilities** | recently activated — see below |
| Small cells re-derived from the shipped file | **0 violations** across 480 | suppression holds end to end |

### Recently activated facilities make DDP's ADP misleading

Two facilities report an ICE average higher than the most DDP ever observed present at
once — which is not a contradiction to resolve but the signature of a facility that came
into use *after* DDP's population window closed:

| Facility | Days in use | DDP peak | DDP ADP | ICE FY26 ADP |
|---|---|---|---|---|
| `CVANXCA` Central Valley Annex | 53 | 1 | 0.15 | **83.7** |
| `LBRTYFL` Liberty County Sheriff's Office | 3 | 1 | 0.01 | **39.9** |

Both joins were checked by hand and are correct: `CVANXCA` is an exact name match, and
its sibling `GLDSACA` (Golden State Annex, same zip, same operator, in use all 365 days)
agrees closely between the two sources. The datasets simply describe different periods.

Where this holds, `population.ddp_figure_stale` carries an explanation and the panel
leads the section with it, because "Average daily population: 0" printed above an ICE
breakdown of 84 people is internally contradictory. **The map still sizes markers by
DDP ADP**, so these two facilities render as the smallest possible dots — a known
limitation, not yet resolved, because sizing on a mixture of vintages would break
integrity rule 2.

**Do not overstate the length-of-stay check.** It compares a median against a mean, so
they cannot coincide, and the mean sits above the median wherever long stays pull it.
r = 0.79 with a quarter of facilities diverging by more than 50% is real corroboration
that attribution and windowing are broadly right — it is *not* evidence that any single
facility's figure is correct. An earlier version of this file cited Adams County alone
(37.7 median against ICE's 34.6) as though one facility settled it. It does not.

**Known upstream contradiction.** `SPRHONY` (Spring Valley Hospital) and `SNTRHNY`
(Sunrise Hospital) carry `state: NY` while their city (Las Vegas), county (Clark), zip
(89118/89109) and hand-verified coordinates all say Nevada. DDP's state field is wrong
for these two. They are left as published rather than silently corrected, and the check
reports them on every run. They surface under NY in the state filter.

### What validation cannot do

Nothing here can verify that ICE's own published figures are true. If ICE publishes a
wrong population, this pipeline faithfully reproduces a wrong population. The checks
establish that the *join, aggregation and rendering* are faithful to the sources —
never that the sources are faithful to reality.

**The 135 ungeocoded facilities are almost entirely ICE hold rooms and field-office
holding areas** (`DALHOLD`, `MTGHOLD`, `PHOHOLD`, `SNAHOLD`, …) plus Guantanamo
(`GTMOACU`). They total **781 ADP — 1.2% of the national 62,498**. This is why deferring
their map treatment is defensible while dropping them from the *list* would not be:
they are real facilities holding real people, just short-term ones DDP could not
geocode to a street address.

### Web app

```bash
cd web && npm install && npm run dev     # http://localhost:5173
```

Vite + TypeScript + MapLibre, no UI framework. Clusters are sized by *summed ADP*, not
facility count, and turn red when they contain a facility that failed its last
inspection. Hover gives three lines; clicking a pin opens an anchored tooltip whose
button leads into the detail panel, which is deep-linkable via `?facility=CODE`.
Production bundle: 293 KB gzipped, almost all of it MapLibre.

### Directions (`web/src/directions.ts`)

Routes in both directions — to a facility for a visit, and from one for someone just
released. The other endpoint is either typed free-text or the browser's geolocation,
and links open in Google Maps, Apple Maps, or OpenStreetMap.

- **Route on coordinates, never on the address string.** Handing a provider
  `"20 Hobo Forks Road"` invites it to geocode the place itself and put someone on a
  road to the wrong building; several facilities sit on unnamed rural roads where that
  is a real failure, not a hypothetical one. DDP's coordinates are hand-verified, so
  they are what gets sent.
- **Only for `location_precision: exact`.** See the integrity rule above.
- **Location is read on user action only**, kept in memory for the session, and leaves
  the page only when the user opens a provider link they picked. Clearing the start box
  forgets it.
- The links are real `<a href>`s kept in sync as the inputs change, not click handlers,
  so they open in a new tab normally and "copy link address" works.
- Straight-line distance is shown when the origin is a coordinate, labelled as such —
  the drive is always longer.

Still to build: `enrich.py` (operator company — the last genuinely missing field), the
About page, and deployment.

## Frontend gotchas

- **`map.on('load')` is one-shot and will have already fired.** The data fetch outlives
  map initialisation, so attaching the listener after `await fetch(...)` waits forever
  and the app hangs with a blank map and an empty list, with no console error.
  Guarding with `map.loaded()` is *not* sufficient — it returns false while tiles are
  still in flight, which is exactly when `load` has already been and gone. Register the
  promise synchronously at startup, before any `await`; see `mapLoaded` in
  `web/src/main.ts`.
- **A hidden browser pane looks identical to that bug.** MapLibre fires `load` from its
  render loop, so when `document.visibilityState === 'hidden'` no frame runs, `load`
  never fires, and the app sits with a rendered sidebar and no map or list. Taking a
  screenshot forces a paint and everything appears at once. Check `visibilityState`
  before debugging further.
- **MapLibre serialises nested feature properties to strings.** Reading
  `feature.properties.population` off `queryRenderedFeatures` hands back a JSON string,
  not an object. The map only carries the flat styling fields (`adp`, `rating`,
  `contract`, `code`); the real objects live in the `byCode` lookup and are fetched by
  code on selection.
- **Cluster expressions cannot reach nested properties either**, which is why
  `build.py` emits those flat duplicates. They are display plumbing — the canonical
  values stay in the nested blocks.
- The map is constructed before the bundled stylesheet applies, so it can measure its
  container wrong and render into an undersized canvas. `map.resize()` after load.
