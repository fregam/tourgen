"""api/app.py: the full browser flow through the HTTP API, with fakes (Phase 6 DoD)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.conftest import PHOTOS
from tourgen.api.app import create_app
from tourgen.config import Settings


def _wait(client: TestClient, job_id: str, key: str, timeout: float = 240) -> dict:  # type: ignore[type-arg]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if not data["running"] and data[key]:
            return data  # type: ignore[no-any-return]
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {key}")


def test_upload_plan_edit_approve_download(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    assert "tourgen" in client.get("/").text
    files = [
        ("files", (p.name, p.read_bytes(), "image/jpeg")) for p in sorted(PHOTOS.glob("*.jpg"))
    ]
    created = client.post(
        "/api/jobs", files=files,
        data={"brief": "family audience", "fake_generation": "true", "fake_llm": "true"},
    )  # fmt: skip
    job_id = created.json()["job_id"]
    assert client.post(f"/api/jobs/{job_id}/plan").status_code == 200
    data = _wait(client, job_id, "plan")
    shots = [s["shot_id"] for s in data["plan"]["shots"]]

    bad = client.put(f"/api/jobs/{job_id}/plan", json={"shot_ids": ["nope"]})
    assert bad.status_code == 422
    kept = [shots[0], *reversed(shots[1:4])]
    ok = client.put(f"/api/jobs/{job_id}/plan", json={"shot_ids": kept})
    assert ok.status_code == 200 and ok.json()["shots"] == 4

    approve = client.post(f"/api/jobs/{job_id}/approve", json={"approved_cost_usd": 0})
    assert approve.status_code == 200
    data = _wait(client, job_id, "outputs")
    assert [s["shot_id"] for s in data["plan"]["shots"]] == kept
    with client.stream("GET", f"/api/jobs/{job_id}/events") as events:
        body = "".join(events.iter_text())
    assert "event: end" in body
    video = client.get(f"/api/jobs/{job_id}/download/16x9")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert client.get("/api/jobs/missing").status_code == 404


def test_create_validates_url_and_email(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    assert client.post("/api/jobs", data={"brief": "x"}).status_code == 422
    bad = client.post("/api/jobs", data={"url": "https://a.example/1", "email": "nope"})
    assert bad.status_code == 422
    ok = client.post("/api/jobs", data={"url": "https://a.example/1", "email": "a@x.com, b@y.org"})
    job = client.get(f"/api/jobs/{ok.json()['job_id']}").json()["job"]
    assert job["inputs"]["urls"] == ["https://a.example/1"]
    assert job["inputs"]["notify_email"] == ["a@x.com", "b@y.org"]
    assert job["inputs"]["paths"] == []
