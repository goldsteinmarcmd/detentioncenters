# Lodging integration

The GitHub Pages frontend can open date-aware searches on Airbnb and Booking.com with
no credentials. Live hotel cards require a server-side adapter because provider keys
must never be included in a Vite build.

The live comparison is currently disabled in production because
`VITE_LODGING_API_URL` is not configured. Outbound date-aware searches do not require
that adapter and remain available.

## Provider access

- [Airbnb API access](https://www.airbnb.com/help/article/3418) is scoped to approved
  API programs. Keep Airbnb as an outbound search unless Airbnb grants this project
  partner access for consumer inventory.
- [Booking.com Demand API](https://developers.booking.com/demand/docs/accommodations/about-accommodation)
  and [Expedia Rapid](https://developers.expediagroup.com/rapid/api/explorer) are
  suitable hotel inventory providers.
  Apply for one provider first; Booking.com's redirect flow is the smallest initial
  integration.

## Affiliate attribution

Affiliate revenue is enabled only after the corresponding program approves the site.
Set GitHub Actions repository variables rather than committing identifiers:

- `VITE_BOOKING_AFFILIATE_ID`: numeric Booking.com affiliate ID; outbound search links
  receive the official `aid` parameter.
- `VITE_AIRBNB_AFFILIATE_TEMPLATE`: exact approved Airbnb/Impact tracking URL. Use
  `{url}` where the encoded Airbnb destination URL belongs. Do not construct or modify
  tracking parameters outside Airbnb's instructions.
- `VITE_EXPEDIA_AFFILIATE_TEMPLATE`: exact Expedia link-builder URL, with `{url}` where
  the encoded Expedia search URL belongs.

When any value is configured, the facility panel displays an affiliate disclosure.
Click analytics and provider reporting are different systems: the first measures an
outbound click; only the affiliate provider can confirm a qualifying completed stay and
commission.

## Frontend contract

Set `VITE_LODGING_API_URL` to the public URL of the adapter. The frontend sends:

```text
GET /nearby?lat=35.0000&lon=-97.0000&checkin=2026-08-02&checkout=2026-08-03
```

The adapter returns provider-normalized JSON:

```json
{
  "provider": "booking",
  "currency": "USD",
  "results": [
    {
      "id": "property-id",
      "name": "Example Hotel",
      "url": "https://provider.example/property",
      "distance_miles": 2.4,
      "price": "$128 total",
      "rating": 8.6
    }
  ]
}
```

The adapter owns credentials, provider authentication, rate limiting, response
normalization, caching, CORS restricted to the Pages origin, and removal of expired
prices. GitHub Pages receives only display-ready results and booking URLs.
