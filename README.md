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

Do not commit secrets.

## Install

```bash
python -m venv venv
source venv/bin/activate
pip install -e .[dev]
```

## Run locally

```bash
uvicorn app.main:app --reload
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

## Tests

```bash
pytest
```

## OpenAPI for ChatGPT Action

- Runtime docs: `http://127.0.0.1:8000/openapi.json`
- Committed document: [`openapi.json`](./openapi.json)

## Deployment notes

- Deploy behind HTTPS.
- Set API keys via environment variables in your runtime platform.
- Scale horizontally; upstream calls are async and use a shared client with retries and timeouts.
