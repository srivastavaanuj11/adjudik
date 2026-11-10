"""Error envelope and DRF exception handling (SPEC §11.1).

Every error is ``{"error": {"code", "message", "details": [...]}}``. Messages
never contain input values (SPEC §15.5).
"""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.response import Response


class ApiError(Exception):
    """An error with a stable code and HTTP status (SPEC §11.1)."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        http_status: int,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.http_status = http_status
        self.details = details or []

    def as_response(self) -> Response:
        return error_response(self.code, self.message, self.details, self.http_status)


def error_response(
    code: str, message: str, details: list[dict[str, Any]] | None, http_status: int
) -> Response:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return Response(body, status=http_status)


# Common constructors.
def bad_request(message: str = "bad request") -> ApiError:
    return ApiError("bad_request", message, http_status=status.HTTP_400_BAD_REQUEST)


def unauthenticated(
    message: str = "authentication required", code: str = "unauthenticated"
) -> ApiError:
    return ApiError(code, message, http_status=status.HTTP_401_UNAUTHORIZED)


def forbidden(message: str = "forbidden") -> ApiError:
    return ApiError("forbidden", message, http_status=status.HTTP_403_FORBIDDEN)


def not_found(message: str = "not found") -> ApiError:
    return ApiError("not_found", message, http_status=status.HTTP_404_NOT_FOUND)


def conflict(code: str, message: str = "") -> ApiError:
    return ApiError(code, message or code, http_status=status.HTTP_409_CONFLICT)


def unprocessable(
    code: str, message: str = "", details: list[dict[str, Any]] | None = None
) -> ApiError:
    return ApiError(code, message or code, http_status=422, details=details)


def rate_limited(message: str = "rate limited") -> ApiError:
    return ApiError("rate_limited", message, http_status=status.HTTP_429_TOO_MANY_REQUESTS)


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    from rest_framework.exceptions import (
        AuthenticationFailed,
        NotAuthenticated,
        PermissionDenied,
        Throttled,
    )
    from rest_framework.views import exception_handler as drf_exception_handler

    if isinstance(exc, ApiError):
        resp = exc.as_response()
    elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        resp = error_response("unauthenticated", str(exc.detail), None, 401)
    elif isinstance(exc, PermissionDenied):
        resp = error_response("forbidden", "forbidden", None, 403)
    elif isinstance(exc, Throttled):
        resp = error_response("rate_limited", "rate limited", None, 429)
        if exc.wait is not None:
            resp["Retry-After"] = str(int(exc.wait) + 1)
        return resp
    else:
        drf_resp = drf_exception_handler(exc, context)
        if drf_resp is None:
            return None
        code = {400: "bad_request", 404: "not_found", 405: "bad_request"}.get(
            drf_resp.status_code, "bad_request"
        )
        msg = drf_resp.data if isinstance(drf_resp.data, str) else "request could not be processed"
        return error_response(code, str(msg), None, drf_resp.status_code)
    return resp
