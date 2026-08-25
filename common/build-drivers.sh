#!/bin/bash
#
# Compile every generated driver, twice.
#
#   build-drivers.sh <gsl|gmp>
#
#   obj/<fn>.bc   the driver linked against the whole library as bitcode and
#                 then pruned to what main can reach. KLEE spends seconds and
#                 hundreds of megabytes just loading and verifying an unpruned
#                 module, and generates exactly the same tests either way.
#   bin/<fn>      the driver against the instrumented native library, for
#                 replay. replay-flush.c goes in with it so a run that dies on
#                 a signal still writes the profile it earned.
#
set -euo pipefail

LIB=${1:?usage: build-drivers.sh <gsl|gmp>}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
WORK=${FP_BENCH_WORK:-/mnt/baranem/fp_bench-work}
LLVM_PREFIX=${LLVM_PREFIX:-/mnt/baranem/llvm16/install}
KLEE_SRC=${KLEE_SRC:-/mnt/baranem/klee-float/3.2}
KLEE_BUILD=${KLEE_BUILD:-/mnt/baranem/klee-float/3.2-buildtest}
NATIVE_CC=${NATIVE_CC:-/usr/bin/clang}
JOBS=${JOBS:-$(nproc)}

# Where each library's header lives, in each of its two builds, and what the
# replay binary links against. The two include directories differ because the
# bitcode and coverage trees are separate builds of the same source.
case $LIB in
  gsl)      INCBC=$WORK/gsl-bc;                   INCCOV=$WORK/gsl-cov
            LIBS="$WORK/gsl-cov/.libs/libgsl.a $WORK/gsl-cov/cblas/.libs/libgslcblas.a" ;;
  gmp)      INCBC=$WORK/gmp-bc;                   INCCOV=$WORK/gmp-cov
            LIBS="$WORK/gmp-cov/.libs/libgmp.a" ;;
  fftw)     INCBC=$WORK/fftw-bc/api;              INCCOV=$WORK/fftw-cov/api
            LIBS="$WORK/fftw-cov/.libs/libfftw3.a" ;;
  blis)     INCBC=$WORK/blis-bc/inst/include;     INCCOV=$WORK/blis-cov/inst/include
            LIBS="$WORK/blis-cov/inst/lib/libblis.a" ;;
  sundials) INCBC=$WORK/sundials-bc/inst/include; INCCOV=$WORK/sundials-cov/inst/include
            # nvecserial before core: a static link resolves left to right.
            LIBS="$WORK/sundials-cov/inst/lib64/libsundials_nvecserial.a $WORK/sundials-cov/inst/lib64/libsundials_core.a" ;;
  *) echo "unknown library: $LIB" >&2; exit 2 ;;
esac

mkdir -p "$WORK/$LIB/obj" "$WORK/$LIB/bin"

build_one() {
  local c=$1 n
  n=$(basename "$c" .c)
  local bc=$WORK/$LIB/obj/$n.bc
  local exe=$WORK/$LIB/bin/$n

  if [ ! -f "$bc" ]; then
    "$LLVM_PREFIX/bin/clang" -emit-llvm -O0 -g -c \
      -I"$INCBC" -I"$KLEE_SRC/include" -o "$bc.d" "$c" 2>/dev/null || return 1
    "$LLVM_PREFIX/bin/llvm-link" "$bc.d" "$WORK/$LIB.bc" -o "$bc.l" 2>/dev/null || return 1
    "$LLVM_PREFIX/bin/opt" -internalize-public-api-list=main \
      -passes='internalize,globaldce' "$bc.l" -o "$bc" 2>/dev/null || return 1
    rm -f "$bc.d" "$bc.l"
  fi

  if [ ! -f "$exe" ]; then
    "$NATIVE_CC" -O0 -g -fprofile-instr-generate -fcoverage-mapping \
      -I"$INCCOV" -I"$KLEE_SRC/include" -o "$exe" "$c" "$HERE/replay-flush.c" \
      $LIBS_STR -L"$KLEE_BUILD/lib" -lkleeRuntest -lm 2>/dev/null || return 1
  fi
  echo "$n"
}
export -f build_one
# An array cannot be exported, and the link line is the only thing that needs
# one; the paths have no spaces in them.
export LIB WORK LLVM_PREFIX KLEE_SRC KLEE_BUILD NATIVE_CC HERE INCBC INCCOV
export LIBS_STR="$LIBS"

find "$ROOT/$LIB/drivers" -name '*.c' | sort |
  xargs -P "$JOBS" -I{} bash -c 'build_one "$@"' _ {} > "$WORK/$LIB/drivers.txt" || true

echo "$LIB: $(wc -l < "$WORK/$LIB/drivers.txt") of $(ls "$ROOT/$LIB/drivers"/*.c | wc -l) drivers built"
