#!/bin/bash
#
# Fetch a library, build it twice, and generate its drivers.
#
#   build-lib.sh <library>
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

LIB=${1:?usage: build-lib.sh <library>}
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

# Hooks a library may set in its case arm below. Empty for all seven of the
# original ones, so nothing changes for them.
#
#   EXTRA_CPPFLAGS  preprocessor flags both builds need. CPPFLAGS rather than
#                   CFLAGS because autoconf's AC_EGREP_CPP runs "$CPP
#                   $CPPFLAGS" and never sees CFLAGS -- which is exactly the
#                   hook FFTW's quad arm needs.
#   EXTRA_CFLAGS    the same flags again for a build system that composes its
#                   compile line out of CFLAGS alone and never reads CPPFLAGS,
#                   which is Cuba's hand-written makefile.
#   EXTRA_LDFLAGS   likewise at link time.
#   CONFIGURE_ENV   environment assignments prefixed to ./configure, for
#                   answering a feature probe that cannot be answered by a flag.
#   MAKE_TARGET     what to build, when it is not everything.
#   MAKE_JOBS       how many at once, when the makefile cannot take -j. Cuba's
#                   runs one `ar -rv` per object against one archive, and
#                   concurrent ones corrupt it: "ar: libcubaq.a: malformed
#                   archive", after which the build carries on and leaves a
#                   short library behind.
#   PRE_BUILD       a shell function run in the unpacked tree before configure.
#   BUILD_FN        a shell function that builds the whole tree, for a tarball
#                   with no configure script -- f2cblaslapack has none, and its
#                   top-level makefile includes PETSc's own configuration.
#                   Takes <destination> <cc> <cflags>, like the cloned
#                   libraries' builders.
#   GEN_CFLAGS      extra flags for the generator's clang AST probe, when the
#                   header needs the same treatment the build did.
#   GEN_HDR         the header the generator reads, when it is not the one the
#                   include directory is named for.
#   SRC_SHARES      another library whose *source clone* this arm reuses, when
#                   the build itself cannot be shared -- SUNDIALS at three
#                   precisions is one checkout compiled three ways.
#   SHARES          another library whose build this arm reuses. Two arms over
#                   one build is how a precision axis is expressed: cmsisdsp
#                   and cmsisdsp-f32 are one library read twice.
EXTRA_CPPFLAGS=""; EXTRA_CFLAGS=""; EXTRA_LDFLAGS=""; MAKE_TARGET=""; PRE_BUILD=""
MAKE_JOBS=""; BUILD_FN=""; SRC_SHARES=""; SUN_PREC=""
GEN_CFLAGS=""; GEN_HDR=""; SHARES=""; CONFIGURE_ENV=()

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
  fftw) VER=3.3.10; URL=https://www.fftw.org/fftw-3.3.10.tar.gz
       SHA=56c932549852cddcfafdab3820b0200c7742675be92179e59e6215b340e26467
       TAR=fftw-3.3.10.tar.gz; SRC=fftw-3.3.10
       # The codelets FFTW's planner dispatches to are generated by its own
       # OCaml generator at release time and ship only in the tarball; a git
       # checkout stops at "No rule to make target 'n1_2.c'".
       CONFIGURE_EXTRA=(--disable-fortran)
       ARCHIVES=(.libs/libfftw3.a)
       # fftw3.h lives under api/ in the source tree rather than at the root.
       INCSUB=api ;;
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
  openlibm) VER=v0.8.7; REPO=https://github.com/JuliaMath/openlibm; TAG=v0.8.7 ;;
  cxsparse) VER=git; REPO=https://github.com/DrTimothyAldenDavis/SuiteSparse
       # SuiteSparse ships GraphBLAS, LAGraph, ParU and a dozen more; a full
       # clone is most of a gigabyte to reach two directories. CXSparse needs
       # only itself and SuiteSparse_config.
       SPARSE="CXSparse SuiteSparse_config" ;;
  sundials) VER=git; REPO=https://github.com/LLNL/sundials ;;
  # SUNDIALS is the one library here written entirely through a numeric type of
  # its own -- sunrealtype, which its own build already switches between float,
  # double and long double. That makes its precision changeable from outside
  # without touching a line of numerics, which is what these two arms do:
  # common/sundials-precision.py adds a branch to the two headers that decide
  # the width, and the macro below selects it. One checkout, three builds.
  sundials-f128) VER=git; REPO=https://github.com/LLNL/sundials
       SRC_SHARES=sundials; SUN_PREC=quad
       EXTRA_CFLAGS="-I$(quadmath_include)"
       GEN_CFLAGS="$EXTRA_CFLAGS" ;;
  sundials-f16)  VER=git; REPO=https://github.com/LLNL/sundials
       SRC_SHARES=sundials; SUN_PREC=half ;;
  # The binary16 corpus, and its binary32 control over the same build.
  cmsisdsp) VER=git; REPO=https://github.com/ARM-software/CMSIS-DSP ;;
  cmsisdsp-f32) VER=git; REPO=https://github.com/ARM-software/CMSIS-DSP
       SHARES=cmsisdsp; GEN_HDR=arm_math.h ;;
  # HDF5's datatype conversions: binary16 in, a narrow integer out, and the
  # range check between them. Its drivers are written from a table rather than
  # read from the header -- see h5_drivers in gen-drivers.py -- but the build
  # is an ordinary one.
  hdf5) VER=git; REPO=https://github.com/HDFGroup/hdf5 ;;
  hdf5-f32) VER=git; REPO=https://github.com/HDFGroup/hdf5
       SHARES=hdf5 ;;
  # FFTW again, through its quadruple-precision fftwq_* API. Same tarball and
  # same checksum as fftw: this is one library configured twice, not two.
  fftwq) VER=3.3.10; URL=https://www.fftw.org/fftw-3.3.10.tar.gz
       SHA=56c932549852cddcfafdab3820b0200c7742675be92179e59e6215b340e26467
       TAR=fftw-3.3.10.tar.gz; SRC=fftw-3.3.10
       # FFTW gates its quad support twice on the compiler *claiming* to be
       # gcc >= 4.6: once in configure (AX_GCC_VERSION) and once in the public
       # header, api/fftw3.h:471, which is where the fftwq_* declarations
       # live. Clang reports 4.2.1, so without this the library does not build
       # and -- the more confusing half -- the generator reads a header with
       # no quad API in it at all and reports zero drivers rather than an
       # error.
       EXTRA_CPPFLAGS="-U__GNUC_MINOR__ -D__GNUC_MINOR__=6"
       GEN_CFLAGS="$EXTRA_CPPFLAGS"
       # configure link-tests -lquadmath, and there is no libquadmath.so on
       # the default search path -- only the versioned SONAME. GCC's own
       # directory has the symlink.
       EXTRA_LDFLAGS="-L$(dirname "$(gcc -print-file-name=libquadmath.so)")"
       CONFIGURE_EXTRA=(--disable-fortran --enable-quad-precision)
       ARCHIVES=(.libs/libfftw3q.a)
       INCSUB=api ;;
  # Cuba's four integration algorithms at binary128. Unlike FFTW's quad arm
  # this one branches on quad values, which is what puts it in the corpus.
  cuba) VER=4.2.2; URL=http://www.feynarts.de/cuba/Cuba-4.2.2.tar.gz
       SHA=8d9f532fd2b9561da2272c156ef7be5f3960953e4519c638759f1b52fe03ed52
       TAR=Cuba-4.2.2.tar.gz; SRC=Cuba-4.2.2
       # REALSIZE=16 is what makes cubareal a __float128 and points Cuba's
       # mathematics at libquadmath.
       CONFIGURE_EXTRA=(--with-real=16)
       # KLEE models neither shmget/shmat nor fork, and Cuba's parallel
       # sampling reaches both: without these every driver dies in
       # src/cuhre/Rule.c before the first subdivision. Answering the probes
       # rather than patching the source keeps the two builds identical.
       CONFIGURE_ENV=(ac_cv_func_fork=no ac_cv_func_shmget=no)
       # Cuba's quad configuration includes <quadmath.h> from its own headers,
       # so both builds need it on the include path -- clang does not ship one.
       # In CFLAGS as well as CPPFLAGS: Cuba's makefile composes its compile
       # line out of CFLAGS and never reads CPPFLAGS.
       EXTRA_CPPFLAGS="-I$(quadmath_include)"
       EXTRA_CFLAGS="$EXTRA_CPPFLAGS"
       GEN_CFLAGS="$EXTRA_CPPFLAGS"
       # ...but one declaration is inside the #ifdef the probe just turned
       # off, and the serial build needs it.
       PRE_BUILD=cuba_serial_fix
       MAKE_TARGET=lib
       MAKE_JOBS=1
       ARCHIVES=(libcubaq.a)
       # The quad build generates its own header; cuba.h is the binary64 one
       # and declares a different ABI.
       GEN_HDR=cubaq.h ;;
  # PETSc's f2c translation of BLAS and LAPACK, which ships a quadruple
  # precision set alongside the usual four. This is the binary128 library that
  # branches: 5,396 fcmp fp128 against FFTW's none, because partial pivoting is
  # a magnitude comparison over the matrix. f2clapack-f64 is the same routines
  # at binary64 over the same build, and is the control.
  f2clapack|f2clapack-f64|f2clapack-f32|f2clapack-f16)
       VER=3.8.0.q2
       URL=https://web.cels.anl.gov/projects/petsc/download/externalpackages/f2cblaslapack-3.8.0.q2.tar.gz
       SHA=12fa8d5001e313aeeea22eb2054883f59e3e3c656f11129f31abb02ddbf8ad60
       TAR=f2cblaslapack-3.8.0.q2.tar.gz; SRC=f2cblaslapack-3.8.0.q2
       BUILD_FN=build_f2clapack
       ARCHIVES=(libf2cblas.a libf2clapack.a)
       # quadmath.h for the quad build; clang ships none.
       EXTRA_CFLAGS="-I$(quadmath_include)"
       GEN_CFLAGS="$EXTRA_CFLAGS"
       # One precision per build, and not by preference: f2cblaslapack's
       # translation failed to rename 65 of its quadruple-precision routines,
       # so qla_gerpvgrw.c defines dla_gerpvgrw__ and the ?sy*_rook/_rk/_aa
       # family does the same. Build quad and double into one archive and
       # llvm-link refuses it -- "symbol multiply defined". The makefile has a
       # target per precision because that is how the library is meant to be
       # used, and it means each arm's module holds only its own precision.
       case $LIB in
         f2clapack)     F2C_PREC=quad ;;
         f2clapack-f64) F2C_PREC=double ;;
         f2clapack-f32) F2C_PREC=single ;;
         f2clapack-f16) F2C_PREC=half ;;
       esac
       ;;
  blis) VER=git; REPO=https://github.com/flame/blis
       # 'generic' is BLIS's assembly-free configuration, for the same reason
       # GMP takes --disable-assembly: KLEE cannot execute the tuned kernels,
       # and every other configuration selects some.
       BLIS_CONFIG=generic ;;
  *) echo "unknown library: $LIB" >&2; exit 2 ;;
