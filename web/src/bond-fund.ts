import { escapeHtml } from './format';

export interface BondCampaign {
  case_id: string;
  region: string | null;
  bond_amount: number;
  amount_raised: number;
  booking_category: {
    code: string;
    label: string;
    note: string;
  };
  verified_on: string;
  verification_expires_on: string;
  public_summary: string | null;
  contribution_url: string | null;
  contribution_recipient: string;
  terms_url: string;
  status:
    | 'accepting'
    | 'funded'
    | 'bond_posted'
    | 'released'
    | 'paused'
    | 'verification_expired';
}

export interface BondCampaignCollection {
  metadata: {
    built_at: string;
    privacy: string;
  };
  cases: BondCampaign[];
}

const dollars = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

const shortDate = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  timeZone: 'UTC',
});

const STATUS_LABELS: Record<BondCampaign['status'], string> = {
  accepting: 'Accepting contributions',
  funded: 'Bond fully funded',
  bond_posted: 'Bond posted',
  released: 'Released',
  paused: 'Contributions paused',
  verification_expired: 'Verification expired',
};

function safeHttpsUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function date(value: string): string {
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.valueOf()) ? value : shortDate.format(parsed);
}

function campaignCard(item: BondCampaign): string {
  const contributionUrl = safeHttpsUrl(item.contribution_url);
  const termsUrl = safeHttpsUrl(item.terms_url);
  const remaining = Math.max(0, item.bond_amount - item.amount_raised);
  const progress = item.bond_amount
    ? Math.min(100, Math.round((item.amount_raised / item.bond_amount) * 100))
    : 0;
  const canContribute = item.status === 'accepting' && contributionUrl;

  return `
    <article class="bond-case">
      <div class="bond-case-head">
        <div>
          <p class="bond-case-id">${escapeHtml(item.case_id)}</p>
          <h3>${escapeHtml(item.region ?? 'Location withheld')}</h3>
        </div>
        <span class="bond-status ${escapeHtml(item.status)}">
          ${escapeHtml(STATUS_LABELS[item.status])}
        </span>
      </div>

      ${item.public_summary ? `<p class="bond-story">${escapeHtml(item.public_summary)}</p>` : ''}

      <dl class="bond-amounts">
        <div><dt>Verified bond</dt><dd>${dollars.format(item.bond_amount)}</dd></div>
        <div><dt>Raised</dt><dd>${dollars.format(item.amount_raised)}</dd></div>
        <div><dt>Remaining</dt><dd>${dollars.format(remaining)}</dd></div>
      </dl>
      <div class="bond-progress" aria-label="${progress}% funded">
        <span style="width:${progress}%"></span>
      </div>

      <div class="bond-classification">
        <span>Reported case classification</span>
        <strong>${escapeHtml(item.booking_category.label)}</strong>
        <p>${escapeHtml(item.booking_category.note)}</p>
      </div>

      <p class="bond-verified">
        Bond verified ${escapeHtml(date(item.verified_on))}; re-verification due
        ${escapeHtml(date(item.verification_expires_on))}.
      </p>

      <div class="bond-case-actions">
        ${
          canContribute
            ? `<a class="bond-contribute" href="${escapeHtml(contributionUrl)}"
                 target="_blank" rel="noopener noreferrer">Contribute toward bond</a>`
            : `<button class="bond-contribute" type="button" disabled>Contributions unavailable</button>`
        }
        ${
          termsUrl
            ? `<a class="bond-terms" href="${escapeHtml(termsUrl)}" target="_blank"
                 rel="noopener noreferrer">Fund terms</a>`
            : ''
        }
      </div>
      <p class="bond-recipient">
        Contributions are received by ${escapeHtml(item.contribution_recipient)},
        which is responsible for posting the bond and administering funds.
      </p>
    </article>`;
}

export function renderBondFund(collection: BondCampaignCollection): string {
  const active = collection.cases.filter((item) => item.status === 'accepting');
  const activeBond = active.reduce((sum, item) => sum + item.bond_amount, 0);
  const activeRaised = active.reduce((sum, item) => sum + item.amount_raised, 0);

  const summary = `
    <div><strong>${active.length.toLocaleString()}</strong><span>Open cases</span></div>
    <div><strong>${dollars.format(activeBond)}</strong><span>Verified bond</span></div>
    <div><strong>${dollars.format(activeRaised)}</strong><span>Raised</span></div>`;

  const cases = collection.cases.length
    ? collection.cases.map(campaignCard).join('')
    : `<div class="bond-empty">
         <h3>No verified cases are accepting contributions yet</h3>
         <p>The first case will appear after consent, current bond verification,
            and a case-specific contribution link are complete.</p>
       </div>`;

  return `${summary}<div class="bond-case-grid">${cases}</div>`;
}
