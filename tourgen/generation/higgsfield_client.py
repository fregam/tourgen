"""
FILE: tourgen/generation/higgsfield_client.py
ROLE: Real Higgsfield client: presigned upload, idempotent submit, status, download.

HOW IT WORKS:
  - Mirrors the official SDK (higgsfield-client 0.2.0) wire protocol, but calls
    httpx directly because the SDK cannot send an Idempotency-Key header
    (docs/decisions/0002-higgsfield-http-client.md).
  - upload_file: POST /files/generate-upload-url {content_type} → PUT bytes to
    upload_url with exactly `upload_headers` (no Higgsfield credentials) → public_url.
  - submit: POST <base>/<endpoint> JSON args + Idempotency-Key → request_id,
    status_url, cancel_url (always use the returned URLs).
  - get_status: GET status_url → status (+ video URL when completed).

CONNECTS TO:
  - Implements generation/base.VideoClient; driven by generation/scheduler.py

INVARIANTS / GOTCHAS:
  - Concurrency cap is returned as HTTP 400 "Maximum number of concurrent
    requests" → ConcurrencyLimitError (retryable). No Retry-After header.
  - The completed-response JSON shape for videos is NOT verified (docs were
    unreachable); extract_video_url() tries the plausible shapes.
  - Output URLs expire (~7 days): the scheduler downloads immediately.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx

from tourgen.config import Settings
from tourgen.generation.base import (
    ConcurrencyLimitError,
    GenerationError,
    StatusResult,
    SubmitResult,
    TransientError,
)
from tourgen.models.generation import RequestStatus

USER_AGENT = "tourgen/0.1"
_RETRYABLE = {408, 429, 500, 502, 503, 504}


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text
    if isinstance(data, dict):
        for key in ("detail", "details", "message", "error"):
            if data.get(key):
                return str(data[key])
    return response.text


def _raise_for(response: httpx.Response) -> None:
    if response.is_success:
        return
    message = _error_message(response)
    if response.status_code == 400 and "concurrent" in message.lower():
        raise ConcurrencyLimitError(message)
    if response.status_code in _RETRYABLE:
        raise TransientError(f"HTTP {response.status_code}: {message}")
    raise GenerationError(f"HTTP {response.status_code}: {message}")


def extract_video_url(data: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        data.get("video"),
        *(data.get("videos") or []),
        data.get("output"),
        *(data.get("outputs") or []),
        data.get("result"),
    ]
    for item in candidates:
        if isinstance(item, str) and item.startswith("http"):
            return item
        if isinstance(item, dict):
            url = item.get("url") or item.get("video_url")
            if isinstance(url, str):
                return url
            nested = item.get("video")
            if isinstance(nested, dict) and isinstance(nested.get("url"), str):
                return str(nested["url"])
    url = data.get("video_url") or data.get("url")
    return url if isinstance(url, str) else None


class HiggsfieldClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        if not settings.hf_key and http is None:
            raise GenerationError("HF_KEY is not set (format key_id:key_secret)")
        self._http = http or httpx.Client(
            base_url=settings.hf_base_url,
            headers={
                "Authorization": f"Key {settings.hf_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=90.0,
        )
        # Uploads go to a presigned URL: a separate client WITHOUT our credentials.
        self._plain = httpx.Client(timeout=300.0, follow_redirects=True)

    def upload_file(self, path: Path) -> str:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            response = self._http.post(
                "/files/generate-upload-url", json={"content_type": content_type}
            )
        except httpx.TransportError as exc:
            raise TransientError(str(exc)) from exc
        _raise_for(response)
        data = response.json()
        headers = data.get("upload_headers") or {"Content-Type": content_type}
        try:
            put = self._plain.put(data["upload_url"], content=path.read_bytes(), headers=headers)
        except httpx.TransportError as exc:
            raise TransientError(str(exc)) from exc
        _raise_for(put)
        return str(data["public_url"])

    def submit(
        self, endpoint: str, arguments: dict[str, Any], idempotency_key: str, label: str = ""
    ) -> SubmitResult:
        try:
            response = self._http.post(
                f"/{endpoint.lstrip('/')}",
                json=arguments,
                headers={"Idempotency-Key": idempotency_key},
            )
        except httpx.TransportError as exc:
            raise TransientError(str(exc)) from exc
        _raise_for(response)
        data = response.json()
        return SubmitResult(
            request_id=data["request_id"],
            status_url=data["status_url"],
            cancel_url=data.get("cancel_url"),
        )

    def get_status(self, request_id: str, status_url: str | None) -> StatusResult:
        url = status_url or f"/requests/{request_id}/status"
        try:
            response = self._http.get(url)
        except httpx.TransportError as exc:
            raise TransientError(str(exc)) from exc
        _raise_for(response)
        data = response.json()
        try:
            status = RequestStatus(data.get("status"))
        except ValueError as exc:
            raise GenerationError(f"Unknown status payload: {data}") from exc
        video_url = extract_video_url(data) if status == RequestStatus.COMPLETED else None
        error = None if status != RequestStatus.FAILED else _error_message_from(data)
        return StatusResult(status=status, video_url=video_url, error=error, raw=data)

    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        try:
            with self._plain.stream("GET", url) as response:
                _raise_for(response)
                with tmp.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except httpx.TransportError as exc:
            raise TransientError(str(exc)) from exc
        tmp.replace(dest)
        return dest


def _error_message_from(data: dict[str, Any]) -> str:
    for key in ("error", "detail", "message"):
        if data.get(key):
            return str(data[key])
    return "generation failed"
