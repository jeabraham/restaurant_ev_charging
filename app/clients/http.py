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
        timeout_seconds: float = 20.0,
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
        timeout_seconds: float | None = None,
        retries: int | None = None,
        timeout_error_status_code: int = 504,
    ) -> Any:
        return await self.request_json(
            method="GET",
            url=url,
            params=params,
            headers=headers,
            service_name=service_name,
            timeout_seconds=timeout_seconds,
            retries=retries,
            timeout_error_status_code=timeout_error_status_code,
        )

    async def post_json(
        self,
        *,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        headers: dict[str, str] | None,
        service_name: str,
        timeout_seconds: float | None = None,
        retries: int | None = None,
        timeout_error_status_code: int = 504,
    ) -> Any:
        return await self.request_json(
            method="POST",
            url=url,
            params=params,
            json_body=json_body,
            headers=headers,
            service_name=service_name,
            timeout_seconds=timeout_seconds,
            retries=retries,
            timeout_error_status_code=timeout_error_status_code,
        )

    async def request_json(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        service_name: str,
        json_body: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        retries: int | None = None,
        timeout_error_status_code: int = 504,
    ) -> Any:
        last_response: httpx.Response | None = None
        retry_count = self._retries if retries is None else max(0, retries)
        timeout = self._timeout_seconds if timeout_seconds is None else timeout_seconds

        for attempt in range(retry_count + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=timeout,
                )
                last_response = response

                if response.status_code in (429, 500, 502, 503, 504) and attempt < retry_count:
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
                if attempt < retry_count:
                    await asyncio.sleep(2**attempt)
                    continue
                raise UpstreamTimeoutError(
                    code=f"{service_name.upper()}_UPSTREAM_TIMEOUT",
                    message=f"{service_name} request timed out.",
                    status_code=timeout_error_status_code,
                ) from exc
            except httpx.HTTPError as exc:
                if attempt < retry_count:
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
                return max(
                    0.0,
                    min(
                        (retry_at - parsedate_to_datetime(response.headers.get("Date", ""))).total_seconds(),
                        30.0,
                    ),
                )
            except Exception:
                return min(float(2**attempt), 10.0)
        return min(float(2**attempt), 10.0)
