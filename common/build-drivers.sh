#!/bin/bash
#
# Compile every generated driver, twice.
#
#   build-drivers.sh <library>
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

LIB=${1:?usage: build-drivers.sh <library>}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
WORK=${FP_BENCH_WORK:-/mnt/baranem/fp_bench-work}
LLVM_PREFIX=${LLVM_PREFIX:-/mnt/baranem/llvm16/install}
KLEE_SRC=${KLEE_SRC:-/mnt/baranem/klee-float/3.2}
KLEE_BUILD=${KLEE_BUILD:-/mnt/baranem/klee-float/3.2-buildtest}
NATIVE_CC=${NATIVE_CC:-/usr/bin/clang}
JOBS=${JOBS:-$(nproc)}

# quadmath.h ships with GCC's libquadmath headers, not with clang -- and not
# necessarily with the *default* gcc: this machine's gcc 11 has none and its
# gcc 7 does. Any of them will do, because the header only declares the ABI;
# the implementation a driver links is the bitcode build-libquadmath.sh makes.
quadmath_include() {
  local d
  d=$(dirname "$(gcc -print-file-name=include/quadmath.h)")
  if [ ! -f "$d/quadmath.h" ]; then
    d=$(find /usr/lib64/gcc /usr/lib/gcc -path '*linux*/include/quadmath.h' \
          -printf '%h\n' 2>/dev/null | sort -V | tail -1)
  fi
  [ -n "$d" ] && [ -f "$d/quadmath.h" ] ||
    { echo "no quadmath.h on this machine; install GCC's libquadmath headers" >&2
      exit 1; }
  printf '%s' "$d"
}

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
  cxsparse) INCBC=$WORK/cxsparse-bc/inst/include/suitesparse
            INCCOV=$WORK/cxsparse-cov/inst/include/suitesparse
            # cxsparse before suitesparseconfig: it calls into it.
            LIBS="$WORK/cxsparse-cov/inst/lib64/libcxsparse.a $WORK/cxsparse-cov/inst/lib64/libsuitesparseconfig.a" ;;
  openlibm) INCBC=$WORK/openlibm-bc/inst/include/openlibm
            INCCOV=$WORK/openlibm-cov/inst/include/openlibm
            LIBS="$WORK/openlibm-cov/inst/lib/libopenlibm.a" ;;
  sundials|sundials-f128|sundials-f16)
            INCBC=$WORK/$LIB-bc/inst/include; INCCOV=$WORK/$LIB-cov/inst/include
            # Dependency order, because a static link resolves left to right:
            # the dense solver calls the dense matrix, which calls the vector,
            # which calls core.
            L=$WORK/$LIB-cov/inst/lib64
            if [ "$LIB" = sundials-f128 ]; then
              EXTRA_BC="$WORK/libquadmath/libquadmath-math.bc"
              EXTRA_CFLAGS="-I$(quadmath_include)"
              EXTRA_LIBS="-L$(dirname "$(gcc -print-file-name=libquadmath.so)") -lquadmath"
            fi
            LIBS="$L/libsundials_sunlinsoldense.a $L/libsundials_sunmatrixdense.a $L/libsundials_nvecserial.a $L/libsundials_core.a" ;;
  # Two arms over one build: cmsisdsp-f32 reads cmsisdsp's trees and links
  # cmsisdsp's bitcode. BCLIB is what carries that to build_one.
  cmsisdsp|cmsisdsp-f32)
            INCBC=$WORK/cmsisdsp-bc/inst/include/CMSIS-DSP
            INCCOV=$WORK/cmsisdsp-cov/inst/include/CMSIS-DSP
            LIBS="$WORK/cmsisdsp-cov/inst/lib64/libCMSISDSP.a"
            BCLIB=cmsisdsp ;;
  hdf5|hdf5-f32)
            INCBC=$WORK/hdf5-bc/inst/include; INCCOV=$WORK/hdf5-cov/inst/include
            LIBS="$WORK/hdf5-cov/inst/lib64/libhdf5.a"
            BCLIB=hdf5 ;;
  fftwq)    INCBC=$WORK/fftwq-bc/api;             INCCOV=$WORK/fftwq-cov/api
            LIBS="$WORK/fftwq-cov/.libs/libfftw3q.a"
            # libquadmath is not in the module. build-libquadmath.sh compiles
            # its math/ directory to bitcode and it is llvm-linked in below,
            # rather than passed to KLEE as --link-llvm-lib, so that
            # opt -internalize -globaldce prunes the 95 unused translation
            # units away before KLEE ever loads the module -- the same reason
            # the library bitcode is linked and pruned per driver today.
            # libfftw3q's only references into it are sinq and cosq.
            EXTRA_BC="$WORK/libquadmath/libquadmath-math.bc"
            # The same gate the build needed: without it fftw3.h preprocesses
            # its whole fftwq_* block away and the driver will not compile.
            EXTRA_CFLAGS="-U__GNUC_MINOR__ -D__GNUC_MINOR__=6"
            EXTRA_LIBS="-L$(dirname "$(gcc -print-file-name=libquadmath.so)") -lquadmath" ;;
  # One tree per precision: see the note in build-lib.sh's f2clapack arm.
  f2clapack|f2clapack-f64|f2clapack-f32|f2clapack-f16)
            INCBC=$WORK/$LIB-bc;                  INCCOV=$WORK/$LIB-cov
            # lapack before blas: a static link resolves left to right.
            LIBS="$WORK/$LIB-cov/libf2clapack.a $WORK/$LIB-cov/libf2cblas.a"
            EXTRA_CFLAGS="-I$(quadmath_include)"
            if [ "$LIB" = f2clapack ]; then
              EXTRA_BC="$WORK/libquadmath/libquadmath-math.bc"
              EXTRA_LIBS="-L$(dirname "$(gcc -print-file-name=libquadmath.so)") -lquadmath"
            fi ;;
  cuba)     INCBC=$WORK/cuba-bc;                  INCCOV=$WORK/cuba-cov
            LIBS="$WORK/cuba-cov/libcubaq.a"
            EXTRA_BC="$WORK/libquadmath/libquadmath-math.bc"
            # Cuba's quad header includes <quadmath.h>, which ships with GCC.
            EXTRA_CFLAGS="-I$(quadmath_include)"
            EXTRA_LIBS="-L$(dirname "$(gcc -print-file-name=libquadmath.so)") -lquadmath" ;;
  *) echo "unknown library: $LIB" >&2; exit 2 ;;
