#!/usr/bin/env python3
"""
scripts/daily_sanity_check.py — daily detect-only sanity check.

Checks:
  1. Season mismatch — active leagues where get_season() doesn't match their NS fixture season
  2. Model HMAC — every data/model_*.pkl must verify against its .sig file
  3. No predictions — curated leagues with NS fixtures but zero predictions in last 48 hours
  4. New leagues — leagues with ≥50 FT fixtures + NS fixtures not in ALL_LEAGUE_IDS

Dedup via sanity_check_issues table:
  - First run (empty table): inserts all found issues as already-known, no Discord alert.
  - Subsequent runs: alerts on new issues and resolved ones.

Does NOT auto-fix anything.
"""
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _load_known_issues(session) -> dict[str, dict]:
    from sqlalchemy import text
    rows = session.execute(text(
        "SELECT issue_key, check_type, detail, first_seen, resolved_at "
        "FROM sanity_check_issues"
    )).fetchall()
    return {r[0]: {"check_type": r[1], "detail": r[2], "first_seen": r[3], "resolved_at": r[4]}
            for r in rows}


def _upsert_issue(session, check_type: str, issue_key: str, detail: str, now: str):
    from sqlalchemy import text
    session.execute(text("""
        INSERT INTO sanity_check_issues (check_type, issue_key, detail, first_seen, last_seen, resolved_at)
        VALUES (:ct, :key, :detail, :now, :now, NULL)
        ON CONFLICT(issue_key) DO UPDATE SET
            last_seen = :now,
            detail = :detail,
            resolved_at = NULL
    """), {"ct": check_type, "key": issue_key, "detail": detail, "now": now})


def _mark_resolved(session, issue_key: str, now: str):
    from sqlalchemy import text
    session.execute(text("""
        UPDATE sanity_check_issues SET resolved_at = :now WHERE issue_key = :key
    """), {"now": now, "key": issue_key})


# ── Checks ────────────────────────────────────────────────────────────────────

def check_season_mismatch() -> list[dict]:
    """Active leagues where NS fixture season ≠ get_season(league_id)."""
    from sqlalchemy import text
    from src.storage.db import get_session
    from config.settings import settings

    issues = []
    with get_session() as s:
        rows = s.execute(text("""
            SELECT DISTINCT league_id, season
            FROM fixtures
            WHERE status = 'NS'
              AND date >= datetime('now', '-1 day')
              AND date <= datetime('now', '+14 days')
        """)).fetchall()

    for league_id, ns_season in rows:
        expected = settings.get_season(league_id)
        if ns_season != expected:
            issues.append({
                "check_type": "season_mismatch",
                "issue_key": f"season_mismatch:league:{league_id}",
                "detail": (
                    f"League {league_id}: NS fixtures use season {ns_season} "
                    f"but get_season() returns {expected}"
                ),
            })
    return issues


def check_model_hmac() -> list[dict]:
    """Verify HMAC signatures on all data/model_*.pkl files."""
    import hmac as _hmac
    import hashlib

    from config.settings import settings as _settings
    issues = []
    signing_key = _settings.secret_key.encode()
    model_dir = _PROJECT_ROOT / "data"

    for pkl_path in sorted(model_dir.glob("model_*.pkl")):
        # safe_load uses path + ".sig" (e.g. model_h2h.pkl.sig)
        sig_path = Path(str(pkl_path) + ".sig")
        market = pkl_path.stem.replace("model_", "")

        if not sig_path.exists():
            issues.append({
                "check_type": "model_hmac",
                "issue_key": f"model_hmac:{market}:no_sig",
                "detail": f"Model {market}: .sig file missing at {sig_path}",
            })
            continue

        try:
            data = pkl_path.read_bytes()
            sig = sig_path.read_text().strip()
            expected_sig = _hmac.new(
                signing_key, data, hashlib.sha256
            ).hexdigest()
            if not _hmac.compare_digest(sig, expected_sig):
                issues.append({
                    "check_type": "model_hmac",
                    "issue_key": f"model_hmac:{market}:mismatch",
                    "detail": f"Model {market}: HMAC mismatch — .sig is stale or file was modified",
                })
        except Exception as e:
            issues.append({
                "check_type": "model_hmac",
                "issue_key": f"model_hmac:{market}:error",
                "detail": f"Model {market}: verification failed — {e}",
            })

    return issues


