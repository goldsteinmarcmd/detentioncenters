/** Consent-gated client for the shared first-party analytics platform. */

const SITE_ID = 'detentioncenters';
const SESSION_TIMEOUT_MS = 30 * 60 * 1000;

let collectUrl = '';
let privacyUrl = 'privacy.html';
let utm: Record<string, string | null> = {};
let pageEnteredAt = 0;
let engagementStarted = 0;
let engaged = false;
let banner: HTMLElement | null = null;

function key(suffix: string): string {
  return `analytics_${SITE_ID}_${suffix}`;
}

function read(name: string): string | null {
  try {
    return localStorage.getItem(name);
  } catch {
    return null;
  }
}

function write(name: string, value: string): void {
  try {
    localStorage.setItem(name, value);
  } catch {
    // Private browsing may disable storage; events still fail closed.
  }
}

function consent(): string | null {
  return read(key('consent'));
}

function setConsent(value: 'granted' | 'denied'): void {
  write(key('consent'), value);
  banner?.remove();
  banner = null;
  document.getElementById('consent-nudge')?.remove();
  if (value === 'granted') track('page_view');
  else showNudge();
}

function uuid(): string {
  if (crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const random = (Math.random() * 16) | 0;
    const value = char === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

function ids(): { clientId: string; sessionId: string; isNewSession: boolean } {
  let clientId = read(key('cid'));
  if (!clientId) {
    clientId = uuid();
    write(key('cid'), clientId);
  }

  const now = Date.now();
  let sessionId = read(key('sid'));
  const timestamp = Number(read(key('sid_ts')) || 0);
  let isNewSession = false;
  if (!sessionId || !timestamp || now - timestamp > SESSION_TIMEOUT_MS) {
    sessionId = uuid();
    isNewSession = true;
  }
  write(key('sid'), sessionId);
  write(key('sid_ts'), String(now));
  return { clientId, sessionId, isNewSession };
}

function send(
  eventName: string,
  clientId: string,
  sessionId: string,
  eventParams: Record<string, string | number | boolean>,
): void {
  if (!collectUrl) return;
  const payload = JSON.stringify({
    site_id: SITE_ID,
    event_name: eventName,
    client_id: clientId,
    session_id: sessionId,
    page_location: location.href,
    page_title: document.title,
    page_referrer: document.referrer || '',
    utm_source: utm.source,
    utm_medium: utm.medium,
    utm_campaign: utm.campaign,
    utm_content: utm.content,
    utm_term: utm.term,
    engaged,
    engagement_ms: Math.max(0, Date.now() - pageEnteredAt),
    event_params: eventParams,
  });
  const endpoint = `${collectUrl.replace(/\/$/, '')}/collect`;
  fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
    body: payload,
    keepalive: true,
    mode: 'cors',
    credentials: 'omit',
  }).catch(() => {
    try {
      navigator.sendBeacon?.(
        endpoint,
        new Blob([payload], { type: 'text/plain;charset=UTF-8' }),
      );
    } catch {
      // Analytics must never interrupt the map.
    }
  });
}

export function track(
  eventName: string,
  eventParams: Record<string, string | number | boolean> = {},
): void {
  if (consent() !== 'granted' || !collectUrl) return;
  const { clientId, sessionId, isNewSession } = ids();
  if (isNewSession) send('session_start', clientId, sessionId, {});
  send(eventName, clientId, sessionId, eventParams);
}

function showBanner(): void {
  if (banner) return;
  banner = document.createElement('div');
  banner.id = 'consent-banner';
  banner.setAttribute('role', 'dialog');
  banner.setAttribute('aria-label', 'Analytics consent');
  banner.innerHTML = `
    <div class="consent-inner">
      <p>Optional first-party analytics help count page views and clicks. No ads,
         sale of data, search text, or detainee information.
         <a href="${privacyUrl}">Privacy</a></p>
      <div class="consent-actions">
        <button type="button" data-consent="denied" class="consent-decline">Decline</button>
        <button type="button" data-consent="granted" class="consent-accept">Accept</button>
      </div>
    </div>`;
  banner.addEventListener('click', (event) => {
    const button = (event.target as HTMLElement).closest<HTMLElement>('[data-consent]');
    const value = button?.dataset.consent;
    if (value === 'granted' || value === 'denied') setConsent(value);
  });
  document.body.appendChild(banner);
}

function showNudge(): void {
  if (document.getElementById('consent-nudge')) return;
  const nudge = document.createElement('button');
  nudge.id = 'consent-nudge';
  nudge.className = 'consent-nudge';
  nudge.type = 'button';
  nudge.textContent = 'Enable analytics';
  nudge.addEventListener('click', () => {
    nudge.remove();
    try {
      localStorage.removeItem(key('consent'));
    } catch {
      // Ignore unavailable storage.
    }
    showBanner();
  });
  document.body.appendChild(nudge);
}

function installClickTracking(): void {
  document.addEventListener('click', (event) => {
    const element =
      event.target instanceof Element
        ? event.target.closest<HTMLElement>(
            'a, button, input, select, summary, [role="button"]',
          )
        : null;
    if (!element || element.closest('#consent-banner')) return;
    const anchor = element.closest<HTMLAnchorElement>('a');
    let outboundHost = '';
    if (anchor?.href) {
      try {
        const url = new URL(anchor.href, location.href);
        if (url.origin !== location.origin) outboundHost = url.hostname;
      } catch {
        // Ignore malformed destinations.
      }
    }
    const target =
      element.dataset.analyticsLabel ||
      element.id ||
      [element.tagName.toLowerCase(), ...element.classList].slice(0, 3).join('.');
    track('click', { target: target.slice(0, 120), outbound_host: outboundHost });
  });
}

function flushEngagement(): void {
  if (consent() !== 'granted') return;
  const elapsed = Date.now() - engagementStarted;
  if (elapsed < 1000) return;
  if (elapsed >= 10_000) engaged = true;
  track('user_engagement', { engagement_ms: Math.round(elapsed) });
}

export function initAnalytics(options: {
  collectUrl: string;
  privacyUrl: string;
}): void {
  collectUrl = options.collectUrl;
  privacyUrl = options.privacyUrl;
  pageEnteredAt = Date.now();
  engagementStarted = Date.now();
  const params = new URLSearchParams(location.search);
  utm = {
    source: params.get('utm_source'),
    medium: params.get('utm_medium'),
    campaign: params.get('utm_campaign'),
    content: params.get('utm_content'),
    term: params.get('utm_term'),
  };

  if (collectUrl) {
    if (consent() === 'granted') track('page_view');
    else if (consent() === 'denied') showNudge();
    else showBanner();
  }
  installClickTracking();
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushEngagement();
    else engagementStarted = Date.now();
  });
  window.addEventListener('pagehide', flushEngagement);
}
