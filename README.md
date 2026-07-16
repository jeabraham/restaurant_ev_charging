# restaurant_ev_charging

A FastAPI service and interactive AI agent that finds restaurants within walking distance of compatible DC fast-charging stations — useful for planning EV road trips.

## Features

- Search for DC fast chargers (CCS, NACS/Tesla) near any location via [OpenChargeMap](https://openchargemap.org)
- Find restaurants within a configurable walking distance of each charger via [Geoapify](https://www.geoapify.com)
- Interactive natural-language agent powered by Gemini function calling
- Rate-limited REST API (20 requests/minute per IP) with OpenAPI schema

## Requirements

- Python 3.10+
- [OpenChargeMap API key](https://openchargemap.org/site/develop#api)
- [Geoapify API key](https://www.geoapify.com)
- [Gemini API key](https://aistudio.google.com/app/apikey) — only needed for the AI agent

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

## Deploy to Railway

1. Push this repo to GitHub.
2. Go to [railway.app](https://railway.app), create a new project, and connect the repo.
3. Add environment variables in the Railway project settings:
   - `OPENCHARGEMAP_API_KEY`
   - `GEOAPIFY_API_KEY`
4. Railway detects the `Procfile` and deploys automatically. Your public HTTPS URL appears in the dashboard.

## Security notes

- API keys are loaded from environment variables — never hardcoded.
- `setup.env` is gitignored. Do not commit it.
- Rate limiting (20 req/min per IP) is enforced on all endpoints.
