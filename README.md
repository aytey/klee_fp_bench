# fp_bench

Does STP decide floating-point queries faster than Bitwuzla, on real numerical
code rather than on kernels written to be hard?

Seven libraries, each driven the way the APSEC floating-point-solver paper
drove GSL: one driver per API function, every argument symbolic, and the
measurement
is how much of that function KLEE's generated tests reach when replayed
natively. There is no bug oracle and none is wanted — coverage under a fixed
budget is the signal, because it is the thing a faster solver actually buys.

| | drivers | of | |
| --- | --- | --- | --- |
| `gsl/` | 646 | 648 | GSL 2.8 — special functions, CDFs, integration, roots |
| `openlibm/` | 141 | 210 | OpenLibm v0.8.7 — the elementary functions, fdlibm-derived |
| `blis/` | 135 | 2143 | BLIS — dense linear algebra, `generic` (assembly-free) build |
| `sundials/` | 88 | 180 | SUNDIALS — N_Vector, dense SUNMatrix, dense SUNLinearSolver |
| `gmp/` | 58 | 250 | GMP 6.3.0 — the `mpf` layer and the double conversions |
| `fftw/` | 27 | 43 | FFTW 3.3.10 — discrete transforms |
| `cxsparse/` | 23 | 64 | CXSparse — sparse LU, Cholesky and QR, with pivoting |
| `common/` | | | the generator, the harness and the reports |

The second column is what each library is *selected* down to. Every one of them
carries a surface that computes nothing — object accessors, runtime settings,
memory pools, argument validators — and those are legitimate coverage targets
but they exercise no arithmetic, so they dilute a solver comparison with paths
that never reach the solver. `--all` keeps them.

GMP is cut hardest and deliberately: `mpz` is multi-precision *integer*
arithmetic, which blasts to bitvectors and never reaches the floating-point
theory at all. What is kept is `mpf`, plus the `_set_d`/`_get_d` conversions,
which are where a double is taken apart and put back together.

BLIS is cut nearly as hard, for a different reason. It exports its blocked
variants, kernel variants, control-tree builders and expert interfaces
alongside its API, and those take a `cntx_t` or a `cntl_t` that the generator
can only fabricate: of 351 of them, 250 abort, crash or fail to compile, and
the 101 that survive run BLIS's plumbing rather than its arithmetic. What is
left is the 135 that are reached through the front door.

Drivers are **generated from the libraries' own headers**, not checked in, so
moving to a newer release is a version bump rather than a rewrite. `common/
gen-drivers.py` reads a header's declarations through clang's AST, maps each
parameter type to a way of making it symbolic, and emits a driver per function
it can handle completely. What it cannot handle it records, with the reason, so
the gap is auditable rather than silent.

## Two things to know before reading any number

**CXSparse matrices are dense-in-sparse and 4x4, with concrete structure.** A
compressed-column matrix is valid only if its column pointers are monotone and
its row indices are in range -- relationships between arguments, which the
generator says it cannot meet -- so the structure is written by hand and only
the values are symbolic. That is the right split as well as the necessary one:
a symbolic index is concretised at an array subscript and spends the run on
integers, where a symbolic value reaches `cs_lu`'s pivot test. Dense rather
than banded, so the pivot search has a free choice in every column. What is
not measured is anything that depends on sparsity structure.

**OpenLibm is measured on its C path too**, for exactly GMP's reason: 23 of its
x86-64 sources are assembly KLEE cannot execute, and each has a C counterpart
in `src/`, so the build overrides `amd64_SRCS` down to `fenv.c` alone. Its
`long double` functions are skipped rather than measured — on x86-64 that is
the x87 80-bit format, which is not one of the IEEE widths the solvers reason
about. That is 66 of the 69 skips, and they are listed with their reason.

**GMP is measured on its C path.** GMP's `mpn` layer ships as hand-written
assembly per architecture, and KLEE cannot execute that, so the bitcode build
configures `--disable-assembly`. What is covered is the C fallback.

**Sizes are bounded, not free.** A symbolic `mp_bitcnt_t` or `size_t` reaching
an allocation makes KLEE concretise it. Those parameters are constrained with
`klee_assume` to a small range; the generator says which, per driver.

