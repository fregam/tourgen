"""core/costs.py: estimates and the budget gate."""

from __future__ import annotations

import pytest

from tourgen.config import CONFIG_DIR, Settings
from tourgen.core.costs import BudgetExceeded, ConfirmationRequired, check_budget, estimate_job
from tourgen.generation.registry import load_registry
from tourgen.models.plan import CameraMove, CameraMoveType, Shot, TourPlan


def _plan(n: int) -> TourPlan:
    shots = [
        Shot(shot_id=f"s{i}", room_id="r", source_asset_ids=["a"], duration_s=5,
             camera_move=CameraMove(type=CameraMoveType.DOLLY_IN), model_id="kling-3.0",
             purpose="p")
        for i in range(n)
    ]  # fmt: skip
    return TourPlan(title="t", shots=shots, target_length_s=5 * n, disclosure_text="AI")


def test_estimate_scales_with_shots_and_retries() -> None:
    registry = load_registry(CONFIG_DIR / "models.yaml")
    est = estimate_job(_plan(10), registry, Settings(), max_retries=2)
    per_second = registry.get("kling-3.0").usd_per_second
    assert est.higgsfield_usd == pytest.approx(50 * per_second, abs=0.01)
    assert est.higgsfield_worst_usd == pytest.approx(3 * est.higgsfield_usd, abs=0.02)
    assert est.claude_usd > 0 and not est.verified_prices
    assert any("UNVERIFIED" in line for line in est.lines())


def test_budget_gate() -> None:
    registry = load_registry(CONFIG_DIR / "models.yaml")
    est = estimate_job(_plan(10), registry, Settings(), max_retries=0)
    with pytest.raises(BudgetExceeded):
        check_budget(est, budget_usd=1, confirm_above_usd=0, approved_usd=None)
    with pytest.raises(ConfirmationRequired):
        check_budget(est, budget_usd=100, confirm_above_usd=1, approved_usd=None)
    check_budget(est, budget_usd=100, confirm_above_usd=1, approved_usd=est.total_usd)
    check_budget(est, budget_usd=100, confirm_above_usd=100, approved_usd=None)