esac


# The library whose build artefacts this arm reads, which is itself unless the
# arm shares another's build. Drivers still land under $ROOT/$LIB.
SRCLIB=${SHARES:-$LIB}
# ...and the checkout it is compiled from, which more arms can share than can
# share a build.
SRCTREE=${SRC_SHARES:-$SRCLIB}

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
  if [ ! -d "$WORK/$SRCTREE-src" ]; then
    if [ -n "${SPARSE:-}" ]; then
      git clone -q --depth 1 --filter=blob:none --sparse "$REPO" "$WORK/$SRCTREE-src"
      ( cd "$WORK/$SRCTREE-src" && git sparse-checkout set $SPARSE )
    else
      git clone -q --depth 1 ${TAG:+--branch "$TAG"} "$REPO" "$WORK/$SRCTREE-src"
    fi
  fi

  # Each of these has its own build system, and each needs to run it before a
  # header exists to generate from: bli_config.h and sundials_config.h are
  # produced by the configure step, and clang emits a *partial* AST before
  # failing on a missing one -- so a generator run against an unconfigured tree
  # silently sees a fraction of the API and reports a number that looks fine.
  build_cmake() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$SRCTREE-src" "$dest"
    cmake -S "$dest" -B "$dest/build" \
      -DCMAKE_C_COMPILER="$cc" -DCMAKE_C_FLAGS="$cflags" \
      -DCMAKE_INSTALL_PREFIX="$dest/inst" \
      -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON \
      -DEXAMPLES_ENABLE_C=OFF -DBUILD_TESTING=OFF \
      -DBUILD_FORTRAN_MODULE_INTERFACE=OFF > "$dest/config.log" 2>&1
    cmake --build "$dest/build" -j"$JOBS" > "$dest/build.log" 2>&1
    cmake --install "$dest/build" > "$dest/install.log" 2>&1
  }

  build_autoconf() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$SRCTREE-src" "$dest"
    ( cd "$dest"
      [ -f configure ] || sh bootstrap.sh --disable-fortran > boot.log 2>&1 || true
      CC="$cc" CFLAGS="$cflags" ./configure --disable-shared --enable-static \
        --disable-fortran --prefix="$dest/inst" > config.log 2>&1
      make -j"$JOBS" > build.log 2>&1
      make install > install.log 2>&1 )
  }

  build_sundials() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$SRCTREE-src" "$dest"
    if [ -n "$SUN_PREC" ]; then
      "$HERE/sundials-precision.py" "$dest" "$SUN_PREC" || return 1
      # The branch is inserted at the *front* of each chain, so this wins over
      # the SUNDIALS_DOUBLE_PRECISION that CMake's generated config.h still
      # defines. Leaving CMake on its default double is deliberate: its own
      # precision option only offers the three widths SUNDIALS ships.
      cflags="$cflags -DSUNDIALS_$(echo "$SUN_PREC" | tr a-z A-Z)_PRECISION $EXTRA_CFLAGS"
    fi
    cmake -S "$dest" -B "$dest/build" \
      -DCMAKE_C_COMPILER="$cc" -DCMAKE_C_FLAGS="$cflags" \
      -DCMAKE_INSTALL_PREFIX="$dest/inst" \
      -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON \
      -DEXAMPLES_ENABLE_C=OFF -DBUILD_TESTING=OFF \
      -DBUILD_FORTRAN_MODULE_INTERFACE=OFF > "$dest/config.log" 2>&1
    cmake --build "$dest/build" -j"$JOBS" > "$dest/build.log" 2>&1
    cmake --install "$dest/build" > "$dest/install.log" 2>&1
  }

  build_cxsparse() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$SRCTREE-src" "$dest"
    # The top-level CMakeLists selects subprojects, and CXSparse alone would
    # go looking for a SuiteSparse_config it has to be told to build first.
    #
    # OpenMP off: SuiteSparse_config times itself with omp_get_wtime, which is
    # an undefined reference at link time without -fopenmp, and a runtime KLEE
    # could not call with it. Nothing in the numerics calls it.
    cmake -S "$dest" -B "$dest/build" \
      -DSUITESPARSE_ENABLE_PROJECTS=cxsparse \
      -DSUITESPARSE_USE_OPENMP=OFF -DSUITESPARSE_CONFIG_USE_OPENMP=OFF \
      -DCMAKE_C_COMPILER="$cc" -DCMAKE_C_FLAGS="$cflags" \
      -DCMAKE_INSTALL_PREFIX="$dest/inst" \
      -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON \
      -DSUITESPARSE_DEMOS=OFF -DBUILD_TESTING=OFF > "$dest/config.log" 2>&1
    cmake --build "$dest/build" -j"$JOBS" > "$dest/build.log" 2>&1
    cmake --install "$dest/build" > "$dest/install.log" 2>&1
  }

  build_openlibm() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$SRCTREE-src" "$dest"
    ( cd "$dest"
      # amd64_SRCS is the arch's source list, and on x86-64 it is 23 assembly
      # files that KLEE cannot execute. Overriding it to fenv.c alone leaves
      # the arch's floating-point environment correct while every one of those
      # 23 falls back to the C in src/ -- the same trade GMP makes with
      # --disable-assembly, and the same caveat applies: what is measured is
      # the C path. ARCH stays amd64 rather than borrowing a pure-C
      # architecture's directory, because that directory's fenv.c is written
      # for a different machine.
      # CFLAGS rather than CFLAGS_add: OpenLibm appends its own essentials to
      # CFLAGS_add (-fno-builtin above all, without which clang is free to
      # turn a call to sin into the intrinsic it is implementing), and a
      # command-line assignment would discard them. CFLAGS is the hook it
      # leaves free, and it suppresses OpenLibm's own -O3 when it carries a -O.
      make -j"$JOBS" ARCH=amd64 amd64_SRCS=fenv.c \
        CC="$cc" USEGCC=0 USECLANG=1 CFLAGS="$cflags" \
        prefix="$dest/inst" > build.log 2>&1
      make ARCH=amd64 amd64_SRCS=fenv.c prefix="$dest/inst" \
        install-static install-headers > install.log 2>&1 )
  }

  build_blis() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$SRCTREE-src" "$dest"
    ( cd "$dest"
      # --disable-tls, because KLEE cannot execute llvm.threadlocal.address
      # and BLIS reads a thread-local error-checking level from inside
      # bli_init_once. Every BLIS driver died there, on its first call into
      # the library, before this. BLIS calls disabling TLS dangerous and it is
      # -- for a program with threads in it, which a KLEE driver is not.
      CC="$cc" CFLAGS="$cflags" ./configure --disable-shared --enable-static \
        --disable-threading --disable-tls \
        --prefix="$dest/inst" "$BLIS_CONFIG" > config.log 2>&1
      # BLIS compiles its reference kernels on its own terms, appending -O3,
      # -funsafe-math-optimizations, -ffp-contract=fast and -fopenmp-simd
      # *after* whatever CFLAGS it was given -- so -fno-vectorize in CFLAGS is
      # simply overruled, and the inner loop of every gemm came out as
      # llvm.vector.reduce.fadd, which KLEE cannot execute.
      #
      # These four variables are where those flags live, and make lets them be
      # replaced. Unsafe math and contraction would have to go regardless of
      # KLEE: they let the compiler reassociate sums and fuse a multiply-add
      # into one rounding, so the bitcode would no longer compute what the
      # source says, in a benchmark whose whole subject is what the source
      # says. Both builds get it, so that a replayed test computes what KLEE
      # explored.
      make -j"$JOBS" COMPSIMDFLAGS= \
        CROPTFLAGS=-O2 CKOPTFLAGS=-O2 \
        CRVECFLAGS="-fno-vectorize -fno-slp-vectorize -ffp-contract=off" \
        CKVECFLAGS="-fno-vectorize -fno-slp-vectorize -ffp-contract=off" \
        > build.log 2>&1
      make install > install.log 2>&1 )
  }

  build_cmsisdsp() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$SRCTREE-src" "$dest"
    # CMSIS-DSP's float16_t is __fp16, and it exists at all only when the
    # compiler defines __ARM_FP16_FORMAT_IEEE. x86-64 clang does not, so every
    # f16 source compiles to nothing -- and forcing the macro is worse, because
    # __fp16 there is a storage type clang refuses as a parameter or a return
    # type, which 24 parameters and 17 return types of this API are. _Float16
    # is the same format and a first-class arithmetic type; the library's own
    # scalar fallbacks already cast to it explicitly, so nothing in the sources
    # depends on which spelling the typedef gets.
    python3 - "$dest/Include/arm_math_types_f16.h" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
