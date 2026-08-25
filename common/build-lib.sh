#!/bin/bash
#
# Fetch a library, build it twice, and generate its drivers.
#
#   build-lib.sh <gsl|gmp>
#
# Twice, because the two builds answer different questions:
#
#   <lib>-bc    LLVM bitcode, via wllvm and the same clang KLEE was built
#               against. This is what KLEE symbolically executes.
#   <lib>-cov   native, with clang's source-based coverage instrumentation.
#               This is what the generated tests are replayed against, and
#               where the coverage number comes from.
#
# Both come from the same release tarball, so the source lines the coverage
# step reports are the lines KLEE executed. Their config.h files are compared
# afterwards for the same reason: a feature probe that answers differently in
# the two builds means they are not the same program.
#
set -euo pipefail

LIB=${1:?usage: build-lib.sh <gsl|gmp>}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
WORK=${FP_BENCH_WORK:-/mnt/baranem/fp_bench-work}

# The LLVM that KLEE was built against, and KLEE itself.
LLVM_PREFIX=${LLVM_PREFIX:-/mnt/baranem/llvm16/install}
KLEE_SRC=${KLEE_SRC:-/mnt/baranem/klee-float/3.2}
KLEE_BUILD=${KLEE_BUILD:-/mnt/baranem/klee-float/3.2-buildtest}
SHIM=${SHIM:-/mnt/baranem/klee-float/3.2-deps/shim-bin16}
PYENV=${PYENV:-/mnt/baranem/klee-float/deps/pyenv}

# The coverage build needs a clang carrying compiler-rt's profile runtime; the
# LLVM built for KLEE has none. It only has to agree with llvm-cov.
NATIVE_CC=${NATIVE_CC:-/usr/bin/clang}
JOBS=${JOBS:-$(nproc)}

# GSL and GMP come from ftp.gnu.org as verified tarballs. The rest are cloned:
# their releases live on github.com, which this network reaches over git but
# not over plain HTTP.
case $LIB in
  gsl) VER=2.8;   URL=https://ftp.gnu.org/gnu/gsl/gsl-2.8.tar.gz
       SHA=6a99eeed15632c6354895b1dd542ed5a855c0f15d9ad1326c6fe2b2c9e423190
       TAR=gsl-2.8.tar.gz; SRC=gsl-2.8
       # GSL's own configure picks -O2; nothing else is needed.
       CONFIGURE_EXTRA=()
       ARCHIVES=(.libs/libgsl.a cblas/.libs/libgslcblas.a) ;;
  gmp) VER=6.3.0; URL=https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz
       SHA=a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898
       TAR=gmp-6.3.0.tar.xz; SRC=gmp-6.3.0
       # GMP's mpn layer ships as hand-written assembly per architecture and
       # KLEE cannot execute it, so both builds take the C path. This is the
       # single largest caveat on any GMP number here: what is measured is
       # GMP's C fallback, not the code that ships.
       CONFIGURE_EXTRA=(--disable-assembly)
       # Only the whole library. GMP also builds a convenience archive per
       # directory, all of which libgmp.a already contains, and linking those
       # too defines every symbol twice.
       ARCHIVES=(.libs/libgmp.a) ;;
  blis) VER=git; REPO=https://github.com/flame/blis
       # 'generic' is BLIS's assembly-free configuration, for the same reason
       # GMP takes --disable-assembly: KLEE cannot execute the tuned kernels,
       # and every other configuration selects some.
       BLIS_CONFIG=generic ;;
  *) echo "unknown library: $LIB" >&2; exit 2 ;;
esac


export PATH="$PYENV/bin:$SHIM:$LLVM_PREFIX/bin:$PATH"
export LLVM_COMPILER=clang
export LLVM_COMPILER_PATH="$SHIM"
export LLVM_CC_NAME=klee-clang
export LLVM_CXX_NAME=klee-clang++

mkdir -p "$WORK"
cd "$WORK"

[ -x "$PYENV/bin/wllvm" ] || "$PYENV/bin/pip" -q install wllvm

