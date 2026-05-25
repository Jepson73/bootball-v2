"""
config/settings.py — single source of truth for all runtime configuration.

All settings are read from environment variables or .env.
Use `from config.settings import settings` everywhere.
backend/config.py is a thin backward-compat shim over this module.
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    model_config = {
        "extra": "ignore",
        "env_file": ".env",
    }

    # ── API-Football ────────────────────────────────────────
    api_football_key: str = Field(default="")
    api_football_base_url: str = Field(default="https://v3.football.api-sports.io")
    api_calls_per_day: int = Field(default=75000)
    api_interval_seconds: float = Field(default=0.15)

    # ── Database ────────────────────────────────────────────
    dry_run: bool = Field(default=False)
    database_url: str = Field(default="sqlite:///data/football.db")

    # ── Flask ───────────────────────────────────────────────
    secret_key: str = Field(default="dev-secret-change-me")
    flask_debug: bool = Field(default=False)
    cors_origins: str = Field(default="http://localhost:3000,http://localhost:5173")

    # ── Auth ────────────────────────────────────────────────
    bootball_password: str = Field(default="")

    # ── Scheduler ───────────────────────────────────────────
    scheduler_enabled: bool = Field(default=True)
    fetch_fixtures_interval_hours: int = Field(default=6)
    fetch_results_interval_hours: int = Field(default=1)
    fetch_odds_interval_hours: int = Field(default=2)
    predictions_run_hour: int = Field(default=3)
    retrain_day_of_week: str = Field(default="mon")
    retrain_hour: int = Field(default=4)

    # ── ML ──────────────────────────────────────────────────
    model_dir: str = Field(default="./backend/models/saved")
    min_confidence_threshold: float = Field(default=0.60)

    # ── Bankroll ────────────────────────────────────────────
    initial_bankroll: float = Field(default=1000.0)
    web_base_url: str = Field(default="http://localhost:5000")

    # ── Betting bot ─────────────────────────────────────────
    bot_enabled: bool = Field(default=False)
    bot_max_stake: float = Field(default=50.0)
    bot_min_ev: float = Field(default=0.05)

    # ── Experiments ─────────────────────────────────────────
    experiment_mode: bool = Field(default=False)
    experiment_variants: int = Field(default=3)

    # ── Logging ─────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_dir: str = Field(default="./logs")
    log_max_bytes: int = Field(default=10_485_760)
    log_backup_count: int = Field(default=5)

    # ── Alerts ──────────────────────────────────────────────
    discord_webhook_url: str = Field(default="")
    alerts_enabled: bool = Field(default=True)
    alerts_top_n: int = Field(default=5)
    alerts_min_ev: float = Field(default=0.05)

    # ── Localisation ────────────────────────────────────────
    timezone: str = Field(default="Europe/Stockholm")
    # Leagues that run on a calendar year (Jan–Dec) rather than European Aug–May.
    # Used everywhere season = current_year is needed.
    # NOTE: A-League (188) runs Oct–May so uses the European convention (current_year - 1).
    calendar_year_leagues: list[int] = Field(
        default_factory=lambda: [
            98,   # J1 League (Japan)
            253,  # MLS (USA)
            909,  # MLS Next Pro (USA)
            292,  # K League 1 (South Korea)
            71,   # Série A (Brazil)
            72,   # Série B (Brazil)
            76,   # Série D (Brazil)
            113,  # Allsvenskan (Sweden)
            114,  # Superettan (Sweden)
            103,  # Eliteserien (Norway)
            104,  # 1. Division (Norway)
            422,  # Premier League (Barbados) — calendar year season observed in DB
            # 119 and 120 (Danish Superliga / 1. Division) run Aug–May like European leagues
            # — they use season=START_YEAR (e.g. 2025 for 2025/26), NOT current calendar year.
        ]
    )

    @property
    def current_season(self) -> int:
        from datetime import datetime
        month = datetime.now().month
        year = datetime.now().year
        # European/Southern Hemisphere seasons start in July–August
        return year if month >= 7 else year - 1

    def get_season(self, league_id: int | None = None) -> int:
        """Return the correct API-Football season year for a given league."""
        if league_id and league_id in self.calendar_year_leagues:
            from datetime import datetime
            return datetime.now().year
        return self.current_season


settings = Settings()
