-- Migration 024: market-blended probability + Closing Line Value (CLV) tracking.
--
-- Context: investigation (2026-06) found the bookmaker's de-vigged
-- (Shin) market-implied probability sits within ~1pt of the actual
-- outcome rate in every market, while the model's "calibrated"
-- probability runs 10-30pts overconfident. EV/Kelly now use a
-- market-blended probability (src/calibration/market_blend.py) instead
-- of trusting the model outright — these columns let us track the
-- model's, the market's, and the blended view side by side so the
-- blend's track record can be evaluated independently.
--
-- CLV (Closing Line Value) compares the odds at bet-placement time to
-- the odds just before kickoff — a same-day signal of whether a
-- claimed "edge" was real foresight or model error, instead of waiting
-- weeks for matches to settle.

ALTER TABLE prediction_records ADD COLUMN market_prob FLOAT;
ALTER TABLE prediction_records ADD COLUMN blended_prob FLOAT;

ALTER TABLE placed_bets ADD COLUMN closing_odds FLOAT;
ALTER TABLE placed_bets ADD COLUMN closing_implied_prob FLOAT;
ALTER TABLE placed_bets ADD COLUMN clv_pct FLOAT;