**A driver can run and still measure nothing, and only running it says so.**
Three of the five libraries produced numbers for a long time while never
reaching the library at all. BLIS died inside `bli_init_once` on an intrinsic
KLEE cannot execute; SUNDIALS died on `SUNContext_Create`, which was not in the
module because only one of its two archives was being harvested; BLIS's objects
were built 1x1 with buffers left exactly as `malloc` returned them, so its
arithmetic ran on whatever was already there. Each looked like a working run
from outside — a driver that starts, a coverage figure, a row in the table.
What found them was running one and reading the stack, and what keeps them
found is that `run-one.sh` records errors per driver.

**Not every driver asks the solver much.** A dot product and a `gemm` are
branch-free: they build large symbolic expressions and reach the end of the
function without ever needing a decision, so they generate one test and issue
almost no queries. The solver load lives in the drivers that branch on a
floating-point value — norms with their scaling tests, CDFs, the special
functions. Both belong in a coverage suite; only the second is a solver
benchmark, and `aggregate.py` reports queries per driver so the difference is
visible rather than averaged away.

## Reproducing this

### What has to be on the machine first

Nothing here builds a solver or a KLEE; all four are pointed at, and every path
below is an environment variable with a default, so a machine laid out
differently overrides rather than edits.

| | variable | what it must be |
| --- | --- | --- |
| KLEE source | `KLEE_SRC` | the tree holding `klee_make_symbolic`'s header |
| KLEE build | `KLEE_BUILD` | a built KLEE with STP, Bitwuzla and Z3 all enabled |
| LLVM 16 | `LLVM_PREFIX` | the clang KLEE was built against, plus `llvm-cov`/`llvm-profdata` |
| wllvm shims | `SHIM` | the `clang`/`ar`/`ranlib` wrappers that keep bitcode alongside objects |
| a Python env | `PYENV` | holds `wllvm`; some libraries' configure needs it on `PATH` |
| working area | `FP_BENCH_WORK` | where sources, both builds, drivers and runs land — tens of GB, and fastest on a RAM disk |
| STP with the terminator | `STP_TERM` | the `lib64` of an STP whose MiniSat can be stopped mid-search |
| libquadmath sources | `QM_SRC` | a GCC source tree's `libquadmath/` directory; only `common/build-libquadmath.sh` reads it |

`STP_TERM` is not optional for timing work. Without it `--max-solver-time`
is only enforced between calls into the SAT solver, so a query that goes deep
into MiniSat overruns the cap and the run stops being a measurement of the cap.

The two SMT-level scripts additionally take `STP_BIN` and `BITWUZLA_BIN`,
solver binaries rather than libraries.

### Starting from a new STP

Nothing here builds a solver, but STP is the one whose build has to be right or
the numbers quietly stop meaning anything. `common/build-stp.sh` captures the
recipe:

    RELINK_KLEE=1 common/build-stp.sh upstream/master master-now

That checks out the ref into a worktree, configures, installs to
`$DEPS/install-stp-<name>`, and reconfigures and rebuilds KLEE against it.
Two of its options are load-bearing:

* **`-DUSE_MINISAT=ON`**, which defaults *off*. Without it MiniSat is not built,
  so `--stp-sat-solver=minisat` -- which every tuned configuration here uses --
  is silently not what runs. It also brings the terminator, and without that
  `--max-solver-time` is enforced only *between* calls into the SAT solver, so
  a query that goes deep overruns the cap and the run stops being a measurement
  of the cap. The script **refuses to install** an STP whose configure log does
  not say `MiniSat can be stopped mid-search`.
* **`-DENABLE_PYTHON_INTERFACE=OFF`**, because the install step otherwise tries
  to write into the system site-packages, fails, and aborts *before* the CMake
  package config is written -- which is exactly the file `-DSTP_DIR` needs.

Then point the sweep at it, or the runs will load a different `libstp` than the
one KLEE was compiled against:

    export STP_TERM=$DEPS/install-stp-master-now/lib64

**Two ABI cautions, both of which have bitten this suite.** KLEE compiles the
integer values of `ifaceflag_t` into its binary, so a `libstp` that inserted a
flag mid-enum makes KLEE set a *different* flag than it names, silently and
without error -- one branch's `--stp-cnf-auto-threshold=0` was setting
`INCREMENTAL_PIECE_REWRITING`. Loading a library by `LD_LIBRARY_PATH` is only
safe when its enum matches the headers KLEE was built against; otherwise
rebuild KLEE against those headers. And a library that is merely *older* than
KLEE's headers can lack a flag entirely, which fails at compile time and is the
kinder case.

