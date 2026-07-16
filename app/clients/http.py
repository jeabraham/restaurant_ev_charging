from __future__ import annotations

import asyncio
import logging
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.errors import UpstreamHttpError, UpstreamTimeoutError

logger = logging.getLogger(__name__)


class RetryingHttpClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timeout_seconds: float = 10.0,
        retries: int = 3,
    ) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._retries = retries

    async def get_json(
        self,
        *,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str] | None,
        service_name: str,
    ) -> Any:
        last_response: httpx.Response | None = None

        for attempt in range(self._retries + 1):
            try:
                response = await self._client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
                last_response = response

                if response.status_code in (429, 500, 502, 503, 504) and attempt < self._retries:
                    wait_seconds = self._retry_delay(response, attempt)
                    logger.info(
                        "Retrying upstream request",
                        extra={
                            "service": service_name,
                            "status_code": response.status_code,
                            "attempt": attempt + 1,
                            "wait_seconds": wait_seconds,
                        },
                    )
                    await asyncio.sleep(wait_seconds)
                    continue

                if response.status_code >= 400:
                    raise UpstreamHttpError(
                        code=f"{service_name.upper()}_UPSTREAM_ERROR",
                        message=f"{service_name} returned HTTP {response.status_code}.",
                        status_code=502,
                        upstream_status=response.status_code,
                    )

                return response.json()
            except httpx.TimeoutException as exc:
                if attempt < self._retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise UpstreamTimeoutError(
                    code=f"{service_name.upper()}_UPSTREAM_TIMEOUT",
                    message=f"{service_name} request timed out.",
                    status_code=504,
                ) from exc
            except httpx.HTTPError as exc:
                if attempt < self._retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise UpstreamHttpError(
                    code=f"{service_name.upper()}_UPSTREAM_ERROR",
                    message=f"{service_name} request failed.",
                    status_code=502,
                ) from exc

        raise UpstreamHttpError(
            code=f"{service_name.upper()}_UPSTREAM_ERROR",
            message=f"{service_name} returned HTTP {last_response.status_code}."
            if last_response
            else f"{service_name} request failed.",
            status_code=502,
            upstream_status=last_response.status_code if last_response else None,
        )

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                if retry_after.isdigit():
                    return min(float(retry_after), 30.0)
                retry_at = parsedate_to_datetime(retry_after)
                return max(0.0, min((retry_at - parsedate_to_datetime(response.headers.get("Date", ""))).total_seconds(), 30.0))
            except Exception:
                return min(float(2**attempt), 10.0)
        return min(float(2**attempt), 10.0)
