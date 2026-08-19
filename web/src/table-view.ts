/**
 * The facility table — the map's equal, not its fallback.
 *
 * The map is the only view that can show where facilities cluster, and it is also the
 * one view a screen reader and a keyboard cannot read at all: a canvas of dots carries
 * no text. Everything the map encodes visually — inspection result, population,
 * whether the position is a real address or a city centroid — is a column here, on the
 * same filtered set, sortable, and reachable with the keyboard alone.
 *
 * Sorting is done here rather than by the sidebar's fixed ADP order because the
 * comparison the map makes easy — "which of these is the big one" — has to be
 * available without sight of the dots.
 */

import { average, contractLabel, escapeHtml, ratingClass, titleCase } from './format';
import type { FacilityProps } from './types';

export type SortKey = 'name' | 'place' | 'contract' | 'adp' | 'rating';
export type SortDir = 'asc' | 'desc';

export interface TableSort {
  key: SortKey;
  dir: SortDir;
}

/** ADP leads, matching the sidebar, so switching views does not reshuffle the list. */
export const DEFAULT_SORT: TableSort = { key: 'adp', dir: 'desc' };

interface Column {
  key: SortKey;
  label: string;
  /** Numeric columns sort high-to-low on first click; text columns A-to-Z. */
  firstDir: SortDir;
  numeric?: boolean;
}

const COLUMNS: readonly Column[] = [
  { key: 'name', label: 'Facility', firstDir: 'asc' },
  { key: 'place', label: 'City and state', firstDir: 'asc' },
  { key: 'contract', label: 'Contract type', firstDir: 'asc' },
  { key: 'adp', label: 'Avg. daily population', firstDir: 'desc', numeric: true },
  { key: 'rating', label: 'Last inspection', firstDir: 'asc' },
];

function place(f: FacilityProps): string {
  return [f.city, f.state].filter(Boolean).join(', ');
}

function sortValue(f: FacilityProps, key: SortKey): string | number | null {
  switch (key) {
    case 'name':
      return titleCase(f.name ?? f.code).toLowerCase();
    case 'place':
      return place(f).toLowerCase();
    case 'contract':
      return f.operator.contract_type?.toLowerCase() ?? null;
    case 'adp':
      return f.adp;
    case 'rating':
      return f.inspection.last_rating?.toLowerCase() ?? null;
  }
}

/**
 * Missing values sort last in both directions.
 *
 * Reversing them with the sort would put "not published" at the top of a
 * descending population sort, which reads as the largest facilities and is the one
 * mistake this project is least willing to make.
 */
export function sortFacilities(list: FacilityProps[], sort: TableSort): FacilityProps[] {
  const factor = sort.dir === 'asc' ? 1 : -1;
  return list.slice().sort((a, b) => {
    const av = sortValue(a, sort.key);
    const bv = sortValue(b, sort.key);
    if (av === null || av === '') return bv === null || bv === '' ? 0 : 1;
    if (bv === null || bv === '') return -1;
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
    return String(av).localeCompare(String(bv)) * factor;
  });
}

function ariaSort(column: Column, sort: TableSort): string {
  if (column.key !== sort.key) return 'none';
  return sort.dir === 'asc' ? 'ascending' : 'descending';
}

/** The direction a header click should produce next. */
function nextDir(column: Column, sort: TableSort): SortDir {
  if (column.key !== sort.key) return column.firstDir;
  return sort.dir === 'asc' ? 'desc' : 'asc';
}

export function renderTable(list: FacilityProps[], sort: TableSort): string {
  const rows = sortFacilities(list, sort);

  const head = COLUMNS.map((c) => {
    const next = nextDir(c, sort);
    const direction = next === 'asc' ? 'ascending' : 'descending';
    return `<th scope="col" aria-sort="${ariaSort(c, sort)}"${c.numeric ? ' class="num"' : ''}>
        <button class="th-sort" type="button" data-sort="${c.key}" data-dir="${next}"
          aria-label="${escapeHtml(c.label)}, sort ${direction}">
          <span>${escapeHtml(c.label)}</span>
          <span class="th-arrow" aria-hidden="true">${
            c.key === sort.key ? (sort.dir === 'asc' ? '↑' : '↓') : '↕'
          }</span>
        </button>
      </th>`;
  }).join('');

  const body = rows
    .map((f) => {
      const rating = f.inspection.last_rating;
      return `<tr>
        <th scope="row">
          <button class="table-name" type="button" data-code="${escapeHtml(f.code)}">
            ${escapeHtml(titleCase(f.name ?? f.code))}
          </button>
        </th>
        <td>${escapeHtml(place(f)) || 'Not published'}${
          f.approx === 1
            ? ' <span class="approx-tag">approximate location</span>'
            : ''
        }</td>
        <td>${escapeHtml(contractLabel(f.operator.contract_type))}</td>
        <td class="num">${f.adp !== null ? escapeHtml(average(f.adp)) : 'Not reported'}</td>
        <td><span class="rating-cell ${ratingClass(rating)}">${
          rating ? escapeHtml(rating) : 'No inspection published'
        }</span></td>
      </tr>`;
    })
    .join('');

  return `
    <table class="facility-table">
      <caption>
        ${rows.length.toLocaleString()} facilit${rows.length === 1 ? 'y' : 'ies'} matching
        the current filters. Select a facility name for its full record.
      </caption>
      <thead><tr>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>`;
}
