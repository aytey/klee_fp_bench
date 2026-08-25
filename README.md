# fp_bench

Does STP decide floating-point queries faster than Bitwuzla, on real numerical
code rather than on kernels written to be hard?

Two libraries, each driven the way the APSEC floating-point-solver paper drove
GSL: one driver per API function, every argument symbolic, and the measurement
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