esac

# Empty for every library that needs none of this, which is the original seven.
BCLIB=${BCLIB:-$LIB}
EXTRA_BC=${EXTRA_BC:-}
EXTRA_CFLAGS=${EXTRA_CFLAGS:-}
EXTRA_LIBS=${EXTRA_LIBS:-}

if [ -n "$EXTRA_BC" ] && [ ! -f "$EXTRA_BC" ]; then
  echo "$LIB needs $EXTRA_BC; run common/build-libquadmath.sh first" >&2
  exit 1
fi

# A KLEE that predates the format this library is about does not fail loudly.
# It reports "Unsupported FAdd operation", writes one test, and a sweep over it
# produces a full set of rows with a plausible-looking coverage number in each
# -- exactly the "a driver can run and still measure nothing" failure README.md
# exists to prevent. Ask once, here, rather than find out from the numbers.
case $LIB in
  fftwq|cuba|f2clapack|sundials-f128)         PROBE_TYPE=__float128 ;;
  cmsisdsp|hdf5|f2clapack-f16|sundials-f16)  PROBE_TYPE=_Float16 ;;
  *)              PROBE_TYPE="" ;;
esac
if [ -n "$PROBE_TYPE" ]; then
  t=$(mktemp -d)
  cat > "$t/p.c" <<EOF
