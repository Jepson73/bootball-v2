"""
src/deploy_info.py — Records which git commit a long-running service started from.

Every commit ≠ every running process picking it up automatically: bootball-runtime.service
and bootball-web-v2.service only load new code when the process restarts (systemd doesn't
watch files). This module lets each service stamp its own startup commit to a file that
survives independent of *how* it was restarted (deploy script, `systemctl restart`, host
reboot), so staleness is visible without correlating journalctl timestamps against git log.

See scripts/deploy.sh (the `check` subcommand reads these files) and
docs/deployment_state.md ("Detecting a stale service").
"""
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "logs" / "deploy_state"


def record_running_commit(service_name: str) -> str | None:
    """Write the current HEAD commit to logs/deploy_state/<service_name>.running_commit.

    Call once at process startup. Returns the commit hash, or None if git isn't available
    (non-fatal — deploy staleness checks just report UNKNOWN for that service).
    """
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception as e:
        logger.warning(f"Could not determine running commit: {e}")
        return None

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"{service_name}.running_commit").write_text(commit + "\n")
    except Exception as e:
        logger.warning(f"Could not write running-commit state file: {e}")

    return commit
