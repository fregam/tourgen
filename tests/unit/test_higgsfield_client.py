"""generation/higgsfield_client.py against an httpx MockTransport (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tourgen.config import Settings
from tourgen.generation.base import ConcurrencyLimitError, GenerationError, TransientError
from tourgen.generation.higgsfield_client import HiggsfieldClient, extract_video_url
from tourgen.models.generation import RequestStatus

SEEN: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    SEEN.append(request)
    path = request.url.path
    if path == "/files/generate-upload-url":
        return httpx.Response(200, json={
            "public_url": "https://cdn.hf/img.jpg", "upload_url": "https://s3.example/put",
            "upload_headers": {"Content-Type": "image/jpeg", "x-amz-acl": "public-read"},
        })  # fmt: skip
    if request.url.host == "s3.example":
        return httpx.Response(200)
    if path == "/kling-video/v3.0/pro/image-to-video":
        if json.loads(request.content).get("prompt") == "busy":
            return httpx.Response(400, json={"detail": "Maximum number of concurrent requests (4)"})
        if json.loads(request.content).get("prompt") == "boom":
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={
            "request_id": "r1", "status_url": "https://api.hf/requests/r1/status",
            "cancel_url": "https://api.hf/requests/r1/cancel",
        })  # fmt: skip
    if path == "/requests/r1/status":
        return httpx.Response(
            200, json={"status": "completed", "video": {"url": "https://cdn.hf/v.mp4"}}
        )
    if path == "/v.mp4":
        return httpx.Response(200, content=b"mp4bytes")
    return httpx.Response(404)


@pytest.fixture
def client() -> HiggsfieldClient:
    SEEN.clear()
    transport = httpx.MockTransport(handler)
    http = httpx.Client(base_url="https://api.hf", transport=transport,
                        headers={"Authorization": "Key id:secret"})  # fmt: skip
    hf = HiggsfieldClient(Settings(hf_key="id:secret"), http=http)
    hf._plain = httpx.Client(transport=transport)
    return hf


def test_upload_uses_presigned_headers_without_credentials(
    client: HiggsfieldClient, tmp_path: Path
) -> None:
    image = tmp_path / "a.jpg"
    image.write_bytes(b"x")
    assert client.upload_file(image) == "https://cdn.hf/img.jpg"
    put = SEEN[-1]
    assert put.method == "PUT" and put.headers["x-amz-acl"] == "public-read"
    assert "authorization" not in put.headers


def test_submit_sends_idempotency_key(client: HiggsfieldClient) -> None:
    result = client.submit("kling-video/v3.0/pro/image-to-video", {"prompt": "ok"}, "key-1")
    assert result.request_id == "r1" and result.status_url.endswith("/status")
    assert SEEN[-1].headers["Idempotency-Key"] == "key-1"


def test_errors_are_classified(client: HiggsfieldClient) -> None:
    with pytest.raises(ConcurrencyLimitError):
        client.submit("kling-video/v3.0/pro/image-to-video", {"prompt": "busy"}, "k")
    with pytest.raises(TransientError):
        client.submit("kling-video/v3.0/pro/image-to-video", {"prompt": "boom"}, "k")
    with pytest.raises(GenerationError):
        client.submit("unknown/endpoint", {}, "k")


def test_status_and_download(client: HiggsfieldClient, tmp_path: Path) -> None:
    status = client.get_status("r1", "https://api.hf/requests/r1/status")
    assert status.status == RequestStatus.COMPLETED and status.video_url == "https://cdn.hf/v.mp4"
    out = client.download(status.video_url, tmp_path / "clip.mp4")
    assert out.read_bytes() == b"mp4bytes"


def test_extract_video_url_shapes() -> None:
    assert extract_video_url({"video": {"url": "https://a"}}) == "https://a"
    assert extract_video_url({"videos": [{"url": "https://b"}]}) == "https://b"
    assert extract_video_url({"video_url": "https://c"}) == "https://c"
    assert extract_video_url({}) is None


def test_requires_key() -> None:
    with pytest.raises(GenerationError):
        HiggsfieldClient(Settings(hf_key=None))
