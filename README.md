# restaurant_ev_charging

Production-ready FastAPI service that finds restaurants near compatible DC fast-charging stations.

## Requirements

- Python 3.12+
- OpenChargeMap API key
- Geoapify API key

## Environment variables

Copy `setup_example.env` to `setup.env` and set:

- `OPENCHARGEMAP_API_KEY`
- `GEOAPIFY_API_KEY`

For the Gemini agent (optional), also set:

- `GEMINI_API_KEY`

Do not commit secrets.

## Install

```bash
python -m venv venv
source venv/bin/activate
pip install -e .[dev]
```

## Run locally

```bash
make run
```

## Example request

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

## Gemini agent

An interactive agent that lets you query the API in natural language using Gemini function calling.

**Install the extra dependency:**

```bash
pip install -e .[ai]
```

**Run (requires the server to be running):**

```bash
make run-agent
```

**Example session:**

```
You: Find me somewhere to eat near EV chargers in Swift Current, Saskatchewan
  [→ find_dining_chargers({"latitude": 50.2865, "longitude": -107.7939, "radius_km": 10})]
Gemini: I found 3 restaurant–charger pairs near Swift Current...
```

## Tests

```bash
pytest
```

## OpenAPI for ChatGPT Action

- Runtime docs: `http://127.0.0.1:8000/openapi.json`
- Committed document: [`openapi.json`](./openapi.json)

## Deploy to Railway

1. Push this repo to GitHub.
2. Go to [railway.app](https://railway.app), create a new project, and connect the repo.
3. In the Railway project settings, add environment variables:
   - `OPENCHARGEMAP_API_KEY`
   - `GEOAPIFY_API_KEY`
4. Railway will detect the `Procfile` and deploy automatically. Your public HTTPS URL appears in the dashboard.

The API enforces a **20 requests per minute** limit per IP.

## Deployment notes

- Set API keys via environment variables in your runtime platform — never commit secrets.
- Scale horizontally; upstream calls are async and use a shared client with retries and timeouts.
