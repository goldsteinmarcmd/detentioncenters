/** Lodging search links and the optional server-backed hotel API contract. */

import type { Point } from './directions';

export interface StayDates {
  checkin: string;
  checkout: string;
}

export interface LodgingResult {
  id: string;
  name: string;
  url: string;
  distance_miles?: number | null;
  price?: string | null;
  rating?: number | null;
}

export interface LodgingResponse {
  results: LodgingResult[];
  provider?: string;
  currency?: string;
}

export interface LodgingAffiliateConfig {
  bookingAffiliateId?: string;
  airbnbTrackingTemplate?: string;
  expediaTrackingTemplate?: string;
}

function localIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function defaultStayDates(today = new Date()): StayDates {
  return {
    checkin: localIsoDate(addDays(today, 1)),
    checkout: localIsoDate(addDays(today, 2)),
  };
}

export function nextDate(date: string): string {
  const [year, month, day] = date.split('-').map(Number);
  return localIsoDate(addDays(new Date(year, month - 1, day), 1));
}

function affiliateUrl(target: string, template?: string): string {
  if (!template) return target;
  try {
    const expanded = template.includes('{url}')
      ? template.replaceAll('{url}', encodeURIComponent(target))
      : template;
    const url = new URL(expanded);
    return url.protocol === 'https:' ? url.toString() : target;
  } catch {
    return target;
  }
}

export function airbnbUrl(
  destination: string,
  dates: StayDates,
  trackingTemplate?: string,
): string {
  const path = encodeURIComponent(destination).replaceAll('%20', '-');
  const params = new URLSearchParams({
    checkin: dates.checkin,
    checkout: dates.checkout,
  });
  params.append('refinement_paths[]', '/homes');
  const target = `https://www.airbnb.com/s/${path}/homes?${params}`;
  return affiliateUrl(target, trackingTemplate);
}

export function bookingUrl(
  destination: string,
  dates: StayDates,
  affiliateId?: string,
): string {
  const params = new URLSearchParams({
    ss: destination,
    checkin: dates.checkin,
    checkout: dates.checkout,
    group_adults: '1',
    no_rooms: '1',
    group_children: '0',
  });
  if (affiliateId && /^\d+$/.test(affiliateId)) params.set('aid', affiliateId);
  return `https://www.booking.com/searchresults.html?${params}`;
}

export function expediaUrl(
  destination: string,
  dates: StayDates,
  trackingTemplate?: string,
): string {
  const params = new URLSearchParams({
    destination,
    startDate: dates.checkin,
    endDate: dates.checkout,
    adults: '1',
    rooms: '1',
  });
  const target = `https://www.expedia.com/Hotel-Search?${params}`;
  return affiliateUrl(target, trackingTemplate);
}

export async function fetchNearbyLodging(
  apiUrl: string,
  point: Point,
  dates: StayDates,
  signal?: AbortSignal,
): Promise<LodgingResponse> {
  const url = new URL(apiUrl);
  url.searchParams.set('lat', String(point.lat));
  url.searchParams.set('lon', String(point.lon));
  url.searchParams.set('checkin', dates.checkin);
  url.searchParams.set('checkout', dates.checkout);

  const response = await fetch(url, { signal, headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`Lodging service returned ${response.status}.`);
  const payload = (await response.json()) as LodgingResponse;
  if (!Array.isArray(payload.results)) throw new Error('Lodging service response is invalid.');
  return payload;
}
