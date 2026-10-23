"""TrustHeal backend configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRUSTHEAL_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Simulation
    seed: int = 42
    speed: float = 10.0  # default sim speed multiplier

    # Persistence
    db_path: Path = Path("./data/trustheal.db")
    models_dir: Path = Path("./models")

    # Logging
    log_level: str = "INFO"

    # API
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # LLM triage (optional)
    llm_provider: str = "template"  # template | openai | ollama
    llm_url: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: float = 10.0

    # Training
    train_min_episodes_per_class: int = 30
    train_fail_on_low_samples: bool = False


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
