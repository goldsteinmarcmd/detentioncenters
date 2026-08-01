/**
 * Wiring: data load, search, filters, selection, deep links.
 *
 * One structural note. MapLibre serialises nested feature properties to strings when
 * they come back out of `queryRenderedFeatures`, so the map only ever carries the flat
 * styling fields (`adp`, `rating`, `contract`, `code`). The real property objects live
 * in `byCode` and are looked up by code on selection. Reading `population` off a
 * rendered feature would silently hand you a string.
 */

import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';

import {
  addFacilityLayers,
  createMap,
  facilityAt,
  onClusterClick,
  setSelected,
  setVisible,
  zoomToFacility,
} from './map';
import { renderPanel } from './panel';
import { average, escapeHtml, ratingClass, titleCase } from './format';
import {
  appleUrl,
  formatMiles,
  getCachedOrigin,
  googleUrl,
  haversineMiles,
  locateUser,
  osmUrl,
  setCachedOrigin,
  type Waypoint,
} from './directions';
import {
  airbnbUrl,
  bookingUrl,
  defaultStayDates,
  fetchNearbyLodging,
  nextDate,
  type LodgingResult,
  type StayDates,
} from './lodging';
import { renderBondFund, type BondCampaignCollection } from './bond-fund';
import type { FacilityCollection, FacilityProps, UnplacedFile } from './types';

const DATA = `${import.meta.env.BASE_URL}data/facilities.geojson`;
const UNPLACED = `${import.meta.env.BASE_URL}data/facilities-unplaced.json`;
const BOND_CASES = `${import.meta.env.BASE_URL}data/bond-cases.json`;
const LODGING_API = import.meta.env.VITE_LODGING_API_URL?.trim() ?? '';

const byCode = new Map<string, FacilityProps>();
const coordsByCode = new Map<string, [number, number]>();
const unplacedCodes = new Set<string>();
let tooltip: maplibregl.Popup | null = null;
let lodgingRequest: AbortController | null = null;

const el = {
  map: document.getElementById('map')!,
  panel: document.getElementById('panel') as HTMLElement,
  results: document.getElementById('results')!,
  summary: document.getElementById('summary')!,
  search: document.getElementById('search') as HTMLInputElement,
  state: document.getElementById('filter-state') as HTMLSelectElement,
  contract: document.getElementById('filter-contract') as HTMLSelectElement,
  rating: document.getElementById('filter-rating') as HTMLSelectElement,
  exactOnly: document.getElementById('filter-approx') as HTMLInputElement,
  hover: document.getElementById('hover-card') as HTMLElement,
  bondFund: document.getElementById('bond-fund-view') as HTMLElement,
  bondFundContent: document.getElementById('bond-fund-content') as HTMLElement,
  bondFundOpen: document.getElementById('bond-fund-open') as HTMLButtonElement,
  bondFundClose: document.getElementById('bond-fund-close') as HTMLButtonElement,
};

const map = createMap(el.map);
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
map.addControl(
  new maplibregl.GeolocateControl({ trackUserLocation: false }),
  'top-right',
);

/**
 * Resolves when the style is usable.
 *
 * Registered here, synchronously, rather than after the data fetch — `load` is a
 * one-shot event and the fetch routinely outlives it, so a listener attached later
 * waits forever and the app hangs with a blank map and an empty list, silently.
 * Checking `map.loaded()` instead is not enough either: it reports false while tiles
 * are still in flight, which is exactly when `load` has already been and gone.
 */
const mapLoaded: Promise<void> = new Promise((resolve) => {
  if (map.loaded()) resolve();
  else map.once('load', () => resolve());
});