def check_no_predictions() -> list[dict]:
    """Curated leagues with NS fixtures in next 7 days but zero predictions in last 48h."""
    from sqlalchemy import text
    from src.storage.db import get_session
    from config.leagues import LEAGUES

    # Curated leagues are those with a "name" key (generic ones lack it)
    curated_ids = {lid for lid, info in LEAGUES.items() if "name" in info}

    issues = []
    with get_session() as s:
        rows = s.execute(text("""
            SELECT f.league_id, COUNT(DISTINCT f.id) as ns_count,
                   COUNT(DISTINCT pr.fixture_id) as pred_fixtures
            FROM fixtures f
            LEFT JOIN prediction_records pr
                ON pr.fixture_id = f.id
               AND pr.created_at >= datetime('now', '-48 hours')
            WHERE f.status = 'NS'
              AND f.date BETWEEN datetime('now') AND datetime('now', '+7 days')
              AND f.league_id IN ({})
            GROUP BY f.league_id
        """.format(",".join(str(lid) for lid in sorted(curated_ids)))
        )).fetchall()

    for league_id, ns_count, pred_fixtures in rows:
        if ns_count > 0 and pred_fixtures == 0:
            league_name = LEAGUES.get(league_id, {}).get("name", str(league_id))
            issues.append({
                "check_type": "no_predictions",
                "issue_key": f"no_predictions:league:{league_id}",
                "detail": (
                    f"League {league_id} ({league_name}): {ns_count} NS fixtures "
                    f"in next 7 days but no predictions generated in last 48h"
                ),
            })

    return issues


def check_new_leagues() -> list[dict]:
    """Leagues with ≥50 FT fixtures + upcoming NS fixtures but absent from ALL_LEAGUE_IDS."""
    from sqlalchemy import text
    from src.storage.db import get_session
    from config.leagues import ALL_LEAGUE_IDS

    known = set(ALL_LEAGUE_IDS)
    issues = []

    with get_session() as s:
        rows = s.execute(text("""
            SELECT f.league_id, COUNT(*) as ft_count
            FROM fixtures f
            WHERE f.status = 'FT'
            GROUP BY f.league_id
            HAVING ft_count >= 50
        """)).fetchall()
        ft_leagues = {r[0] for r in rows}

        ns_rows = s.execute(text("""
            SELECT DISTINCT league_id FROM fixtures
            WHERE status = 'NS'
              AND date BETWEEN datetime('now') AND datetime('now', '+14 days')
        """)).fetchall()
        ns_leagues = {r[0] for r in ns_rows}

    active_unknown = (ft_leagues & ns_leagues) - known
    for league_id in sorted(active_unknown):
        issues.append({
            "check_type": "new_league",
            "issue_key": f"new_league:{league_id}",
            "detail": (
                f"League {league_id}: ≥50 FT fixtures + upcoming NS fixtures "
                f"but not in ALL_LEAGUE_IDS — may need adding to config"
            ),
        })

    return issues


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from src.storage.db import get_session, init_db
    init_db()

    now = _now_iso()
    logger.info("=== Daily sanity check starting at %s ===", now)

    # Run all checks
    current_issues: list[dict] = []
    for check_fn in [
        check_season_mismatch,
        check_model_hmac,
        check_no_predictions,
        check_new_leagues,
    ]:
        try:
            found = check_fn()
            current_issues.extend(found)
            logger.info("%s: %d issue(s)", check_fn.__name__, len(found))
        except Exception as e:
            logger.exception("Check %s raised an exception: %s", check_fn.__name__, e)

    current_keys = {issue["issue_key"] for issue in current_issues}

    with get_session() as s:
        known = _load_known_issues(s)
        is_bootstrap = not known

        if is_bootstrap:
            logger.info("Bootstrap run — inserting %d issues as already-known (no alert)", len(current_issues))
            for issue in current_issues:
                _upsert_issue(s, issue["check_type"], issue["issue_key"], issue["detail"], now)
            s.commit()
            logger.info("Bootstrap complete.")
            return

        # Determine new and resolved issues
        previously_unresolved = {k for k, v in known.items() if v["resolved_at"] is None}
        new_issue_keys = current_keys - previously_unresolved
        resolved_keys = previously_unresolved - current_keys

        new_issues = [i for i in current_issues if i["issue_key"] in new_issue_keys]
        resolved_issues = [
            {"check_type": known[k]["check_type"], "detail": known[k]["detail"]}
            for k in resolved_keys
        ]

        # Upsert all current issues (insert new, refresh last_seen on existing)
        for issue in current_issues:
            _upsert_issue(s, issue["check_type"], issue["issue_key"], issue["detail"], now)

        # Mark resolved
        for k in resolved_keys:
            _mark_resolved(s, k, now)

        s.commit()

    logger.info(
        "Sanity check complete: %d new issue(s), %d resolved, %d ongoing",
        len(new_issues), len(resolved_issues),
        len(current_keys & {k for k, v in known.items() if v["resolved_at"] is None}),
    )

    # Discord notification
    if new_issues or resolved_issues:
        try:
            from src.notifications.discord_system_notifier import notify_sanity_check
            notify_sanity_check(new_issues, resolved_issues)
        except Exception as e:
            logger.warning("Discord notification failed: %s", e)


if __name__ == '__main__':
    main()
