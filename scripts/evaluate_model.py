#!/usr/bin/env python3
"""Evaluate Dixon-Coles model performance."""
import sys
sys.path.insert(0, '/opt/projects/bootball')

from src.models.dixon_coles import DixonColesModel
from src.storage.db import get_session
from src.storage.models import Fixture
from sqlalchemy import select

def main():
    with get_session() as s:
        train = s.execute(select(Fixture).where(
            Fixture.status == 'FT',
            Fixture.league_id == 39,
            Fixture.season < 2025
        )).scalars().all()
        test = s.execute(select(Fixture).where(
            Fixture.status == 'FT',
            Fixture.league_id == 39,
            Fixture.season == 2025
        )).scalars().all()
        
        train_data = [(f.home_team_id, f.away_team_id, f.goals_home, f.goals_away) for f in train]
        test_data = [(f.home_team_id, f.away_team_id, f.goals_home, f.goals_away) for f in test]
    
    print(f'Train: {len(train_data)}, Test: {len(test_data)}')
    
    model = DixonColesModel(league_id=39)
    model.fit()
    
    results = []
    for home_id, away_id, gh, ga in test_data:
        probs = model.predict_proba(home_id, away_id)
        if gh > ga:
            actual = 'H'
        elif gh == ga:
            actual = 'D'
        else:
            actual = 'A'
        results.append((probs, actual))
    
    bs = 0
    correct = 0
    for (h, d, a), actual in results:
        if actual == 'H':
            bs += (h - 1) ** 2
        elif actual == 'D':
            bs += (d - 1) ** 2
        else:
            bs += (a - 1) ** 2
        
        pred = 'H' if (h > d and h > a) else ('A' if a > d else 'D')
        if pred == actual:
            correct += 1
    
    bs /= len(results)
    print(f'Brier Score: {bs:.3f}')
    print(f'Accuracy: {correct}/{len(results)} = {correct/len(results):.1%}')

if __name__ == '__main__':
    main()