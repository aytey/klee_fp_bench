#!/bin/bash
#
# Sweep every library, one after another, and aggregate each.
#
#   sweep-all.sh [configs.tsv]
#
# Strides are per library rather than global. A global one would be dominated
# by GSL, which is 646 of the corpus's 1118 drivers: the small libraries are
# the ones carrying the sparse factorisations and the transforms, and a sample
# that is four fifths special functions answers a narrower question than the
# suite was built to ask. Each library is cut to roughly the same contribution.
#
# Libraries run in sequence, not in parallel, because the thing being measured
# is solver time and a second library's KLEE processes are just noise in it.
#
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=${FP_BENCH_WORK:-/mnt/baranem/fp_bench-work}
TABLE=${1:-$HERE/configs/backends.tsv}
# Which subdirectory of each library's work area the runs land in, so a second
# sweep does not overwrite the first.
RUNS=${RUNS:-runs-all}

# Where an STP carrying the MiniSat terminator lives. KLEE is linked against it
# now, so this is belt and braces; a KLEE built against the other one would
# otherwise enforce --max-solver-time only between calls into the SAT solver.
export STP_TERM=${STP_TERM:-/mnt/baranem/klee-float/deps/install-stp-term2/lib64}

# Where the STP carrying the auto CNF-effort threshold lives -- the build the
# cnfauto/cnfthreshold/vsbitwuzla tables measure. Separate from STP_TERM
# because it is a branch build until stp/stp#998 lands.
export STP_CNF=${STP_CNF:-/mnt/baranem/klee-float/deps/install-stp-cnf/lib64}

# The STP master build KLEE is linked against now, and the v3 CEGAR branch that
# is ABI-compatible with it.
export STP_MASTER=${STP_MASTER:-/mnt/baranem/klee-float/deps/install-stp-master/lib64}
export STP_CODEXV3=${STP_CODEXV3:-/mnt/baranem/klee-float/deps/install-stp-cegar-codex-v3/lib64}

# PAR and MAX_MEMORY together, not separately: this box keeps its working set
# on a RAM disk, so the memory KLEE is allowed to take is memory the drivers
# and bitcode are not holding.
export PAR=${PAR:-12}
export MAX_MEMORY=${MAX_MEMORY:-2000}
export BUDGET=${BUDGET:-60}

#            library   stride
LIBS=(  "gsl      10"
        "openlibm  3"
        "blis      4"
        "sundials  3"
        "gmp       2"
        "fftw      1"
        "cxsparse  1" )

for entry in "${LIBS[@]}"; do
  set -- $entry
  lib=$1 stride=$2
  [ -f "$WORK/$lib/drivers.txt" ] || { echo "skipping $lib: no drivers built"; continue; }
  echo "=== $lib (stride $stride) ==="
  FP_BENCH_OUT=$WORK/$lib/$RUNS \
    "$HERE/matrix-sweep.sh" "$lib" "$TABLE" "$stride" || echo "$lib: sweep returned $?"
done

echo
echo "########## aggregate ##########"
for entry in "${LIBS[@]}"; do
  set -- $entry
  lib=$1
  [ -f "$WORK/$lib/$RUNS/results.psv" ] || continue
  echo; echo "################ $lib ################"
  FP_BENCH_OUT=$WORK/$lib/$RUNS "$HERE/aggregate.py" "$lib" || true
done
