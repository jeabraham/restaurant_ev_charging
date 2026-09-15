from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.clients.geoapify import GeoapifyClient
from app.clients.google_places import GooglePlacesClient
from app.clients.http import RetryingHttpClient
from app.clients.openchargemap import OpenChargeMapClient
from app.clients.yelp import YelpClient
from app.config import load_settings
from app.errors import ApiError
from app.schemas import FindDiningChargersRequest, GeoRouteRequest
from app.services.reviews import GooglePlacesReviewProvider, YelpReviewProvider
from app.services.search import DiningChargerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("app.services.search").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)
    shared_client = httpx.AsyncClient(timeout=timeout)

    retrying_client = RetryingHttpClient(shared_client)
    ocm_client = OpenChargeMapClient(
        retrying_client,
        settings.openchargemap_api_key,
    )
    geo_client = GeoapifyClient(
        retrying_client,
        settings.geoapify_api_key,
    )
    app.state.geo_client = geo_client
    google_client = None
    if settings.google_places_api_key:
        google_client = GooglePlacesClient(retrying_client, settings.google_places_api_key)

    review_provider = None
    if settings.enable_reviews:
        if settings.yelp_api_key:
            yelp_client = YelpClient(retrying_client, settings.yelp_api_key)
            review_provider = YelpReviewProvider(yelp_client)
        elif google_client:
            review_provider = GooglePlacesReviewProvider(google_client)

    app.state.dining_service = DiningChargerService(
        ocm_client,
        geo_client,
        review_provider,
        google_client=google_client,
        restaurant_search_geoapify=settings.restaurant_search_geoapify,
        restaurant_search_google=settings.restaurant_search_google,
        enable_charger_reviews=settings.enable_reviews,
        enable_opening_hours=settings.enable_opening_hours,
    )

    try:
        yield
    finally:
        await shared_client.aclose()


app = FastAPI(
    title="Restaurant EV Charging API",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(_: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": {"code": "RATE_LIMIT_EXCEEDED", "message": str(exc.detail)}},
    )


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    payload = {"code": exc.code, "message": exc.message}
    if hasattr(exc, "details") and getattr(exc, "details") is not None:
        payload["details"] = getattr(exc, "details")
    if exc.upstream_status is not None:
        payload["upstream_status"] = exc.upstream_status
    return JSONResponse(status_code=exc.status_code, content={"error": payload})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    semantic_error = any(err.get("type") == "semantic_error" for err in exc.errors())
    status_code = 422 if semantic_error else 400
    code = "SEMANTIC_VALIDATION_ERROR" if semantic_error else "INVALID_REQUEST"
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": "Invalid request payload.",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(json.JSONDecodeError)
async def json_error_handler(_: Request, __: json.JSONDecodeError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "MALFORMED_JSON",
                "message": "Malformed JSON request body.",
            }
        },
    )


@app.exception_handler(Exception)
async def generic_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled internal error", extra={"error_type": type(exc).__name__})
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected internal error occurred.",
            }
        },
    )


@app.post("/find-dining-chargers")
@limiter.limit("20/minute")
async def find_dining_chargers(request: Request, payload: FindDiningChargersRequest) -> dict:
    service: DiningChargerService = app.state.dining_service
    return await service.find(payload)


def _require_geoapify_key() -> None:
    geo_client: GeoapifyClient | None = getattr(app.state, "geo_client", None)
    if geo_client is None or not geo_client.is_configured():
        raise ApiError(
            code="GEOAPIFY_NOT_CONFIGURED",
            message="GEOAPIFY_API_KEY is not configured.",
            status_code=500,
        )


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            parsed = float(value)
            return int(parsed) if parsed.is_integer() else None
        except (TypeError, ValueError):
            return None
    return None


def _normalize_geocode_result(item: dict[str, Any]) -> dict[str, Any]:
    rank_data = item.get("rank")
    rank = rank_data if isinstance(rank_data, dict) else None
    return {
        "formatted": item.get("formatted"),
        "coordinates": {
            "lat": _to_float(item.get("lat")),
            "lon": _to_float(item.get("lon")),
        },
        "components": {
            "country": item.get("country"),
            "state": item.get("state"),
            "city": item.get("city"),
            "postcode": item.get("postcode"),
        },
        "provider": {
            "place_id": item.get("place_id"),
            "result_type": item.get("result_type"),
            "rank": rank,
        },
    }


def _normalize_route_response(raw: dict[str, Any], mode: str) -> dict[str, Any]:
    features = raw.get("features")
    if not isinstance(features, list) or not features:
        return {
            "mode": mode,
            "total_distance_m": None,
            "total_duration_s": None,
            "geometry": None,
            "polyline": None,
            "legs": [],
        }
    first = features[0] if isinstance(features[0], dict) else {}
    properties = first.get("properties") if isinstance(first.get("properties"), dict) else {}
    legs_raw = properties.get("legs")
    legs: list[dict[str, Any]] = []
    if isinstance(legs_raw, list):
        for leg in legs_raw:
            if not isinstance(leg, dict):
                continue
            steps_raw = leg.get("steps")
            steps: list[dict[str, Any]] = []
            if isinstance(steps_raw, list):
                for step in steps_raw:
                    if not isinstance(step, dict):
                        continue
                    steps.append(
                        {
                            "instruction": step.get("instruction"),
                            "distance_m": _to_float(step.get("distance")),
                            "duration_s": _to_float(step.get("time")),
                            "from_index": _to_int(step.get("from_index")),
                            "to_index": _to_int(step.get("to_index")),
                        }
                    )
            legs.append(
                {
                    "distance_m": _to_float(leg.get("distance")),
                    "duration_s": _to_float(leg.get("time")),
                    "steps": steps,
                }
            )
    return {
        "mode": mode,
        "total_distance_m": _to_float(properties.get("distance")),
        "total_duration_s": _to_float(properties.get("time")),
        "geometry": first.get("geometry"),
        "polyline": properties.get("polyline"),
        "legs": legs,
    }


@app.get("/api/geo/geocode")
async def geo_geocode(
    query: str = Query(..., min_length=1),
    limit: int = Query(default=5, ge=1, le=10),
    lang: str | None = Query(default=None, min_length=2, max_length=10),
    filter: str | None = Query(default=None, min_length=1),
    bias: str | None = Query(default=None, min_length=1),
) -> dict[str, Any]:
    _require_geoapify_key()
    geo_client: GeoapifyClient = app.state.geo_client
    raw = await geo_client.geocode(
        query=query,
        limit=limit,
        lang=lang,
        filter_value=filter,
        bias=bias,
    )
    features = raw.get("features")
    normalized_results: list[dict[str, Any]] = []
    if isinstance(features, list):
        for feature in features:
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties")
            if isinstance(properties, dict):
                normalized_results.append(_normalize_geocode_result(properties))
    return {
        "query": query,
        "total": len(normalized_results),
        "results": normalized_results,
    }


@app.post("/api/geo/route")
async def geo_route(payload: GeoRouteRequest) -> dict[str, Any]:
    _require_geoapify_key()
    geo_client: GeoapifyClient = app.state.geo_client
    waypoints = "|".join(f"{point.lat},{point.lon}" for point in payload.waypoints)
    raw = await geo_client.route(
        waypoints=waypoints,
        mode=payload.mode,
        details=payload.details,
    )
    return _normalize_route_response(raw, payload.mode)
