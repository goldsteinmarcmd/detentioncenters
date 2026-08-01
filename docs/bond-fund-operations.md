# Bond release fund operations

The public site can raise money for a specific person without publishing that person's
identity. It is the public campaign layer of a larger process; it is not a confidential
case-management system or an ICE payment processor.

## Ownership

Every campaign needs a referring organization or fiscal sponsor that accepts the
contributions, verifies the case, posts the bond through the official process, maintains
the confidential identity record, and publishes its rules for refunds and unused funds.
The organization named in `contribution_recipient` owns those responsibilities.

Confidential records must never be stored in this repository, in GitHub Actions, in the
daily worker's public outputs, or in a URL. This includes names, A-Numbers, birth dates,
contact details, identity documents, court documents, and payment credentials.

## Verification checklist

Before setting a campaign to `accepting`, the responsible partner confirms:

1. The person or their authorized representative consented to the anonymous campaign.
2. The person is currently detained and is eligible to have this bond posted.
3. The bond amount is current and the correct ICE office or other authorized payment
   location is known.
4. The reported booking category comes from the reviewed case record and is not inferred
   from the map's historical aggregate data.
5. The contribution link is case-specific, controlled by the named recipient, and its
   terms explain refunds, excess funds, fees, and what happens if release is blocked.
6. `verification_expires_on` is soon enough that a stale amount cannot continue taking
   contributions. Seven days is the recommended maximum for an open campaign.

The builder removes the contribution link when verification expires. Re-verification
requires changing both verification dates in the source CSV and rebuilding the feed.

## Public fields

`pipeline/data/manual/bond_campaigns.csv` accepts only publishable values:

| Field | Meaning |
| --- | --- |
| `case_id` | Random public ID such as `BF-7K2M9Q`; never derive it from an A-Number. |
| `region` | Broad, consented location such as a state; omit when it could identify someone. |
| `bond_amount` | Current verified bond in whole dollars. |
| `amount_raised` | Contributions credited to this campaign in whole dollars. |
| `booking_category` | `conviction_reported`, `pending_charge_reported`, `immigration_only`, or `not_reported`. |
| `verified_on` | Date the partner confirmed the current bond and category. |
| `verification_expires_on` | Date after which contributions automatically close. |
| `public_summary` | Optional consented summary, maximum 360 characters, with no identifying details. |
| `contribution_url` | HTTPS case-specific checkout controlled by the recipient. |
| `contribution_recipient` | Public name of the organization receiving the money. |
| `terms_url` | HTTPS page explaining fees, refunds, and unused-fund policy. |
| `status` | `accepting`, `funded`, `bond_posted`, `released`, or `paused`. |
| `consent_confirmed` | Must be `TRUE`; the consent record remains with the partner. |

## Status changes

- `accepting`: verified and open for contributions.
- `funded`: the target has been reached; checkout is disabled.
- `bond_posted`: the responsible organization has posted the bond.
- `released`: the partner confirmed release.
- `paused`: a case worker has stopped contributions pending review.
- `verification_expired`: generated automatically when an accepting case passes its
  verification deadline; checkout is disabled.

The contribution processor remains the financial system of record. Update
`amount_raised` from its case-specific report; do not infer it from page visits or donor
messages.
