# fp_bench

Does STP decide floating-point queries faster than Bitwuzla, on real numerical
code rather than on kernels written to be hard?

And does the answer depend on the width it is asked at?

Eleven libraries, each driven the way the APSEC floating-point-solver paper
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
| `sundials-f128/` | 88 | 180 | the same 88, with `sunrealtype` retyped to **binary128** |
| `sundials-f16/` | 88 | 180 | the same 88 at **binary16** |
| `cmsisdsp/` | 79 | 560 | CMSIS-DSP — DSP, matrix, statistics and distance kernels, **binary16** |
| `cmsisdsp-f32/` | 79 | 506 | the same 79 kernels at **binary32**, which is the control |
| `gmp/` | 58 | 250 | GMP 6.3.0 — the `mpf` layer and the double conversions |
| `fftw/` | 27 | 43 | FFTW 3.3.10 — discrete transforms |
| `fftwq/` | 27 | 43 | the same 27, through FFTW's `fftwq_*` API at **binary128** |
| `f2clapack/` | 38 | 461 | f2cblaslapack — BLAS 1-3 and the LAPACK factorisations, **binary128** |
| `f2clapack-f64/` | 38 | 463 | the same 38 routines at **binary64** |
| `f2clapack-f32/` | 38 | 458 | ...at **binary32** |
| `f2clapack-f16/` | 38 | 461 | ...and at **binary16**: one source tree, four widths |
| `cxsparse/` | 23 | 64 | CXSparse — sparse LU, Cholesky and QR, with pivoting |
| `hdf5/` | 11 | 32 | HDF5 — datatype conversion out of and into **binary16** |
| `hdf5-f32/` | 11 | 33 | the same 11 conversions at **binary32**, the control |
| `cuba/` | 4 | 6 | Cuba 4.2.2 — Vegas, Suave, Divonne and Cuhre at **binary128** |
| `common/` | | | the generator, the harness and the reports |

`SELECTION.md` records why these libraries and not the sixteen others that were
surveyed and measured -- most of the rejections are non-obvious, and several of
the projects that look strongest on paper produce no floating-point queries at
all.

Nineteen driver sets over fifteen builds. `cmsisdsp-f32` and `hdf5-f32` are
second readings of the build above them; `fftw`/`fftwq`, the four `f2clapack`
arms and the three `sundials` arms are each one source tree compiled at
different widths.

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

## The formats, and the six driver sets that are about them

The original seven libraries are binary32 and binary64, because that is what
numerical C is written in. But the mechanism behind the headline result is
width-dependent: STP's gap to Bitwuzla is SymFPU's `fixedPointDivide` blowing
one `fp.div` into a bitvector division about twice the significand wide, which
is 107 bits at binary64 and 22 at binary16. A result measured at one width is
therefore not a result about the solvers; it is a result about the solvers *at
that width*. These six driver sets are what makes the width a variable.

Two of them are controls, and they are not optional. A binary16 corpus on its
own says nothing, because a difference between it and the binary64 libraries
above could be the format or could be that it is different code. What answers
the question is the same kernels at two widths, so `cmsisdsp` and `hdf5` each
have a `-f32` arm generated from the same build, cut to exactly the functions
that exist at both widths, and swept at the same stride. Then the format is the
only thing that differs, and `aggregate.py` compares like with like.

**CMSIS-DSP, at binary16 and binary32.** Arm's DSP library writes the same
kernel once per element type — `arm_add_f16`, `arm_add_f32` and `arm_add_f64`
are one algorithm written three times by the library's own authors — so the
matched pair costs nothing to construct and involves no macro block anybody can
get wrong. 79 kernels exist at both widths and build: filters, matrix operations,
statistics, distance functions, fast maths. It is the only project surveyed
whose half-precision API is broad, native and buildable for x86-64 — its
`float16_t` is `__fp16`, which clang refuses as a parameter or return type on
this target, so `build-lib.sh` redirects the typedef to `_Float16`, the same
format as a first-class arithmetic type.

