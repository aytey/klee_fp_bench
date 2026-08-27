#!/bin/bash
#
# Build an STP from a git ref, install it, and optionally relink KLEE against it.
#
#   build-stp.sh <git-ref> [install-name]
#   RELINK_KLEE=1 build-stp.sh upstream/master master
#
# This exists because the recipe is not obvious and getting it wrong is quiet.
# Two of the options below decide whether the results mean anything:
#
#   USE_MINISAT=ON      defaults OFF. Without it MiniSat is not built, so
#                       --stp-sat-solver=minisat -- which every tuned
#                       configuration here uses -- silently is not what runs.
#                       It also brings in the terminator: the configure log
#                       says "MiniSat can be stopped mid-search" and without
#                       that --max-solver-time is only enforced *between*
#                       calls into the SAT solver, so a query that goes deep
#                       overruns the cap and the run stops measuring the cap.
#
#   ENABLE_PYTHON_INTERFACE=OFF
#                       the install step otherwise tries to write into the
#                       system site-packages and fails, which aborts install
#                       before the CMake package config lands -- and that is
#                       what KLEE's -DSTP_DIR needs.
#
# CryptoMiniSat is the one dependency STP does not build for itself; point
# CMS_DIR at an existing install.
set -eu
REF=${1:?usage: build-stp.sh <git-ref> [install-name]}
NAME=${2:-$(echo "$REF" | tr '/' '-')}
DEPS=${DEPS:-/mnt/baranem/klee-float/deps}
SRC=${STP_SRC:-/mnt/baranem/stp-cnf}
CMS_DIR=${CMS_DIR:-$DEPS/cms-install/lib/cmake/cryptominisat5}
FETCH=${FETCH:-$DEPS/stp-deps/deps}
KLEE_SRC=${KLEE_SRC:-/mnt/baranem/klee-float/3.2}
KLEE_BUILD=${KLEE_BUILD:-/mnt/baranem/klee-float/3.2-buildtest}
PREFIX=$DEPS/install-stp-$NAME
WT=${WT:-/mnt/baranem/stp-build-$NAME}

git -C "$SRC" worktree add --detach "$WT" "$REF" 2>/dev/null || \
  git -C "$WT" checkout --detach "$REF"

cmake -S "$WT" -B "$WT/build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON -DENABLE_TESTING=OFF \
  -DENABLE_AUTO_DOWNLOAD=ON -DUSE_CRYPTOMINISAT=ON -DUSE_MINISAT=ON \
  -DSTP_TIMESTAMPS=ON -DENABLE_PYTHON_INTERFACE=OFF \
  -Dcryptominisat5_DIR="$CMS_DIR" -DFETCHCONTENT_BASE_DIR="$FETCH" \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" > "$WT/configure.log" 2>&1

grep -q "MiniSat can be stopped mid-search" "$WT/configure.log" || {
  echo "REFUSING: this STP cannot be stopped mid-search, so --max-solver-time" >&2
  echo "would not be enforced during a solve and every timing here would be a" >&2
  echo "measurement of something else. See $WT/configure.log" >&2
  exit 1; }

cmake --build "$WT/build" -j"${J:-6}" --target install > "$WT/build.log" 2>&1
echo "installed: $PREFIX"
echo "  lib64/libstp.so   $([ -f "$PREFIX/lib64/libstp.so" ] && echo ok || echo MISSING)"
echo "  cmake/STP config  $([ -f "$PREFIX/lib64/cmake/STP/STPConfig.cmake" ] && echo ok || echo MISSING)"

if [ "${RELINK_KLEE:-0}" = "1" ]; then
  cmake -S "$KLEE_SRC" -B "$KLEE_BUILD" \
    -DSTP_DIR="$PREFIX/lib64/cmake/STP" > "$WT/klee-configure.log" 2>&1
  cmake --build "$KLEE_BUILD" -j"${J:-6}" --target klee > "$WT/klee-build.log" 2>&1
  echo "KLEE relinked against $PREFIX"
  echo "  NOTE: the sweep still needs STP_TERM (or the relevant prefix variable)"
  echo "  pointed at $PREFIX/lib64, or it will load a different libstp at runtime."
fi
