#!/bin/bash
#
# Run a table of solver configurations over a library's drivers, interleaved.
#
#   matrix-sweep.sh <library> <configs.tsv> [stride]
#
# The table is one configuration per line, tab separated, '#' comments allowed:
#
#   label <TAB> --solver-backend <TAB> LD_LIBRARY_PATH for libstp (or -) <TAB> extra klee args
#
# Configurations are interleaved per driver rather than run as blocks. Run as
# blocks, whichever goes first is measured against whatever the machine was
# still finishing and becomes a bad baseline for the rest -- which is not
# hypothetical: an earlier sweep elsewhere had two configurations that differed
# in nothing land 28% apart, and eleven of twelve beating the block that ran
# first.
#
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=${FP_BENCH_WORK:-/mnt/baranem/fp_bench-work}

LIB=${1:?usage: matrix-sweep.sh <library> <configs.tsv> [stride]}
TABLE=${2:?usage: matrix-sweep.sh <library> <configs.tsv> [stride]}
STRIDE=${3:-1}
OUT=${FP_BENCH_OUT:-$WORK/$LIB/runs}
SEARCH=${SEARCH:-dfs}
PAR=${PAR:-20}

[ -f "$WORK/$LIB/drivers.txt" ] || { echo "run build-drivers.sh $LIB first" >&2; exit 1; }
[ -f "$TABLE" ] || { echo "no such config table: $TABLE" >&2; exit 1; }

mkdir -p "$OUT"
sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$TABLE" | envsubst > "$OUT/configs.tsv"
# (NR-1) % s, not NR % s: the latter selects nothing at all when s is 1.
awk -v s="$STRIDE" '(NR - 1) % s == 0' "$WORK/$LIB/drivers.txt" > "$OUT/drivers.txt"

: > "$OUT/jobs.txt"
while read -r d; do
  cut -f1 "$OUT/configs.tsv" | while read -r label; do
    printf '%s %s %s\n' "$d" "$label" "$SEARCH"
  done >> "$OUT/jobs.txt"
done < "$OUT/drivers.txt"

: > "$OUT/results.psv"
echo "$LIB: $(wc -l < "$OUT/drivers.txt") drivers x $(wc -l < "$OUT/configs.tsv") configurations"\
     "= $(wc -l < "$OUT/jobs.txt") runs, $PAR at a time"

export FP_BENCH_WORK=$WORK FP_BENCH_OUT=$OUT OUT HERE LIB

run_job() {
  local name=$1 label=$2 search=$3 row lib
  row=$(awk -F'\t' -v l="$label" '$1 == l {print; exit}' "$OUT/configs.tsv")
  lib=$(printf '%s' "$row" | cut -f3)
  [ "$lib" = "-" ] && lib=""
  SOLVER=$(printf '%s' "$row" | cut -f2) \
  STP_LIB_DIR=$lib \
  EXTRA_ARGS=$(printf '%s' "$row" | cut -f4) \
    "$HERE/run-one.sh" "$LIB" "$name" "$label" "$search"
}
export -f run_job

xargs -r -a "$OUT/jobs.txt" -P "$PAR" -L1 bash -c 'run_job "$@"' _

echo "done: $(wc -l < "$OUT/results.psv") runs"
"$HERE/aggregate.py" "$LIB"
