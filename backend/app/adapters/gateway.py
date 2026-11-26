"""HTTP client for the on-robot `armpi_debug_gateway`.

The gateway owns the vendor ROS 2 stack. Read methods cover `/health`,
`/state`, `/battery`, `/camera/status`, `/camera/snapshot`, `/tf` and
`/motion/status`. `post_json` exists only for the supervised motion endpoints;
it is not used unless the backend was explicitly started with hardware motion
enabled.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_s: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept}
        if self.token:
            headers["X-Armpi-Token"] = self.token
        return headers

    @staticmethod
    def _error_payload(exc: HTTPError) -> tuple[str | None, str | None]:
        """Return ``(code, message)`` from a gateway error body, if present."""
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            return None, None
        error = data.get("error") if isinstance(data, dict) else None
        if not isinstance(error, dict):
            return None, None
        code = error.get("code")
        message = error.get("message")
        return (
            str(code) if code is not None else None,
            str(message) if message is not None else None,
        )

    def _request(
        self,
        method: str,
        path: str,
        accept: str,
        body: Any = None,
        timeout_s: float | None = None,
    ):
        url = f"{self.base_url}{path}"
        headers = self._headers(accept)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, headers=headers, data=data, method=method)
        try:
            return urlopen(request, timeout=timeout_s or self.timeout_s)
        except HTTPError as exc:
            code, message = self._error_payload(exc)
            raise GatewayError(
                message or f"gateway returned HTTP {exc.code} for {path}",
                status=exc.code,
                code=code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GatewayError(f"gateway request failed for {path}: {exc}") from exc

    def _open(self, path: str, accept: str, timeout_s: float | None = None):
        return self._request("GET", path, accept, timeout_s=timeout_s)

    def _decode_json(self, response, path: str) -> dict[str, Any]:
        with response:
            payload = response.read()
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError(f"gateway returned invalid JSON for {path}") from exc
        if not isinstance(data, dict):
            raise GatewayError(f"gateway returned a non-object JSON payload for {path}")
        return data

    def get_json(
        self, path: str, timeout_s: float | None = None
    ) -> dict[str, Any]:
        return self._decode_json(
            self._open(path, "application/json", timeout_s), path
        )

    def post_json(
        self,
        path: str,
        body: dict[str, Any],
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """POST a JSON object; only the supervised motion endpoints use this."""
        return self._decode_json(
            self._request("POST", path, "application/json", body, timeout_s),
            path,
        )

    def get_bytes(
        self, path: str, timeout_s: float | None = None
    ) -> tuple[bytes, str]:
        with self._open(path, "*/*", timeout_s) as response:
            payload = response.read()
            content_type = response.headers.get_content_type() or "application/octet-stream"
        return payload, content_type
