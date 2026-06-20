#!/bin/bash
# Functional non-regression recette: regenerate the FULL deliverable (every page
# + digest) with the CURRENT code AND an older commit, then byte-diff the two.
# This is what proves a refactor does not change the report a user sees — a
# technical unit test (test_history.py) does NOT (e.g. 92 == 92.0 in Python but
# renders "92.0"). Stdlib + git only; runs on COPIES, never the real workdirs.
#
# Usage:
#   scripts/recette_nonreg.sh <old-git-ref> <workdir_prev> <workdir_last> [more...]
# Example:
#   scripts/recette_nonreg.sh v2.0.0 \
#     ~/raids/equipage-du-roux ~/raids/equipage-du-roux-2026-06-18
#
# evolution.py needs >=2 weeks; pass them in chronological order. Exit 0 +
# "DIFF VIDE" = functional non-regression proven. Any diff is printed and must
# be explained line by line (a number "12" vs "12.0" is a real render change).
#
# Note: the OLD code cannot READ an lzma-migrated cache (it predates the
# decompress) — harmless here, because analyze/evolution/pages read the
# EXTRACTED tables, not wcl_raw. A bench module that re-fetches may warn on the
# old side; bench.json is then carried over from the new run (same unchanged
# code), so it compares equal.
set -u
SELF=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$SELF/.." && git rev-parse --show-toplevel 2>/dev/null) || {
  echo "not a git repo: $SELF/.."; exit 2; }

[ $# -ge 3 ] || { echo "usage: $0 <old-git-ref> <workdir_prev> <workdir_last> [...]"; exit 2; }
OLDREF=$1; shift
WDS=("$@")

TMP=$(mktemp -d)
OLD="$TMP/old-code"
cleanup() { git -C "$REPO" worktree remove --force "$OLD" 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

echo "### old worktree @ $OLDREF"
git -C "$REPO" worktree add --detach "$OLD" "$OLDREF" >/dev/null 2>&1 || {
  echo "cannot create worktree at $OLDREF"; exit 2; }

# copy workdirs (outputs are rewritten in place by each run)
COPIES=()
for i in "${!WDS[@]}"; do
  cp -r "${WDS[$i]}" "$TMP/wd$i"; COPIES+=("$TMP/wd$i")
done
LAST="${COPIES[-1]}"

run_pipeline() {  # $1 = scripts dir
  local S=$1
  for wd in "${COPIES[@]}"; do
    RAID_WORKDIR="$wd" python3 "$S/analyze.py" all >/dev/null 2>&1
  done
  python3 "$S/evolution.py" "${COPIES[@]}" >/dev/null 2>&1
  RAID_WORKDIR="$LAST" python3 "$S/pages.py" >/dev/null 2>&1
}

echo "### run NEW ($REPO/scripts)"; run_pipeline "$REPO/scripts"
mkdir -p "$TMP/snapN"; cp -r "$LAST/digests" "$LAST/pages" "$TMP/snapN/"

echo "### run OLD ($OLDREF)"; run_pipeline "$OLD/scripts"
mkdir -p "$TMP/snapO"; cp -r "$LAST/digests" "$LAST/pages" "$TMP/snapO/"

echo "### DIFF deliverable OLD vs NEW"
if diff -rq "$TMP/snapO" "$TMP/snapN"; then
  echo ">>> DIFF VIDE — deliverable byte-identical, functional non-regression OK"
  exit 0
else
  echo ">>> differences above — explain each before claiming non-regression"
  exit 1
fi