**The tuned settings live in `common/configs/backends.tsv`,** and two of them
were established here rather than inherited: `--stp-cnf-auto-threshold=0`
(needs stp/stp#998, in master) and `--bitwuzla-rewrite-level=1`. Re-running
against a new STP without them measures the untuned solvers.

### The pipeline

    common/build-lib.sh      <lib>        # fetch, build twice, generate drivers
    common/build-drivers.sh  <lib>        # compile every driver, twice
    common/sweep-all.sh      [configs.tsv]  # run the table over every library
    common/aggregate.py      <lib>        # read one library's results

`build-lib.sh` builds each library twice on purpose: once to LLVM bitcode
through wllvm, which is what KLEE executes, and once natively with clang's
source-based coverage instrumentation, which is what the generated tests are
replayed against. `build-drivers.sh` likewise emits a `.bc` per driver for KLEE
and a native binary for replay.

A sweep is driven by a tab-separated table, one configuration per line:

    label <TAB> backend <TAB> LD_LIBRARY_PATH for libstp (or -) <TAB> extra klee args

`common/configs/` holds the ones these results came from — `backends.tsv` for
the four solvers, and one per question asked since. Paths in a table go through
`envsubst`, so write `$STP_TERM` rather than an absolute path.

### The knobs that change what a number means

    BUDGET=60           # seconds of exploration per driver
    MAX_SOLVER_TIME=5   # per-query cap; see "Where to set the query cap" in RESULTS.md
    PAR=12              # drivers in flight; with MAX_MEMORY, sized to the RAM disk
    MAX_MEMORY=2000     # MB per KLEE
    RUNS=runs-all       # subdirectory per sweep, so one does not overwrite another

`PAR` and `MAX_MEMORY` are the values `sweep-all.sh` exports, and those are the
ones a sweep runs under. The scripts below it carry their own, higher defaults
for when they are invoked directly on one driver.

**Nothing else may be running.** These are wall-clock measurements of one
machine, and they are not robust to sharing it. A sweep run while this repo's
own solver was being compiled read 27% slower for STP and 17% for Bitwuzla --
enough to move the headline ratio from 0.785 to 0.841 and to disagree with the
baseline it should have reproduced.

**Sweeps run a strided subset, not the whole corpus.** `sweep-all.sh` carries a
stride per library — 10 for GSL, 1 for FFTW and CXSparse — which takes 1,118
drivers down to **255**. That is what every figure in `RESULTS.md` is measured
over. The stride is per library rather than global because the libraries differ
by an order of magnitude in size and a global one would make the corpus almost
entirely GSL. Editing the `LIBS` table changes it; a stride of 1 everywhere is
the whole corpus and roughly four times the wall clock.

### Comparing solvers with KLEE out of the way

Inside KLEE two solvers stop being asked the same questions the moment one of
them times out a query the other answered — the state dies, the path goes
unexplored, and every later query differs. `split-queries.py` and
`replay-queries.sh` take a dumped corpus and run both solvers over identical
files, which is the only comparison with nothing else in it. `RESULTS.md` reads
both, and says where they disagree.

## Quad precision

Every library above is measured at binary32 and binary64. binary128 is the
format where the queries get genuinely hard -- an `fp.div` on quads blasts to
a ~227-bit division where a double gives ~107 -- so it is worth being able to
reach, and `common/build-libquadmath.sh` is what reaches it.

Numerical code gets there through a macro block, not through a type:

    #ifdef FLOAT128
    #include <quadmath.h>
    #define REAL __float128
    #define sin  sinq
    #define exp  expq
    #define sqrt sqrtq
    ...
    #define atof(a) strtoflt128((a), NULL)
    #endif

so the kernel underneath is written once and says nothing about its
precision. `quadmath/quad_example.c` is a worked example of exactly that
shape, buildable at either precision from one source.

Clang needs nothing special for the arithmetic: `__float128` compiles to
native `fp128` IR -- `fadd`, `fcmp`, `fpext`, `fptrunc` -- with the soft-float
lowering happening in the backend, so KLEE sees genuine quad operations. What
is not free is libquadmath, which ships as part of GCC and has no standalone
build. The script compiles its sources directly and generates the `config.h`
and the `__builtin_huge_valq` shim that GCC's own build would have supplied:

    common/build-libquadmath.sh          # 95 sources -> libquadmath-math.bc
    llvm-link -o prog_linked.bc prog.bc libquadmath-math.bc

It builds `math/` only, and refuses to write a partial library. `printf/` and
`strtod/` cannot be built with clang at all -- `printf_fp.c` uses GCC nested
functions -- and both reference libgcc's `__clz_tab`, which kills a KLEE run
at global-init rather than at the call. So `quadmath_snprintf` and
`strtoflt128` are unavailable, which means the `atof` line of the macro block
above is the one entry point with no path. Everything else has one.

### Why it is not an eighth library here

The corpus wants drivers that reach the arithmetic and finish. Measured one
function at a time, with a symbolic quad argument in (0,10), a 60s budget and
a 5s query cap under Bitwuzla:

| | outcome |
| --- | --- |
| `fabsq` `sqrtq` `floorq` `ceilq` `truncq` `modfq` `powq` | explored, 37-153 instructions |
| `fmodq` | 8,443 instructions, bounded by the exploration budget |
| `expq` `logq` `sinq` `cosq` `tanq` `atanq` | query timeouts |

The second group is the interesting half and it is the half that does not
finish. `sinq` and `cosq` reduce their argument through a 5,312-byte table
indexed by a symbolic value, which goes to the solver whole.

The control is what settles it: the same sweep against libm at binary64 has
the same shape -- `exp`, `log`, `sin`, `cos`, `tan` and `atan` all time out
there too. This is the standing difficulty of symbolically executing a libm
implementation rather than anything quad introduces. A libquadmath library
here would be a corpus whose measurable half is branch-free and whose
interesting half never returns, which is the shape of measurement this suite
exists to avoid -- the same reason OSQP was dropped below.

The obvious use for quad here is therefore not a library at all but
`reproducers/`. A single quad `fp.div` would isolate the same division
mechanism the two reproducers already there isolate at binary64, at twice the
width and for the cost of one query rather than a driver. That has not been
measured yet.

## Considered and not included

**OSQP.** Integrated, measured, and dropped. Its only route into the numerics
is `osqp_setup`, which equilibrates the problem, assembles a KKT matrix and
factorises it before the first ADMM iteration. At four variables every driver
that reached setup hit the wall without finishing it; at two, `osqp_setup`
reached 2% of its own lines in 150 seconds, and the drivers that did return
cleanly did so because setup had failed and they exited early. Fixing P and A
concrete and leaving only q, l and u symbolic did not change that. Nine drivers
of which the interesting ones never run is the shape of measurement this suite
exists to avoid.

**Qhull.** 65 of its 69 entry points take a `qhT*`, which holds two `jmp_buf`s,
and every precision failure goes through `qh_errexit` to `longjmp` -- which
KLEE's `runtime/POSIX/illegal.c` makes a hard error. With symbolic coordinates
that is the common path, not the rare one.

**CMSIS-DSP.** Its bulk is block arithmetic over arrays: branch-free, and this
corpus already has more of that than it needs.

**PETSc, hypre, SuperLU.** Python-driven configuration and MPI for the first
two; for the third, near-duplicate `s/d/c/z` sources over the same sparse
construction problem CXSparse already covers.

## What the generator does

`common/gen-drivers.py` reads a library's declarations through clang's AST and
emits a driver per function whose every argument it can make symbolic. Scalars
and arrays are direct. Enums become symbolic over exactly the enumerators clang
reports for that type. Opaque objects are **built**, by searching for a function
that yields one -- one that returns it, or one named like a constructor taking
a `T *` to write through -- recursively, because a constructor's own arguments
can be opaque in turn. SUNDIALS is the case that needs the whole mechanism:

    SUNComm  a_0 = SUN_COMM_NULL;
    SUNContext a_1; SUNContext_Create(a_0, &a_1);
    N_Vector a = N_VNew_Serial(8, a_1);
    { sunrealtype *p = N_VGetArrayPointer(a);
      klee_make_symbolic(p, 8 * sizeof(sunrealtype), "a_d"); }

Three heuristics in there are worth knowing about, because each one was wrong
first and produced code that compiled:

* A constructor must come from the library under test. Without that the search
  found `realloc()` as a way to make a `void` and `__ctype_get_mb_cur_max()`
  for a `size_t`.
* The *emptiest* constructor is the wrong one. "Fewest arguments" picks
  `N_VNewEmpty(ctx)` over `N_VNew_Serial(len, ctx)`, and an empty vector has no
  storage -- the accessor returns NULL and filling it faults. Names matching
  empty/null/shell/clone/wrap are tried last.
* Some arguments are switches, not values. FFTW's planner flags left to a
  bounded integer came out as 8, which is `FFTW_EXHAUSTIVE` -- telling the
  planner to time every algorithm it knows. Those are pinned by parameter name.
