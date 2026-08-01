/**
 * The facility detail panel.
 *
 * Everything the tooltip cannot hold. Three rules drive the layout:
 *
 *  - Stock and flow never sit in the same block. Average daily population and
 *    "people booked in during a window" are different quantities on different
 *    cutoffs, so they get separate sections with their own dates.
 *  - Absent values render as a stated absence with a reason, never as a blank or a
 *    zero. Guard counts get the fullest treatment because they are the field people
 *    will look hardest for.
 *  - Every section ends up covered by the provenance footer.
 */

import {
  average,
  contractLabel,
  count,
  countValue,
  date,
  escapeHtml,
  money,
  NOT_REPORTED,
  num,
  ratingClass,
  text,
  titleCase,
} from './format';
import { isSuppressed, type Count, type FacilityProps, type SourceRef } from './types';

const AGE_BAND_ORDER = ['0-17', '18-24', '25-34', '35-44', '45-54', '55-64', '65+'];

export function renderPanel(p: FacilityProps): string {
  return [
    header(p),
    directionsSection(p),
    populationSection(p),
    demographicsSection(p),
    operatorSection(p),
    inspectionSection(p),
    contextSection(p),
    notReportedSection(p),
    provenanceSection(p),
  ].join('');
}

function header(p: FacilityProps): string {
  const place = [p.city, p.state].filter(Boolean).join(', ');
  const approx = p.location_note
    ? `<p class="approx-banner"><strong>Approximate location.</strong>
         ${escapeHtml(p.location_note)}</p>`
    : '';
  return `
    <div class="panel-head">
      <button id="panel-close" aria-label="Close">&times;</button>
      <h2>${escapeHtml(titleCase(p.name ?? 'Unnamed facility'))}</h2>
      <p class="addr">${escapeHtml(
        p.address ?? `No address published${place ? ` · ${place}` : ''}`,
      )}</p>
      ${approx}
      <div class="head-actions">
        <span class="code">${escapeHtml(p.code)}</span>
      </div>
    </div>`;
}

/**
 * Getting there.
 *
 * Routing is offered in both directions because both are real needs: a family
 * travelling to a visit, and someone released from custody trying to get somewhere.
 * The starting point can be the browser's location or anything typed in.
 *
 * Facilities without a published address get an explanation rather than a button —
 * routing to a city centroid would imply a precision the data does not have.
 */
function directionsSection(p: FacilityProps): string {
  if (p.location_precision !== 'exact') {
    return `
      <section class="directions">
        <h3>Getting there</h3>
        <p class="absent">No directions offered. No street address is published for this
        facility, so the pin is only accurate to ${escapeHtml(
          p.location_matched_to ?? 'the area shown',
        )}. Routing to that point would send you to the middle of a city, not to a
        building.</p>
      </section>`;
  }

  const place = [p.city, p.state].filter(Boolean).join(', ');
  return `
    <section class="directions" data-code="${escapeHtml(p.code)}">
      <h3>Getting there</h3>

      <div class="dir-row">
        <label for="dir-origin">Start</label>
        <input id="dir-origin" class="dir-input" type="text"
               placeholder="Address, city, or airport"
               autocomplete="off" />
        <button class="dir-locate" type="button" title="Use my current location">
          Use my location
        </button>
      </div>

      <div class="dir-swap-row">
        <button class="dir-swap" type="button" aria-pressed="false">
          <span class="dir-arrow">↓</span>
          <span class="dir-swap-label">Travelling <strong>to</strong> the facility</span>
        </button>
      </div>

      <div class="dir-row">
        <label>End</label>
        <span class="dir-facility">${escapeHtml(
          titleCase(p.name ?? p.code),
        )}<span class="dir-facility-place">${escapeHtml(place)}</span></span>
      </div>

      <p class="dir-status" hidden></p>

      <div class="dir-links">
        <a class="btn dir-go" data-provider="google" target="_blank" rel="noopener noreferrer" href="#">Google Maps</a>
        <a class="btn ghost dir-go" data-provider="apple" target="_blank" rel="noopener noreferrer" href="#">Apple Maps</a>
        <a class="btn ghost dir-go" data-provider="osm" target="_blank" rel="noopener noreferrer" href="#">OpenStreetMap</a>
      </div>

      <p class="note">
        Routing uses the facility's verified coordinates rather than its address text, so
        a mapping service cannot re-geocode it to the wrong building. Your starting point
        is only read when you ask for it and is sent nowhere until you open a provider.
      </p>
    </section>`;
}

