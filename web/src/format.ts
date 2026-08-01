/**
 * Rendering rules for values that may not exist.
 *
 * The whole point of the project is that a missing number is never dressed up as a
 * real one. Null means "not published" and says so; zero means "published as zero"
 * and renders as 0. These two must never collapse into the same output.
 */

import { isSuppressed, type Count } from './types';

export const NOT_REPORTED = 'Not publicly reported';

/** ADP values are fractional averages — round only here, at render time. */
export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NOT_REPORTED;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Preserve small non-zero averages instead of rounding them to a misleading zero. */
export function average(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NOT_REPORTED;
  const magnitude = Math.abs(value);
  const digits = magnitude === 0 ? 0 : magnitude < 0.1 ? 3 : 1;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: magnitude >= 0.1 ? 1 : 0,
    maximumFractionDigits: digits,
  });
}

export function count(value: Count | null | undefined): string {
  if (isSuppressed(value)) return `<${value.lt}`;
  if (value === null || value === undefined) return NOT_REPORTED;
  return value.toLocaleString('en-US');
}

/** Suppressed cells carry no usable magnitude, so they contribute nothing to a bar. */
export function countValue(value: Count | null | undefined): number {
  if (isSuppressed(value)) return 0;
  return typeof value === 'number' ? value : 0;
}

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return NOT_REPORTED;
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NOT_REPORTED;
  return value.toLocaleString('en-US', {
    style: 'percent',
    maximumFractionDigits: 1,
  });
}

export function date(value: string | null | undefined): string {
  if (!value) return NOT_REPORTED;
  const d = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

export function text(value: string | null | undefined): string {
  return value && value.trim() ? value : NOT_REPORTED;
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .replace(/\b[a-z]/g, (c) => c.toUpperCase())
    .replace(/\bIce\b/g, 'ICE')
    .replace(/\bUs\b/g, 'US');
}

/** ICE publishes contract structure, not company. Spell the codes out. */
const CONTRACT_LABELS: Record<string, string> = {
  IGSA: 'Intergovernmental Service Agreement (local government)',
  DIGSA: 'Dedicated Intergovernmental Service Agreement',
  CDF: 'Contract Detention Facility (private operator)',
  'USMS IGA': 'US Marshals intergovernmental agreement',
  'USMS CDF': 'US Marshals contract detention facility',
  BOP: 'Federal Bureau of Prisons',
  SPC: 'Service Processing Center (ICE-owned)',
  STAGING: 'Staging facility',
  STATE: 'State facility',
  FAMILY: 'Family residential centre',
};

export function contractLabel(code: string | null): string {
  if (!code) return NOT_REPORTED;
  return CONTRACT_LABELS[code] ?? code;
}

export function ratingClass(rating: string | null): 'pass' | 'fail' | 'none' {
  if (!rating) return 'none';
  if (/fail/i.test(rating)) return 'fail';
  if (/pass|meets|acceptable|superior|good/i.test(rating)) return 'pass';
  return 'none';
}

export function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string,
  );
}
