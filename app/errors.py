from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ApiError(Exception):
    code: str
    message: str
    status_code: int
    upstream_status: int | None = None


@dataclass
class UpstreamTimeoutError(ApiError):
    pass


@dataclass
class UpstreamHttpError(ApiError):
    pass
