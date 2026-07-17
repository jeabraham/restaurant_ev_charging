# restaurant_ev_charging

A FastAPI service and interactive AI agent that finds restaurants within walking distance of compatible DC fast-charging stations — useful for planning EV road trips.

## Features

- Search for DC fast chargers (CCS, NACS/Tesla) near any location via [OpenChargeMap](https://openchargemap.org)
- Find restaurants within a configurable walking distance of each charger via [Geoapify](https://www.geoapify.com)
- Interactive natural-language agent powered by Gemini function calling
- Rate-limited REST API (20 requests/minute per IP) with OpenAPI schema
- Optional restaurant review enrichment (ratings, price, open-now) via Yelp Fusion or Google Places

## Requirements

- Python 3.10+
- [OpenChargeMap API key](https://openchargemap.org/site/develop#api)
- [Geoapify API key](https://www.geoapify.com)
- [Gemini API key](https://aistudio.google.com/app/apikey) — only needed for the AI agent
- [Yelp Fusion API key](https://docs.developer.yelp.com/docs/fusion-intro) — **optional**; enriches results with ratings, price levels, and open-now status. Yelp Fusion is a **commercial API** (paid plan required). Leave `YELP_API_KEY` blank to run without review data.
- [Google Places API key](https://developers.google.com/maps/documentation/places/web-service/overview) — **optional alternative** to Yelp; provides the same review enrichment and has a free monthly credit. Used automatically when `YELP_API_KEY` is not set.

## Setup

```bash
cp setup_example.env setup.env
# Edit setup.env and fill in your API keys
```

## Install

```bash
make install
```

This creates a `venv/` virtual environment and installs all dependencies.

## Run the API server

```bash
make run
```

The server starts at `http://127.0.0.1:8000`. Interactive docs at `http://127.0.0.1:8000/docs`.

## Example API request

```bash
curl -X POST "http://127.0.0.1:8000/find-dining-chargers" \
  -H "Content-Type: application/json" \
  -d '{
    "latitude": 51.467,
    "longitude": -109.156,
    "radius_km": 10,
    "restaurant_radius_m": 500,
    "nacs": true,
    "ccs": true,
    "tesla_only": false
  }'
```

## AI agent (Gemini)

An interactive CLI agent that lets you plan EV charging stops in natural language. It geocodes locations, calls the API, and recommends restaurants near chargers.

**Run agent only** (requires the server to already be running):

```bash
make run-agent
```

**Plan a trip** (starts the server automatically if needed, then runs the agent):

```bash
make plan-trip
```

You can specify a walking distance in your request, for example:

```
You: Find somewhere to eat near EV chargers in Medicine Hat within a 1 km walk
You: Show me options in Lethbridge with a 10 minute walking distance
```

## Tests

```bash
make test
```

## OpenAPI schema

- Live (server running): `http://127.0.0.1:8000/openapi.json`
- Committed snapshot: [`openapi.json`](./openapi.json)

The schema is suitable for use as a ChatGPT Action or similar tool-calling integration.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `OPENCHARGEMAP_API_KEY` | Yes | [OpenChargeMap](https://openchargemap.org/site/develop#api) API key |
| `GEOAPIFY_API_KEY` | Yes | [Geoapify](https://www.geoapify.com) API key |
| `GEMINI_API_KEY` | Agent only | [Gemini](https://aistudio.google.com/app/apikey) API key (required only for the CLI agent) |
| `GEMINI_MODEL` | No | Gemini model name (default: `gemini-2.5-flash-lite`) |
| `YELP_API_KEY` | No | [Yelp Fusion](https://docs.developer.yelp.com/docs/fusion-intro) API key. **Yelp Fusion is a commercial (paid) API.** Leave blank to run without review enrichment. |
| `GOOGLE_PLACES_API_KEY` | No | [Google Places](https://developers.google.com/maps/documentation/places/web-service/overview) API key. Used for review enrichment when `YELP_API_KEY` is not set. Requires a Google Cloud project with the Places API enabled (free monthly credit available). |
| `ENABLE_REVIEWS` | No | Set to `false` (or `0` / `no`) to disable review enrichment even when a review API key is configured. Defaults to `true`. |

## Deploy to Railway

1. Push this repo to GitHub.
2. Go to [railway.app](https://railway.app), create a new project, and connect the repo.
3. Add environment variables in the Railway project settings:
   - `OPENCHARGEMAP_API_KEY`
   - `GEOAPIFY_API_KEY`
   - `YELP_API_KEY` *(optional — Yelp Fusion is a commercial API; omit to run without reviews)*
   - `GOOGLE_PLACES_API_KEY` *(optional alternative to Yelp; used automatically when `YELP_API_KEY` is absent)*
   - `ENABLE_REVIEWS=false` *(optional — add this to disable review enrichment entirely)*
4. Railway detects the `Procfile` and deploys automatically. Your public HTTPS URL appears in the dashboard.

## Security notes

- API keys are loaded from environment variables — never hardcoded.
- `setup.env` is gitignored. Do not commit it.
- Rate limiting (20 req/min per IP) is enforced on all endpoints.
