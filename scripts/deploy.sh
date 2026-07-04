#!/usr/bin/env bash
# scripts/deploy.sh — the required post-commit step. Restarts every long-running
# service that executes committed code in-process, and verifies each came back active.
#
# "Committed" must imply "running." Before this script existed, services only picked
# up new code on the host's own daily reboot (~04:00-04:20 UTC) or an ad-hoc manual
# restart — meaning intraday commits routinely sat inert for hours (see Phase 22/23
# in docs/deployment_state.md for the incident history).
#
# Usage:
#   scripts/deploy.sh          restart all services, verify each is active
#   scripts/deploy.sh check    report staleness without restarting anything
#
# Cron-triggered scripts (daily_run.py, auto_bet.py, settle_fixtures.py, odds_poll.py,
# backfill_cron.py) are NOT in scope here — each cron invocation is a fresh process that
# reads current disk state, so they can never go stale between commits.
set -euo pipefail

REPO_ROOT="/opt/projects/bootball"
cd "$REPO_ROOT"

# Every long-running service that imports and executes this repo's code in-process.
# If you add a new systemd service running app code, add it here too.
SERVICES=(
  bootball-runtime.service    # APScheduler + AgentCoordinator + settlement (backend/runtime/execution_runtime.py)
  bootball-web-v2.service     # V2 Flask UI, port 5000 (scripts/web_ui_v2.py)
  bootball-web.service        # V1 Flask UI, port 5001 (scripts/web_ui.py) — reference only, frozen
)

STATE_DIR="$REPO_ROOT/logs/deploy_state"
mkdir -p "$STATE_DIR"
# bootball-runtime/web-v2 run as user 'bootball' and self-report their commit into this
# dir — it must stay bootball-writable or those writes silently fail (same class of bug
# as the CACHE_DIR permission gap; see docs/deployment_state.md).
chown bootball:bootball "$STATE_DIR" 2>/dev/null || true

COMMIT=$(git rev-parse HEAD)
COMMIT_SHORT=$(git rev-parse --short HEAD)

if [ "${1:-}" == "check" ]; then
  echo "HEAD is $COMMIT_SHORT ($(git log -1 --format=%s))"
  echo
  status=0
  for svc in "${SERVICES[@]}"; do
    running_file="$STATE_DIR/${svc}.running_commit"
    deploy_file="$STATE_DIR/${svc}.commit"
    if [ -f "$running_file" ]; then
      running=$(tr -d '[:space:]' < "$running_file")
      src="self-reported"
    elif [ -f "$deploy_file" ]; then
      running=$(tr -d '[:space:]' < "$deploy_file")
      src="deploy.sh record (not self-reported — restart method since then is unverified)"
    else
      echo "$svc: UNKNOWN — never restarted via deploy.sh and has no self-reported commit"
      status=1
      continue
    fi
    if [ "$running" == "$COMMIT" ]; then
      echo "$svc: up to date ($running, $src)"
    else
      behind=$(git rev-list --count "${running}..${COMMIT}" 2>/dev/null || echo "?")
      echo "$svc: STALE — running ${running:0:9}, HEAD is $COMMIT_SHORT ($behind commits behind) [$src]"
      status=1
    fi
  done
  exit $status
fi

echo "Deploying commit $COMMIT_SHORT: $(git log -1 --format=%s)"
echo

FAILED=0
declare -A SERVICE_ACTIVE
for svc in "${SERVICES[@]}"; do
  echo "== $svc =="
  systemctl restart "$svc"

  active=0
  for _ in $(seq 1 15); do
    if systemctl is-active --quiet "$svc"; then
      active=1
      break
    fi
    sleep 1
  done

  SERVICE_ACTIVE[$svc]=$active
  if [ "$active" -eq 1 ]; then
    echo "$COMMIT" > "$STATE_DIR/${svc}.commit"
    echo "  ACTIVE (commit recorded: $COMMIT_SHORT)"
  else
    echo "  FAILED TO START — status:"
    systemctl status "$svc" --no-pager -l | tail -20
    FAILED=1
  fi
  echo
done

# Phase 30: push the deploy result to Discord (V2 identity) — turns the
# committed-but-not-running class of bug into a notification instead of a
# silent gap discovered later. Best-effort: never fails the deploy itself.
SERVICES_JSON="{"
for svc in "${SERVICES[@]}"; do
  val="false"; [ "${SERVICE_ACTIVE[$svc]}" -eq 1 ] && val="true"
  SERVICES_JSON+="\"$svc\": $val, "
done
SERVICES_JSON="${SERVICES_JSON%, }}"
python3 -c "
import json, sys
sys.path.insert(0, '$REPO_ROOT')
from src.notifications.v2_discord_notifier import notify_deploy_complete
notify_deploy_complete('$COMMIT_SHORT', json.loads('$SERVICES_JSON'))
" || echo "  (deploy notification failed — non-fatal)"

if [ "$FAILED" -ne 0 ]; then
  echo "DEPLOY FAILED — one or more services did not come back active. See above."
  exit 1
fi

echo "All ${#SERVICES[@]} services restarted and active on commit $COMMIT_SHORT."
echo "Run 'scripts/deploy.sh check' any time to confirm what's actually running vs HEAD."
