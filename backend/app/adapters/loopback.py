"""Loopback-only URL helpers for ecosystem HTTP calls.

The studio backend is an operator console; it talks to the teleop status
server and the system stop endpoint on loopback only. A non-loopback URL is a
configuration error, not a feature.
"""

from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def require_loopback(url: str, label: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        raise ValueError(f"{label}: URL must be http(s): {url!r}")
    if (parts.hostname or "") not in LOOPBACK_HOSTS:
        raise ValueError(f"{label}: URL must be loopback-only: {url!r}")
    if not parts.port:
        raise ValueError(f"{label}: URL must include an explicit port: {url!r}")
    return url.strip().rstrip("/")
