# Backlog

Deferred work. Nothing here blocks v1 ([PLAN.md](PLAN.md)).

---

## Reddit app (Devvit)

The eventual goal. Deferred until the web map ships and the data pipeline is stable —
porting a moving target would mean doing the work twice.

**Why it's deferred, not just "later":** Reddit's Developer Platform sandboxes apps
tightly, and several constraints hit design decisions we'd otherwise get wrong. Better
to build the web version knowing where it's going than to retrofit.

### Constraints to verify before starting

Devvit's API moves quickly — check all of this against current docs at
`developers.reddit.com` rather than trusting the notes below.

- **External network access is allowlisted per-domain**, not open. Basemap tiles, the
  GeoJSON, and any API call need explicit declaration. This is the main reason
  [CLAUDE.md](CLAUDE.md) picks MapLibre + a Protomaps `.pmtiles` path over a
  tile-server-dependent setup: one self-hosted file, one domain.
- **Bundle/asset size limits.** Now measured rather than estimated: the built GeoJSON
  is **104 KB gzipped** (1.26 MB raw) for all 709 mapped facilities, comfortably small
  even before age and operator fields land. The basemap is the real question — likely a
  US-only, low-zoom pmtiles extract.
- **Interactive custom posts** are the render surface; webviews allow full HTML/JS.
  Decide which — a webview is closer to the existing map, a native custom post feels
  more Reddit-y and performs better in-feed.
- **Redis + scheduler** are available for caching the dataset server-side and refreshing
  it on a cron, so the client doesn't refetch upstream.
- Apps are **reviewed before publishing** to communities.

### Product shape (undecided)

Options, roughly increasing in effort:

1. **Lookup bot** — comment `!detention <zip>` and get facilities near that zip with
   population and inspection rating. Cheapest, no map rendering at all, works today's
   data as-is.
2. **Interactive map post** — a custom post a mod can pin, browsable in-feed.
3. **Per-facility post generator** — mods generate a post for one facility; the app
   renders a card with current stats and updates it as data refreshes.

Option 1 is the honest starting point — it validates whether anyone wants this before
paying the map-in-a-sandbox cost.

### Open questions

- Which subreddits, and have their mods actually agreed? An unrequested app in a
  political-topic subreddit is a moderation problem, not a feature.
- Does it stay strictly factual-with-citations in a space that will read it as
  advocacy? The integrity rules in CLAUDE.md matter more here, not less.
- Refresh cadence within Reddit's quotas.

---

## Data expansion

- **Current detainee referral roster for bond campaigns.** The public DDP stays source
  is anonymized and delayed, while ICE's current Detainee Locator requires a known name
  or A-Number and does not expose a bulk roster or current bond amount. Build a private,
  consent-based intake from detainees, families, attorneys, and legal-service partners;
  verify current custody and bond with ICE/ERO; then publish only the anonymous campaign
  record. Do not infer current detention from an open historical stay or infer offense
  severity from ICE's broad book-in criminality category.
- **Deaths in custody.** ICE publishes detainee death reports separately. High-value,
  sensitive — needs careful presentation and its own source verification.
- **ODO / OIG inspection reports.** The FY26 spreadsheet gives a rating and a date; the
  underlying reports contain the actual findings. Link out per facility at minimum;
  extracting findings is a bigger project.
- **Contract values from USAspending.gov.** Per-facility federal spend. Doubles as the
  cross-check for the operator crosswalk and as the least-bad proxy for facility scale
  given guard counts don't exist.
- **Historical time series.** `facilities-detention-management.parquet` has 664 rows
  across multiple fiscal years — enough for a population-over-time chart per facility
  and a time slider on the map.
- **Facility capacity vs. occupancy.** `Guaranteed Minimum` is a contractual floor, not
  a capacity. Real capacity figures appear in contracts and would make an occupancy
  metric possible.
- **New/proposed facilities.** ICE's site lists newly-opened facilities before they
  appear in statistics. Worth flagging as "newly listed, no data yet."
- **Hold rooms and short-term CBP sites.** Present in DDP (844 rows vs. ICE's 208) but
  thinly documented. Decide whether they get map pins or a separate list.

## Map features

- Choropleth by state/county — detained population per capita.
- Nearest-facility routing and travel time (matters for families and legal visitation).
- Compare mode — two facilities side by side.
- Data export: CSV/GeoJSON download per current filter.
- Embeddable iframe widget for other sites.
- Accessibility pass: full keyboard navigation, screen-reader table view as a first-class
  alternative to the map, not a fallback.
- i18n — Spanish first.

## Pipeline

- Diffing between releases: "what changed since last ICE release" — new facilities,
  closures, population swings, inspection-rating changes. Probably the most compelling
  recurring content the project could produce, and it feeds the Reddit app directly.
- Alerting on upstream schema changes rather than discovering them via a failed build.
- Archive every raw release so historical rebuilds stay possible if ICE removes files.
- Expand the operator crosswalk past the top-50 facilities.

## Infrastructure

- Move geocoding of the 135 ungeocoded DDP facilities in-house, or contribute fixes
  upstream to DDP.
- API endpoint (`/api/facilities`) if anything beyond the map needs the data.
- Uptime and data-freshness monitoring — alert if ICE hasn't published in >30 days,
  which is itself newsworthy.
