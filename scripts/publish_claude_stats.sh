#!/bin/bash
# Recalcule les stats Claude sur le serveur et les pousse sur le dépôt de profil.
# Tourne depuis un clone dédié (cron mensuel, le 1er à 05h30, après l'Action GitHub).
#
#   PROFILE_BRANCH   branche publiée (défaut : refonte-profil, à passer à main après fusion)
#   PROFILE_STATE    agrégats mensuels conservés hors dépôt
#   HEAVY_RUN        wrapper mémoire de la VM (heavy-run), ignoré s'il n'existe pas
set -euo pipefail

BRANCH="${PROFILE_BRANCH:-refonte-profil}"
STATE="${PROFILE_STATE:-$HOME/profile-stats/claude-state.json}"
HEAVY_RUN="${HEAVY_RUN:-/home/louis/agents/fynex/scripts/heavy-run}"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

cd "$(dirname "$0")/.."
git fetch -q origin "$BRANCH"
git checkout -q "$BRANCH"
git reset -q --hard "origin/$BRANCH"

run=(python3 scripts/claude_stats.py --state "$STATE")
if [ -x "$HEAVY_RUN" ]; then run=("$HEAVY_RUN" "${run[@]}"); fi
"${run[@]}"

if git diff --quiet -- claude-stats.json assets; then
  echo "$(date -Is) no change"
  exit 0
fi
git add claude-stats.json assets/claude-*.svg
git commit -q -m "chore(claude-stats): refresh Claude usage stats"
git push -q origin "HEAD:$BRANCH"
echo "$(date -Is) pushed to $BRANCH"
