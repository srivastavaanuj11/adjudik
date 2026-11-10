"""SSRF-safe webhook delivery and signatures (SPEC §11.6).

Every attempt resolves the host, requires each resolved address to be globally
routable (after unwrapping IPv4-mapped, 6to4 and NAT64 forms), then connects to
the *checked* address with the Host header and TLS SNI preserved, so a DNS record
that flips to a private address between the check and the connect cannot be used
to reach internal services. Redirects and ambient proxy env are disabled; only
``https`` on ports 443/8443 is allowed; the whole attempt has a 5 s budget and at
most 64 KB of the response is read. The log keeps only the status code and an
error class. ``ADJUDIK_ALLOW_*_WEBHOOKS`` relax these checks in development only.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import hmac
import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings

from apps.common import crypto

TOTAL_BUDGET_S = 5.0
MAX_RESPONSE_BYTES = 64 * 1024
ALLOWED_PORTS = {443, 8443}
ROTATION_GRACE = _dt.timedelta(hours=24)

# Successive retry delays, then `dead` (SPEC §11.6).
RETRY_SCHEDULE_S = [10, 60, 300, 1800, 7200, 21600]


@dataclass
class Outcome:
    status_code: int | None
    error_class: str | None  # None on success

    @property
    def ok(self) -> bool:
        return self.error_class is None


class WebhookError(Exception):
    """A delivery attempt failed; ``error_class`` is one of the SPEC §11.6 classes."""

    def __init__(self, error_class: str) -> None:
        super().__init__(error_class)
        self.error_class = error_class


# --- address checks (SPEC §11.6) ---


def _unwrap(addr: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """Unwrap IPv4-mapped, 6to4 and NAT64 forms to the embedded IPv4 (SPEC §11.6)."""
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return addr.ipv4_mapped
        if addr.sixtofour is not None:
            return addr.sixtofour
        # NAT64 well-known prefix 64:ff9b::/96 embeds IPv4 in the low 32 bits.
        if int(addr) >> 32 == (int(ipaddress.IPv6Address("64:ff9b::")) >> 32):
            return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr


def _is_allowed(ip_str: str) -> bool:
    if settings.ADJUDIK_ALLOW_PRIVATE_WEBHOOKS:
        return True
    try:
        addr = _unwrap(ipaddress.ip_address(ip_str))
    except ValueError:
        return False
    return addr.is_global


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:  # DNS failure
        raise WebhookError("dns") from exc
    addrs = []
    for info in infos:
        sockaddr = info[4]
        addrs.append(sockaddr[0])
    if not addrs:
        raise WebhookError("dns")
    return addrs


def check_url(url: str) -> tuple[str, int, str]:
    """Validate the scheme/port and resolve+check the host; return (host, port, ip).

    Raises :class:`WebhookError` with ``blocked_address`` when any resolved address
    is not globally routable (SPEC §11.6).
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    host = parts.hostname or ""
    if scheme != "https" and not settings.ADJUDIK_ALLOW_INSECURE_WEBHOOKS:
        raise WebhookError("blocked_address")
    port = parts.port or (443 if scheme == "https" else 80)
    if (
        scheme == "https"
        and port not in ALLOWED_PORTS
        and not settings.ADJUDIK_ALLOW_INSECURE_WEBHOOKS
    ):
        raise WebhookError("blocked_address")
    if not host:
        raise WebhookError("blocked_address")

    addrs = _resolve(host)
    if not all(_is_allowed(a) for a in addrs):
        raise WebhookError("blocked_address")
    return host, port, addrs[0]


# --- signatures (SPEC §11.6) ---


def _sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


def signature_header(endpoint, timestamp: int, body: bytes, *, now: _dt.datetime) -> str:
    """``v1=<hmac>`` for the current secret, plus the previous during the 24 h grace."""
    secrets = [crypto.decrypt_str(endpoint.secret_encrypted)]
    if (
        endpoint.previous_secret_encrypted
        and endpoint.previous_secret_expires_at
        and endpoint.previous_secret_expires_at > now
    ):
        secrets.append(crypto.decrypt_str(endpoint.previous_secret_encrypted))
    return ",".join(f"v1={_sign(s, timestamp, body)}" for s in secrets)


# --- the HTTPS connection pinned to a checked address ---


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a pre-validated IP while keeping the original Host/SNI."""

    def __init__(self, host: str, port: int, ip: str, *, context, timeout: float) -> None:
        super().__init__(host, port, context=context, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:  # pragma: no cover - exercised only against a live endpoint
        sock = socket.create_connection((self._pinned_ip, self.port), timeout=self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _send(host: str, port: int, ip: str, path: str, body: bytes, headers: dict) -> int:
    """POST ``body`` to the pinned address; return the status code (SPEC §11.6)."""
    insecure = settings.ADJUDIK_ALLOW_INSECURE_WEBHOOKS
    try:
        if insecure:  # pragma: no cover - dev only
            conn = http.client.HTTPConnection(ip, port, timeout=TOTAL_BUDGET_S)
            headers = {**headers, "Host": host}
        else:
            ctx = ssl.create_default_context()
            conn = _PinnedHTTPSConnection(host, port, ip, context=ctx, timeout=TOTAL_BUDGET_S)
        conn.request("POST", path or "/", body=body, headers=headers)
        resp = conn.getresponse()
        resp.read(MAX_RESPONSE_BYTES)
        return resp.status
    except ssl.SSLError as exc:
        raise WebhookError("tls") from exc
    except TimeoutError as exc:
        raise WebhookError("timeout") from exc
    except OSError as exc:
        raise WebhookError("connection") from exc
    finally:
        with contextlib.suppress(Exception):
            conn.close()  # type: ignore[has-type]


def deliver(delivery, *, now: _dt.datetime) -> Outcome:
    """Perform one SSRF-checked delivery attempt and classify the result."""
    endpoint = delivery.endpoint
    body = json.dumps(delivery.payload, separators=(",", ":")).encode("utf-8")
    timestamp = int(now.timestamp())
    headers = {
        "Content-Type": "application/json",
        "X-Adjudik-Event-Id": delivery.event_id,
        "X-Adjudik-Timestamp": str(timestamp),
        "X-Adjudik-Signature": signature_header(endpoint, timestamp, body, now=now),
    }
    try:
        host, port, ip = check_url(endpoint.url)
        parts = urlsplit(endpoint.url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        status = _send(host, port, ip, path, body, headers)
    except WebhookError as exc:
        return Outcome(status_code=None, error_class=exc.error_class)
    if 200 <= status < 300:
        return Outcome(status_code=status, error_class=None)
    return Outcome(status_code=status, error_class="http_error")


def next_delay_seconds(attempt: int) -> int | None:
    """Delay before the attempt after ``attempt`` failures, or ``None`` when dead."""
    idx = attempt - 1
    if 0 <= idx < len(RETRY_SCHEDULE_S):
        return RETRY_SCHEDULE_S[idx]
    return None
