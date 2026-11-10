"""Cursor pagination for list endpoints (SPEC §11.1).

``?cursor=&limit=`` (default 50, max 200) returning
``{"results": [...], "next_cursor": "..." | null}``. Keyset pagination over
``(created_at desc, id desc)`` so pages are stable under inserts.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from django.db.models import Q, QuerySet

from .clock import iso_micros
from .errors import bad_request

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _parse_limit(request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return DEFAULT_LIMIT
    try:
        limit = int(raw)
    except ValueError:
        raise bad_request("limit must be an integer") from None
    if limit < 1:
        raise bad_request("limit must be positive")
    return min(limit, MAX_LIMIT)


def _decode_cursor(raw: str) -> tuple[str, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
        return str(data[0]), str(data[1])
    except (ValueError, binascii.Error, KeyError, IndexError, TypeError):
        raise bad_request("invalid cursor") from None


def _encode_cursor(created_at_iso: str, obj_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([created_at_iso, obj_id]).encode("ascii")).decode(
        "ascii"
    )


def paginate(queryset: QuerySet, request) -> tuple[list[Any], str | None]:
    """Return ``(page_objects, next_cursor)`` ordered by newest first."""
    limit = _parse_limit(request)
    qs = queryset.order_by("-created_at", "-id")

    cursor = request.query_params.get("cursor")
    if cursor:
        created_iso, obj_id = _decode_cursor(cursor)
        # strictly older than the cursor row
        qs = qs.filter(
            Q(created_at__lt=created_iso) | (Q(created_at=created_iso) & Q(id__lt=obj_id))
        )

    rows = list(qs[: limit + 1])
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _encode_cursor(iso_micros(last.created_at), str(last.id))
    return rows, next_cursor