old = "    #if defined(__ARM_FP16_FORMAT_IEEE)"
new = ("    #if defined(FP_BENCH_X86_FLOAT16)\n"
       "      typedef _Float16 float16_t;\n"
       "      #define ARM_FLOAT16_SUPPORTED\n"
       "    #elif defined(__ARM_FP16_FORMAT_IEEE)")
assert s.count(old) == 1, "arm_math_types_f16.h no longer has the f16 gate"
open(p, "w").write(s.replace(old, new))
PY
    # HOST=ON is CMSIS-DSP's own switch for a build off Cortex-M; it defines
    # __GNUC_PYTHON__, which drops the CMSIS-Core dependency. What comes out is
    # entirely scalar -- no vector types and no target intrinsics reach the
    # bitcode -- because every MVE and Neon path is behind a feature macro
    # x86-64 does not set.
    cmake -S "$dest/Source" -B "$dest/build" -DHOST=ON \
      -DCMAKE_C_COMPILER="$cc" \
      -DCMAKE_C_FLAGS="$cflags -DFP_BENCH_X86_FLOAT16" \
      -DCMAKE_INSTALL_PREFIX="$dest/inst" -DCMAKE_BUILD_TYPE=None \
      > "$dest/config.log" 2>&1
    cmake --build "$dest/build" -j"$JOBS" > "$dest/build.log" 2>&1
    cmake --install "$dest/build" > "$dest/install.log" 2>&1
  }

  build_hdf5() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$SRCTREE-src" "$dest"
    # Everything but the C library itself is off: the tools, the examples, the
    # high-level wrappers and the compression filters are none of them on the
    # path from H5Tconvert to a conversion, and each is more source for the
    # coverage build to instrument and more archive for extract-bc to walk.
    # HDF5_ENABLE_NONSTANDARD_FEATURE_FLOAT16 is what puts _Float16 in, and is
    # on by default -- named here because it is the whole reason for the arm.
    cmake -S "$dest" -B "$dest/build" \
      -DCMAKE_C_COMPILER="$cc" -DCMAKE_C_FLAGS="$cflags" \
      -DCMAKE_INSTALL_PREFIX="$dest/inst" -DCMAKE_BUILD_TYPE=None \
      -DHDF5_ENABLE_NONSTANDARD_FEATURE_FLOAT16=ON \
      -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON \
      -DBUILD_TESTING=OFF -DHDF5_BUILD_TOOLS=OFF -DHDF5_BUILD_EXAMPLES=OFF \
      -DHDF5_BUILD_UTILS=OFF -DHDF5_BUILD_HL_LIB=OFF -DHDF5_BUILD_CPP_LIB=OFF \
      -DHDF5_ENABLE_Z_LIB_SUPPORT=OFF -DHDF5_ENABLE_SZIP_SUPPORT=OFF \
      > "$dest/config.log" 2>&1
    cmake --build "$dest/build" -j"$JOBS" > "$dest/build.log" 2>&1
    cmake --install "$dest/build" > "$dest/install.log" 2>&1
  }

  # A library that ships more than one archive needs all of the ones a driver
  # calls into. SUNDIALS keeps its context and its error handling in
  # libsundials_core, apart from the vectors in libsundials_nvecserial, and
  # taking only the latter left SUNContext_Create outside the module: every
  # SUNDIALS driver died on it as a failed external call, thirty instructions
  # in, having never entered the library. The native link line had both from
  # the start, which is why nothing looked wrong until one was run.
  case $LIB in
    blis)     BUILD=build_blis;     ARCS=(libblis.a);     HDR=blis.h ;;
    openlibm) BUILD=build_openlibm; ARCS=(libopenlibm.a); HDR=openlibm_math.h ;;
    cxsparse) BUILD=build_cxsparse; ARCS=(libcxsparse.a libsuitesparseconfig.a)
              HDR=cs.h ;;
    sundials|sundials-f128|sundials-f16)
              BUILD=build_sundials; ARCS=(libsundials_nvecserial.a
                                          libsundials_sunmatrixdense.a
                                          libsundials_sunlinsoldense.a
                                          libsundials_core.a)
              HDR=sundials/sundials_config.h ;;
    fftw)     BUILD=build_autoconf; ARCS=(libfftw3.a);    HDR=fftw3.h ;;
    cmsisdsp|cmsisdsp-f32)
              BUILD=build_cmsisdsp; ARCS=(libCMSISDSP.a)
              HDR=arm_math_f16.h ;;
    hdf5|hdf5-f32)
              BUILD=build_hdf5;     ARCS=(libhdf5.a);     HDR=hdf5.h ;;
  esac

  if [ ! -f "$WORK/$SRCLIB.bc" ]; then
    # -fno-openmp-simd is the one that is not obvious. BLIS's reference
    # kernels carry a PRAGMA_SIMD, which expands to #pragma omp simd, and
    # clang honours that even under -fno-vectorize: the inner loop of every
    # gemm came out as llvm.vector.reduce.fadd.v2f64, which KLEE cannot
    # execute. Scalar is also the right shape for this benchmark -- a
    # horizontal reduction is a different summation order, so a different
    # rounding, from the one the source describes.
    #
    # -fexcess-precision=16 is the one that decides what a binary16 corpus
    # measures. C's excess-precision rules let a _Float16 statement keep a
    # binary32 intermediate, so without this `s += x[i]*y[i]` compiles to
    # llvm.fmuladd.f32 at *every* -O level and the corpus measures binary32
    # while claiming binary16. With it, both builds round per operation and
    # agree. Inert for a library with no _Float16 in it, which is all seven of
    # the original ones. Not -ffp-contract=off: llvm.fmuladd.f16 is an FMA with
    # a single rounding, KLEE implements it as Expr::FMA and all three solvers
    # have the operation natively, so leaving contraction on is the more
    # interesting query rather than a less faithful one.
    "$BUILD" "$WORK/$SRCLIB-bc" wllvm \
      "-O2 -g -fexcess-precision=16 -fno-vectorize -fno-slp-vectorize \
       -fno-openmp-simd -Wno-implicit-function-declaration $EXTRA_CFLAGS"
    parts=()
    for arc in "${ARCS[@]}"; do
      # -print -quit rather than a pipe into head: under `set -o pipefail`,
      # head closing the pipe kills find with SIGPIPE, the pipeline reports
      # failure, and `set -e` takes the whole script down at the assignment.
      a=$(find "$WORK/$SRCLIB-bc/inst" "$WORK/$SRCLIB-bc/build" \
            -name "$arc" -print -quit 2>/dev/null)
      [ -n "$a" ] || { echo "no $arc built for $LIB" >&2; exit 1; }
      extract-bc -b "$a" -o "$WORK/$SRCLIB.$arc.bc"
      parts+=("$WORK/$SRCLIB.$arc.bc")
    done
    "$LLVM_PREFIX/bin/llvm-link" "${parts[@]}" -o "$WORK/$SRCLIB.bc"
    rm -f "${parts[@]}"
  fi
  if [ ! -d "$WORK/$SRCLIB-cov/inst" ]; then
    "$BUILD" "$WORK/$SRCLIB-cov" "$NATIVE_CC" \
      "-O0 -g -fexcess-precision=16 -fprofile-instr-generate -fcoverage-mapping \
       -fno-openmp-simd -Wno-implicit-function-declaration $EXTRA_CFLAGS"
  fi

  # The generated monolithic header, which is what the generator reads and what
  # the drivers include.
  INC=$(find "$WORK/$SRCLIB-bc/inst" "$WORK/$SRCLIB-bc/build" \
          -name "$(basename "${GEN_HDR:-$HDR}")" -printf '%h\n' -quit 2>/dev/null)
  # A header nested under a directory of its own (sundials/...) is included as
  # such, so the include path is the directory above it.
  case ${GEN_HDR:-$HDR} in */*) INC=$(dirname "$INC") ;; esac
  rm -rf "$ROOT/$LIB/drivers"; mkdir -p "$ROOT/$LIB/drivers"
  "$HERE/gen-drivers.py" --library "$LIB" --include "$INC" \
                         --out "$ROOT/$LIB/drivers" --cflags="$GEN_CFLAGS"
  echo
  echo "$LIB $VER${BLIS_CONFIG:+ ($BLIS_CONFIG)}"
  echo "  bitcode:  $WORK/$SRCLIB.bc"
  echo "  header:   $INC/${GEN_HDR:-$HDR}"
  echo "  drivers:  $(ls "$ROOT/$LIB/drivers"/*.c 2>/dev/null | wc -l)"
  exit 0
fi

# Cuba answers the fork and shmget probes "no" (KLEE models neither), and one
# declaration it still needs sits inside the #ifdef those probes turn off. Run
# in the unpacked tree, before configure, in both builds.
cuba_serial_fix() {
  python3 - src/common/Fork.c <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
decl = "extern coreinit cubafun_;\n"
gate = "#ifdef HAVE_FORK\n"
assert s.count(decl) == 1 and s.count(gate) == 1, \
    "Fork.c no longer has the shape this patch expects"
open(p, "w").write(s.replace(decl, "", 1).replace(gate, decl + gate, 1))
PY
}

# f2cblaslapack has no configure script, and its top-level makefile includes
# PETSc's own configuration -- so drive the two sub-makefiles directly, which is
# all the top-level one does anyway. Both precisions go into one pair of
# archives: `ar cr` appends, so the quad and double targets accumulate, and the
# two library arms then read one build.
build_f2clapack() {  # <destination> <cc> <cflags>
  local dest=$1 cc=$2 cflags=$3 d
  rm -rf "$dest"
  tar xf "$WORK/$TAR" -C "$WORK" && mv "$WORK/$SRC" "$dest"
  ( cd "$dest"
    # The half configuration types itself __fp16, which on x86-64 is a storage
    # format clang refuses as a parameter or a return type -- and f2c.h itself
    # declares `extern scalar hf__cabs(scalar, scalar)`, so nothing compiles at
    # all. _Float16 is the same format as a first-class arithmetic type. This
    # is the identical patch build_cmsisdsp applies, for the identical reason.
    if [ "$F2C_PREC" = half ]; then
      sed -i -e 's/typedef __fp16 halfreal;/typedef _Float16 halfreal;/' \
             -e 's/define scalar __fp16/define scalar _Float16/' \
             -e 's/define dscalar __fp16/define dscalar _Float16/' \
             blas/f2c.h lapack/f2c.h
      grep -q '_Float16 halfreal' blas/f2c.h ||
        { echo "f2c.h no longer has the __fp16 typedef this patch expects" >&2
          exit 1; }
    fi
    for d in blas lapack; do
      ( cd "$d"
        make "$F2C_PREC" CC="$cc" COPTFLAGS="$cflags" CNOOPT="$cflags" \
          AR=ar AR_FLAGS=cr RM=/bin/rm LIBNAME="libf2c$d.a" ) || exit 1
    done
    ranlib libf2cblas.a libf2clapack.a 2>/dev/null || true
    # The library ships no prototypes at all -- PETSc declares what it calls --
    # so the header the generator reads is made from the definitions, for the
    # precision this tree was built at.
    cp lapack/f2c.h .
    "$HERE/f2c-protos.py" "${F2C_PREC:0:1}" f2clapack.h blas lapack \
      ) > "$dest/build.log" 2>&1
}

###############################################################################
# 1. Fetch, and say what was fetched
###############################################################################
[ -f "$TAR" ] || curl -sSL -o "$TAR" "$URL"
echo "$SHA  $TAR" | sha256sum -c -

###############################################################################
# 2. Bitcode -- what KLEE executes
###############################################################################
if [ ! -f "$WORK/$SRCLIB.bc" ]; then
 if [ -n "$BUILD_FN" ]; then
  "$BUILD_FN" "$WORK/$SRCLIB-bc" wllvm \
    "-O2 -g -fexcess-precision=16 -fno-vectorize -fno-slp-vectorize \
     -Wno-implicit-function-declaration -Wno-implicit-int $EXTRA_CFLAGS"
 else
  rm -rf "$LIB-bc"
  tar xf "$TAR" && mv "$SRC" "$LIB-bc"
  ( cd "$LIB-bc"
    [ -n "$PRE_BUILD" ] && "$PRE_BUILD"
    # -fno-vectorize keeps the bitcode scalar, so what KLEE executes lines up
    # with the -O0 coverage build. -Wno-implicit-* is not cosmetic: a current
    # clang makes implicit declarations an error, several configure probes call
    # exit() without stdlib.h, and a probe that fails to compile is recorded as
    # a failed feature -- giving the two builds different config.h files.
    # -fexcess-precision=16 is inert without a _Float16; see the cloned path.
    CC=wllvm CPPFLAGS="$EXTRA_CPPFLAGS" LDFLAGS="$EXTRA_LDFLAGS" \
    CFLAGS="-O2 -g -fexcess-precision=16 -fno-vectorize -fno-slp-vectorize -Wno-implicit-function-declaration -Wno-implicit-int $EXTRA_CFLAGS" \
      env ${CONFIGURE_ENV[@]+"${CONFIGURE_ENV[@]}"} \
      ./configure --disable-shared --enable-static "${CONFIGURE_EXTRA[@]}" \
      > config.log 2>&1
    make -j"${MAKE_JOBS:-$JOBS}" $MAKE_TARGET > build.log 2>&1 )
 fi
  # extract-bc names archive members by basename, and both libraries have
  # same-named objects in different directories, so the archive form silently
  # loses all but one. Take the whole-module form; the per-driver prune below
  # cuts it back down.
  bcs=()
  for a in "${ARCHIVES[@]}"; do
    extract-bc -b "$WORK/$SRCLIB-bc/$a" -o "$WORK/$SRCLIB-bc/$a.bc"
    bcs+=("$WORK/$SRCLIB-bc/$a.bc")
  done
  llvm-link "${bcs[@]}" -o "$WORK/$SRCLIB.bc"
fi

###############################################################################
# 3. Native, instrumented -- what the tests are replayed against
###############################################################################
if [ ! -d "$WORK/$SRCLIB-cov/.libs" ] && [ ! -f "$WORK/$SRCLIB-cov/build.log" ]; then
 if [ -n "$BUILD_FN" ]; then
  "$BUILD_FN" "$WORK/$SRCLIB-cov" "$NATIVE_CC" \
    "-O0 -g -fprofile-instr-generate -fcoverage-mapping \
     -Wno-implicit-function-declaration -Wno-implicit-int $EXTRA_CFLAGS"
 else
  rm -rf "$LIB-cov"
  tar xf "$TAR" && mv "$SRC" "$LIB-cov"
  ( cd "$LIB-cov"
    [ -n "$PRE_BUILD" ] && "$PRE_BUILD"
    CC="$NATIVE_CC" CPPFLAGS="$EXTRA_CPPFLAGS" \
    CFLAGS="-O0 -g -fexcess-precision=16 -fprofile-instr-generate -fcoverage-mapping -Wno-implicit-function-declaration -Wno-implicit-int $EXTRA_CFLAGS" \
    LDFLAGS="-fprofile-instr-generate $EXTRA_LDFLAGS" \
      env ${CONFIGURE_ENV[@]+"${CONFIGURE_ENV[@]}"} \
      ./configure --disable-shared --enable-static "${CONFIGURE_EXTRA[@]}" \
      > config.log 2>&1
    make -j"${MAKE_JOBS:-$JOBS}" $MAKE_TARGET > build.log 2>&1 )
 fi
fi

# The two trees must agree on what they compiled, or the coverage step reports
# on source KLEE never saw.
cfg_of() { grep -v -e '^/\*' -e '_CC "' -e 'COMPILER' "$1" 2>/dev/null; }
if ! diff -q <(cfg_of "$WORK/$SRCLIB-bc/config.h") \
             <(cfg_of "$WORK/$SRCLIB-cov/config.h") > /dev/null 2>&1; then
  echo "WARNING: the two $LIB builds configured differently:" >&2
  diff <(cfg_of "$WORK/$SRCLIB-bc/config.h") <(cfg_of "$WORK/$SRCLIB-cov/config.h") >&2 || true
fi

###############################################################################
# 4. Drivers, from the header of the build that was just made
###############################################################################
INC=$WORK/$SRCLIB-bc${INCSUB:+/$INCSUB}
rm -rf "$ROOT/$LIB/drivers"
mkdir -p "$ROOT/$LIB/drivers"
"$HERE/gen-drivers.py" --library "$LIB" --include "$INC" \
                       --out "$ROOT/$LIB/drivers" --cflags="$GEN_CFLAGS"

echo
echo "$LIB $VER"
echo "  bitcode:   $WORK/$SRCLIB.bc"
echo "  coverage:  $WORK/$SRCLIB-cov"
echo "  drivers:   $(ls "$ROOT/$LIB/drivers"/*.c 2>/dev/null | wc -l)"
echo "  skipped:   $ROOT/$LIB/skipped.tsv"