Its branching lives in the distance and statistics functions; the block
arithmetic is branch-free and is here for coverage rather than for the solver,
which is the split the last paragraph of the previous section describes.

**HDF5, at binary16 and binary32.** Its datatype conversions are the purest
binary16 queries in any of the libraries here: `H5T_CONV_Fx_CORE`'s range
check, `*(S) > (ST)(D_MAX)`, per element, and *nothing else* — no accumulator,
no libm, no wider intermediate anywhere on the path. One driver's queries carry
1,742 binary16 terms and zero terms of any other floating-point sort. For a
comparison across formats that purity is worth more than the driver count
suggests: whatever the two solvers do differently there, they are doing it to
binary16 and not to something binary16 is mixed with.

The destination type is what decides whether there is a query at all. Into
`int`, binary16's whole range fits, so clang folds the checks away and the
conversion is a bare `fptosi`; into `signed char` the checks survive with 32
`fcmp half` in one function. Both are in the table, because the contrast is
part of what the arm has to say.

HDF5 is also the one library whose drivers are **written from a table rather
than read from a header**. All 32 of its `_Float16` conversion functions are
private; every one is reachable through the public `H5Tconvert`, which names
its types with `hid_t` handles rather than with C types, and an `hid_t` is an
opaque integer whose value must name a registered datatype. That is CXSparse's
split applied to a datatype instead of a sparse matrix: structure by hand,
values symbolic. `h5_drivers` in `gen-drivers.py` is the table.

**FFTW at binary128, and what it does not do.** FFTW ships a complete
quadruple-precision `fftwq_*` API on `__float128`, and it builds here: 27
drivers, the same 27 as the binary64 arm, with replay coverage byte-identical
to it. Two things had to be got past, both preprocessor arithmetic gating quad
support on the compiler *claiming* to be GCC ≥ 4.6 — one in `configure.ac`, and
one in `api/fftw3.h`, which otherwise preprocesses the entire `fftwq_*`
declaration block away and leaves the generator reading a header with no quad
API in it rather than reporting an error. One `CPPFLAGS` setting clears both.

**But it contributes coverage, not solver load, and the reason is worth
stating: there is not one `fcmp fp128` in the whole library.** 14,206 quad
adds, 13,601 subtracts, 10,118 multiplies, 10 divides, and no comparison at
all. A transform never branches on a value it is transforming, so no quad path
condition is ever built and the execute drivers issue no queries. It exercises
KLEE's binary128 arithmetic end to end, which is worth having and worth
knowing; it does not discriminate between two solvers.

It is also, incidentally, the answer to the objection the "Quad precision"
section below raises against libquadmath. FFTW calls `sinq` and `cosq` — the
two functions it needs — only to build twiddle factors, from concrete planner
arguments, so they cost nothing; the symbolic quads go through the codelets as
plain arithmetic. `quadmath_snprintf` and `strtoflt128`, the two symbols the
suite cannot build, are never reached: FFTW's plan printer has no float
conversion in it.

**f2cblaslapack, at binary128 and binary64 — the quad library that branches.**
PETSc's f2c translation of BLAS and LAPACK ships a quadruple-precision set
beside the usual four, and it is the answer to what FFTW-quad turned out not to
be. Where FFTW has 38k quad operations and *no* comparison, this has **11,058
`fcmp fp128` and 5,899 `fdiv fp128`**, and the 445 `d*.c` twins ship in the
same tarball — so this is the only axis here spanning binary64 to binary128 on
identical code.

**Where the branching is, is not where it looks like it should be.** The
obvious answer is partial pivoting, and the obvious answer is wrong: `iqamax`,
which `qgetf2` calls to find each pivot, does carry fourteen `fcmp fp128`
around `if ((d__1 = dx[i__], abs(d__1)) > dmax__)`, but at `-O2` every one of
them is consumed by a `select` rather than a branch. Its driver runs 117
instructions and issues **zero queries**. That is the same thing that demoted
CMSIS-NN, found the same way — by running it.

