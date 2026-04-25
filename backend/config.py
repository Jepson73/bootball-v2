import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Runtime Mode ───────────────────────────────────
    RUNTIME_MODE = os.getenv("RUNTIME_MODE", "dev").lower()
    
    # ── Flask ───────────────────────────────────────────
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    # ── Database ────────────────────────────────────────
    DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/football.db")
    SCHEDULER_DB_PATH = os.getenv("SCHEDULER_DB_PATH", "./data/scheduler.db")

    # ── CORS ────────────────────────────────────────────
    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",")

    # ── api-football ────────────────────────────────────
    API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
    API_FOOTBALL_BASE_URL = os.getenv(
        "API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io"
    )

    # ── Betting Bot ─────────────────────────────────────
    BOT_ENABLED = os.getenv("BOT_ENABLED", "false").lower() == "true"
    BOT_MAX_STAKE = float(os.getenv("BOT_MAX_STAKE", "50"))
    BOT_MIN_EV = float(os.getenv("BOT_MIN_EV", "0.05"))

    # ── ML ──────────────────────────────────────────────
    MODEL_DIR = os.getenv("MODEL_DIR", "./backend/models/saved")
    MIN_CONFIDENCE_THRESHOLD = float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.60"))

    # ── Scheduler ───────────────────────────────────────
    SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    FETCH_FIXTURES_INTERVAL_HOURS = int(os.getenv("FETCH_FIXTURES_INTERVAL_HOURS", "6"))
    FETCH_RESULTS_INTERVAL_HOURS = int(os.getenv("FETCH_RESULTS_INTERVAL_HOURS", "1"))
    FETCH_ODDS_INTERVAL_HOURS = int(os.getenv("FETCH_ODDS_INTERVAL_HOURS", "2"))
    PREDICTIONS_RUN_HOUR = int(os.getenv("PREDICTIONS_RUN_HOUR", "3"))
    RETRAIN_DAY_OF_WEEK = os.getenv("RETRAIN_DAY_OF_WEEK", "mon")
    RETRAIN_HOUR = int(os.getenv("RETRAIN_HOUR", "4"))

    # ── Logging ─────────────────────────────────────────
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR = os.getenv("LOG_DIR", "./logs")
    LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "10485760"))
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}


def get_config():
    env = os.getenv("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
