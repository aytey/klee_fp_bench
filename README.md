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
| `blis/` | 484 | 2143 | BLIS — dense linear algebra, `generic` (assembly-free) build |
| `sundials/` | 72 | 113 | SUNDIALS — the N_Vector layer |
| `gmp/` | 58 | 250 | GMP 6.3.0 — the `mpf` layer and the double conversions |
| `fftw/` | 28 | 43 | FFTW 3.3.10 — discrete transforms |
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

Drivers are **generated from the libraries' own headers**, not checked in, so
moving to a newer release is a version bump rather than a rewrite. `common/
gen-drivers.py` reads a header's declarations through clang's AST, maps each
parameter type to a way of making it symbolic, and emits a driver per function
it can handle completely. What it cannot handle it records, with the reason, so
the gap is auditable rather than silent.

## Two things to know before reading any number

**GMP is measured on its C path.** GMP's `mpn` layer ships as hand-written
assembly per architecture, and KLEE cannot execute that, so the bitcode build
configures `--disable-assembly`. What is covered is the C fallback.

**Sizes are bounded, not free.** A symbolic `mp_bitcnt_t` or `size_t` reaching
an allocation makes KLEE concretise it. Those parameters are constrained with
`klee_assume` to a small range; the generator says which, per driver.

**BLIS objects are 1x1.** The constructor search takes the constructor with
fewest arguments, which for BLIS is `bli_obj_create_1x1`, so the arithmetic
covered is of degenerate dimensions. Selecting the BLAS-like operations does
not fix that.

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