async function boot() {
  const [collection, ungeo, bondCases] = await Promise.all([
    fetch(DATA).then((r) => r.json() as Promise<FacilityCollection>),
    fetch(UNPLACED).then((r) => r.json() as Promise<UnplacedFile>),
    fetch(BOND_CASES).then((r) => r.json() as Promise<BondCampaignCollection>),
  ]);

  el.bondFundContent.innerHTML = renderBondFund(bondCases);
  wireBondFund();
  const params = new URLSearchParams(location.search);
  if (params.get('view') === 'bond-fund') openBondFund(false);

  for (const f of collection.features) {
    byCode.set(f.properties.code, f.properties);
    coordsByCode.set(f.properties.code, f.geometry.coordinates);
  }
  for (const f of ungeo.facilities) {
    byCode.set(f.code, f);
    unplacedCodes.add(f.code);
  }

  const m = collection.metadata;
  el.summary.innerHTML = `
    <strong>${m.counts.facilities_total}</strong> facilities ·
    ${m.counts.exact_location} at a published address ·
    ${m.counts.approximate_location} placed approximately<br />
    Facility data as of ${escapeHtml(m.sources[0]?.as_of ?? '—')},
    ICE attributes as of ${escapeHtml(m.sources[1]?.as_of ?? '—')}`;

  populateFilters();

  await mapLoaded;
  // The map is constructed before the bundled stylesheet lands, so it can measure its
  // container at the wrong size. Re-measure once layout is settled.
  map.resize();

  addFacilityLayers(map, collection as unknown as GeoJSON.FeatureCollection);
  onClusterClick(map);
  wireInteractions();
  wireDirections();
  wireLodging();
  applyFilters();

  const deepLink = params.get('facility');
  if (deepLink && byCode.has(deepLink)) select(deepLink, { zoom: true });
}

function populateFilters() {
  const states = new Set<string>();
  const contracts = new Set<string>();
  for (const f of byCode.values()) {
    if (f.state) states.add(f.state);
    if (f.operator.contract_type) contracts.add(f.operator.contract_type);
  }
  for (const s of [...states].sort()) {
    el.state.add(new Option(s, s));
  }
  for (const c of [...contracts].sort()) {
    el.contract.add(new Option(c, c));
  }
}

function matches(f: FacilityProps): boolean {
  const q = el.search.value.trim().toLowerCase();
  if (q) {
    const hay = [f.name, f.city, f.county, f.state, f.code]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    if (!hay.includes(q)) return false;
  }
  if (el.state.value && f.state !== el.state.value) return false;
  if (el.contract.value && f.operator.contract_type !== el.contract.value) return false;

  const rating = el.rating.value;
  if (rating === '__none' && f.inspection.last_rating) return false;
  if (rating && rating !== '__none') {
    if (!f.inspection.last_rating?.includes(rating)) return false;
  }
  if (el.exactOnly.checked && f.approx) return false;
  if (unplacedCodes.has(f.code)) return false;
  return true;
}

function applyFilters() {
  const visible = [...byCode.values()].filter(matches);
  const mappable = new Set(
    visible.filter((f) => !unplacedCodes.has(f.code)).map((f) => f.code),
  );
  setVisible(map, mappable);
  renderResults(visible);
}

function renderResults(list: FacilityProps[]) {
  const sorted = list
    .slice()
    .sort((a, b) => (b.adp ?? 0) - (a.adp ?? 0))
    .slice(0, 300);

  el.results.innerHTML =
    `<p class="count">${list.length.toLocaleString()} facilit${
      list.length === 1 ? 'y' : 'ies'
    }${list.length > 300 ? ' — showing the 300 largest' : ''}</p>` +
    sorted
      .map((f) => {
        const approx = f.approx === 1;
        return `
        <button class="result" data-code="${escapeHtml(f.code)}">
          <span class="swatch ${ratingClass(f.inspection.last_rating)}"></span>
          <span class="result-body">
            <span class="result-name">${escapeHtml(titleCase(f.name ?? f.code))}</span>
            <span class="result-meta">
              ${escapeHtml([f.city, f.state].filter(Boolean).join(', '))}
              ${approx ? '· <em>approx.</em>' : ''}
            </span>
          </span>
          <span class="result-adp">${f.adp !== null ? average(f.adp) : '—'}</span>
        </button>`;
      })
      .join('');
}

function select(code: string, opts: { zoom?: boolean } = {}) {
  const f = byCode.get(code);
  if (!f) return;
  el.panel.innerHTML = renderPanel(f);
  el.panel.hidden = false;
  el.panel.scrollTop = 0;
  setSelected(map, code);

  const coords = coordsByCode.get(code);
  if (coords && opts.zoom) zoomToFacility(map, coords);

  const url = new URL(location.href);
  url.searchParams.set('facility', code);
  history.replaceState(null, '', url);

  document.getElementById('panel-close')?.addEventListener('click', closePanel);

  // A remembered location carries across facilities so it need only be given once.
  const cached = getCachedOrigin();
  const input = el.panel.querySelector<HTMLInputElement>('.dir-input');
  if (input && cached) input.value = cached.label;
  refreshDirections();
  refreshLodging();
}