function populationSection(p: FacilityProps): string {
  const pop = p.population;
  const rows: Array<[string, string]> = [
    ['Average daily population', average(pop.ddp_avg_daily_trailing_year)],
    ['Peak daily population', num(pop.ddp_max_daily_trailing_year, 0)],
  ];

  const iceRows: Array<[string, string]> = p.has_ice_attributes
    ? [
        ['Men, criminal conviction', average(pop.ice_fy_adp_male_criminal)],
        ['Men, no criminal conviction', average(pop.ice_fy_adp_male_noncriminal)],
        ['Women, criminal conviction', average(pop.ice_fy_adp_female_criminal)],
        ['Women, no criminal conviction', average(pop.ice_fy_adp_female_noncriminal)],
        ['Held under mandatory detention', average(pop.ice_fy_adp_mandatory)],
      ]
    : [];

  return `
    <section>
      <h3>How many people <span class="tag">stock</span></h3>
      <p class="note">A daily average over the trailing year, not a headcount today.</p>
      ${
        pop.ddp_figure_stale
          ? `<p class="absent"><strong>The trailing-year average is out of date here.</strong>
             ${escapeHtml(pop.ddp_figure_stale)}</p>`
          : ''
      }
      ${dl(rows)}
      ${
        iceRows.length
          ? `<h4>Breakdown <span class="muted">(ICE, fiscal year to date)</span></h4>${dl(
              iceRows,
            )}`
          : `<p class="absent">${NOT_REPORTED} — this facility is not in ICE's
             current statistics release, which lists only facilities with a
             population above zero.</p>`
      }
      ${
        p.guaranteed_minimum_beds !== null
          ? `<p class="note">ICE pays for a guaranteed minimum of
             <strong>${num(p.guaranteed_minimum_beds)}</strong> beds here regardless of
             how many are occupied.</p>`
          : ''
      }
      ${sourceNote('Population sources', [
        p.sources['population.ddp_*'],
        p.sources[
          'population.ice_*, classification_adp, inspection, operator.contract_type, guaranteed_minimum_beds, avg_length_of_stay_days'
        ],
      ])}
    </section>`;
}

function demographicsSection(p: FacilityProps): string {
  const d = p.demographics;
  if (!d) {
    return `
      <section>
        <h3>Who is held here</h3>
        <p class="absent">${NOT_REPORTED} for this facility. No detention stays were
        recorded here in the published individual-level data for the window covered.</p>
        ${sourceNote('Demographic source', [p.sources.demographics])}
      </section>`;
  }

  const bands = AGE_BAND_ORDER.filter((b) => b in d.age_bands);
  const max = Math.max(...bands.map((b) => countValue(d.age_bands[b])), 1);

  const ageChart = bands
    .map((band) => {
      const v = d.age_bands[band];
      const pct = (countValue(v) / max) * 100;
      return `
        <div class="bar-row ${isSuppressed(v) ? 'suppressed' : ''}">
          <span class="bar-label">${band}</span>
          <span class="bar-track"><span class="bar-fill" style="width:${pct}%"></span></span>
          <span class="bar-value">${count(v)}</span>
        </div>`;
    })
    .join('');

  return `
    <section>
      <h3>Who is held here <span class="tag flow">flow</span></h3>
      <p class="note">
        ${escapeHtml(d.stays_in_window.toLocaleString('en-US'))} detention stays beginning
        between ${date(d.window_start)} and ${date(d.window_end)}. This counts people
        booked in over a year, not people present on one day — it is not comparable to
        the average daily population above, and covers an earlier period.
      </p>

      <h4>Age at book-in</h4>
      <p class="note">
        Bands, not exact ages: the source carries only a birth year, so any single age
        would be off by up to a year. Cells under ${d.suppression_threshold} people show
        as “&lt;${d.suppression_threshold}”.
      </p>
      <div class="chart">${ageChart}</div>
      ${
        d.age_median !== null
          ? `<p class="note">Median age at book-in: <strong>${d.age_median}</strong>.</p>`
          : ''
      }

      ${distribution('Gender', d.gender)}
      ${distribution('Most common citizenship countries', d.top_citizenship)}
      ${distribution('Criminality at book-in', d.book_in_criminality)}
      ${distribution('How stays ended', d.release_reason, 6)}

      <h4>Length of stay</h4>
      ${dl([
        ['Median', d.length_of_stay_days.median !== null ? `${d.length_of_stay_days.median} days` : NOT_REPORTED],
        ['90th percentile', d.length_of_stay_days.p90 !== null ? `${d.length_of_stay_days.p90} days` : NOT_REPORTED],
        ['Completed stays counted', count(d.length_of_stay_days.n_completed)],
      ])}

      <h4>Bond</h4>
      ${dl([
        ['Median bond set', money(d.bond.median_set)],
        ['Stays with a bond set', count(d.bond.n_set)],
        ['Stays with bond posted', count(d.bond.n_posted)],
      ])}
      ${sourceNote('Demographic source', [p.sources.demographics])}
    </section>`;
}