What does branch is the *zero and exception* tests, and they are everywhere:
`qgetf2` on whether the pivot is zero (2 of 3), `qpotf2` on whether the matrix
is still positive definite (2 of 2), `qtrsm` on its `alpha` (13 of 13),
`qgemm` on `alpha` and `beta` (10 of 10), `qgecon` (4 of 7). So the corpus
earns its place through the singularity and special-case tests rather than
through the pivot search, and it is worth saying which, because the two look
identical from the source.

The driver that shows the width is `getrf`, the blocked LU, measured at both
formats under one budget: **binary64 explores 30 paths and 382k instructions
with no query timing out; binary128 gets 6 paths and one timeout.** Same code,
same budget, one variable.

Two things are written rather than generated, both for reasons the suite has
met before. **The shape is concrete and only the values are symbolic**, which is
CXSparse's split for CXSparse's reason: a BLAS call's integer arguments are the
geometry of the matrix — `lda` is the distance between columns — and a symbolic
one is concretised at the first subscript and spends the run on integers. So
the matrix is 4x4 and the character flags are pinned, and what is not measured
is anything depending on the shape. The pins come from a **role table keyed on
parameter name**, which is BLIS's argument: LAPACK names its arguments by role
across all 3,851 of them, by a convention nobody deviates from, and that is a
fact about LAPACK rather than about C.

**And the library ships no prototypes at all.** PETSc declares what it calls in
its own headers, so f2cblaslapack never needed one, and there is nothing for
the generator to read. `common/f2c-protos.py` makes the missing header out of
the definitions themselves — one per precision, because `f2c.h` types the whole
library from a macro — which keeps it honest: a signature there is a signature
the library has, not one somebody transcribed.

The selection is BLIS's too. LAPACK is two thirds auxiliary routines —
everything spelled `?la*` is internal by its own naming convention — and those
are reached, and covered, through the drivers that call them, exactly as
CXSparse's `cs_chol` is measured through `cs_cholsol`. What is kept is BLAS
levels 1 to 3 and the factorisations, solves and condition estimates built on
them: 38 of 461.

**And the same routines at four widths.** f2cblaslapack ships half, single,
double and quad of all 445 routines, with a makefile target for each, so
`f2clapack-f16`, `-f32`, `-f64` and the quad arm are one source tree compiled
four ways — the only place in this corpus where binary16 through binary128 can
be compared on *identical* code rather than on four libraries that resemble
each other. All four generate the same 38 routines, and each module carries
about 24,700 arithmetic operations at its own width and essentially none at any
other.

What that buys is a controlled reading of what width costs. `getrf`, one
routine, a 60-second budget, nothing changed but the format:

| | instructions | tests | query timeouts |
| --- | ---: | ---: | ---: |
| binary16 | 67,293 | **99** | 0 |
| binary32 | 74,582 | 40 | 1 |
| binary64 | 381,476 | 28 | 0 |
| binary128 | 5,607,102 | **7** | 1 |

The half arm needs one patch, and it is the one CMSIS-DSP needs: as shipped its
`halfreal` is `__fp16`, and `f2c.h` declares `extern scalar hf__cabs(scalar,
scalar)`, so on x86-64 nothing compiles at all — `function return value cannot
have __fp16 type`. Redirected to `_Float16` it builds clean.

**SUNDIALS at binary128 and binary16 — the same trick without a second source
tree.** SUNDIALS is written throughout in terms of `sunrealtype`, a type its
own build already switches between float, double and long double, which makes
it the one library here whose precision can be changed from outside without
touching a line of numerics. `common/sundials-precision.py` adds a branch to
the two headers that decide the width — `sundials_types.h` for the typedef and
the literal suffix, `sundials_math.h` for the `SUNRsqrt` family — at the
*front* of each chain, because CMake's generated config.h goes on defining
`SUNDIALS_DOUBLE_PRECISION` and the first matching branch wins.

At quad the maths goes to libquadmath, which the suite already builds. At half
it goes through binary32 and rounds back, and for the square root that is exact
rather than approximate: double rounding through a wider format is benign when
the wider format has at least 2p+2 bits, which for binary16 means 24, and
binary32 has exactly 24. For `exp` and `pow` it is an approximation, and is
marked as one in the script — neither is on the paths these drivers reach.