/** True when the user has flipped the route to start at the facility. */
let dirReversed = false;

/**
 * Keep the three provider links in step with the start box and the direction toggle.
 *
 * The links are real anchors with real hrefs rather than click handlers, so they open
 * in a new tab the way the browser and the user expect — no popup blocking, and
 * "copy link address" works.
 */
function refreshDirections() {
  const section = el.panel.querySelector<HTMLElement>('.directions[data-code]');
  if (!section) return;

  const code = section.dataset.code!;
  const coords = coordsByCode.get(code);
  const facility = byCode.get(code);
  if (!coords || !facility) return;

  const facilityPoint = {
    lat: coords[1],
    lon: coords[0],
    label: titleCase(facility.name ?? code),
  };

  const input = section.querySelector<HTMLInputElement>('.dir-input')!;
  const status = section.querySelector<HTMLElement>('.dir-status')!;
  const typed = input.value.trim();
  const cached = getCachedOrigin();

  // A typed value always wins over a remembered location — it is the more recent
  // statement of intent.
  let other: Waypoint | null = null;
  if (typed && typed !== cached?.label) other = { text: typed };
  else if (cached) other = cached;

  const from = dirReversed ? facilityPoint : other;
  const to = dirReversed ? other : facilityPoint;

  for (const link of section.querySelectorAll<HTMLAnchorElement>('.dir-go')) {
    const provider = link.dataset.provider;
    if (!to) {
      // Nothing to route to yet: offer the facility as a plain destination.
      link.href =
        provider === 'apple'
          ? appleUrl(null, facilityPoint)
          : provider === 'osm'
            ? osmUrl(null, facilityPoint)
            : googleUrl(null, facilityPoint);
      continue;
    }
    link.href =
      provider === 'apple'
        ? appleUrl(from, to)
        : provider === 'osm'
          ? osmUrl(from, to)
          : googleUrl(from, to);
  }

  if (cached && !typed) {
    const miles = haversineMiles(cached, facilityPoint);
    status.innerHTML = `Using your location · about <strong>${formatMiles(
      miles,
    )}</strong> away in a straight line — the drive will be longer.`;
    status.hidden = false;
    status.classList.remove('error');
  } else if (!typed) {
    status.hidden = true;
  }
}

function wireDirections() {
  el.panel.addEventListener('input', (e) => {
    const target = e.target as HTMLElement;
    if (!target.classList.contains('dir-input')) return;
    // Emptying the box means "forget where I am", not "fall back to the old location".
    if (!(target as HTMLInputElement).value.trim()) setCachedOrigin(null);
    refreshDirections();
  });

  el.panel.addEventListener('click', async (e) => {
    const target = e.target as HTMLElement;

    const swap = target.closest<HTMLButtonElement>('.dir-swap');
    if (swap) {
      dirReversed = !dirReversed;
      swap.setAttribute('aria-pressed', String(dirReversed));
      const arrow = swap.querySelector('.dir-arrow')!;
      const label = swap.querySelector('.dir-swap-label')!;
      arrow.textContent = dirReversed ? '↑' : '↓';
      label.innerHTML = dirReversed
        ? 'Travelling <strong>from</strong> the facility'
        : 'Travelling <strong>to</strong> the facility';
      const section = swap.closest('.directions')!;
      const startLabel = section.querySelector('label[for="dir-origin"]')!;
      startLabel.textContent = dirReversed ? 'Destination' : 'Start';
      refreshDirections();
      return;
    }

    const locate = target.closest<HTMLButtonElement>('.dir-locate');
    if (locate) {
      const section = locate.closest('.directions')!;
      const status = section.querySelector<HTMLElement>('.dir-status')!;
      const input = section.querySelector<HTMLInputElement>('.dir-input')!;
      status.hidden = false;
      status.classList.remove('error');
      status.textContent = 'Asking your browser for your location…';
      locate.disabled = true;
      try {
        const origin = await locateUser();
        input.value = origin.label;
        refreshDirections();
      } catch (err) {
        status.classList.add('error');
        status.textContent =
          err instanceof Error ? err.message : 'Could not determine your location.';
      } finally {
        locate.disabled = false;
      }
    }
  });
}

