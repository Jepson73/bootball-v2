#!/usr/bin/env python3
"""Evaluate Dixon-Coles with proper train/test split."""
import sys
sys.path.insert(0, '/opt/projects/bootball')

import numpy as np
from sklearn.isotonic import IsotonicRegression
from src.models.dixon_coles import fit_dixon_coles, DCParams
from src.storage.db import get_session
from src.storage.models import Fixture
from sqlalchemy import select

def main():
    with get_session() as s:
        fixtures = s.execute(select(Fixture).where(
            Fixture.status == 'FT',
            Fixture.league_id == 39
        )).scalars().all()
        
        data = [(f.home_team_id, f.away_team_id, f.goals_home, f.goals_away, f.season, f.date) for f in fixtures]
    
    # Sort by date and split: 80% train, 20% test
    data.sort(key=lambda x: x[5])
    split = int(len(data) * 0.8)
    train_data = data[:split]
    test_data = data[split:]
    
    print(f'Train: {len(train_data)}, Test: {len(test_data)}')
    
    # Extract train data for fitting
    train_team_ids = sorted(set(t[0] for t in train_data) | set(t[1] for t in train_data))
    
    # Create fixture-like objects for Dixon-Coles
    class FakeFixture:
        def __init__(self, home_id, away_id, gh, ga, date):
            self.home_team_id = home_id
            self.away_team_id = away_id
            self.goals_home = gh
            self.goals_away = ga
            self.date = date
    
    fake_fixtures = [FakeFixture(h, a, gh, ga, d) for h, a, gh, ga, s, d in train_data]
    params = fit_dixon_coles(fake_fixtures, train_team_ids, xi=0.005)
    
    print(f"Fitted: home_adv={params.home_adv:.3f}, rho={params.rho:.3f}")
    
    # Get model predictions for calibration
    train_preds = []
    train_outcomes = []
    for home_id, away_id, gh, ga, s, d in train_data:
        attack_h = params.attack.get(home_id, 0.0)
        defense_h = params.defense.get(home_id, 0.0)
        attack_a = params.attack.get(away_id, 0.0)
        defense_a = params.defense.get(away_id, 0.0)
        lambda_home = np.exp(attack_h + defense_a + params.home_adv)
        
        # Raw probability
        raw_home = min(max(1 - np.exp(-lambda_home), 0.95), 0.05)
        train_preds.append(raw_home)
        train_outcomes.append(1.0 if gh > ga else 0.0)
    
    # Fit isotonic calibration
    ir = IsotonicRegression(out_of_bounds='clip')
    ir.fit(train_preds, train_outcomes)
    print("Calibration fitted")
    
    # Evaluate on test data
    bs_before = 0
    bs_after = 0
    correct = 0
    
    for home_id, away_id, gh, ga, s, d in test_data:
        attack_h = params.attack.get(home_id, 0.0)
        defense_h = params.defense.get(home_id, 0.0)
        attack_a = params.attack.get(away_id, 0.0)
        defense_a = params.defense.get(away_id, 0.0)
        lambda_home = np.exp(attack_h + defense_a + params.home_adv)
        
        raw_home = min(max(1 - np.exp(-lambda_home), 0.95), 0.05)
        cal_home = ir.predict([raw_home])[0]
        
        if gh > ga:
            actual = 'H'
        elif gh == ga:
            actual = 'D'
        else:
            actual = 'A'
        
        bs_before += (raw_home - (1 if actual == 'H' else 0)) ** 2
        bs_after += (cal_home - (1 if actual == 'H' else 0)) ** 2
        
        pred = 'H' if cal_home > 0.5 else 'A'
        if pred == actual:
            correct += 1
    
    n = len(test_data)
    print(f'Brier Score (raw): {bs_before/n:.3f}')
    print(f'Brier Score (calibrated): {bs_after/n:.3f}')
    print(f'Accuracy: {correct}/{n} = {correct/n:.1%}')

if __name__ == '__main__':
    main()