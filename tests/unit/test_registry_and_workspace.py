"""Model registry loading + workspace layout/IO."""

from __future__ import annotations

from pathlib import Path

import pytest

from tourgen.config import CONFIG_DIR, load_defaults
from tourgen.core.workspace import Workspace
from tourgen.generation.registry import load_registry
from tourgen.models.job import JobState


def test_registry_loads_all_models() -> None:
    registry = load_registry(CONFIG_DIR / "models.yaml")
    assert {"kling-3.0", "kling-3.0-turbo", "seedance-2.0", "wan-2.7"} <= set(registry.models)
    kling = registry.get("kling-3.0")
    assert kling.supports_last_frame and kling.last_frame_field == "last_image_url"
    assert not registry.get("kling-3.0-turbo").supports_last_frame
    assert kling.clamp_duration(1.2) == 3.0
    assert kling.clamp_duration(4.4) == 4.0
    assert kling.clamp_duration(99) == 15.0
    assert "kling-3.0" in registry.capabilities_summary()
    with pytest.raises(KeyError):
        registry.get("nope")


def test_defaults_load() -> None:
    defaults = load_defaults(CONFIG_DIR / "defaults.yaml")
    assert defaults.disclosure_text
    assert "9:16" in defaults.aspect_ratios


def test_workspace_layout_and_atomic_io(tmp_path: Path) -> None:
    ws = Workspace.for_job(tmp_path, "job-1")
    ws.create()
    assert ws.clip_file("s01", 2).name == "s01__attempt2.mp4"
    assert ws.final_render_file("9:16").name == "final_9x16.mp4"
    ws.save_job(JobState(job_id="job-1"))
    assert ws.load_job().job_id == "job-1"
    assert ws.rel(ws.assets_dir / "a.jpg") == "assets/a.jpg"
    assert not list(ws.root.glob(".*.tmp"))
    (ws.root / "bad.json").write_text("{not json")
    assert ws.read_model_or_none(ws.root / "bad.json", JobState) is None


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "a b"])
def test_workspace_rejects_bad_job_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        Workspace.for_job(tmp_path, bad)