**What does not work, and fails silently.** The tempting shortcut is to compile
the whole corpus with `-Dfloat=_Float16` or `-Ddouble=__float128` and skip all
of the above. It does not work, and the way it does not work is worth
recording. `-Ddouble=` dies inside glibc rather than inside any library, because
`long double` becomes `long _Float16`:

    /usr/include/bits/floatn-common.h:285:9: error: 'long _Float16' is invalid

`-Dfloat=` is worse, because it compiles, links, runs, and lies:

    normal                    sinf(1.0) = 0.841471
    with -Dfloat=_Float16     sinf(1.0) = 1.000000

The declaration becomes `_Float16 sinf(_Float16)` while glibc's *definition* is
still `float sinf(float)`, so the argument goes over in the wrong format and
the answer is wrong, with no diagnostic anywhere. `float` and `double` are not
the library's types; they are the ABI's, and they cannot be redefined from
outside without redefining every libc and libm definition already compiled.
Which is why a precision axis needs a library written through a type
indirection of its own -- `sunrealtype`, CMSIS-DSP's per-width triples,
f2cblaslapack's `scalar` -- rather than a compiler flag.

**Cuba, at binary128 — the one that asks the solver something.** Four drivers,
one per algorithm, and Cuhre's adaptive convergence test is a quad `fp.div`
with a symbolic denominator: the ~227-bit division that is the reason binary128
is worth reaching at all. `epsrel` and `epsabs` are deliberately left symbolic
where the counters around them are pinned, because they are what that test
divides by. The integrand is a callback rather than a value, so it is written
rather than generated — the smallest one that puts a symbolic quad inside the
integration — and `maxeval` is what bounds the run instead.

Cuba needs two things said about its build. KLEE models neither `fork` nor
`shmget`, and Cuba's parallel sampling reaches both, so both feature probes are
answered "no" in the environment rather than patched out of the source, which
keeps the two builds identical. One declaration then sits inside the `#ifdef`
that turns off, which is the single-line source change in `cuba_serial_fix`.

**One flag decides whether any of the binary16 work means anything.** C's
excess-precision rules let a `_Float16` statement keep a binary32 intermediate,
so without `-fexcess-precision=16` a statement like `s += x[i]*y[i]` compiles to
`llvm.fmuladd.f32` at *every* optimisation level, and a corpus built that way
measures binary32 while claiming binary16. Both builds carry the flag. It is
inert for every library with no `_Float16` in it, which is the original seven.
`-ffp-contract=off` is deliberately *not* set: `llvm.fmuladd.f16` is an FMA with
a single rounding, KLEE implements it as `Expr::FMA`, and all three solvers have
the operation natively, so leaving contraction on is the more interesting query
rather than a less faithful one.

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
| KLEE build | `KLEE_BUILD` | a built KLEE with STP, Bitwuzla and Z3 all enabled, **carrying the binary16 and binary128 commits** |
| LLVM 16 | `LLVM_PREFIX` | the clang KLEE was built against, plus `llvm-cov`/`llvm-profdata` |
| wllvm shims | `SHIM` | the `clang`/`ar`/`ranlib` wrappers that keep bitcode alongside objects |
| a Python env | `PYENV` | holds `wllvm`; some libraries' configure needs it on `PATH` |
| working area | `FP_BENCH_WORK` | where sources, both builds, drivers and runs land — tens of GB, and fastest on a RAM disk |
| STP with the terminator | `STP_TERM` | the `lib64` of an STP whose MiniSat can be stopped mid-search |
| libquadmath sources | `QM_SRC` | a GCC source tree's `libquadmath/` directory; only `common/build-libquadmath.sh` reads it |

