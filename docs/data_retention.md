# Data Retention Policy

## Training Data

### Fixture Data
- **Current approach**: Keep up to 5 seasons of data per league
- **Rationale**: Models need sufficient historical data for training, but older data may not reflect current form
- **Season rollover**: When a new season starts, remove the oldest (6th) season's fixtures

### Standing Data
- Keep standings for all seasons that have fixture data
- Standing data is lightweight (~4k rows) so cleanup is low priority

## Retention Schedule
1. Before training, check available seasons per league
2. If league has 6+ seasons of data, drop oldest season before training
3. Consider reducing to 4 seasons if model quality degrades

## Notes
- Storage is not currently a constraint (~70MB DB)
- Older fixtures (2022 and earlier) can be investigated for model quality impact
- If models underperform, experiment with reducing to 3-4 seasons

## Future Cleanup (when needed)
```sql
-- Remove oldest season fixtures
DELETE FROM fixtures
WHERE league_id = :league_id
AND season = :oldest_season
AND status = 'FT';

-- Vacuum to reclaim space
VACUUM;
```