#!/usr/bin/env python3
"""
Wave 1 feature engineering for Bootball Phase 2.

Three feature groups — all strictly leakage-safe (only data where
fixture date < target fixture date contributes):

  F1: Rolling team form from fixture_stats
      Shots-on-goal, possession, corners, pass-accuracy, yellow-cards
      averaged over each team's last N prior matches (N=5 and N=10).

  F2: League-context features (trailing 2-season window)
      avg_goals, btts_rate, HHI win-concentration — computed as of
      the target fixture date, using matches in the prior 2 seasons only.

  F3: Head-to-head features from fixtures table
      H2H W/D/L record, avg goals, recency-weighted win-rate
      from the last 10 prior meetings of the same pair.

Leakage boundaries:
  F1: rolling over matches where f.date < target.date (same-day excluded)
  F2: trailing 2-season rolling window; match must be FT before target.date
  F3: prior meetings only where f.date < target.date

Usage:
    from features_v1 import FeatureBuilder, FEATURE_NAMES

    builder = FeatureBuilder(conn, n_rolling=5)
    builder.load()                   # one-time prefetch
    vec = builder.build(fixture_row) # returns np.array for that fixture
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_left
from collections import defaultdict
from typing import Optional

import numpy as np

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_SHOTS = 4.5      # league-average shots-on-goal per team per match
DEFAULT_POSS = 50.0      # possession %
DEFAULT_CORNERS = 5.0
DEFAULT_PASS_ACC = 75.0  # pass accuracy %
DEFAULT_YELLOW = 1.5
DEFAULT_H2H_GOALS = 2.5
APPROX_SEASON_DAYS = 730  # 2 years in days for trailing league window

# ── Feature names (26 per fixture, ordered) ───────────────────────────────────

def _form_names(prefix: str, n: int) -> list[str]:
    return [
        f"{prefix}_shots_on_goal_avg{n}",
        f"{prefix}_shots_total_avg{n}",
        f"{prefix}_possession_avg{n}",
        f"{prefix}_corners_avg{n}",
        f"{prefix}_pass_acc_avg{n}",
        f"{prefix}_yellow_avg{n}",
    ]


def feature_names(n_rolling: int) -> list[str]:
    return (
        _form_names("home", n_rolling) +      # 6: home team rolling form
        _form_names("away", n_rolling) +      # 6: away team rolling form
        [                                     # 3: league context
            "league_avg_goals",
            "league_btts_rate",
            "league_hhi",
        ] +
        [                                     # 5: H2H
            "h2h_home_win_rate",
            "h2h_draw_rate",
            "h2h_away_win_rate",
            "h2h_avg_goals",
            "h2h_weighted_home_win_rate",
        ]
    )


FEATURE_NAMES_N5 = feature_names(5)
FEATURE_NAMES_N10 = feature_names(10)


# ── Core builder ──────────────────────────────────────────────────────────────

class FeatureBuilder:
    """
    Pre-fetches all relevant fixture_stats and fixture history at construction,
    then answers per-fixture feature queries in O(log n) time.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open connection to football.db (read only). historical_odds.db need not
        be attached — features come from football.db only.
    n_rolling : int
        Rolling window size (5 or 10).
    min_season : int
        Oldest season to load (inclusive). Defaults to 2019.
    """

    def __init__(self, conn: sqlite3.Connection, n_rolling: int = 5,
                 min_season: int = 2019):
        self.conn = conn
        self.n = n_rolling
        self.min_season = min_season

        # Per-team form history: team_id → [(date_str, shots_on, shots_tot,
        #   possession, corners, pass_acc, yellow_cards), ...]  sorted by date ASC
        self._team_form: dict[int, list[tuple]] = defaultdict(list)

        # League context history: league_id → [(date_str, goals, btts, winner_team_id)]
        self._league_history: dict[int, list[tuple]] = defaultdict(list)

        # H2H history: (home_id, away_id) and (away_id, home_id) →
        #   [(date_str, home_goals, away_goals), ...] sorted by date ASC
        #   Stored by canonical pair (min_id, max_id) with a direction flag
        self._h2h: dict[tuple, list[tuple]] = defaultdict(list)

    def load(self) -> None:
        """Load all required data from DB into memory."""
        self._load_form()
        self._load_league_history()
        self._load_h2h()

    # ── F1: rolling team form ─────────────────────────────────────────────────

    def _load_form(self) -> None:
        """
        Pull fixture_stats joined to fixtures. For each completed match,
        record two entries — one per team — containing their own-side stats.
        """
        rows = self.conn.execute("""
            SELECT
                f.date,
                f.home_team_id,
                f.away_team_id,
                fs.home_shots_on_goal,
                fs.away_shots_on_goal,
                fs.home_shots_total,
                fs.away_shots_total,
                fs.home_possession,
                fs.away_possession,
                fs.home_corners,
                fs.away_corners,
                fs.home_passes_accurate,
                fs.away_passes_accurate,
                fs.home_passes_total,
                fs.away_passes_total,
                fs.home_yellow_cards,
                fs.away_yellow_cards
            FROM fixture_stats fs
            JOIN fixtures f ON f.id = fs.fixture_id
            WHERE f.status = 'FT'
              AND f.season >= ?
              AND f.goals_home IS NOT NULL
        """, (self.min_season,)).fetchall()

        for row in rows:
            (date, hid, aid,
             h_sog, a_sog, h_st, a_st,
             h_poss, a_poss,
             h_corn, a_corn,
             h_pa, a_pa, h_pt, a_pt,
             h_yc, a_yc) = row

            h_pacc = (100.0 * h_pa / h_pt) if (h_pa is not None and h_pt and h_pt > 0) else None
            a_pacc = (100.0 * a_pa / a_pt) if (a_pa is not None and a_pt and a_pt > 0) else None

            self._team_form[hid].append(
                (date, h_sog, h_st, h_poss, h_corn, h_pacc, h_yc)
            )
            self._team_form[aid].append(
                (date, a_sog, a_st, a_poss, a_corn, a_pacc, a_yc)
            )

        for tid in self._team_form:
            self._team_form[tid].sort(key=lambda x: x[0])

    def _rolling_form(self, team_id: int, before_date: str) -> np.ndarray:
        """
        Return mean of the last N stat rows for team_id where date < before_date.
        Returns defaults if insufficient history.
        stat order: shots_on, shots_total, possession, corners, pass_acc, yellow
        """
        entries = self._team_form.get(team_id, [])
        dates = [e[0] for e in entries]
        idx = bisect_left(dates, before_date)   # first idx with date >= before_date
        window = entries[max(0, idx - self.n):idx]

        defaults = np.array([DEFAULT_SHOTS, DEFAULT_SHOTS * 2,
                              DEFAULT_POSS, DEFAULT_CORNERS,
                              DEFAULT_PASS_ACC, DEFAULT_YELLOW])
        if not window:
            return defaults

        matrix = np.zeros((len(window), 6))
        for i, (_, sog, st, poss, corn, pacc, yc) in enumerate(window):
            matrix[i, 0] = sog  if sog  is not None else DEFAULT_SHOTS
            matrix[i, 1] = st   if st   is not None else DEFAULT_SHOTS * 2
            matrix[i, 2] = poss if poss is not None else DEFAULT_POSS
            matrix[i, 3] = corn if corn is not None else DEFAULT_CORNERS
            matrix[i, 4] = pacc if pacc is not None else DEFAULT_PASS_ACC
            matrix[i, 5] = yc   if yc   is not None else DEFAULT_YELLOW

        return matrix.mean(axis=0)

    # ── F2: league context ────────────────────────────────────────────────────

    def _load_league_history(self) -> None:
        rows = self.conn.execute("""
            SELECT f.date, f.league_id,
                   f.goals_home, f.goals_away,
                   f.home_team_id, f.away_team_id
            FROM fixtures f
            WHERE f.status = 'FT'
              AND f.season >= ?
              AND f.goals_home IS NOT NULL
        """, (max(self.min_season - 2, 2017),)).fetchall()

        for date, lid, gh, ga, hid, aid in rows:
            total = (gh or 0) + (ga or 0)
            btts = 1 if (gh and gh > 0 and ga and ga > 0) else 0
            winner = hid if gh > ga else (aid if ga > gh else None)
            self._league_history[lid].append((date, total, btts, winner))

        for lid in self._league_history:
            self._league_history[lid].sort(key=lambda x: x[0])

    def _league_context(self, league_id: int, before_date: str) -> np.ndarray:
        """
        Return avg_goals, btts_rate, HHI for the 2-year trailing window ending
        at before_date (exclusive).
        """
        entries = self._league_history.get(league_id, [])
        dates = [e[0] for e in entries]
        end_idx = bisect_left(dates, before_date)

        # trailing 2-year window: ~730 days
        cutoff_date = _subtract_days(before_date, APPROX_SEASON_DAYS)
        start_idx = bisect_left(dates, cutoff_date)
        window = entries[start_idx:end_idx]

        if len(window) < 20:
            return np.array([2.5, 0.50, 0.05])   # neutral defaults

        goals = [e[1] for e in window]
        btts  = [e[2] for e in window]
        wins: dict[int, int] = defaultdict(int)
        for e in window:
            if e[3] is not None:
                wins[e[3]] += 1

        avg_goals  = float(np.mean(goals))
        btts_rate  = float(np.mean(btts))
        total_wins = sum(wins.values()) or 1
        hhi        = float(sum((w / total_wins) ** 2 for w in wins.values()))

        return np.array([avg_goals, btts_rate, hhi])

    # ── F3: H2H ───────────────────────────────────────────────────────────────

    def _load_h2h(self) -> None:
        rows = self.conn.execute("""
            SELECT f.date, f.home_team_id, f.away_team_id,
                   f.goals_home, f.goals_away
            FROM fixtures f
            WHERE f.status = 'FT'
              AND f.goals_home IS NOT NULL
              AND f.season >= ?
        """, (max(self.min_season - 5, 2014),)).fetchall()

        for date, hid, aid, gh, ga in rows:
            key = (min(hid, aid), max(hid, aid))
            self._h2h[key].append((date, hid, aid, gh or 0, ga or 0))

        for key in self._h2h:
            self._h2h[key].sort(key=lambda x: x[0])

    def _h2h_features(self, home_id: int, away_id: int,
                      before_date: str) -> np.ndarray:
        """
        Last 10 prior meetings (either side as home). Returns 5 features:
        home_win_rate, draw_rate, away_win_rate, avg_goals,
        recency-weighted home_win_rate (exponential decay, half-life 3 matches).
        """
        key = (min(home_id, away_id), max(home_id, away_id))
        entries = self._h2h.get(key, [])
        dates = [e[0] for e in entries]
        end_idx = bisect_left(dates, before_date)
        window = entries[max(0, end_idx - 10):end_idx]

        if not window:
            return np.array([0.45, 0.27, 0.28, DEFAULT_H2H_GOALS, 0.45])

        hw = dw = aw = 0
        total_goals = 0.0
        weighted_hw = 0.0
        weight_sum = 0.0

        n = len(window)
        for i, (_, match_hid, match_aid, gh, ga) in enumerate(window):
            # Is home_id actually playing at home in this historical match?
            is_home_playing_as_home = (match_hid == home_id)
            if match_hid == home_id:
                result = "H" if gh > ga else ("D" if gh == ga else "A")
            else:
                result = "A" if gh > ga else ("D" if gh == ga else "H")

            if result == "H":   hw += 1
            elif result == "D": dw += 1
            else:               aw += 1

            total_goals += gh + ga

            # Exponential decay: most-recent match has weight 1, older decay by 0.7
            w = 0.7 ** (n - 1 - i)
            weight_sum += w
            if result == "H":
                weighted_hw += w

        n = len(window)
        home_win_r = hw / n
        draw_r = dw / n
        away_win_r = aw / n
        avg_g = total_goals / n
        w_home_win_r = weighted_hw / weight_sum if weight_sum > 0 else home_win_r

        return np.array([home_win_r, draw_r, away_win_r, avg_g, w_home_win_r])

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self, fixture: dict, n_override: Optional[int] = None) -> np.ndarray:
        """
        Build the full Wave 1 feature vector for a single fixture dict.

        fixture must contain:
          date, home_team_id, away_team_id, league_id

        Returns np.ndarray of shape (20,):
          [home_form×6, away_form×6, league×3, h2h×5]
        """
        n = n_override or self.n
        old_n = self.n
        self.n = n

        date = fixture["date"]
        hid  = fixture["home_team_id"]
        aid  = fixture["away_team_id"]
        lid  = fixture["league_id"]

        home_form = self._rolling_form(hid, date)
        away_form = self._rolling_form(aid, date)
        league    = self._league_context(lid, date)
        h2h       = self._h2h_features(hid, aid, date)

        self.n = old_n
        return np.concatenate([home_form, away_form, league, h2h])

    def build_pair(self, fixture: dict) -> tuple[np.ndarray, np.ndarray]:
        """
        Return (features_n5, features_n10) for direct N comparison.
        """
        f5  = self.build(fixture, n_override=5)
        f10 = self.build(fixture, n_override=10)
        return f5, f10


# ── Helper ────────────────────────────────────────────────────────────────────

def _subtract_days(date_str: str, days: int) -> str:
    """Subtract `days` from an ISO date string (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)."""
    from datetime import datetime, timedelta
    fmt = "%Y-%m-%d %H:%M:%S" if len(date_str) > 10 else "%Y-%m-%d"
    dt = datetime.strptime(date_str[:19] if len(date_str) > 10 else date_str, fmt)
    result = dt - timedelta(days=days)
    return result.strftime("%Y-%m-%d")


# ── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path
    DB = Path(__file__).resolve().parent.parent.parent / "data" / "football.db"
    conn = sqlite3.connect(DB)

    builder = FeatureBuilder(conn, n_rolling=5)
    print("Loading form data...")
    builder.load()
    print(f"  Loaded form for {len(builder._team_form):,} teams")
    print(f"  League history for {len(builder._league_history):,} leagues")
    print(f"  H2H pairs: {len(builder._h2h):,}")

    # Sample: grab one recent fixture and compute features
    row = conn.execute("""
        SELECT id, league_id, date, home_team_id, away_team_id,
               goals_home, goals_away, outcome
        FROM fixtures
        WHERE season=2025 AND status='FT' AND goals_home IS NOT NULL
        ORDER BY date DESC LIMIT 1
    """).fetchone()

    if row:
        fix = dict(zip(
            ["id","league_id","date","home_team_id","away_team_id",
             "goals_home","goals_away","outcome"], row))
        f5, f10 = builder.build_pair(fix)
        names5 = feature_names(5)
        print(f"\nSample fixture {fix['id']} ({fix['date']}):")
        print(f"  Home {fix['home_team_id']} vs Away {fix['away_team_id']}")
        print(f"  N=5  features ({len(f5)}): {dict(zip(names5, f5.round(3)))}")
        print(f"  N=10 features ({len(f10)}): ...league={f10[12:15].round(3)}, h2h={f10[15:].round(3)}")

    conn.close()
