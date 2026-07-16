from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.clients.geoapify import GeoapifyClient
from app.clients.http import RetryingHttpClient
from app.clients.openchargemap import OpenChargeMapClient
from app.clients.yelp import YelpClient
from app.config import load_settings
from app.errors import ApiError
from app.schemas import FindDiningChargersRequest
from app.services.reviews import YelpReviewProvider
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
    timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)
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
    review_provider = None
    if settings.enable_reviews and settings.yelp_api_key:
        yelp_client = YelpClient(retrying_client, settings.yelp_api_key)
        review_provider = YelpReviewProvider(yelp_client)
    app.state.dining_service = DiningChargerService(ocm_client, geo_client, review_provider)

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