**The KLEE has to be new enough for the format, and it does not say so.** A
KLEE without `_Float16` or `__float128` in `fpWidthToSemantics()` does not
refuse the module: it reports `Unsupported FAdd operation`, writes one test,
and a sweep over it produces a full set of rows with a plausible coverage
number in each. Both formats were added to the branch on the same day and a
build from the morning before has neither. `build-drivers.sh` now puts a
five-line symbolic add of the relevant type to `$KLEE_BUILD/bin/klee` before
compiling anything for `cmsisdsp`, `hdf5`, `fftwq` or `cuba`, and refuses the
library if it comes back unsupported — in the same spirit as
`build-libquadmath.sh` refusing to write a partial library.

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
    common/dump-queries.sh   <lib> [tag]  # one .smt2 per query, for replay
    common/query-sorts.py    <dir>...     # which FP formats those queries carry
    common/queries-by-function.py <run>   # instructions and queries per subprogram

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
stride per library — 10 for GSL, 1 for FFTW and CXSparse — which takes 1,657
drivers down to **574**. The figures in `RESULTS.md` predate the six
format-axis driver sets and were measured over the 255 that remain when those
are removed. The stride is per library rather than global because the libraries
differ by an order of magnitude in size and a global one would make the corpus
almost entirely GSL. Editing the `LIBS` table changes it; a stride of 1
everywhere is the whole corpus and roughly four times the wall clock.

**HDF5 is the expensive row.** `H5open()` runs before any conversion does, and
it is about 75M instructions and two and a half minutes of the wall clock per
driver before the arithmetic starts, so `run-one.sh` triples the budget for it
(`HDF5_BUDGET` overrides). Twenty-two drivers at that rate is roughly an extra
hour per solver configuration — a four-configuration sweep gains about four
hours from HDF5 alone. `SKIP_REPLAY=1` does not help; the time is in KLEE.
Drop `hdf5` and `hdf5-f32` from the `LIBS` table for a quick sweep and put them
back for a measurement.

**The two arms of a format axis must carry the same stride**, and their driver
lists are generated to correspond kernel for kernel so that the stride selects
the same kernels from each. That is why `cmsisdsp` is cut to the functions that also
exist at binary32 -- 79, against the 84 that exist at binary16: with lists of
different lengths a stride selects different kernels from each, and the pair
stops being a pair. (The eightieth, `arm_cfft_radix2_*`, is excluded at both
widths for a different reason: CMSIS-DSP declares it in the header and compiles
it into neither build.)

### Comparing solvers with KLEE out of the way

Inside KLEE two solvers stop being asked the same questions the moment one of
them times out a query the other answered — the state dies, the path goes
unexplored, and every later query differs. `split-queries.py` and
`replay-queries.sh` take a dumped corpus and run both solvers over identical
files, which is the only comparison with nothing else in it. `RESULTS.md` reads
both, and says where they disagree.

`dump-queries.sh` is what produces that corpus — until recently nothing did,
and the corpus behind `RESULTS.md` was dumped by hand. It takes a tag, which is
what the filenames end in, and that is how a format axis is measured:

    FP_BENCH_OUT=$WORK/axis common/dump-queries.sh cmsisdsp     f16
    FP_BENCH_OUT=$WORK/axis common/dump-queries.sh cmsisdsp-f32 f32
    common/replay-queries.sh $WORK/axis/split $WORK/axis/replay.tsv

`replay-queries.sh` runs the two solvers back to back per file, so a slow patch
of machine falls on both. Putting two tagged corpora in one directory extends
that across the two formats as well: `find | sort` lands each query next to its
counterpart, so the same slow patch falls on both formats too.

One warning that script's own comments carry and that a format comparison makes
easy to trip over: **check the verdict column before the times.** STP answers an
operator it does not support with an `(error …)` in ten milliseconds, which
reads exactly like a very fast solve — 26 queries in the first corpus were
scored as instant STP wins that way.

## Quad precision

The original seven libraries are measured at binary32 and binary64. binary128
is the format where the queries get genuinely hard -- an `fp.div` on quads
blasts to a ~227-bit division where a double gives ~107 -- so it is worth
being able to reach. `fftwq/` and `cuba/` are what reach it now, and
`common/build-libquadmath.sh` is what makes them linkable.

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

