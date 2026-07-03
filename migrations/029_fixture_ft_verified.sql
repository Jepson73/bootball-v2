-- Migration 029: ft_verified_at on fixtures
--
-- Phase 27 settlement-integrity guard. Two confirmed cases (Dundee II vs
-- Peterhead, Thor Akureyri vs KR Reykjavik, both 2026-07-02) showed a fixture
-- marked status=FT with goals frozen at the halftime score — an internal
-- freeze (provider glitch or stale-cache read), not a provider error, since a
-- live re-check showed the provider had the correct final all along.
-- get_market_result() already requires is_final for h2h/Under/BTTS-No, but
-- nothing ever re-verified that the FT snapshot backing is_final was actually
-- trustworthy before settling those reversible markets.
--
-- ft_verified_at is set by settle_predictions()'s new confirmation step: the
-- first time a fixture with unsettled reversible-market predictions is seen
-- at FT/AET/PEN, it force-refetches once before settling. NULL means
-- not yet confirmed — reversible markets stay unsettled until it is.

ALTER TABLE fixtures ADD COLUMN ft_verified_at DATETIME;
