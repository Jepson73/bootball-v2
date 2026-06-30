#!/usr/bin/env python3
"""
scripts/web_ui_v2.py — Bootball V2 Web Interface

TWO-TRACK MODEL:
  Track A: Prediction accuracy (all leagues, scored on outcomes)
  Track B: EV/CLV overlay (Pinnacle-gated, collection clock not yet started)

STRICT V1 ISOLATION:
  Does NOT import from scripts/web_ui.py or any V1-only module.
  Shared infrastructure only: src/storage, config, .env.
  Safe to keep both running simultaneously; V2 on port 5000, V1 on 5001.

Shared imports (explicitly listed):
  - src/storage/db.py       get_session, init_db
  - src/storage/models.py   ORM models (Fixture, PredictionRecord, OddsSnapshot, League)
  - config/settings.py      settings (via db_v2.py)
  - config/forward_leagues  FORWARD_LEAGUE_IDS (via db_v2.py)
  - v2/*                    V2-only package (auth, db helpers, templates, routes)

V1 imports: NONE.
"""
import os
import sys
import logging
from pathlib import Path

# Add project root to path so src/ and v2/ are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, redirect, url_for
from src.storage.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("web_ui_v2")

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24).hex())

# ── Register blueprints ────────────────────────────────────────────────────────
from v2.routes.home_v2 import bp_home
from v2.routes.track_a_v2 import bp_track_a
from v2.routes.predictions_v2 import bp_predictions
from v2.routes.collection_v2 import bp_collection
from v2.routes.explorer_v2 import bp_explorer

app.register_blueprint(bp_home)
app.register_blueprint(bp_track_a)
app.register_blueprint(bp_predictions)
app.register_blueprint(bp_collection)
app.register_blueprint(bp_explorer)


@app.route("/health")
def health():
    return {"status": "ok", "version": "v2"}, 200


# ── Startup ────────────────────────────────────────────────────────────────────
def main():
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    logger.info("Bootball V2 starting on %s:%s", host, port)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
