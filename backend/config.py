"""
backend/config.py — backward-compat shim over config.settings.

All settings are now owned by config/settings.py (pydantic-settings).
This module exposes a Config class with UPPER_CASE attributes so existing
callers (backend/app.py etc.) continue to work without changes.

DO NOT add new settings here — add them to config/settings.py instead.
"""
import logging
import os

from config.settings import settings

logger = logging.getLogger(__name__)


def _validate_production_config() -> None:
    """Fail loudly if production secrets are missing or weak."""
    env = os.getenv("FLASK_ENV", "development").lower()
    if env != "production":
        return

    if (
        not settings.secret_key
        or settings.secret_key == "dev-secret-change-me"
        or len(settings.secret_key) < 32
    ):
        raise ValueError(
            "PRODUCTION SECURITY ERROR: SECRET_KEY must be ≥32 chars. "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    if not settings.bootball_password:
        raise ValueError(
            "PRODUCTION SECURITY ERROR: BOOTBALL_PASSWORD must be set."
        )


_validate_production_config()


class Config:
    """Upper-case attribute proxy over pydantic Settings.

    Evaluated once at class-definition time (module load), which is correct
    because `settings` is also a module-level singleton built from env vars.
    """
    # ── Flask ───────────────────────────────────────────────
    SECRET_KEY = settings.secret_key
    DEBUG = settings.flask_debug
    CORS_ORIGINS = [o.strip() for o in settings.cors_origins.split(",")]

    # ── Database ────────────────────────────────────────────
    DATABASE_PATH = settings.database_url

    # ── API-Football ────────────────────────────────────────
    API_FOOTBALL_KEY = settings.api_football_key
    API_FOOTBALL_BASE_URL = settings.api_football_base_url

    # ── Betting bot ─────────────────────────────────────────
    BOT_ENABLED = settings.bot_enabled
    BOT_MAX_STAKE = settings.bot_max_stake
    BOT_MIN_EV = settings.bot_min_ev

    # ── Experiments ─────────────────────────────────────────
    EXPERIMENT_MODE = settings.experiment_mode
    EXPERIMENT_VARIANTS = settings.experiment_variants

    # ── ML ──────────────────────────────────────────────────
    MODEL_DIR = settings.model_dir
    MIN_CONFIDENCE_THRESHOLD = settings.min_confidence_threshold

    # ── Scheduler ───────────────────────────────────────────
    SCHEDULER_ENABLED = settings.scheduler_enabled
    FETCH_FIXTURES_INTERVAL_HOURS = settings.fetch_fixtures_interval_hours
    FETCH_RESULTS_INTERVAL_HOURS = settings.fetch_results_interval_hours
    FETCH_ODDS_INTERVAL_HOURS = settings.fetch_odds_interval_hours
    PREDICTIONS_RUN_HOUR = settings.predictions_run_hour
    RETRAIN_DAY_OF_WEEK = settings.retrain_day_of_week
    RETRAIN_HOUR = settings.retrain_hour

    # ── Logging ─────────────────────────────────────────────
    LOG_LEVEL = settings.log_level
    LOG_DIR = settings.log_dir
    LOG_MAX_BYTES = settings.log_max_bytes
    LOG_BACKUP_COUNT = settings.log_backup_count


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


_config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}


def get_config():
    """Return the Config class for the current FLASK_ENV."""
    env = os.getenv("FLASK_ENV", "development")
    return _config_map.get(env, DevelopmentConfig)
