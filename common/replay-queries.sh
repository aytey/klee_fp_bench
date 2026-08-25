#!/bin/bash
#
# Time every query in a corpus against each solver, on identical input.
#
#   replay-queries.sh <query-dir> <out.tsv> [timeout-seconds]
#
# This is the only comparison of two solvers with nothing else in it. Inside
# KLEE they stop being asked the same questions the moment one of them times
# out a query the other answered: the state dies, the path is not explored, and
# every later query differs. Here both get the same file.
#
# Each query is run against both solvers back to back rather than all of one
# then all of the other, so that a slow patch of machine falls on both.
#
set -uo pipefail

DIR=${1:?usage: replay-queries.sh <query-dir> <out.tsv> [timeout]}
OUT=${2:?usage: replay-queries.sh <query-dir> <out.tsv> [timeout]}
TMO=${3:-10}
PAR=${PAR:-6}

STP=${STP_BIN:-/mnt/baranem/klee-float/deps/install-stp-term2/bin/stp}
BTW=${BITWUZLA_BIN:-/home/avj/clones/bitwuzla/main/build/src/main/bitwuzla}

for b in "$STP" "$BTW"; do
  [ -x "$b" ] || { echo "no solver binary at $b" >&2; exit 1; }
done

printf 'query\tbytes\tstp_s\tstp_res\tbtw_s\tbtw_res\n' > "$OUT"

one() {
  local f=$1 n bytes t0 t1 sres bres ssec bsec
  n=$(basename "$f" .smt2)
  bytes=$(wc -c < "$f")
  t0=$(date +%s.%N)
  sres=$(timeout "$TMO" "$STP" --SMTLIB2 "$f" 2>/dev/null | tail -1)
  t1=$(date +%s.%N); ssec=$(echo "$t1 - $t0" | bc)
  [ -z "$sres" ] && sres=timeout
  t0=$(date +%s.%N)
  bres=$(timeout "$TMO" "$BTW" "$f" 2>/dev/null | tail -1)
  t1=$(date +%s.%N); bsec=$(echo "$t1 - $t0" | bc)
  [ -z "$bres" ] && bres=timeout
  printf '%s\t%s\t%.3f\t%s\t%.3f\t%s\n' "$n" "$bytes" "$ssec" "$sres" "$bsec" "$bres"
}
export -f one; export STP BTW TMO

find "$DIR" -name '*.smt2' | sort |
  xargs -P "$PAR" -I{} bash -c 'one "$@"' _ {} >> "$OUT"

echo "$(($(wc -l < "$OUT") - 1)) queries timed, into $OUT"
