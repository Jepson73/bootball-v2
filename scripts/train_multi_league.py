#!/usr/bin/env python3
"""Train ML model on all leagues."""
import sys
sys.path.insert(0, '/opt/projects/bootball')

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from src.storage.db import get_session
from src.storage.models import Fixture, Standing
from sqlalchemy import select

def main():
    with get_session() as s:
        # Extract standings while in session
        stands = s.execute(select(Standing).where(Standing.season.in_([2024, 2023]))).all()
        
        league_teams = {}
        for st in stands:
            lid = st[0].league_id
            tid = st[0].team_id
            rank = st[0].rank
            if lid not in league_teams:
                league_teams[lid] = {}
            league_teams[lid][tid] = rank
        
        fixtures = s.execute(select(Fixture).where(Fixture.status == 'FT')).scalars().all()
        data = []
        for f in fixtures:
            data.append((f.league_id, f.home_team_id, f.away_team_id, f.goals_home, f.goals_away))
    
    print(f'Fixtures: {len(data)}')
    print(f'Leagues with standings: {len(league_teams)}')
    
    X, y = [], []
    for lid, hid, aid, gh, ga in data:
        h_rank = league_teams.get(lid, {}).get(hid, 10)
        a_rank = league_teams.get(lid, {}).get(aid, 10)
        X.append([lid, h_rank, a_rank])
        y.append(1 if gh > ga else (0 if gh == ga else 2))
    
    X, y = np.array(X), np.array(y)
    print(f'With features: {len(X)}')
    
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    
    clf = GradientBoostingClassifier(n_estimators=100, max_depth=4, random_state=42)
    clf.fit(X_train, y_train)
    preds = clf.predict(X_test)
    acc = (preds == y_test).mean()
    print(f'Accuracy: {acc:.1%}')

if __name__ == '__main__':
    main()