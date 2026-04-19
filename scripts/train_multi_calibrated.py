#!/usr/bin/env python3
"""Train calibrated ML model on all leagues."""
import sys
sys.path.insert(0, '/opt/projects/bootball')

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss
from src.storage.db import get_session
from src.storage.models import Fixture, Standing
from sqlalchemy import select

def main():
    with get_session() as s:
        stands = s.execute(select(Standing).where(Standing.season == 2024)).all()
        
        league_teams = {}
        for st in stands:
            league_teams[st[0].league_id] = {st[0].team_id: st[0].rank}
        
        fixtures = s.execute(select(Fixture).where(Fixture.status == 'FT')).scalars().all()
        data = [(f.league_id, f.home_team_id, f.away_team_id, f.goals_home, f.goals_away) for f in fixtures]
    
    print(f'Fixtures: {len(data)}')
    
    X, y = [], []
    for lid, hid, aid, gh, ga in data:
        h_rank = league_teams.get(lid, {}).get(hid, 10)
        a_rank = league_teams.get(lid, {}).get(aid, 10)
        X.append([lid, h_rank, a_rank])
        y.append(1 if gh > ga else (0 if gh == ga else 2))
    
    X, y = np.array(X), np.array(y)
    
    # Sort by index (approximate time) and split
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    
    print(f'Train: {len(X_train)}, Test: {len(X_test)}')
    
    # Train
    clf = GradientBoostingClassifier(n_estimators=100, max_depth=4, random_state=42)
    clf.fit(X_train, y_train)
    
    # Get predictions for all 3 classes
    proba = clf.predict_proba(X_test)
    
    # Evaluate for each outcome
    for target, name in [(0, 'Draw'), (1, 'Home'), (2, 'Away')]:
        y_binary = (y_test == target).astype(float)
        bs = brier_score_loss(y_binary, proba[:, target])
        pred = proba[:, target]
        print(f'{name} Brier: {bs:.3f}')
    
    # Overall Brier
    bs_total = 0
    for i, actual in enumerate(y_test):
        for j in range(3):
            bs_total += (proba[i, j] - (1 if j == actual else 0)) ** 2
    bs_total /= len(y_test)
    print(f'Overall Brier: {bs_total:.3f}')

if __name__ == '__main__':
    main()