#include <klee/klee.h>
int main(void) {
  $PROBE_TYPE a, b;
  klee_make_symbolic(&a, sizeof a, "a");
  klee_make_symbolic(&b, sizeof b, "b");
  $PROBE_TYPE c = a + b;
  return c > (${PROBE_TYPE})0;
}
EOF
  "$LLVM_PREFIX/bin/clang" -emit-llvm -O1 -c -I"$KLEE_SRC/include" \
    -o "$t/p.bc" "$t/p.c" 2>/dev/null
  probe_out=$("$KLEE_BUILD/bin/klee" --output-dir="$t/out" "$t/p.bc" 2>&1 || true)
  rm -rf "$t"
  case $probe_out in
    *Unsupported*)
      echo "$KLEE_BUILD/bin/klee cannot execute $PROBE_TYPE arithmetic:" >&2
      echo "  $(printf '%s' "$probe_out" | grep -m1 Unsupported)" >&2
      echo "  point KLEE_BUILD at a build carrying the floating-point commits." >&2
      exit 1 ;;
  esac
fi

mkdir -p "$WORK/$LIB/obj" "$WORK/$LIB/bin"

# A driver that will not build has to say so. Swallowing the error and
# printing a count leaves the suite quietly smaller than it looks: six GSL
# drivers went missing this way, and the reason turned out to be opt dying of a
# bus error under parallel load -- a machine limit, nothing to do with the
# driver, and worth one retry. Reasons land in build-failed.tsv, the same way
# the generator writes skipped.tsv.
build_one() {
  local c=$1 n step
  n=$(basename "$c" .c)
  local bc=$WORK/$LIB/obj/$n.bc
  local exe=$WORK/$LIB/bin/$n
  local err=$WORK/$LIB/obj/$n.err

  if [ ! -f "$bc" ]; then
    for step in 1 2; do
      : > "$err"
      "$LLVM_PREFIX/bin/clang" -emit-llvm -O0 -g -c \
        $EXTRA_CFLAGS -I"$INCBC" -I"$KLEE_SRC/include" -o "$bc.d" "$c" 2>>"$err" &&
      "$LLVM_PREFIX/bin/llvm-link" "$bc.d" "$WORK/$BCLIB.bc" $EXTRA_BC \
        -o "$bc.l" 2>>"$err" &&
      "$LLVM_PREFIX/bin/opt" -internalize-public-api-list=main \
        -passes='internalize,globaldce' "$bc.l" -o "$bc" 2>>"$err" && break
    done
    rm -f "$bc.d" "$bc.l"
    [ -f "$bc" ] || { report "$n" "$err"; return 1; }
  fi

  if [ ! -f "$exe" ]; then
    : > "$err"
    "$NATIVE_CC" -O0 -g -fprofile-instr-generate -fcoverage-mapping \
      $EXTRA_CFLAGS -I"$INCCOV" -I"$KLEE_SRC/include" \
      -o "$exe" "$c" "$HERE/replay-flush.c" \
      $LIBS_STR -L"$KLEE_BUILD/lib" -lkleeRuntest $EXTRA_LIBS -lm 2>>"$err" ||
      { report "$n" "$err"; return 1; }
  fi
  rm -f "$err"
  echo "$n"
}

report() {
  printf '%s\t%s\n' "$1" \
    "$(tr '\n' ' ' < "$2" | cut -c1-200)" >> "$WORK/$LIB/build-failed.tsv"
}
export -f build_one report
# An array cannot be exported, and the link line is the only thing that needs
# one; the paths have no spaces in them.
export LIB WORK LLVM_PREFIX KLEE_SRC KLEE_BUILD NATIVE_CC HERE INCBC INCCOV
export BCLIB EXTRA_BC EXTRA_CFLAGS EXTRA_LIBS
export LIBS_STR="$LIBS"

printf 'driver\tbuild error\n' > "$WORK/$LIB/build-failed.tsv"
find "$ROOT/$LIB/drivers" -name '*.c' | sort |
  xargs -P "$JOBS" -I{} bash -c 'build_one "$@"' _ {} > "$WORK/$LIB/drivers.txt" || true

echo "$LIB: $(wc -l < "$WORK/$LIB/drivers.txt") of $(ls "$ROOT/$LIB/drivers"/*.c | wc -l) drivers built"
nfail=$(($(wc -l < "$WORK/$LIB/build-failed.tsv") - 1))
[ "$nfail" -gt 0 ] && echo "  $nfail did not build; why, in $WORK/$LIB/build-failed.tsv"
exit 0