function lodgingDates(section: HTMLElement): StayDates {
  const checkin = section.querySelector<HTMLInputElement>('.lodging-checkin')!;
  const checkout = section.querySelector<HTMLInputElement>('.lodging-checkout')!;
  const defaults = defaultStayDates();
  if (!checkin.value) checkin.value = defaults.checkin;
  if (!checkout.value) checkout.value = defaults.checkout;
  if (checkout.value <= checkin.value) checkout.value = nextDate(checkin.value);
  checkout.min = nextDate(checkin.value);
  return { checkin: checkin.value, checkout: checkout.value };
}

function lodgingDestination(facility: FacilityProps): string {
  const place = [facility.city, facility.state].filter(Boolean).join(', ');
  return facility.address || place || facility.name || facility.code;
}

function refreshLodging() {
  const section = el.panel.querySelector<HTMLElement>('.lodging[data-code]');
  if (!section) return;
  const facility = byCode.get(section.dataset.code!);
  if (!facility) return;

  const dates = lodgingDates(section);
  const destination = lodgingDestination(facility);
  for (const link of section.querySelectorAll<HTMLAnchorElement>('.lodging-go')) {
    link.href =
      link.dataset.provider === 'airbnb'
        ? airbnbUrl(destination, dates)
        : bookingUrl(destination, dates);
  }

  const live = section.querySelector<HTMLButtonElement>('.lodging-live')!;
  live.hidden = !LODGING_API;
}

function safeLodgingUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function lodgingResultRow(result: LodgingResult): string {
  const url = safeLodgingUrl(result.url);
  if (!url) return '';
  const details = [
    result.distance_miles != null ? `${result.distance_miles.toFixed(1)} mi` : null,
    result.rating != null ? `Rating ${result.rating}` : null,
    result.price ?? null,
  ]
    .filter(Boolean)
    .join(' · ');
  return `
    <a class="lodging-result" href="${escapeHtml(url)}" target="_blank"
       rel="noopener noreferrer">
      <strong>${escapeHtml(result.name)}</strong>
      ${details ? `<span>${escapeHtml(details)}</span>` : ''}
    </a>`;
}

async function loadLiveLodging(section: HTMLElement) {
  const code = section.dataset.code!;
  const coords = coordsByCode.get(code);
  if (!coords || !LODGING_API) return;

  const status = section.querySelector<HTMLElement>('.lodging-status')!;
  const results = section.querySelector<HTMLElement>('.lodging-results')!;
  const button = section.querySelector<HTMLButtonElement>('.lodging-live')!;
  const dates = lodgingDates(section);

  lodgingRequest?.abort();
  lodgingRequest = new AbortController();
  button.disabled = true;
  status.hidden = false;
  status.textContent = 'Checking live hotel availability…';
  results.hidden = true;

  try {
    const payload = await fetchNearbyLodging(
      LODGING_API,
      { lat: coords[1], lon: coords[0] },
      dates,
      lodgingRequest.signal,
    );
    const rows = payload.results.map(lodgingResultRow).filter(Boolean).join('');
    results.innerHTML = rows || '<p class="absent">No available hotels were returned.</p>';
    results.hidden = false;
    status.textContent = payload.provider
      ? `Live results from ${payload.provider}.`
      : 'Live hotel results.';
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return;
    status.textContent =
      err instanceof Error ? err.message : 'Could not load live hotel availability.';
  } finally {
    button.disabled = false;
  }
}

function wireLodging() {
  el.panel.addEventListener('change', (event) => {
    const target = event.target as HTMLElement;
    if (!target.matches('.lodging-checkin, .lodging-checkout')) return;
    lodgingRequest?.abort();
    const section = target.closest<HTMLElement>('.lodging');
    section?.querySelector<HTMLElement>('.lodging-results')?.setAttribute('hidden', '');
    refreshLodging();
  });

  el.panel.addEventListener('click', (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('.lodging-live');
    const section = button?.closest<HTMLElement>('.lodging');
    if (section) void loadLiveLodging(section);
  });
}

function closePanel() {
  lodgingRequest?.abort();
  el.panel.hidden = true;
  setSelected(map, null);
  const url = new URL(location.href);
  url.searchParams.delete('facility');
  history.replaceState(null, '', url);
}

function openBondFund(updateHistory = true) {
  tooltip?.remove();
  el.hover.hidden = true;
  el.bondFund.hidden = false;
  document.body.classList.add('show-bond-fund');
  if (updateHistory) {
    const url = new URL(location.href);
    url.searchParams.set('view', 'bond-fund');
    history.replaceState(null, '', url);
  }
  el.bondFundClose.focus();
}

