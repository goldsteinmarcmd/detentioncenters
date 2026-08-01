/**
 * Routing links to and from a facility.
 *
 * Three rules shape this:
 *
 * **Coordinates, not address strings.** DDP's coordinates are hand-verified; handing a
 * provider an address string invites it to geocode the place itself and put someone on
 * a road to the wrong building. Several facilities sit on unnamed rural roads where
 * that failure is likely rather than theoretical.
 *
 * **Only for exact locations.** A facility placed at a city centroid has no address to
 * route to, and offering turn-by-turn directions to a centroid would imply a precision
 * the data does not have. Those facilities get an explanation instead of a button.
 *
 * **Nothing leaves the page on its own.** A location is read only when the user asks
 * for it, is kept in memory for the session, and is only ever sent anywhere by the
 * user clicking a provider link they chose.
 */

export interface Point {
  lat: number;
  lon: number;
}

export interface Endpoint extends Point {
  /** Human-readable, shown in the UI. Never sent as the routing key. */
  label: string;
}

/** Remembered for the session so it survives moving between facilities. */
let cachedOrigin: Endpoint | null = null;

export function getCachedOrigin(): Endpoint | null {
  return cachedOrigin;
}

export function setCachedOrigin(origin: Endpoint | null): void {
  cachedOrigin = origin;
}

export function coordString(p: Point): string {
  return `${p.lat.toFixed(6)},${p.lon.toFixed(6)}`;
}

/**
 * A free-text place the user typed. Passed through to the provider verbatim so it can
 * geocode it — we deliberately do not attempt to resolve addresses ourselves.
 */
export interface TypedPlace {
  text: string;
}

export type Waypoint = Endpoint | TypedPlace;

function asQuery(w: Waypoint): string {
  return 'text' in w ? w.text : coordString(w);
}

export function isTyped(w: Waypoint): w is TypedPlace {
  return 'text' in w;
}

export function googleUrl(from: Waypoint | null, to: Waypoint, mode = 'driving'): string {
  const params = new URLSearchParams({ api: '1', destination: asQuery(to), travelmode: mode });
  if (from) params.set('origin', asQuery(from));
  return `https://www.google.com/maps/dir/?${params}`;
}

export function appleUrl(from: Waypoint | null, to: Waypoint): string {
  const params = new URLSearchParams({ daddr: asQuery(to), dirflg: 'd' });
  if (from) params.set('saddr', asQuery(from));
  return `https://maps.apple.com/?${params}`;
}

export function osmUrl(from: Waypoint | null, to: Waypoint): string {
  const params = new URLSearchParams({ route: `${from ? asQuery(from) : ''};${asQuery(to)}` });
  return `https://www.openstreetmap.org/directions?${params}`;
}

/** Great-circle distance. Straight-line — real driving distance is always longer. */
export function haversineMiles(a: Point, b: Point): number {
  const R = 3958.8;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.sin(dLon / 2) ** 2 * Math.cos(lat1) * Math.cos(lat2);
  return 2 * R * Math.asin(Math.sqrt(h));
}

export function formatMiles(miles: number): string {
  if (miles < 10) return `${miles.toFixed(1)} miles`;
  return `${Math.round(miles).toLocaleString('en-US')} miles`;
}

export class GeolocationUnavailable extends Error {}

/**
 * Read the browser's location, on explicit user action only.
 *
 * The browser shows its own permission prompt; nothing is read without it, and the
 * result stays in this tab.
 */
export function locateUser(timeoutMs = 10_000): Promise<Endpoint> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new GeolocationUnavailable('This browser does not offer location access.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const origin: Endpoint = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          label: 'Your location',
        };
        cachedOrigin = origin;
        resolve(origin);
      },
      (err) => {
        const message =
          err.code === err.PERMISSION_DENIED
            ? 'Location permission was declined. You can type a starting point instead.'
            : err.code === err.TIMEOUT
              ? 'Timed out finding your location. You can type a starting point instead.'
              : 'Could not determine your location. You can type a starting point instead.';
        reject(new GeolocationUnavailable(message));
      },
      { enableHighAccuracy: false, timeout: timeoutMs, maximumAge: 300_000 },
    );
  });
}
