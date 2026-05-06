#!/usr/bin/env python3
"""
scripts/setup_db.py

Create all database tables.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.models import Base
from src.storage.db import get_engine

if __name__ == "__main__":
    engine = get_engine()
    print("Creating tables...")
    Base.metadata.create_all(engine)
    print("Tables created!")
    
    # List tables
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"\nTables: {', '.join(sorted(tables))}")