# ---------------------------------------------------------------------------
# Libraries with their own build system, cloned rather than downloaded.
# ---------------------------------------------------------------------------
if [ "${REPO:-}" != "" ]; then
  [ -d "$WORK/$LIB-src" ] || git clone -q --depth 1 "$REPO" "$WORK/$LIB-src"

  build_blis() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$LIB-src" "$dest"
    ( cd "$dest"
      CC="$cc" CFLAGS="$cflags" ./configure --disable-shared --enable-static \
        --prefix="$dest/inst" "$BLIS_CONFIG" > config.log 2>&1
      make -j"$JOBS" > build.log 2>&1
      make install > install.log 2>&1 )
  }

  if [ ! -f "$WORK/$LIB.bc" ]; then
    build_blis "$WORK/$LIB-bc" wllvm \
      "-O2 -g -fno-vectorize -fno-slp-vectorize -Wno-implicit-function-declaration"
    a=$(find "$WORK/$LIB-bc/inst" -name 'libblis.a' | head -1)
    extract-bc -b "$a" -o "$WORK/$LIB.bc"
  fi
  if [ ! -d "$WORK/$LIB-cov/inst" ]; then
    build_blis "$WORK/$LIB-cov" "$NATIVE_CC" \
      "-O0 -g -fprofile-instr-generate -fcoverage-mapping -Wno-implicit-function-declaration"
  fi

  # The generated monolithic header, which is what the generator reads and what
  # the drivers include.
  INC=$(dirname "$(find "$WORK/$LIB-bc/inst" -name 'blis.h' | head -1)")
  rm -rf "$ROOT/$LIB/drivers"; mkdir -p "$ROOT/$LIB/drivers"
  "$HERE/gen-drivers.py" --library "$LIB" --include "$INC" --out "$ROOT/$LIB/drivers"
  echo
  echo "$LIB $VER ($BLIS_CONFIG)"
  echo "  bitcode:  $WORK/$LIB.bc"
  echo "  header:   $INC/blis.h"
  echo "  drivers:  $(ls "$ROOT/$LIB/drivers"/*.c 2>/dev/null | wc -l)"
  exit 0
fi

###############################################################################
# 1. Fetch, and say what was fetched
###############################################################################
[ -f "$TAR" ] || curl -sSL -o "$TAR" "$URL"
echo "$SHA  $TAR" | sha256sum -c -

###############################################################################
# 2. Bitcode -- what KLEE executes
###############################################################################
if [ ! -f "$WORK/$LIB.bc" ]; then
  rm -rf "$LIB-bc"
  tar xf "$TAR" && mv "$SRC" "$LIB-bc"
  ( cd "$LIB-bc"
    # -fno-vectorize keeps the bitcode scalar, so what KLEE executes lines up
    # with the -O0 coverage build. -Wno-implicit-* is not cosmetic: a current
    # clang makes implicit declarations an error, several configure probes call
    # exit() without stdlib.h, and a probe that fails to compile is recorded as
    # a failed feature -- giving the two builds different config.h files.
    CC=wllvm \
    CFLAGS="-O2 -g -fno-vectorize -fno-slp-vectorize -Wno-implicit-function-declaration -Wno-implicit-int" \
      ./configure --disable-shared --enable-static "${CONFIGURE_EXTRA[@]}" \
      > config.log 2>&1
    make -j"$JOBS" > build.log 2>&1 )
  # extract-bc names archive members by basename, and both libraries have
  # same-named objects in different directories, so the archive form silently
  # loses all but one. Take the whole-module form; the per-driver prune below
  # cuts it back down.
  bcs=()
  for a in "${ARCHIVES[@]}"; do
    extract-bc -b "$WORK/$LIB-bc/$a" -o "$WORK/$LIB-bc/$a.bc"
    bcs+=("$WORK/$LIB-bc/$a.bc")
  done
  llvm-link "${bcs[@]}" -o "$WORK/$LIB.bc"
fi

###############################################################################
# 3. Native, instrumented -- what the tests are replayed against
###############################################################################
if [ ! -d "$WORK/$LIB-cov/.libs" ]; then
  rm -rf "$LIB-cov"
  tar xf "$TAR" && mv "$SRC" "$LIB-cov"
  ( cd "$LIB-cov"
    CC="$NATIVE_CC" \
    CFLAGS="-O0 -g -fprofile-instr-generate -fcoverage-mapping -Wno-implicit-function-declaration -Wno-implicit-int" \
    LDFLAGS="-fprofile-instr-generate" \
      ./configure --disable-shared --enable-static "${CONFIGURE_EXTRA[@]}" \
      > config.log 2>&1
    make -j"$JOBS" > build.log 2>&1 )
fi

# The two trees must agree on what they compiled, or the coverage step reports
# on source KLEE never saw.
if ! diff -q <(grep -v '^/\*' "$WORK/$LIB-bc/config.h" 2>/dev/null) \
             <(grep -v '^/\*' "$WORK/$LIB-cov/config.h" 2>/dev/null) > /dev/null 2>&1; then
  echo "WARNING: the two $LIB builds configured differently:" >&2
  diff <(grep -v '^/\*' "$WORK/$LIB-bc/config.h") \
       <(grep -v '^/\*' "$WORK/$LIB-cov/config.h") >&2 || true
fi

###############################################################################
# 4. Drivers, from the header of the build that was just made
###############################################################################
INC=$WORK/$LIB-bc
[ "$LIB" = gsl ] || INC=$WORK/$LIB-bc   # both put their header at the top level
rm -rf "$ROOT/$LIB/drivers"
mkdir -p "$ROOT/$LIB/drivers"
"$HERE/gen-drivers.py" --library "$LIB" --include "$INC" \
                       --out "$ROOT/$LIB/drivers"

echo
echo "$LIB $VER"
echo "  bitcode:   $WORK/$LIB.bc"
echo "  coverage:  $WORK/$LIB-cov"
echo "  drivers:   $(ls "$ROOT/$LIB/drivers"/*.c 2>/dev/null | wc -l)"
echo "  skipped:   $ROOT/$LIB/skipped.tsv"
