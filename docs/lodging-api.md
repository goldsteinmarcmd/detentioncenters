# Lodging integration

The GitHub Pages frontend can open date-aware searches on Airbnb and Booking.com with
no credentials. Live hotel cards require a server-side adapter because provider keys
must never be included in a Vite build.

## Provider access

- [Airbnb API access](https://www.airbnb.com/help/article/3418) is scoped to approved
  API programs. Keep Airbnb as an outbound search unless Airbnb grants this project
  partner access for consumer inventory.
- [Booking.com Demand API](https://developers.booking.com/demand/docs/accommodations/about-accommodation)
  and [Expedia Rapid](https://developers.expediagroup.com/rapid/api/explorer) are
  suitable hotel inventory providers.
  Apply for one provider first; Booking.com's redirect flow is the smallest initial
  integration.

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
