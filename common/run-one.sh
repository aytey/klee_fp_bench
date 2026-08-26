#!/bin/bash
#
# Run one driver under one solver configuration, replay its tests, measure.
#
#   run-one.sh <library> <function> <config-label> [search]
#
# The configuration's actual solver arguments come from the environment, so a
# sweep can vary them per job: SOLVER, EXTRA_ARGS, STP_LIB_DIR.
#
# Appends one pipe-separated row to $OUT/results.psv.
#
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=${FP_BENCH_WORK:-/mnt/baranem/fp_bench-work}
KLEE_BUILD=${KLEE_BUILD:-/mnt/baranem/klee-float/3.2-buildtest}
UCLIBC=${UCLIBC:-/mnt/baranem/klee-float/3.2-deps/klee-uclibc-16}
KLEE=${KLEE:-$KLEE_BUILD/bin/klee}

LIB=$1 name=$2 label=$3 search=${4:-dfs}
OUT=${FP_BENCH_OUT:-$WORK/$LIB/runs}

BUDGET=${BUDGET:-60}                  # exploration budget, seconds
# 5s, not 30s. A query that is going to time out burns the whole cap and then
# has its state discarded, and on this corpus that tail is 29% of all solver
# time for a tenth of a percent of the queries. Measured over the 45 drivers
# that exhaust the exploration budget, dropping the cap from 30s to 5s is
# 2.165x the instructions per driver and +0.95 coverage points, with no driver
# covering less -- against the -0.32 points that separates STP from Bitwuzla.
# Lowering it does not make a hard query answerable; it makes giving up cheap.
MAX_SOLVER_TIME=${MAX_SOLVER_TIME:-5}
# SIGKILL if the run overruns that badly. The wall has to allow for the query
# cap as well as the budget: KLEE only notices the budget has expired between
# instructions, so a run ends by finishing the queries in flight and then
# solving once per state it still holds, each of which may take the full cap.
#
# That is a widening, not a cure. OpenLibm's j0 and j1 are still killed here at
# a 30s cap and still finish in seconds at a 5s one -- the cap decides how long
# a doomed state survives, so a large cap can cost more wall time than the
# budget it was meant to protect. A killed run is recorded as such and
# aggregate.py already excludes it from the comparable set.
HARD=${HARD:-$((BUDGET * 5 / 2 + MAX_SOLVER_TIME * 3))}
MAX_MEMORY=${MAX_MEMORY:-4000}
REPLAY_TIMEOUT=${REPLAY_TIMEOUT:-5}
MAX_REPLAY=${MAX_REPLAY:-0}           # 0 = replay every test
SKIP_REPLAY=${SKIP_REPLAY:-0}
SOLVER=${SOLVER:-stp}
# Every library here calls into a libm that has to come from somewhere, and
# klee-uclibc's is what supplies it -- except for the library that *is* a libm.
# Linking uclibc's alongside OpenLibm would put two definitions of sin in the
# module and leave which one is being measured up to the linker.
case $LIB in
  openlibm) LINK_LIBM="" ;;
  *)        LINK_LIBM="--link-llvm-lib=$UCLIBC/lib/libm.a" ;;
esac
EXTRA_ARGS=${EXTRA_ARGS:-}
STP_LIB_DIR=${STP_LIB_DIR:-}

dir=$OUT/$label/$name
log=$OUT/$label/$name.log
mkdir -p "$(dirname "$dir")"
rm -rf "$dir"

start=$(date +%s.%N)
LD_LIBRARY_PATH="${STP_LIB_DIR:+$STP_LIB_DIR:}${LD_LIBRARY_PATH:-}" \
timeout -s KILL "$HARD" "$KLEE" \
  --output-dir="$dir" \
  --solver-backend="$SOLVER" \
  --search="$search" \
  --max-time="${BUDGET}s" \
  --max-solver-time="${MAX_SOLVER_TIME}s" \
  --max-memory="$MAX_MEMORY" \
  $LINK_LIBM \
  $EXTRA_ARGS \
  "$WORK/$LIB/obj/$name.bc" > "$log" 2>&1
rc=$?
end=$(date +%s.%N)

ntests=$(find "$dir" -maxdepth 1 -name '*.ktest' 2>/dev/null | wc -l)
nerr=$(find "$dir" -maxdepth 1 -name '*.err' 2>/dev/null | wc -l)

prof="$dir/prof"
cov="0,0,0,0,0,0,0,0"
if [ "$ntests" -gt 0 ] && [ "$SKIP_REPLAY" = 0 ]; then
  mkdir -p "$prof"
  # As a group: a replay that dies on a signal makes the shell announce it, and
  # a library aborting on a domain error is a normal outcome here.
  {
    i=0
    for k in "$dir"/*.ktest; do
      i=$((i + 1))
      [ "$MAX_REPLAY" -gt 0 ] && [ "$i" -gt "$MAX_REPLAY" ] && break
      KTEST_FILE=$k LLVM_PROFILE_FILE="$prof/$i.profraw" \
        LD_LIBRARY_PATH="$KLEE_BUILD/lib" \
        timeout -s KILL "$REPLAY_TIMEOUT" "$WORK/$LIB/bin/$name"
    done
  } > /dev/null 2>&1
  # The symbol the coverage report carries, which is not always the name the
  # driver is called after -- see functions.tsv.
  sym=$(awk -F'\t' -v d="$name" '$1 == d {print $2; exit}' \
        "$HERE/../$LIB/drivers/functions.tsv" 2>/dev/null)
  cov=$("$HERE/coverage.py" "$WORK/$LIB/bin/$name" "$prof" "${sym:-$name}" 2>/dev/null \
        || echo "0,0,0,0,0,0,0,0")
fi
nreplayed=$(find "$prof" -name '*.profraw' 2>/dev/null | wc -l)

mkdir -p "$OUT"
printf '%s|%s|%d|%s|%d|%d|%d|%s\n' \
  "$label" "$name" "$rc" "$(echo "$end - $start" | bc)" \
  "$ntests" "$nerr" "$nreplayed" "$cov" >> "$OUT/results.psv"

rm -rf "$prof"
