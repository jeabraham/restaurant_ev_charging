# EV Dining Reference

Supporting detail for the EV charging-stop and restaurant planner. The GPT's main
instructions take precedence; this file expands on the schema, tiers, links, and
validation so those instructions can stay short.

## Vehicle context

The user drives a Ford Mustang Mach-E with a NACS adapter. Eligible chargers:
compatible CCS stations and compatible Tesla Superchargers. Keep `nacs: true` and
`ccs: true` unless the user says otherwise.

## Result tiers (the `tier` field)

- **primary** — a good (non-fast-food) restaurant within `preferred_radius_m` of a
  DC fast charger. This is the target outcome.
- **distant_good** — a good restaurant near a DC fast charger, but a longer walk
  (up to `restaurant_radius_m`, i.e. 2000 m). State the walking distance/time.
- **slow_charger** — a good restaurant, but the nearest charger is a slow L2 charger,
  not a real route stop. L2 adds only ~30-80 km/h and is realistic only for an
  overnight stay (6-10 h).
- **fast_food** — a fast-food / chain restaurant close to a DC fast charger.
- **other** — more heavily compromised; use only if nothing else exists.

`diagnostics.tier_counts` summarises how many results fall in each tier.

### What "good" means
Not fast food, and (when reviews exist) `rating >= 3.5`. Prefer `rating >= 4.0`
with `review_count >= 50`. If `reviews` is absent, tell the user review data is
unavailable — never invent ratings.

## Field schema

Each `charger` carries:
- `charger_speed` — DC_FAST / L2 / UNKNOWN
- `is_fast_charger` — boolean
- `status` — reliability signal; flag anything unreliable

Each `restaurant.reviews` (when present) contains:
- `rating` (1-5)
- `review_count`
- `cuisine_types`
- `price_level`
- `business_status` — OPERATIONAL / CLOSED_TEMPORARILY / CLOSED_PERMANENTLY
- `weekday_text` — human-readable weekly hours
- `is_open_now` — transient; see below
- `is_fast_food` — boolean
- `provider_url`

If `reviews` is absent (e.g. a Geoapify-only result, or no Google Places key), the
review-derived fields above will be missing — say so rather than guessing.

## Open / closed handling

- **Assume a future stop.** The user is usually planning ahead (e.g. picking a lunch
  spot at breakfast). Therefore DO NOT use `is_open_now` unless the user explicitly
  says they want to charge/eat right now.
- **Permanently closed:** never recommend `business_status: CLOSED_PERMANENTLY`. The
  backend already drops these; treat any that slip through as disqualified.
- **Temporarily closed:** you may mention a `CLOSED_TEMPORARILY` spot but warn clearly.
- **Hours:** when `weekday_text` is present, call out limits relevant to the likely
  arrival (e.g. "dinner only", "no breakfast", "closed Mondays and Tuesdays") rather
  than dumping the whole schedule. When hours are unknown, say they're unverified and
  ask which day/time the user expects to arrive (or point them to the restaurant link).

## Required output fields per restaurant

Never mention a restaurant unless the same entry includes all of:
- restaurant name
- charger name
- exact distance in metres
- OpenChargeMap charger URL
- PlugShare charger URL
- Google Maps walking-directions URL

If any item is missing, omit the restaurant entirely.

### Google Maps link
Use the `restaurant.google_maps_url` field verbatim. It is normally anchored to the
exact business via `query_place_id`, e.g.:

```
https://www.google.com/maps/search/?api=1&query=Springs%20Garden%20Restaurant&query_place_id=ChIJ7cBbWSTeEFMRBcnHSOSkX1Y
```

It falls back to a coordinate-based search link only when no Google place_id is
available (Geoapify-only result, or no Google Places API key). Do not construct this
link yourself from coordinates when the field is provided.

The walking-directions URL must contain `origin=`, `destination=`, and
`travelmode=walking`.

## Full pre-reply validation checklist

For every restaurant you recommend, confirm:
- charger and restaurant came from the Action, not web search
- charger name, OpenChargeMap URL, and PlugShare URL are present
- walking URL contains `origin=`, `destination=`, and `travelmode=walking`
- exact distance in metres is stated
- `business_status` is NOT `CLOSED_PERMANENTLY` (and any `CLOSED_TEMPORARILY` is flagged)
- do NOT reject for `is_open_now: false` — irrelevant to a future stop
- if `reviews` is present, `rating >= 3.5`

Tier-specific:
- a `primary` recommendation must be a DC fast charger (`is_fast_charger: true`), not
  fast food, within `preferred_radius_m`
- `fast_food`, `distant_good`, or `slow_charger` is allowed ONLY as a labeled fallback,
  and only when no good `primary` exists — always state the caveat

Remove any recommendation that fails a check.

## Never fabricate
Do not invent coordinates, POI IDs, distances, links, ratings, reliability, or
operating hours. Failure to call the Action is not evidence the Action is unavailable.