### Why libquadmath is not itself a library here

Two libraries above are driven at binary128; libquadmath is not one of them,
and the distinction is what the rest of this section is about. `fftwq` and
`cuba` *use* libquadmath, with concrete arguments, from code whose own
arithmetic is what gets driven. Driving libquadmath itself is a different
proposition, and it does not work.

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

What FFTW and Cuba do instead is call into it concretely. FFTW needs exactly
two of its functions, `sinq` and `cosq`, and calls them on planner arguments to
build twiddle factors, so they complete in milliseconds and issue no queries at
all; the symbolic quads travel through the codelets as plain arithmetic. Cuba
reaches its own convergence test the same way. Neither ever reaches
`quadmath_snprintf` or `strtoflt128`, the two symbols this suite cannot build.

`reproducers/` remains the other use for quad, and a cheaper one: a single
quad `fp.div` isolates the same division mechanism the two reproducers already
there isolate at binary64, at twice the width and for the cost of one query
rather than a driver. Note what it will not tell you, though. Asked in
isolation, four widths of `fp.div` separate cleanly -- but four of six
isolated *identity* queries answer in about 15 ms at every width in both
solvers, because they hit a rewrite rule rather than a circuit, and an
isolated `fp.div` reports the two solvers as level at binary64 where the real
corpus reports 2.45x. A reproducer isolates the mechanism; it misreports its
size. That is what the libraries are for.

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

**CMSIS-DSP was here, and is now a library above.** The reason it was rejected
still holds and is not the reason it is now in: its bulk *is* branch-free block
arithmetic over arrays, and this corpus does have more of that than it needs at
binary64. What changed is the question. It is the only surveyed project whose
binary16 API is broad, native and buildable for x86-64, and its per-width
triples make the matched pair the format axis needs. It is in for what it is at
two widths, not for what it computes at one.

**PETSc, hypre, SuperLU.** For hypre, Python-driven configuration and MPI; for
SuperLU, near-duplicate `s/d/c/z` sources over the same sparse construction
problem CXSparse already covers.

PETSc's reason has been re-checked and was half wrong. `--with-mpi=0` gives a
serial MPIUNI build and `--with-precision=__float128` compiles clean under
clang 16, so neither MPI nor the configuration is the barrier. What is, is the
API shape: `construct.py` models a constructor as one call where PETSc needs
four, so the search settles on `VecCreate` and the resulting shell returns
`PETSC_ERR_SUP` **silently** — 1,579 drivers that run, return, and measure
nothing, which is exactly the failure this suite exists to avoid.

**Rejected for binary16, each on evidence.** XNNPACK, SIMDe, NumKong and
OpenBLAS all turn out to emit no binary16 arithmetic at all on x86-64 — the
token `half` does not appear in the live kernel's IR, because each one stores a
half and computes in `float`. KleidiAI does not build for this target: 61 of
its 62 f16 sources `#error` on non-AArch64 and there is no portable-C kernel.
PULP-TrainLib's `fp16` is a PULP-GCC RISC-V type clang rejects, and it needs a
`pmsis.h` that is not in the repository. CORE-MATH is the one worth a sentence
of its own, because it looks like the strongest candidate and is not: 41 of its
43 binary16 sources abort KLEE on `llvm.experimental.constrained.*`, which is a
fixable gap — but only 2 of the 43 contain any binary16 arithmetic, the rest
being a `_Float16` API over binary32 and binary64 evaluation, which is not.

**Rejected for binary128.** Surface Evolver routes every branch through
`kb_error` to `longjmp`, which is Qhull's problem again, and sends every
numeric literal through `strtoflt128`, which is one of the two libquadmath
symbols this suite cannot build. galculator gates its quad support on
`#if !defined(__clang__)`, so it is off by construction for this toolchain.
Perl 5 generates 1,123 drivers of which 825 fabricate an `SV` from an
uninitialised stack slot and return 0 without crashing, and it needs both
missing libquadmath symbols on central paths.

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
