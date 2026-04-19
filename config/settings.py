"""
config/settings.py

Typed settings from .env via pydantic-settings.
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    model_config = {
        "extra": "ignore",
        "env_file": ".env",
    }
    
    api_football_key: str = Field(default="")
    api_football_base_url: str = Field(default="https://v3.football.api-sports.io")
    api_calls_per_day: int = Field(default=100000)
    api_interval_seconds: float = Field(default=0.15)  # ~400 calls/min, safely under 450/min
    dry_run: bool = Field(default=False)
    database_url: str = Field(default="sqlite:///data/football.db")

    # Calendar year leagues (starts Mar, ends Nov): A-League, MLS, J-League, etc.
    calendar_year_leagues: list[int] = Field(default_factory=lambda: [1602, 253, 98, 176, 113, 1191])
    bootball_password: str = Field(default="changeme")

    @property
    def current_season(self) -> int:
        """
        Returns the current season year for Aug-May leagues.
        If month >= 8 (Aug), use current year.
        If month <= 5 (Jan-May), use previous year (still in that season).
        """
        from datetime import datetime
        month = datetime.now().month
        year = datetime.now().year
        return year if month >= 8 else year - 1

    def get_season(self, league_id: int | None = None) -> int:
        """
        Returns the appropriate season for a league.
        If league_id is a calendar-year league (MLS, A-League, etc.), use current year.
        Otherwise, use current_season (Aug-May format).
        """
        if league_id and league_id in self.calendar_year_leagues:
            from datetime import datetime
            return datetime.now().year
        return self.current_season


settings = Settings()