function distribution(title: string, data: Record<string, Count>, limit = 8): string {
  const entries = Object.entries(data)
    .sort((a, b) => countValue(b[1]) - countValue(a[1]))
    .slice(0, limit);
  if (!entries.length) return '';
  const total = entries.reduce((sum, [, v]) => sum + countValue(v), 0) || 1;
  const rows = entries
    .map(
      ([k, v]) => `
      <div class="bar-row ${isSuppressed(v) ? 'suppressed' : ''}">
        <span class="bar-label">${escapeHtml(titleCase(k))}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${
          (countValue(v) / total) * 100
        }%"></span></span>
        <span class="bar-value">${count(v)}</span>
      </div>`,
    )
    .join('');
  return `<h4>${escapeHtml(title)}</h4><div class="chart">${rows}</div>`;
}

function operatorSection(p: FacilityProps): string {
  const company =
    p.operator.company ??
    `<span class="absent-inline">Not independently verified.</span>
     ICE publishes the contract structure but not the operating company; naming one
     without a citation would be a guess.`;
  return `
    <section>
      <h3>Who runs it</h3>
      ${dl([
        ['Contract type', contractLabel(p.operator.contract_type)],
        ['ICE field office', text(p.field_office)],
      ])}
      <p class="note">Operating company: ${company}</p>
    </section>`;
}

function inspectionSection(p: FacilityProps): string {
  const i = p.inspection;
  if (!i.last_rating && !i.last_end_date) {
    return `
      <section>
        <h3>Inspections</h3>
        <p class="absent">${NOT_REPORTED} — no inspection appears for this facility in
        ICE's current release.</p>
      </section>`;
  }
  const cls = ratingClass(i.last_rating);
  return `
    <section>
      <h3>Inspections</h3>
      <p class="rating ${cls}">${escapeHtml(text(i.last_rating))}</p>
      ${dl([
        ['Date', date(i.last_end_date)],
        ['Inspection type', text(i.last_type)],
        ['Standard applied', text(i.last_standard)],
      ])}
    </section>`;
}

function contextSection(p: FacilityProps): string {
  return `
    <section>
      <h3>Legal and geographic context</h3>
      ${dl([
        ['County', text(p.county)],
        ['Federal district of confinement', text(p.federal_court_district)],
        ['Federal circuit', text(p.federal_court_circuit)],
        [
          'Average length of stay (ICE)',
          p.avg_length_of_stay_days !== null
            ? `${num(p.avg_length_of_stay_days, 1)} days`
            : NOT_REPORTED,
        ],
        ['Sex designation', text(p.sex_designation)],
      ])}
      <p class="note">
        The district of confinement is where the facility sits — one factor in venue for
        a habeas petition, though not by itself decisive.
      </p>
    </section>`;
}

function notReportedSection(p: FacilityProps): string {
  const items = Object.entries(p.not_publicly_reported);
  if (!items.length) return '';
  return `
    <section class="gaps">
      <h3>What nobody publishes</h3>
      ${items
        .map(
          ([field, why]) => `
        <div class="gap">
          <span class="gap-name">${escapeHtml(titleCase(field.replace(/_/g, ' ')))}</span>
          <p>${escapeHtml(why)}</p>
        </div>`,
        )
        .join('')}
    </section>`;
}

function provenanceSection(p: FacilityProps): string {
  const rows = Object.entries(p.sources)
    .filter(([, v]) => v)
    .map(
      ([fields, src]) => `
      <tr>
        <td class="fields">${escapeHtml(fields)}</td>
        <td>${sourceLink(src!)}</td>
        <td class="asof">${date(src!.as_of)}</td>
      </tr>`,
    )
    .join('');
  return `
    <section class="provenance">
      <h3>Where these numbers came from</h3>
      <table>
        <thead><tr><th>Fields</th><th>Source</th><th>As of</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="note">
        Different fields carry different dates on purpose. They are not blended into a
        single figure, because they do not describe the same period.
      </p>
    </section>`;
}

function sourceLink(src: SourceRef): string {
  const label = escapeHtml(src.source);
  return src.url
    ? `<a href="${escapeHtml(src.url)}" target="_blank" rel="noopener noreferrer">${label}</a>`
    : label;
}

function sourceNote(label: string, sources: Array<SourceRef | null | undefined>): string {
  const available = sources.filter((src): src is SourceRef => Boolean(src));
  if (!available.length) return '';
  const items = available
    .map((src) => `${sourceLink(src)}${src.as_of ? ` (as of ${date(src.as_of)})` : ''}`)
    .join('; ');
  return `<p class="section-source"><strong>${escapeHtml(label)}:</strong> ${items}.</p>`;
}

function dl(rows: Array<[string, string]>): string {
  return `<dl>${rows
    .map(
      ([k, v]) =>
        `<div><dt>${escapeHtml(k)}</dt><dd class="${
          v === NOT_REPORTED ? 'absent-inline' : ''
        }">${v}</dd></div>`,
    )
    .join('')}</dl>`;
}
