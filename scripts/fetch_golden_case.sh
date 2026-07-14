#!/usr/bin/env bash
# Materialize the VERDICT golden case under data/re2_ss/.
#
# RE2-SS is distributed as a single archive (RE2-SS.zip, ~245 MB) from the
# RCAEval benchmark. This script extracts ONLY the golden case so the golden
# harness has data to run against, without committing the dataset.
#
# Resolution order for the archive:
#   1) $1 (first arg)            2) $RE2SS_ZIP env var
#   3) ./RE2-SS.zip  ./data/RE2-SS.zip
#   4) any RE2-SS.zip in a sibling directory of the repo
set -euo pipefail
cd "$(dirname "$0")/.."

GOLDEN_CASE="${GOLDEN_CASE:-catalogue_cpu}"
GOLDEN_RUN="${GOLDEN_RUN:-1}"
DEST="data/re2_ss/${GOLDEN_CASE}/${GOLDEN_RUN}"

if [ -f "${DEST}/simple_metrics.csv" ]; then
  echo "golden case already present: ${DEST}"
  exit 0
fi

find_zip() {
  [ -n "${1:-}" ] && [ -f "$1" ] && { echo "$1"; return; }
  [ -n "${RE2SS_ZIP:-}" ] && [ -f "${RE2SS_ZIP}" ] && { echo "${RE2SS_ZIP}"; return; }
  for c in "./RE2-SS.zip" "./data/RE2-SS.zip"; do
    [ -f "$c" ] && { echo "$c"; return; }
  done
  # sibling directories of the repo
  local hit
  hit="$(find .. -maxdepth 2 -iname 'RE2-SS.zip' 2>/dev/null | head -1 || true)"
  [ -n "$hit" ] && { echo "$hit"; return; }
  echo ""
}

ZIP="$(find_zip "${1:-}")"
if [ -z "$ZIP" ]; then
  echo "ERROR: RE2-SS.zip not found." >&2
  echo "Pass its path:  scripts/fetch_golden_case.sh /path/to/RE2-SS.zip" >&2
  echo "or set RE2SS_ZIP, or place it at ./RE2-SS.zip." >&2
  echo "Dataset: RCAEval RE2-SS (Sock-Shop fault-injection benchmark)." >&2
  exit 1
fi

echo "extracting ${GOLDEN_CASE}/${GOLDEN_RUN} from ${ZIP} ..."
TMP="data/re2_ss/_extract_tmp"
rm -rf "$TMP"
mkdir -p "$TMP"
unzip -o -q "$ZIP" "RE2-SS/${GOLDEN_CASE}/${GOLDEN_RUN}/*" -d "$TMP"
mkdir -p "data/re2_ss/${GOLDEN_CASE}"
rm -rf "$DEST"
mv "$TMP/RE2-SS/${GOLDEN_CASE}/${GOLDEN_RUN}" "$DEST"
rm -rf "$TMP"

echo "golden case ready: ${DEST}"
ls -1 "${DEST}"
