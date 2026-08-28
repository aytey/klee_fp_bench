#!/bin/bash
#
# Dump the queries a library's drivers put to the solver, as one SMT-LIB2 file
# per query.
#
#   dump-queries.sh <library> [tag] [driver-list]
#
# split-queries.py and replay-queries.sh both consume a corpus like this, and
# until now nothing produced one -- the corpus behind RESULTS.md was dumped by
# hand. Which is fine once and a hazard thereafter: a comparison is only as
# reproducible as the corpus it ran on.
#
# `tag` is what the filenames end in, and it is how two corpora are compared.
# replay-queries.sh times the files in `find | sort` order and runs both
# solvers back to back per file, so a slow patch of machine falls on both; put
# two tagged corpora in one directory and each query lands next to its
# counterpart, which makes the same true across the two corpora as well. That
# is what a precision axis needs:
#
#   FP_BENCH_OUT=$WORK/axis dump-queries.sh cmsisdsp     f16
#   FP_BENCH_OUT=$WORK/axis dump-queries.sh cmsisdsp-f32 f32
#   replay-queries.sh $WORK/axis/split $WORK/axis/replay.tsv
#
# The queries are dumped under one solver and replayed against both. Which one
# dumps them does not decide the answer -- KLEE builds the same query either
# way -- but it does decide the printing, and Bitwuzla's is the one both
# parsers accept after split-queries.py has been over it.
#
set -euo pipefail

LIB=${1:?usage: dump-queries.sh <library> [tag] [driver-list]}
TAG=${2:-$LIB}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=${FP_BENCH_WORK:-/mnt/baranem/fp_bench-work}
OUT=${FP_BENCH_OUT:-$WORK/$LIB/queries}
KLEE_BUILD=${KLEE_BUILD:-/mnt/baranem/klee-float/3.2-buildtest}
UCLIBC=${UCLIBC:-/mnt/baranem/klee-float/3.2-deps/klee-uclibc-16}
KLEE=${KLEE:-$KLEE_BUILD/bin/klee}
BUDGET=${BUDGET:-60}
MAX_SOLVER_TIME=${MAX_SOLVER_TIME:-5}
MAX_MEMORY=${MAX_MEMORY:-4000}
SOLVER=${SOLVER:-bitwuzla}
LIST=${3:-$WORK/$LIB/drivers.txt}

[ -f "$LIST" ] || { echo "no driver list at $LIST; run build-drivers.sh $LIB" >&2; exit 1; }

case $LIB in
  openlibm) LINK_LIBM="" ;;
  *)        LINK_LIBM="--link-llvm-lib=$UCLIBC/lib/libm.a" ;;
esac
case $LIB in
  hdf5|hdf5-f32) LIB_ARGS="--libc=uclibc --posix-runtime"
                 BUDGET=${HDF5_BUDGET:-$((BUDGET * 3))} ;;
  *)             LIB_ARGS="" ;;
esac

mkdir -p "$OUT/raw" "$OUT/split"
total=0
while read -r name; do
  [ -f "$WORK/$LIB/obj/$name.bc" ] || continue
  rm -rf "$OUT/run"
  "$KLEE" --output-dir="$OUT/run" \
    --solver-backend="$SOLVER" --search=dfs \
    --max-time="${BUDGET}s" --max-solver-time="${MAX_SOLVER_TIME}s" \
    --max-memory="$MAX_MEMORY" \
    "--debug-$SOLVER-dump-queries=$OUT/raw/$name.smt2" \
    $LINK_LIBM $LIB_ARGS "$WORK/$LIB/obj/$name.bc" > /dev/null 2>&1 || true
  [ -s "$OUT/raw/$name.smt2" ] || continue

  # Split into a directory of this driver's own, then rename the tag onto the
  # end. split-queries.py puts the index last, and the tag has to be last for
  # two corpora to interleave under `sort`.
  rm -rf "$OUT/tmp"
  "$HERE/split-queries.py" "$OUT/raw/$name.smt2" "$OUT/tmp" q > /dev/null
  for q in "$OUT/tmp"/q*.smt2; do
    [ -e "$q" ] || continue
    i=$(basename "$q" .smt2); i=${i#q}
    mv "$q" "$OUT/split/${name}__${i}__${TAG}.smt2"
    total=$((total + 1))
  done
done < "$LIST"
rm -rf "$OUT/tmp" "$OUT/run"

echo "$LIB ($TAG): $total queries in $OUT/split"