function closeBondFund(updateHistory = true) {
  el.bondFund.hidden = true;
  document.body.classList.remove('show-bond-fund');
  if (updateHistory) {
    const url = new URL(location.href);
    url.searchParams.delete('view');
    history.replaceState(null, '', url);
  }
  el.bondFundOpen.focus();
}

function wireBondFund() {
  el.bondFundOpen.addEventListener('click', () => openBondFund());
  el.bondFundClose.addEventListener('click', () => closeBondFund());
  window.addEventListener('popstate', () => {
    const open = new URLSearchParams(location.search).get('view') === 'bond-fund';
    el.bondFund.hidden = !open;
    document.body.classList.toggle('show-bond-fund', open);
  });
}

/**
 * The click tooltip.
 *
 * Anchored to the facility rather than following the cursor, so it can hold a button
 * and be reached on a touch screen. It carries only enough to confirm you clicked the
 * right place; the button is the way through to everything else.
 */
function openTooltip(code: string, coords: [number, number]) {
  const f = byCode.get(code);
  if (!f) return;

  tooltip?.remove();
  const place = [f.city, f.state].filter(Boolean).join(', ');
  const approx = f.approx
    ? `<p class="tip-approx">Approximate location${
        f.location_matched_to ? ` — ${escapeHtml(f.location_matched_to)}` : ''
      }</p>`
    : '';

  tooltip = new maplibregl.Popup({
    offset: 14,
    closeButton: true,
    maxWidth: '260px',
    className: 'facility-tip',
  })
    .setLngLat(coords)
    .setHTML(
      `<div class="tip">
         <strong>${escapeHtml(titleCase(f.name ?? code))}</strong>
         <span class="tip-place">${escapeHtml(place)}</span>
         <span class="tip-pop">${
           f.adp !== null
             ? `${average(f.adp)} average daily population`
             : 'Population not publicly reported'
         }</span>
         ${approx}
         <button class="tip-btn" data-code="${escapeHtml(code)}">View full details →</button>
       </div>`,
    )
    .addTo(map);

  tooltip
    .getElement()
    ?.querySelector<HTMLButtonElement>('.tip-btn')
    ?.addEventListener('click', () => {
      select(code, { zoom: true });
      tooltip?.remove();
    });
}

function wireInteractions() {
  map.on('click', 'facility', (e) => {
    const hit = facilityAt(map, e.point);
    if (!hit?.code) return;
    const coords = coordsByCode.get(hit.code);
    if (coords) openTooltip(hit.code, coords);
  });

  map.on('mousemove', 'facility', (e) => {
    const hit = facilityAt(map, e.point);
    if (!hit?.code) return;
    const f = byCode.get(hit.code);
    if (!f) return;
    map.getCanvas().style.cursor = 'pointer';
    // Hover stays to three lines. Anything richer belongs in the panel, where it can
    // be read on a touch device and reached by a keyboard.
    el.hover.innerHTML = `
      <strong>${escapeHtml(titleCase(f.name ?? f.code))}</strong>
      <span>${escapeHtml([f.city, f.state].filter(Boolean).join(', '))}</span>
      <span>${f.adp !== null ? `${average(f.adp)} avg. daily population` : 'Population not reported'}</span>`;
    el.hover.hidden = false;
    el.hover.style.left = `${e.point.x + 14}px`;
    el.hover.style.top = `${e.point.y + 14}px`;
  });

  map.on('mouseleave', 'facility', () => {
    map.getCanvas().style.cursor = '';
    el.hover.hidden = true;
  });

  map.on('mouseenter', 'clusters', () => (map.getCanvas().style.cursor = 'pointer'));
  map.on('mouseleave', 'clusters', () => (map.getCanvas().style.cursor = ''));

  el.results.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLElement>('.result');
    if (btn?.dataset.code) select(btn.dataset.code, { zoom: true });
  });

  for (const control of [el.search, el.state, el.contract, el.rating, el.exactOnly]) {
    control.addEventListener('input', applyFilters);
  }

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!el.bondFund.hidden) closeBondFund();
    else closePanel();
  });
}

boot().catch((err) => {
  el.summary.textContent = 'Could not load facility data.';
  console.error(err);
});
