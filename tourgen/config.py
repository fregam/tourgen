"""
FILE: tourgen/config.py
ROLE: Runtime settings (env vars + .env) and loaders for config/*.yaml.

HOW IT WORKS:
  - Settings is a pydantic-settings model. Secrets use their conventional names
    (ANTHROPIC_API_KEY, HF_KEY); everything else is prefixed TOURGEN_.
  - load_defaults() reads config/defaults.yaml into a typed TourDefaults.

READS:  environment, .env, config/defaults.yaml
WRITES: nothing

CONNECTS TO:
  - Used by cli.py, core/pipeline.py, llm/claude.py, generation/*
  - Model registry lives in generation/registry.py (config/models.yaml)

INVARIANTS / GOTCHAS:
  - Claude model IDs live here only (BUILD_PROMPT §2). Never hardcode them elsewhere.
  - Prices are estimates for `tourgen estimate`; the ledger records what was actually used.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


class ClaudePrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TOURGEN_", env_file=".env", extra="ignore", populate_by_name=True
    )

    anthropic_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key")
    )
    hf_key: str | None = Field(default=None, validation_alias=AliasChoices("HF_KEY", "hf_key"))

    jobs_dir: Path = Path("jobs")
    models_yaml: Path = CONFIG_DIR / "models.yaml"
    defaults_yaml: Path = CONFIG_DIR / "defaults.yaml"

    # Claude models (BUILD_PROMPT §2): Sonnet for per-image work + QA, Opus for planning.
    analysis_model: str = "claude-sonnet-5-5"
    property_model_model: str = "claude-opus-5-5"
    planning_model: str = "claude-opus-5-5"
    qa_model: str = "claude-sonnet-5-5"
    claude_max_image_px: int = 1568
    claude_prices: dict[str, ClaudePrice] = {
        "claude-opus-5-5": ClaudePrice(input_per_mtok=4.0, output_per_mtok=20.0),
        "claude-sonnet-5-5": ClaudePrice(input_per_mtok=2.0, output_per_mtok=10.0),
    }

    # Higgsfield
    hf_base_url: str = "https://api.higgsfield.ai"
    hf_concurrency: int = 4  # account's real concurrency limit; check your plan
    hf_poll_initial_s: float = 5.0
    hf_poll_max_s: float = 30.0
    hf_timeout_s: float = 1800.0

    # Safety / cost controls (BUILD_PROMPT §6)
    budget_usd: float = 25.0
    confirm_above_usd: float = 5.0
    max_retries: int = 2
    max_shots: int = 30

    # Email delivery (stage 10). Any SMTP server works, e.g. Gmail with an app password:
    # host smtp.gmail.com, port 587, user = your address, password = the app password.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # defaults to smtp_user
    smtp_starttls: bool = True
    email_max_attachments_mb: float = 18.0  # total; ~25 MB provider caps after base64
    email_outbox_only: bool = False  # write .eml to render/outbox instead of sending

    # Media
    max_image_side_px: int = 2560
    render_scale: float = 1.0  # <1 renders smaller (tests/dev previews); 1.0 = 1080p
    font_path: Path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    music_dir: Path = REPO_ROOT / "assets" / "music"


class TourDefaults(BaseModel):
    """Default tour settings; a Brief overrides these per job."""

    social_length_s: tuple[float, float] = (45.0, 75.0)
    full_length_s: tuple[float, float] = (90.0, 150.0)
    shot_duration_s: tuple[float, float] = (3.0, 6.0)
    transition: str = "dissolve"
    transition_duration_s: float = 0.5
    music_mood: str = "warm"
    disclosure_text: str = "Video animated with AI from real property photos."
    aspect_ratios: list[str] = ["16:9", "9:16"]
    fps: int = 30
    preferred_model: str = "kling-3.0"
    transition_model: str = "kling-3.0"
    title_card_s: float = 2.5
    end_card_s: float = 3.0


def load_defaults(path: Path | None = None) -> TourDefaults:
    path = path or Settings().defaults_yaml
    if not path.exists():
        return TourDefaults()
    data = yaml.safe_load(path.read_text()) or {}
    return TourDefaults.model_validate(data)
