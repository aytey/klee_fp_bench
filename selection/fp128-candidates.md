# binary128 benchmark candidates for `fp_bench`

Assessment of the six projects in `libquadmath-c-projects.md` against
`fp_bench`'s criteria, and a worked-through recipe for adding FFTW's
quadruple-precision `fftwq_*` API to the suite.

Everything below marked **verified** was run on this machine on 2026-08-28.
Everything marked **inferred** was read rather than executed. Timings are
feasibility signals only: another agent was running KLEE and a Z3 test suite
on the same box throughout, so no number here is a measurement.

Work area for the FFTW part: `/mnt/baranem/fp_bench-work/fftwq-probe/`.
Nothing under `/home/avj/clones/fp_bench/` was modified; the generator patch
was applied to a copy at `/mnt/baranem/fp_bench-work/fftwq-probe/common/`.

Work areas for the other five: `/mnt/baranem/fp_bench-work/cand-probe/`
(Cuba, galculator), `/mnt/baranem/fp_bench-work/petsc-perl-probe/`,
`/mnt/baranem/fp_bench-work/evolver-probe/`. `git status` in the fp_bench
checkout is clean.

## The answer in one table

| project | quad shape | builds, clang 16 | drivers | asks the solver at quad? | verdict |
| --- | --- | --- | ---: | --- | --- |
| **FFTW 3.3.10** | parallel `fftwq_*` API | **yes**, both builds | **27** | **no** — 0 `fcmp fp128` in the whole library | **add**, as coverage |
| **Cuba 4.2.2** | `--with-real=16`, `#define sqrtx sqrtq` | yes, with fork/shm off + a 1-line patch | 4 | **yes** — 17 queries, ~25s of solver time on one driver | **add**, as solver load |
| PETSc | `typedef __float128 PetscReal` | yes (serial, MPIUNI) | ~35 real, 1,579 hollow | yes (40 `fcmp fp128` in one file) | reject — API shape, *not* MPI |
| Surface Evolver | `#define REAL __float128` | yes, 93/93 files | 24, of which 22 branch-free | only via libm-shaped loops that time out | reject |
| galculator | `typedef __float128 G_REAL` | **no** — `!defined(__clang__)` | 48 of 8,304 emitted | only inside `powq`/`floorq` | reject |
| Perl 5 | `typedef __float128 NV` | yes (`miniperl`) | 5 usable of 1,123 emitted | n/a | reject |

Two of the six are worth having, and for opposite reasons: FFTW because it is
a large, real, buildable binary128 workload that exercises KLEE's quad
arithmetic end to end, and Cuba because it is the only one that puts hard
binary128 queries in front of the solver. Neither is a substitute for the
other. The other four fail on the generator, on the two libquadmath symbols
the suite cannot build, or on both — and §2.6 tabulates exactly where those
two symbols bite.

---

## 0. The one thing that has to be fixed first

`fp_bench`'s default KLEE, `/mnt/baranem/klee-float/3.2-buildtest/bin/klee`
(built 2026-08-27 09:12), **cannot execute binary128 at all**. The commit that
adds `Expr::Int128` to `fpWidthToSemantics()` — `ec002431 "Let KLEE reason
about __float128"` — is dated 2026-08-28 09:26, i.e. after that binary was
linked. **Verified** on a five-line program:

    __float128 a, b; klee_make_symbolic(...); __float128 c = a + b;

    3.2-buildtest:  KLEE: ERROR: qmin.c:6: Unsupported FAdd operation
                    KLEE: done: total instructions = 13

    quad-2026:      KLEE: done: completed paths = 2
                    KLEE: done: total instructions = 25

`/mnt/baranem/quad-2026/klee-build/bin/klee` (built 2026-08-28 10:12 from the
same source tree, `CMAKE_HOME_DIRECTORY=/mnt/baranem/klee-float/3.2`, with
Z3+STP+Bitwuzla all `ON`) does have it. Every KLEE run reported below uses
that binary. `KLEE_BUILD` must be repointed, or `3.2-buildtest` rebuilt,
before any quad work in the suite means anything — the failure mode is a
driver that "runs", produces a test, and reports a coverage figure, having
terminated on the first arithmetic instruction. That is exactly the class of
silent-zero the README's "A driver can run and still measure nothing" section
is about.

---

## 1. FFTW at binary128 — it works, end to end

**Verdict: buildable, generatable, runnable, and driver-for-driver identical
to the existing `fftw` benchmark. Worth adding as a new library `fftwq`.
But read §1.6 before expecting it to be a *solver* benchmark: FFTW's quad
build contains zero `fcmp fp128`, so it never asks the solver a
binary128 question.**

### 1.1 What FFTW's `--enable-quad-precision` actually tests for

Two gates, both in `configure`, both from
`/mnt/baranem/fp_bench-work/fftwq-probe/fftw-3.3.10/configure.ac:561-565`:

```m4
if test $PRECISION = q; then
   AX_GCC_VERSION(4,6,0,[],[AC_MSG_ERROR([gcc 4.6 or later required for quad precision support])])
   AC_CHECK_LIB(quadmath, sinq, [], [AC_MSG_ERROR([quad precision requires libquadmath ...])])
   LIBQUADMATH=-lquadmath
fi
```

`AX_GCC_VERSION` (`m4/ax_gcc_version.m4`) is a *preprocessor* test:

```c
#if (__GNUC__ > 4) || (__GNUC__ == 4 && __GNUC_MINOR__ > 6) \
 || (__GNUC__ == 4 && __GNUC_MINOR__ == 6 && __GNUC_PATCHLEVEL__ >= 0)
     yes;
#endif
```

Clang 16.0.6 reports `__GNUC__ 4`, `__GNUC_MINOR__ 2`, `__GNUC_PATCHLEVEL__ 1`
(**verified**, `clang -dM -E`), so it fails — the documented "requires GCC" is
in fact "requires a compiler claiming GCC >= 4.6". `$GCC` itself is `yes` for
clang, so only the version arithmetic is in the way.

The same arithmetic appears a **second** time, and this one matters far more,
in the public header `api/fftw3.h:471-484`:

```c
#if (__GNUC__ > 4 || (__GNUC__ == 4 && __GNUC_MINOR__ >= 6)) \
 && !(defined(__ICC) || defined(__INTEL_COMPILER) || ...) \
 && (defined(__i386__) || defined(__x86_64__) || defined(__ia64__))
...
FFTW_DEFINE_API(FFTW_MANGLE_QUAD, __float128, fftwq_complex)
#endif
```

Under clang the **entire `fftwq_*` declaration block is preprocessed away**.
**Verified**:

    $ clang -c -I fftw-3.3.10/api h1.c        # h1.c: #include <fftw3.h>; fftwq_plan p;
    h1.c:2:1: error: unknown type name 'fftwq_plan'

This breaks three separate things at once: the library's own `api/*.c` files
take their `X(plan)` typedef from that block (`api/api.h:52` includes
`api/fftw3.h`); `gen-drivers.py`'s AST probe would see no `fftwq_*` functions;
and the generated drivers would not compile.

### 1.2 The workaround, and the second problem

One flag fixes both gates, because both are preprocessor arithmetic:

    CPPFLAGS="-U__GNUC_MINOR__ -D__GNUC_MINOR__=6"

`CPPFLAGS` rather than `CFLAGS` on purpose — autoconf's `AC_EGREP_CPP` runs
`$CPP $CPPFLAGS` and never sees `CFLAGS`, so putting it in `CFLAGS` passes the
header but still fails `AX_GCC_VERSION`. **Verified**: with it in `CPPFLAGS`,
`config.log:132-133` reads

    checking whether we are using gcc 4.6.0 or later... yes
    checking for sinq in -lquadmath... yes

and `configure` exits 0. (The alternative — `ax_cv_gcc_4_6_0=yes` in the
environment plus a one-line `|| defined(__clang__)` patch to `api/fftw3.h` —
is two changes rather than one and needs a source patch. The `CPPFLAGS`
route also works with the *distro* clang 21.1.6 used for the coverage build,
which reports the same `__GNUC__ 4.2.1`. **Verified** for both.)

`quadmath.h` itself is fine: clang 16 compiles GCC 10's
`/usr/src/debug/gcc-10.5.0+git3415/libquadmath/quadmath.h` with **no
diagnostics**, including

```c
typedef _Complex float __attribute__((mode(TC))) __complex128;
```

and a `__complex128` multiply. **Verified** — `mode(TC)` is supported by
clang 16. (FFTW does not include `quadmath.h` anyway; `kernel/trig.c:40-41`
declares `sinq`/`cosq` itself, and `api/fftw3.h` redefines
`FFTW_DEFINE_COMPLEX` with the same `mode(TC)` attribute.)

The second problem is the link, not the compile. `AC_CHECK_LIB(quadmath,
sinq)` links a test program, and there is no `libquadmath` on the default
search path — only `/usr/lib64/libquadmath.so.0`, a versioned SONAME with no
`.so` symlink. **Verified**:

    $ clang -o lqtest lqtest.c -lquadmath
    ld: cannot find -lquadmath: No such file or directory

The `.so` lives in GCC's own directory. Discover it portably with

    -L$(dirname "$(gcc -print-file-name=libquadmath.so)")
    # -> /usr/lib64/gcc/x86_64-suse-linux/11

(**verified**; clang's `-print-file-name` returns the bare name and is no
use here). With that in `LDFLAGS`, the probe links and runs.

### 1.3 The exact recipe

**Bitcode build (what KLEE executes)** — **verified**, `make` exit 0, no
errors in `build.log`:

```sh
export PATH="$PYENV/bin:$SHIM:$LLVM_PREFIX/bin:$PATH"
export LLVM_COMPILER=clang LLVM_COMPILER_PATH="$SHIM"
export LLVM_CC_NAME=klee-clang LLVM_CXX_NAME=klee-clang++
QMDIR=$(dirname "$(gcc -print-file-name=libquadmath.so)")

CC=wllvm \
CPPFLAGS="-U__GNUC_MINOR__ -D__GNUC_MINOR__=6" \
CFLAGS="-O2 -g -fno-vectorize -fno-slp-vectorize \
        -Wno-implicit-function-declaration -Wno-implicit-int" \
LDFLAGS="-L$QMDIR" \
  ./configure --disable-shared --enable-static --disable-fortran \
              --enable-quad-precision
make -j"$JOBS"
extract-bc -b .libs/libfftw3q.a -o "$WORK/fftwq.bc"     # 7,450,772 bytes
```

**Native coverage build (what tests are replayed against)** — **verified**,
`make` exit 0, `.libs/libfftw3q.a` = 13,549,558 bytes:

```sh
CC=/usr/bin/clang \
CPPFLAGS="-U__GNUC_MINOR__ -D__GNUC_MINOR__=6" \
CFLAGS="-O0 -g -fprofile-instr-generate -fcoverage-mapping \
        -Wno-implicit-function-declaration -Wno-implicit-int" \
LDFLAGS="-fprofile-instr-generate -L$QMDIR" \
  ./configure --disable-shared --enable-static --disable-fortran \
              --enable-quad-precision
make -j"$JOBS"
```

The two `config.h` files are **identical** under `build-lib.sh`'s own
`cfg_of()` filter (**verified**: `diff` returns nothing), and both carry
`#define FFTW_QUAD 1` and `#define HAVE_LIBQUADMATH 1`. No SIMD is enabled in
either (`HAVE_SSE2`, `HAVE_AVX*`, `HAVE_GENERIC_SIMD*` all `#undef`) —
FFTW's `simd-support/*.h` all `#error` under `FFTW_QUAD`, so the quad build is
scalar by construction and needs none of the BLIS-style vectorisation
wrangling.

**Driver link** — the drivers need the same `-U__GNUC_MINOR__
-D__GNUC_MINOR__=6`, and the bitcode side needs libquadmath linked in:

```sh
clang -emit-llvm -O0 -g -c -U__GNUC_MINOR__ -D__GNUC_MINOR__=6 \
      -I "$WORK/fftwq-bc/api" -I "$KLEE_SRC/include" -o d.bc drv.c
llvm-link d.bc "$WORK/fftwq.bc" "$WORK/libquadmath/libquadmath-math.bc" -o l.bc
opt -internalize-public-api-list=main -passes='internalize,globaldce' l.bc -o obj.bc

# native replay
/usr/bin/clang -O0 -g -fprofile-instr-generate -fcoverage-mapping \
  -U__GNUC_MINOR__ -D__GNUC_MINOR__=6 \
  -I "$WORK/fftwq-cov/api" -I "$KLEE_SRC/include" \
  -o bin/<name> drv.c common/replay-flush.c \
  "$WORK/fftwq-cov/.libs/libfftw3q.a" \
  -L"$KLEE_BUILD/lib" -lkleeRuntest -L"$QMDIR" -lquadmath -lm
```

All 27 drivers built both ways. **Verified**.

### 1.4 What the quad build needs from libquadmath — two symbols, both covered

`llvm-nm --undefined-only fftwq.bc` over the *whole* `libfftw3q.a` gives
**18** undefined symbols. **Verified**, complete list:

    abort  cosq  fclose  ferror  fflush  fopen  fprintf  fread  free
    fwrite  gettimeofday  log  malloc  qsort  sinq  stderr  stdout  strcmp

**The only libquadmath symbols are `sinq` and `cosq`.** Both are in
`common/build-libquadmath.sh`'s `libquadmath-math.bc` (95 sources, 0 failures,
"all 21 math entry points of the macro block are defined" — **verified** by
running the script into a private `OUT`). After `llvm-link`, the driver module
has no unresolved libquadmath reference; what is left is `floor`, `log`,
`scalbn` from libm (supplied by `--link-llvm-lib=<uclibc>/lib/libm.a`) and the
usual stdio stubs KLEE warns about and never calls.

**Neither `quadmath_snprintf` nor `strtoflt128` appears anywhere.** This is
not luck and it is not because plan printing was excluded — `fftwq_print_plan`,
`fftwq_fprint_plan`, `fftwq_export_wisdom`, `fftwq_export_wisdom_to_string`,
`fftwq_import_wisdom` and their siblings are all **defined** in the module
(**verified**, `llvm-nm --defined-only`). The reason is that FFTW's plan
printer has no floating-point conversion at all: `kernel/print.c` implements
its own mini-printf whose entire directive set is
`%M %c %s %d %D %v %o %u %x %( %) %p %P %T` (**verified**, `grep "case '"`) —
md5 digests, integers, tensors and plan structure. Wisdom is a structural
description, not numbers. **The two known gaps in the suite's libquadmath
cost FFTW nothing, and no part of the `fftwq_*` API is unreachable because of
them.**

### 1.5 The twiddle-factor hypothesis — confirmed, and it matters

The README's reason for not making libquadmath its own library is that
`sinq`/`cosq` reduce a symbolic argument through a 5,312-byte table and time
out. FFTW calls exactly those two functions. The hypothesis to test was that
it calls them only with **concrete** arguments.

**Reading it** (`kernel/trig.c`): the calls live in

```c
static void real_cexp(INT m, INT n, trigreal *out)
{
     theta = by2pi(m, n);            /* (K2PI * m) / n, both INT */
     c = COS(theta); s = SIN(theta); /* cosq / sinq under TRIGREAL_IS_QUAD */
}
```

`m` and `n` are `INT` (`ptrdiff_t`) and come from `X(mktriggen)(wakefulness,
n)` and a loop index, i.e. from the *plan size*, never from the data. The
data path is `rotate_sqrtn_table` / the codelets, which only multiply and add.

**Running it**: four probes, concrete `n`, symbolic complex quad input,
`fftwq_plan_dft_1d` + `fftwq_execute`, Bitwuzla, 60s budget / 5s cap. Self-cost
instruction counts extracted from `run.istats`. **Verified**:

| n | wall | instrs | `sinq`+`cosq` self | `trig.c` self | transform kernel | solver queries | solver time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15 | 3.2s | 1,640,723 | 0 | 0 | `n1_15` 563 | 0 | 0.000s |
| 32 | 3.3s | 1,723,760 | 0 | 0 | `n1_32` 1,224 | 0 | 0.000s |
| 128 | 3.3s | 1,737,762 | 8,562 | 2,911 | `t2_8` 4,567 / `n1_16` 4,162 | 0 | 0.000s |
| 1024 | 3.3s | 1,879,433 | 21,181 | 7,711 | `t2_32` 48,285 / `n1_32` 38,486 | 0 | 0.000s |

At n=15 and n=32 FFTW picks a single direct codelet and never builds a twiddle
table, so `sinq`/`cosq` are not called at all. At n=128 and n=1024 it goes
Cooley-Tukey, calls `X(mktriggen)`, and **does** run `sinq`/`cosq` —
`__quadmath_kernel_sinq` 8,378 instructions, `__quadmath_kernel_cosq` 8,251 at
n=1024 — and the whole run still **completes in 3.3 seconds with one path,
zero solver queries and zero query timeouts**. The transcendentals are folded
concretely; the symbolic quad data goes through `t2_32` and `n1_32` as plain
`fadd`/`fsub`/`fmul` on `fp128`.

**The hypothesis holds.** FFTW at quad reaches real binary128 arithmetic
without paying libquadmath's symbolic-transcendental cost.

### 1.6 …and the finding that qualifies all of it

A census of the whole `libfftw3q` module (**verified**, `llvm-dis | grep -oE`):

| op | count |
| --- | --- |
| `fadd fp128` | 14,206 |
| `fsub fp128` | 13,601 |
| `fmul fp128` | 10,118 |
| `fneg fp128` | 1,993 |
| `fdiv fp128` | 10 |
| **`fcmp fp128`** | **0** |
| `fptrunc`/`fpext` involving `fp128` | 0 |

There is not one floating-point comparison on a quad anywhere in FFTW. (One
survives into a *driver* module after `llvm-link` and `globaldce`: the
tiny-argument test in libquadmath's `__quadmath_kernel_sinq`, which runs on a
concrete argument. libquadmath's `math/` bitcode has 464 `fcmp fp128` in
total; the prune keeps one. **Verified.**) A
transform is branch-free by construction, and FFTW's planner cost model is
`double` at every precision — `kernel/ifftw.h:337-340` (`opcnt` is four
`double`s), `:561` `double pcost`, `:753` `double timelimit`. So:

* the `fftwq_execute*` drivers reach the arithmetic, build large symbolic
  `fp128` expressions, and issue **zero** solver queries;
* the `fftwq_plan_*` drivers are budget-bound in the planner, and their
  queries are about the symbolic `int n` — **verified** by grepping the
  emitted `.kquery` files, which contain only `w32` and `w64` terms and no
  `fp128` at all.

Head-to-head, same KLEE binary, same flags, Bitwuzla, 60s/5s (**feasibility
signal, not a measurement** — machine shared):

| driver | wall | instrs | solver queries | solver time | tests | outcome |
| --- | --- | --- | --- | --- | --- | --- |
| `fftwq_plan_dft_1d` | 63.0s | 1,637,620 | 73 | 59.07s | 9 | budget-bound; `planner.c:683 Query timed out (bounds check)` |
| `fftw_plan_dft_1d` | 67.7s | 1,649,273 | 99 | 62.19s | 9 | budget-bound; `planner.c:181 Query timed out (fork)` |
| `fftwq_execute_dft` | 3.3s | 1,651,933 | 0 | 0.000s | 1 | completed, ran `r2cf_8` (142 instrs of quad arithmetic) |
| `fftw_execute_dft` | 6.2s | 1,643,964 | 0 | 0.000s | 1 | completed |

And the replay closes the loop. `fftwq_execute_dft`'s single test replayed
against the instrumented native quad library gives

    6,7,1,2,2275,76822,287,3833

which is **byte-for-byte the coverage row the double `fftw_execute_dft`
already records** in `fftw/runs-all/results.psv`. The two builds are the same
program at two precisions, and the suite's own measurement says so.

So `fftwq` is a **coverage and robustness** benchmark for KLEE's binary128
support — 27 drivers over 40,000 quad arithmetic instructions, a real library,
building and running end to end — and it is **not** a source of hard fp128
queries. Under the suite's own standard ("only the second is a solver
benchmark"), it belongs in the corpus for the same reason a `gemm` driver
does, and no more. If the goal is specifically to make STP and Bitwuzla decide
`fp.div` on quads, `reproducers/` remains the right home, as the README
already says: there are ten `fdiv fp128` in all of FFTW.

### 1.7 The drivers

**Verified**: with the generator patch in §3.1 applied to a scratch copy,

    fftwq: 27 drivers, 22 skipped

against 72 `fftwq_` `FunctionDecl`s in `api/fftw3.h`, 49 surviving the exclude
regex. Re-running the *unmodified* generator on the existing double build for
comparison prints `fftw: 27 drivers, 22 skipped` — the same two numbers, with
the same six skip reasons in the same proportions (7 `iodim *`, 7 `iodim64 *`,
3 `r2r_kind`, 2 `r2r_kind *`, 2 `size_t`, 1 `FILE *`). **Verified.** `diff` of the two driver-name lists with the
prefix stripped is **empty**, and the two `skipped.tsv` files differ only in
the prefix — the same seven `guru`/seven `guru64` `iodim` cases, the same five
`r2r_kind` cases, `alloc_complex`/`alloc_real` on `size_t`, and
`fprint_plan` on `FILE *`.

A generated driver:

```c
/* Generated by common/gen-drivers.py -- do not edit. */
#include <fftw3.h>
#include <klee/klee.h>

int main(void) {
  int a0;
  klee_make_symbolic(&a0, sizeof(a0), "a0");
  klee_assume(a0 <= 8);
  klee_assume(a0 >= 0);
  fftwq_complex a1[8];
  klee_make_symbolic(a1, sizeof(a1), "a1");
  fftwq_complex a2[8];
  klee_make_symbolic(a2, sizeof(a2), "a2");
  int a3 = FFTW_FORWARD;
  unsigned int a4 = FFTW_ESTIMATE;
  volatile fftwq_plan r_ = fftwq_plan_dft_1d(a0, a1, a2, a3, a4);
  fftwq_execute(r_);
  return 0;
}
```

`fftwq_complex` is `__float128[2]` in a driver, because a driver includes
`<fftw3.h>` and not `<complex.h>`, so the `FFTW_DEFINE_COMPLEX` override to
`_Complex float __attribute__((mode(TC)))` is not taken. That is why the
existing `fftw_complex *` maker shape transfers unchanged.

### 1.8 Smoke test, wider

Eleven of the 27 quad drivers and their eleven double counterparts, run one
at a time with the **same** KLEE binary, Bitwuzla, 60s budget / 5s cap, on a
machine that was also running someone else's KLEE throughout. **Feasibility
signals, not measurements.** **Verified.**

| driver | quad wall | quad instrs | quad tests | double wall | double instrs | double tests |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `plan_dft_1d` | 63.0s | 1,637,620 | 9 | 67.7s | 1,649,273 | 9 |
| `plan_dft_r2c_1d` | 66.1s | 1,644,885 | 9 | 64.6s | 1,636,912 | 9 |
| `plan_dft` | 63.4s | 1,728,729 | 30 | 64.8s | 1,739,259 | 30 |
| `plan_many_dft` | 66.8s | 1,725,869 | 31 | 63.5s | 1,746,842 | 34 |
| `execute` | 3.3s | 1,651,932 | 1 | 4.6s | 1,643,963 | 1 |
| `execute_dft` | 3.3s | 1,651,933 | 1 | 6.2s | 1,643,964 | 1 |
| `execute_split_dft` | 3.4s | 1,651,889 | 1 | 3.6s | 1,643,920 | 1 |
| `cost` | 3.5s | 1,651,726 | 1 | 3.5s | 1,643,757 | 1 |
| `estimate_cost` | 5.6s | 1,651,755 | 1 | 3.4s | 1,643,786 | 1 |
| `flops` | 3.6s | 1,651,760 | 1 | 3.5s | 1,643,791 | 1 |
| `destroy_plan` | 3.4s | 1,651,855 | 1 | 3.6s | 1,643,886 | 1 |

The two columns are the same benchmark. Every error class that appears at
quad appears at double, at the same place:

* `planner.c:683 Query timed out (bounds check)` / `ct.c:97` /
  `planner.c:221 Query timed out (fork)` — the symbolic `n` in the planner;
* `kalloc.c:135 concretized symbolic size` and `tensor7.c:57/140 failed
  external call: qsort` in `plan_dft` and `plan_many_dft`, both precisions;
* `r2cf_8.c:142 memory error: out of bound pointer` in `execute_split_dft`,
  **both precisions** — a driver artefact (the generator plans a split-format
  transform and then hands `execute_split_dft` interleaved buffers), not
  anything quad introduced.

No error was specific to the quad build, no quad driver failed to build, load
or start, and no quad driver terminated on an unsupported instruction. The
~1.64M-instruction floor every driver shares is FFTW's one-time solver-table
registration — 1.31M of it inside KLEE's own `memcpy` — and it is the same at
both precisions.

---

## 2. The other five projects

Each was fetched, built where it would build, and run under the same
quad-capable KLEE. Work areas: `/mnt/baranem/fp_bench-work/cand-probe/`
(Cuba, galculator), `/mnt/baranem/fp_bench-work/petsc-perl-probe/`,
`/mnt/baranem/fp_bench-work/evolver-probe/`.

One distinction runs through all five and is worth stating once. FFTW is the
only project here with a **parallel quad API** (`fftwq_*`, separate symbols,
separate archive, both precisions installable side by side). Every other one
is ordinary numerical code with a **`#define REAL __float128` build
configuration**: one source, one set of symbol names, and the precision chosen
at configure time. That is the shape `README.md`'s "Quad precision" section
describes, and it has a consequence for the suite — a `REAL` library gives one
*or* the other precision per build tree, so measuring both means two work
directories and two `build-lib.sh` arms, where FFTW gives both from one build.

### 2.1 Cuba — **the one worth adding for solver load**

**Verdict: include, conditionally.** It is the only candidate found whose
binary128 solver work is its *own* — an adaptive-subdivision test on a quad
`fp.div` — rather than libquadmath's.

**Get the right version.** The git mirror named in the input document,
`github.com/JohannesBuchner/cuba` (head `d3a5245`), is **Cuba 4.1**
(`configure.ac:4`), and it has **no `REALSIZE` at all** — `grep -rn REALSIZE`
returns nothing, and `src/common/stddecl.h:360` is
`typedef /*long*/ double real;` with a comment saying switching is "not as
trivial as it might seem". The quad configuration exists only in 4.2.x.
Upstream plain HTTP **does** work from this machine: `curl
http://www.feynarts.de/cuba/` and then `Cuba-4.2.2.tar.gz` (744,120 bytes)
both succeeded. **Verified.** So Cuba is a `curl`-a-tarball library like GSL
and GMP, not a `git clone` one, and the input document's "useful Git mirror"
is a trap.

**Which kind, and how it is switched.** A `REALSIZE` configuration.
`configure.ac:61-73` gives `--with-real=16`, which sets `SUFFIX=q` and appends
`-lquadmath`; `makefile.in:3` passes `-DREALSIZE=@REALSIZE@`;
`src/common/stddecl.h:366-377` is the macro block:

```c
#if REALSIZE == 16
#include <quadmath.h>
typedef __float128 real;
#define RC(x) x##Q
#define sqrtx sqrtq
#define expx  expq
#define powx  powq
#define erfx  erfq
#define fabsx fabsq
#define ldexpx ldexpq
```

The public header is *generated by `sed`*, which is the non-obvious part —
`makefile.in:298-299`:

    cubaq.h: cuba.h
    	sed 's/double/__float128/g' cuba.h > cubaq.h

so `cubaq.h:11` is `typedef __float128 cubareal;` while `cuba.h:11` is
hardcoded `double`. A `build-lib.sh` arm has to generate drivers from
`cubaq.h`, not `cuba.h`.

**Build.** `./configure --with-real=16 CC=wllvm CFLAGS="-O1 -g -I<quadmath>"`
then `make lib` **succeeds**: `libcubaq.a` 2,640,592 bytes, `libcubaq.bc`
3,032,118 bytes with `Vegas`/`Cuhre` defined. **Verified.** Two things have to
be done to it, both with precedent in the suite:

* **fork/shm must be configured off** — `ac_cv_func_fork=no
  ac_cv_func_shmget=no`. Cuba's `FrameAlloc` (`stddecl.h:206-218`) expands to
  `shmget`/`shmat`; KLEE calls them externally, gets memory it cannot model,
  and dies with `KLEE: ERROR: ./src/cuhre/Rule.c:618: memory error: out of
  bound pointer` after 32,172 instructions. **Verified.** This is exactly
  GMP's `--disable-assembly` and BLIS's `--disable-tls`.
* **That configuration does not compile upstream.** Exact error:

      ./src/common/Fork.c:160:3: error: use of undeclared identifier 'cubafun_'
        MasterExit();

  `extern coreinit cubafun_;` is at `Fork.c:19`, inside `#ifdef HAVE_FORK`;
  `MasterExit()` at `:160` is in the `#else`. A one-line patch moving the
  extern out fixes it. **Verified.** A checked-in patch is a new thing for
  this suite, but it is one line and it is an upstream bug.

The quad IR is real: `src/cuhre/Cuhre.c` alone has 813 `fp128` references, 26
`fmul` / 26 `fdiv` / 18 `fadd` / 18 `fsub`, **30 `fcmp` on quads** (16 `ogt`,
5 `ugt`, 4 `oeq`, 3 `olt`, 1 `ole`, 1 `ule`), and calls `expq` `fabsq` `powq`
`sqrtq` — all four in `libquadmath-math.bc`. Native sanity check: quad Cuhre
on ∫∫(x²+y) over [0,1]² returns `0.833333333333333471…`. **Verified.**

**Generatable surface — 14 functions, and the generator emits nothing useful
as it stands.** The AST probe over `cubaq.h` gives 14 free functions with
parameter histogram `int` 63, `cubareal *` 26, `cubareal` 24, `int *` 22,
`void *` 20, `long long` 19, **`integrand_t` 8**, `char *` 8. Run with a
stock library entry using only existing makers, the real generator prints

    cuba: 2 drivers, 12 skipped
         8  integrand_t
         2  void *
         2  void (*)()

and the two are `cubacores`/`cubaaccel`, plumbing setters that store two ints.
**Verified.** `gen-drivers.py` **cannot make a function pointer**:
`construct.py:build()` finds `integrand_t` is not in `types`, not an enum, and
its spelling does not end in `*`, so it falls through to
`yielders["integrand_t"]` — empty — and returns `(None, "integrand_t")`.
`void *` is refused outright at `construct.py:163`.

What a `preamble` (the BLIS mechanism) has to supply: a
`static int fpb_integrand(...)` matching `integrand_t` plus a file-static
symbolic `cubareal` it multiplies in; `pin` entries for
`integrand`/`userdata`/`spin`/`statefile`/`peakfinder` and for
`ndim`/`ncomp`/`nvec`/`flags`/`key*`/`maxeval`; and two new makers,
`sym_scalar("cubareal", n)` and `sym_array("cubareal", n)` — the same two-line
`__float128` addition FFTW needs. With those the generator prints
**`cuba: 12 drivers, 2 skipped`** (`cubainit`/`cubaexit` on `void (*)()`).
**Verified.** The realistic yield is **4** — `Vegas`, `Suave`, `Divonne`,
`Cuhre` — because the `ll*` variants are the same four algorithms with a
`long long` counter and would duplicate every measurement, which is the reason
CXSparse is cut to `cs_di_`.

**KLEE at binary128**, Bitwuzla, 60s/5s, on the generator's own output.
**Verified** (`SolverQueries` is the column `aggregate.py` reports):

| driver | instrs | SolverQueries | solver time | wall | completed paths | timeouts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `Cuhre` (generator output, verbatim) | 223,451 | 17 | 25.29s | 25.40s | 4 | 1 |
| `Cuhre`, symbolic quad ε (hand) | 331,187 | 19 | 25.14s | 25.31s | 6 | 2 |
| `Cuhre`, symbolic quad *in the integrand* | 48,274 | 2 | 8.72s | 8.76s | **0** | 1 |
| `Cuhre`, `key=7` (smallest rule) | 10,122 | 2 | 6.00s | 6.01s | **0** | 1 |
| `Vegas`, symbolic quad ε | 1,663,983 | 6 | 0.21s | 0.72s | 4 | 0 |

**The symbolic quad reaches two floating-point branches, and both are the
adaptive machinery** — this is the direct answer to whether the values stay
concrete:

* `src/cuhre/Integrate.c:141,148` —
  `creal ratio = tot->err/MaxErr(tot->avg);` then
  `if( maxratio <= 1 && t->neval >= t->mineval ) break;` — a quad `fp.div`
  with a symbolic denominator feeding the subdivision test. This is where the
  generated driver spends its 25s.
* `src/cuhre/Rule.c:734` — the null-rule error-estimate selection,
  `(errcoeff[0]*sum[1] <= sum[2] && errcoeff[0]*sum[2] <= sum[3]) ? ... : ...`
  — reached only when the *integrand* carries the symbolic quad, and it
  **times out** there, with zero completed paths. `sum[rul]` is a 65–195-term
  linear combination of quad samples, so the query is dozens of 227-bit
  multiplies at once. Shrinking to the smallest cubature rule did not help.

So the usable shape is **symbolic tolerances with a concrete-coefficient
integrand**, which is exactly what the generator produces unaided.

**Against the four criteria:** (a) partial — 4 entry points, and zero drivers
without a preamble, a pin table and a `cubareal` maker; (b) yes, with
fork/shm off and the one-line `Fork.c` fix; (c) marginal — the generated
Cuhre driver completes 4 paths in 25.4s with one timeout, Vegas 4 in 0.72s, a
symbolic integrand never finishes; (d) **yes, strongly** — 17–19 solver
queries and ~25s of binary128 solver time on one driver, on genuine
`fp.div`-plus-comparison in Cuba's own adaptive test.

Four drivers is a small benchmark. But it is the only one of the six that
would put hard binary128 queries in front of STP and Bitwuzla, which is the
question the suite exists to ask, and it is 25 seconds of solver time per
driver rather than FFTW's zero.

*(Aside, worth recording: `src/suave/Fluct.c:50` and `:58` call plain `fabs()`
on a `__float128`, which clang flags as `-Wabsolute-value` … "may cause
truncation of value". `fabsx` exists and is not used there. An upstream
precision bug in Suave's quad build, not a blocker for Cuhre/Vegas/Divonne.)*

### 2.2 galculator — **reject**

**Verdict: not worth including**, on three independent grounds, each verified.

**Which kind:** a `G_REAL` typedef block, `src/g_real.h:34-72`, selected by
`--enable-quadmath` (default yes) via `configure.ac:52-62`
(`AC_CHECK_LIB(quadmath,sinhq)` → `HAVE_LIBQUADMATH`).

**Blocker 1 — quad is switched off for clang, deliberately, in the header.**
`src/g_real.h:34`:

```c
#if HAVE_LIBQUADMATH && defined (__GNUC__) && !defined (__clang__) && !defined (__INTEL_COMPILER)
#define USE_LIBQUADMATH 1
#include <quadmath.h>
typedef __float128 G_REAL;
#define G_SIN sinq ... #define G_POW powq #define G_SQRT sqrtq
#else
typedef double G_REAL;
```

with a comment at `:24-32` explaining the intent. fp_bench's toolchain is
clang. **Verified**: compiled with clang 16 and `HAVE_LIBQUADMATH=1`,
`src/math_functions.c` produces **0 `fp128` references** — all `double`, with
calls to `@sin`/`@cos`/`@pow`. Deleting the one clause gives 109 `fp128`
references and `@sinq @cosq @tanq @powq @floorq @scalbnq`. Unpatched, the
benchmark would silently measure binary64 while claiming binary128 — the
suite's own "runs and measures nothing" failure mode, in its most misleading
form.

**Blocker 2 — the numeric core cannot be compiled or driven without GTK.**
`math_functions.c` contains no GTK call (`grep -c gtk_` = 0), but its header
chain requires GTK — **verified**:

    In file included from src/math_functions.c:25:
    In file included from src/general_functions.h:24:
    src/config_file.h:21:10: fatal error: 'gtk/gtk.h' file not found

and worse, the arithmetic *reads the application's state*: `cmp()`
(`math_functions.c:78-90`) switches on `current_status.number` and reads
`prefs.hex_bits`/`oct_bits`/`bin_bits`; every trig wrapper is
`return G_SIN(x2rad(x))`, and `x2rad` (`general_functions.c:734`) switches on
`current_status.angle` in a file with 51 GTK calls. Driving it required
hand-writing `current_status`, `prefs`, `x2rad`, `rad2x` and `error_message`
— re-implementing the application, not a four-line preamble in the BLIS
sense. The build does not configure here either: `autoreconf -fi` →
`configure.ac:15: error: possibly undefined macro: AC_PROG_INTLTOOL`, and
`./configure` → `cannot find required auxiliary files: config.guess
config.sub compile missing install-sh`.

**Blocker 3 — the generator cannot select it.** galculator has no name
prefix, so the only usable setting is OpenLibm's `prefixes: ("",)`. Run that
way the real generator prints **`galculator: 8304 drivers, 6681 skipped`** —
48 of galculator's own and 8,256 of GTK/GLib/libc/quadmath
(`GTK_FILE_CHOOSER_NATIVE`, `G_IS_LIST_MODEL`, `__acos`, `_Exit`, …).
**Verified.** OpenLibm gets away with an empty prefix because
`openlibm_math.h` includes nothing; galculator's headers include
`<gtk/gtk.h>`. A hand-written `select` regex enumerating its own functions
would forfeit the "complete by construction" property the README says is the
point of generating from headers.

**And after all three are worked around, it is libquadmath again.** With
`g_real.h` patched and the globals stubbed, at binary128, Bitwuzla, 60s/5s
(**verified**):

| driver | instrs | SolverQueries | solver time | completed paths | timeouts |
| --- | ---: | ---: | ---: | ---: | ---: |
| `reciprocal` (`return 1/x`) | 23 | 1 | 0.01s | 1 | 0 |
| `factorial` | 262 | 20 | 0.14s | 11 | 0 |
| `cmp` | 2,489 | 12 | 0.11s | 1 | 0 |
| `sin_wrapper` | 2,808 | 111 | 11.93s | 11 | 2 — `libm/s_scalbn.c:50` |
| `pow10y` | 856 | 30 | 32.81s | 3 | 4 — `powq.c:437`, `floorq.c:45` |

This is the README's "Why it is not an eighth library here" table one wrapper
layer up: the half that finishes is 23–2,800 instructions of near-branch-free
kernel, and every query of substance is inside `powq`/`floorq`/`scalbn` —
libquadmath, already measured and already excluded. galculator contributes no
arithmetic libquadmath does not already have.

### 2.3 PETSc — **reject, but the README's stated reason needs correcting**

**Verdict: reject.** The barrier is the shape of the API, not MPI.

fp_bench's current line is *"PETSc, hypre, SuperLU. Python-driven
configuration and MPI for the first two."* Clause by clause, **verified**
against `petsc` at `2f0f64be` (2026-08-28):

* **"Python-driven configuration" — still true.** `configure` is a 407-byte
  `#!/usr/bin/env python3` script that imports `config/BuildSystem/`.
  Requires Python ≥ 3.6.
* **"MPI" — no longer true.** `--with-mpi=0` gives a genuine serial build via
  MPIUNI (`configure.log:6693 Setup MPIUNI, our uniprocessor version of MPI`;
  `include/petsc/mpiuni/mpi.h:188` is `typedef int MPI_Comm;`). No MPI
  library, no `mpicc`, no `mpiexec`.
* **`--with-precision=__float128` still exists** (declared
  `config/PETSc/options/scalarTypes.py:24`, handled `:150-155`) and it
  **works with clang 16**. It needs: real `-lquadmath` (`scalarTypes.py:91`
  link-probes `logq`), `quadmath.h` on the include path (clang's default
  search does **not** have it — `fatal error: 'quadmath.h' file not found`),
  and f2c BLAS/LAPACK, which is not optional
  (`config/BuildSystem/config/packages/BlasLapack.py:485-486`). No Fortran
  compiler needed.

Three configure attempts were run. The first failed with
`__float128 precision requires f2c libraries; suggest
--download-f2cblaslapack`; the second downloaded f2cblaslapack and failed
with `./f2c.h:12:11: fatal error: 'quadmath.h' file not found` while building
`qlamch.o`, because PETSc forwards `CFLAGS` into the package's `COPTFLAGS`
but not into `CNOOPT` and `qlamch.c` is a `CNOOPT` file. The third, with a
one-line compiler wrapper putting `-I<quadmath>` in front of every flag,
**configured and built clean**:

```sh
./configure PETSC_ARCH=arch-quad-C \
  --with-cc=<wrapper> --with-cxx=0 --with-fc=0 --with-mpi=0 \
  --with-precision=__float128 --with-debugging=0 --download-f2cblaslapack \
  LDFLAGS="-L$(dirname "$(gcc -print-file-name=libquadmath.so)")"
make all
```

→ `Precision: __float128`, `Scalar type: real`, `MPI: PETSc MPIUNI`,
`libpetsc.so.3.025.4`, 62 MB, ~12 minutes. **Verified.**

**The precision configuration is textbook** — `include/petscsystypes.h:571-579`
`#include <quadmath.h>` + `typedef __float128 PetscReal`, and
`include/petscmath.h:564-` a block of 36 `#define PetscSqrtReal(a) sqrtq(a)`,
`PetscPowReal`→`powq`, `PetscExpReal`→`expq`, … through `PetscLGamma`.
Exactly the `#define REAL` shape the README documents. And the IR is what the
suite wants: one source file, `src/dm/dt/interface/dt.c` at `-O1`, carries
136 `fadd fp128`, 176 `fmul`, 51 `fdiv` and **40 `fcmp fp128`**. Of the 25
quad symbols `libpetsc.so` imports, exactly one — **`strtoflt128`** — is
missing from `libquadmath-math.bc`, and its only two call sites are
`src/sys/objects/options.c:2310` and `src/sys/objects/aoptions.c:458`,
command-line option parsing a driver never reaches. `quadmath_snprintf` is
not needed at all.

**So why reject.** Because `construct.py` models a constructor as *one call*
and PETSc's objects need *four*. Running fp_bench's own `Constructor` against
PETSc's real clang AST (4,327 `FunctionDecl`s from `petscvec.h` +
`petscmat.h` + `petscksp.h` + `petscdt.h`) — **verified** — the search
succeeds and picks the shell constructor every time: `Vec` ← `VecCreate`
(33 yielders available), `Mat` ← `MatCreate` (80), `KSP` ← `KSPCreate`,
`IS` ← `ISCreate`. It emits

```c
Vec a;
MPI_Comm a_0 = PETSC_COMM_SELF;
VecCreate(a_0, &a);
```

and a `VecCreate`d vector is a shell — a usable one needs
`VecCreate`→`VecSetSizes`→`VecSetType`/`VecSetUp`. **Verified natively
against the built library**: `VecCreate` returns 0, then `VecNorm` returns
**56** = `PETSC_ERR_SUP`, "no support for requested operation". The driver
exits 0, prints nothing (no `PetscInitialize`, so no error handler), and
records coverage of PETSc's argument validators. That is the
`N_VNewEmpty`/BLIS-1x1 failure in a form the existing `hollow` guard
(`empty|null|shell|clone|wrap`) does not catch, because the function is
called `VecCreate`.

The counts, over 2,978 prefix-matched PETSc functions with ≥1 argument
(**verified**):

| | |
| --- | --- |
| every parameter buildable | 1,847 |
| …needing a constructed opaque object (all hollow) | **1,579** |
| …scalars / arrays / enums only | 268 |
| all-buildable **and** touching a float type | 363 |
| …**scalars/arrays only — the honest core** | **91** |

Of the 91, roughly 35 are genuinely numerical and callable with no
initialisation at all — the `PetscDTGaussQuadrature` / `PetscDTJacobiEval` /
`PetscDTAltV*` / `PetscPDF*`/`PetscCDF*` families. **Verified**:
`PetscDTGaussQuadrature(5, -1, 1, x, w)` with no `PetscInitialize` returns the
correct 5-point Gauss-Legendre node `-0.906180` and weight `0.236927` in
`__float128`. Thirty-five functions is comparable to FFTW's 27 — but bought
with a 62 MB library, an external BLAS download, a compiler wrapper, and a
type map that must teach the generator a construction protocol it does not
have. And PETSc declares its public prototypes **without parameter names**
(only 16.2% of 10,561 parameters carry one), so `gen-drivers.py`'s `pin`
mechanism — which is keyed on parameter name and is what stopped FFTW's
planner from being told `FFTW_EXHAUSTIVE` — is essentially inoperative.

**Suggested README replacement:** *"PETSc. Python-driven configuration, and
an API of opaque handles built through multi-call `Create`/`SetSizes`/
`SetType` sequences the constructor search cannot express — `VecCreate` alone
yields a shell whose every operation returns `PETSC_ERR_SUP`, silently.
`--with-mpi=0` does give a serial build and `--with-precision=__float128`
does build with clang 16; the barrier is the shape of the API, not MPI."*

**Two bugs in `construct.py` found on the way, worth fixing regardless:**

1. `DENY = re.compile(r"\b(FILE|va_list|jmp_buf|pthread_|MPI_|DIR)\b")` — the
   trailing `\b` makes the `MPI_` and `pthread_` entries **dead**, because
   `MPI_Comm` continues with a word character. `DENY.search("MPI_Comm")` is
   `False`; so is `pthread_mutex_t`. **Verified.**
   `\b(FILE|va_list|jmp_buf|DIR)\b|\b(pthread_|MPI_)` works.
2. `_index`'s accessor rule requires a **one**-parameter function returning
   `T *` named `getarray|data|buffer|ptr`. The commoner spelling —
   `XxxGetArray(T, elem **)`, which is what PETSc uses — is missed, so
   `ctor.accessors["Vec"] is None` and even a correctly built object would
   never have its storage made symbolic. That is the general form of the BLIS
   "buffers left as `malloc` returned them" bug.

### 2.4 Surface Evolver — **reject**

**Verdict: reject**, and it is Qhull's problem stacked on OSQP's stacked on
CMSIS-DSP's.

**Source.** The canonical upstream tarball **does** download from this
machine: `curl -sSLo evolver.tar.gz
https://kenbrakke.com/evolver/downloads/evolver-2.70.tar.gz` → 3,157,309
bytes. The GitHub mirror was not needed. **Verified.** Version 2.70a; `src/`
holds 100 `.c` and 15 `.h`, 190,576 lines; no `configure`, no `include/`, no
install target, one artefact — the `evolver` binary.

**Which kind, and how selected.** The `#define REAL __float128` shape, and in
fact the *literal source* of the block the README quotes at lines 230-238.
`src/include.h:59-66`:

```c
#ifdef FLOAT128
// For gcc __float128 with libquadmath
#include <quadmath.h>
#define REAL  __float128
```

and `include.h:638-661`, deliberately placed after `math.h`:

```c
#ifdef FLOAT128
/* have to do these after math.h */
#define sin sinq
#define cos cosq
...
#define fmod fmodq
#define modf modfq
#define atof(a) strtoflt128(a,NULL)
```

Selected by uncommenting `CFLAGS= ... -DFLOAT128` at `src/Makefile:78` —
there is no configure, so a `build-lib.sh` arm would just pass `-DFLOAT128`.
(Undocumented outside that Makefile comment; `README.unix.txt:77-79` mentions
only `-DLONGDOUBLE`.)

**It builds, cleanly, at both precisions.** `-DFLOAT128` compiles **93 of 93**
`OBJ` files with clang 16 and zero extra diagnostics, needing only
`-w -Wno-implicit-function-declaration` (the latter is *not* implied by `-w`
and is needed for `evalmore.c:2506:13: error: call to undeclared function
'wait'`) and a one-line `set_graphics_title` stub for an upstream link bug.
The resulting binary runs: `Compiled for float128, 33 digits precision.` /
`3. energy: 5.13907252918613603776998098072999`. **Verified.** This is the
one criterion Evolver meets comfortably and it is the least interesting one.

**`strtoflt128` kills it.** `quadmath_snprintf` is used **nowhere** — Evolver
prints with plain `sprintf` and a `%Qf`/`%Qg` conversion at 100 sites, which
this machine's glibc happens to honour. But `include.h:661` routes `atof` to
`strtoflt128`, and there are **20 `atof` call sites, seven of them in
`lexyy.c`** — the flex scanner. `lexyy.c:1959` is the rule that converts
*every* numeric literal in the language:
`{ yylval.r = atof(yytext); return(tok = REAL_TOK); }`. Plus a direct call at
`graphgen.c:291`. Symbol-by-symbol against `libquadmath-math.bc`: every quad
function Evolver needs is present; exactly two are missing, and one of them is
this. After `llvm-link`, `strtoflt128` is the only unresolved quad symbol and
KLEE warns about it. **Verified.** So under fp_bench's libquadmath the entire
datafile reader and command language are dead — and a loaded surface is the
precondition for essentially all of Evolver's numerics.

**The generatable surface, measured properly.** The AST probe over
`include.h` sees 2,752 `FunctionDecl`s, **1,561 declared in Evolver's own
headers**; **189** have every parameter in the generator's directly-mappable
set (12%). The blockers are Evolver's packed element handles —
`struct qinfo *` (355), `vertex_id` (93), `edge_id` (83), `struct linsys *`
(78), `struct method_instance *` (73), `facet_id` (55). But the decisive
number comes from a call-graph closure over the built module (1,840 defined
functions), computing for each of the 189 which module globals its transitive
callees touch. **Verified:**

| | count |
| --- | ---: |
| all parameters scalar/array | 189 |
| …defined in the module | 173 |
| reference the global `web` transitively | **133** |
| reach `kb_error` transitively | **121** |
| **touch no global and never reach `kb_error`** | **24** |

`web` matters because `model.h:31` is `#define SDIM web.sdim` and `model.h:19`
makes `MAXCOORD` be `SDIM`: before a `.fe` file is read `web.sdim == 0`, so
`vnormal` loops zero times and computes nothing. `machine_eps` is set only at
`tmain.c:126`, in `main`. `kb_error` matters because `userio.c:1113` ends it
with `longjmp(jumpbuf[...], 1)` and KLEE's
`runtime/POSIX/illegal.c:39-40` makes `longjmp` a hard error — and every
domain check in Evolver's numerics is `kb_error(..., RECOVERABLE)`, so with a
symbolic argument that is the common path. This is Qhull's rejection verbatim.

**And of the 24 survivors, 22 contain no `fcmp` at all.** They are
`cross_prod`, `dot`, `dotf`, `vector_add`, `tetra_vol`, `triple_prod`,
`interpoly`, `intpoly6`, `grule` (Gauss-Legendre: 21 `fdiv`, 0 `fcmp`),
`int32hash`, `binom_coeff` and friends — branch-free block arithmetic, which
is what CMSIS-DSP was rejected for. The two exceptions are `binary_tree_add`
(1 comparison) and `lens_wulff` (2 comparisons, 15 lines).

**KLEE at binary128 confirms it.** **Verified**, Bitwuzla, 60s/5s:

| driver | prec | instrs | SolverQueries | solver time | completed paths | outcome |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `tetra_vol` | q128 | 246 | **0** | 0.000s | 1 | clean; branch-free |
| `lens_wulff` | q128 | 162 | 3 | 1.638s | 3 | clean |
| `ellipticK` | q128 | **58** | 3 | 5.812s | **0** | query timeout in the AGM loop |
| `ellipticK` | f64 | 101 | 4 | 9.288s | 1 | 1 query timeout |
| `tridiag_QL` | q128 | 1,623 | 22 | **58.12s** | **0** | 9 timeouts, budget exhausted |
| `tridiag_QL` | f64 | 1,850 | 24 | 62.32s | **0** | 11 timeouts |

The functions that *do* iterate on a floating-point condition —
`ellipticK`/`ellipticE`/`incompleteEllipticF` (`userfunc.c:443,462,559`) and
`tridiag_QL` (`hessian2.c:1213`, the Numerical-Recipes QL eigenvalue
iteration) — are all in the 121 that need `machine_eps` and call `kb_error`,
and all of them either time out or exhaust the budget with zero completed
paths — **at binary64 too**, which is the control that settles it. And a
whole-application run under `--posix-runtime --libc=uclibc` on a `.fe` file
dies at 476,801 instructions in `path_open` before ever reaching the lexer.

fp_bench would gain exactly one function, `lens_wulff`, that both runs and
asks the solver anything.

**Suggested README one-liner:** *"Surface Evolver. An application, not a
library: 24 of its 1,561 declared functions can be called without a loaded
surface, 22 of those are branch-free, and every numerical entry point routes
its domain checks through `kb_error` → `longjmp`. Its `FLOAT128`
configuration reads numeric literals through `strtoflt128`, which has no
clang build."*

The one thing genuinely worth taking from it: `include.h:56-66` and `638-661`
are the real-world provenance of the macro block `quadmath/quad_example.c`
already models.

### 2.5 Perl 5 — **reject, decisively**

**Verdict: reject**, on two independent sufficient grounds.

**Which kind, and how selected.** A `#define NV __float128` configuration.
The flag is **`-Dusequadmath`** (not `-Duse3quadmath`) — `INSTALL:450`,
`Porting/Glossary:5763-5766` — and on `main` it additionally needs
`-Dusedevel`. `Configure:16805-16849` sets `nvtype="__float128"`, `nvsize=16`,
`libs="$libs -lquadmath"`, and compiles and *runs* a `logq`/`fabsq` probe;
`Configure:21375` sets `nvmantbits=112`; `config_h.SH:4472` emits
`#define USE_QUADMATH`. `perl.h:2687` is `typedef NVTYPE NV;` and
`perl.h:2804-2846` is the macro block: `#define Perl_acos acosq`,
`Perl_exp expq`, `Perl_pow powq`, `Perl_sqrt sqrtq`, `Perl_isnan isnanq`, …

**It builds.** `Configure -des -Dusedevel -Dusequadmath -Dcc=<clang wrapper>`
exits 0 with `nvtype='__float128'`, `nvsize='16'`, `nvmantbits='112'`,
`archname='x86_64-linux-quadmath'`; `make miniperl` exits 0; and the result
prints `NV size=16  0.1+0.2=0.3  sqrt2=1.414213562373095048801688724209698`
— 34 significant digits of genuine binary128 under clang 16. **Verified.**

**Ground 1 — there is no generatable C API.** `proto.h` has 1,450
`PERL_CALLCONV` declarations, 1,299 (89.6%) taking `pTHX`/`pTHX_`. But
`MULTIPLICITY` is off in the default build and `perl.h:798-799` then makes
`pTHX` = `void` and `pTHX_` = nothing, so the type-level barrier vanishes at
the AST and the generator sails straight past it. Running fp_bench's own
`Constructor` against the real clang AST of `EXTERN.h`+`perl.h`+`XSUB.h`
(126 MB of JSON; parses clean) — **verified**:

| | |
| --- | --- |
| `FunctionDecl`s clang sees | 4,707 |
| named `Perl_`/`perl_`, ≥1 arg, distinct | 1,276 |
| `gen-drivers.py` would emit a driver for | **1,123** |
| …with at least one **fabricated interpreter object** | **825** |
| …purely scalars / arrays | 298 |
| …of those, taking an `NV` | **11** |

The 825 emit things like `SV a; Perl_newRV(&a);` — an uninitialised
interpreter struct on the stack, passed to a function expecting a live one —
and `PerlIO *a = Perl_PerlIO_stdin(); klee_make_symbolic(Perl_PerlIO_get_ptr(a), ...)`.
**Verified for a representative case**: the generator's verbatim output
`SV a; Perl_newRV(&a); NV r = Perl_sv_2nv_flags(&a, 0);` **exits 0 and prints
`0.000000`**. It does not crash. It reads a garbage refcount and returns zero.
Of the 11 that take an `NV`, `my_atof2`/`my_atof3` route into `strtoflt128`,
`grok_bin`/`grok_hex`/`grok_oct` take `NV *` as an overflow-only
out-parameter, and `newSVnv` is a constructor — leaving **five**:
`cast_i32`, `cast_iv`, `cast_ulong`, `cast_uv`, `isinfnan`, all NV→integer
range checks with three `fcmp`s apiece. The arithmetic that matters
(`pp_add`, `pp_pow`, `sv_2nv`) is reached only through `SV *`, and an `SV` is
a tagged union with a refcount, a magic chain and an arena.

**Ground 2 — the quad build cannot be linked for KLEE.** It needs *both*
missing symbols, on its most central paths. **Verified** by `nm -u` on the
freshly built core objects: `numeric.o` → `strtoflt128`; `sv.o`, `util.o`,
`pp_ctl.o` → `quadmath_snprintf`. The sites:

* `numeric.c:41` — `result = strtoflt128(s, e);` inside
  `S_strtod` under `#ifdef USE_QUADMATH`. `S_strtod` *is* `Perl_strtod`,
  which `my_atof`/`my_atof2`/`my_atof3`/`grok_number` all reach. This is
  *the* string→number path of the interpreter.
* `sv.c:44-45` — `#define SNPRINTF_G(nv, buffer, size, ndig)
  quadmath_snprintf(buffer, size, "%.*Qg", ...)`, used at `sv.c:14954`. This
  is how every `NV` becomes a string.
* `util.c:5326`/`:5355` in `Perl_my_snprintf`; `pp_ctl.c:930` in the
  `format`/`write` opcode.

These live in four of the ~45 core objects every Perl build links. A KLEE
module from a quad Perl carries two unresolvable externals on the
interpreter's central numeric conversions — and even the five viable
functions sit in `numeric.o`, the same object as the `strtoflt128` call.

**Suggested README entry:** *"Perl 5. `Configure -Dusequadmath` does make
`NV` a `__float128` and it builds with clang 16 — but its API is an
interpreter's: 825 of the 1,123 functions the generator would accept
fabricate an `SV`/`HV`/`OP` from an uninitialised stack slot and return zero
rather than crash, and only five entry points take an `NV` with no
interpreter object. The quad build also needs `strtoflt128` and
`quadmath_snprintf` on its central number↔string paths, and the suite has
neither."*

### 2.6 Where the two missing symbols actually bite

Because the answer differs sharply per project, and it is the single
sharpest discriminator found:

| project | `strtoflt128` | `quadmath_snprintf` | effect |
| --- | --- | --- | --- |
| **FFTW** | not used | not used | none — the whole `fftwq_*` API is reachable |
| **Cuba** | not used | not used | none |
| **PETSc** | 2 sites, both option parsing (`options.c:2310`, `aoptions.c:458`) | not used | negligible — no driver reaches them |
| **galculator** | not used | not used | none (its problems are elsewhere) |
| **Surface Evolver** | `include.h:661` → `atof` → **7 sites in the lexer** | not used (uses `%Qg` through glibc) | **fatal** — no numeric literal can be read, so no surface can be loaded |
| **Perl 5** | `numeric.c:41`, the interpreter's `strtod` | `sv.c:45`, `util.c:5326`, `pp_ctl.c:930` | **fatal** — number↔string is what the interpreter does |

---

## 3. What `fp_bench` itself needs

All four changes were applied to a copy of `common/` at
`/mnt/baranem/fp_bench-work/fftwq-probe/common/` and exercised; nothing under
`/home/avj/clones/fp_bench/` was touched. The generator diff below is the one
that was actually run.

### 3.1 `common/gen-drivers.py` — a type map and a library entry

**Verified**: with this applied, `gen-drivers.py --library fftwq --include
<fftwq-bc>/api --out <dir> --cflags "-U__GNUC_MINOR__ -D__GNUC_MINOR__=6"`
prints `fftwq: 27 drivers, 22 skipped`.

```diff
--- a/common/gen-drivers.py
+++ b/common/gen-drivers.py
@@ -219,6 +219,24 @@
 }
 FFTW_TYPES.update(ARRAY_TYPES)
 
+# --------------------------------------------------------------------------
+# FFTW at binary128. Same library, the fftwq_* API, R = __float128.
+# --------------------------------------------------------------------------
+FFTWQ_TYPES = {
+    "__float128":   lambda n: sym_scalar("__float128", n),
+    "double":       lambda n: sym_scalar("double", n),
+    "int":          lambda n: sym_scalar("int", n, bound="8"),
+    "unsigned int": lambda n: sym_scalar("unsigned int", n, bound="8"),
+    "unsigned":     lambda n: (["  unsigned %s = FFTW_ESTIMATE;" % n], n),
+    "__float128 *": lambda n: sym_array("__float128", n),
+    # fftwq_complex is __float128[2] (no <complex.h> in a driver), so an
+    # array of them is an array of quads twice as long.
+    "fftwq_complex *": lambda n: (["  fftwq_complex %s[%d];" % (n, ARRAY_N),
+                                   '  klee_make_symbolic(%s, sizeof(%s), "%s");'
+                                   % (n, n, n)], n),
+}
+FFTWQ_TYPES.update(ARRAY_TYPES)
+
 # The order of a BLIS matrix. Small, for the reason ARRAY_N is small.
 BLIS_N = 4
 
@@ -510,6 +528,17 @@
         "exclude": r"(wisdom|_print|sprint|export|import|cleanup|forget|"
                    r"alignment_of|set_timelimit|thread|malloc|free)",
         "includes": ["<fftw3.h>"],
+    },
+    "fftwq": {
+        "header": "fftw3.h",
+        "internal": {},
+        "prefixes": ("fftwq_",),
+        "types": FFTWQ_TYPES,
+        "pin": {"flags": "FFTW_ESTIMATE", "sign": "FFTW_FORWARD"},
+        "after": {"fftwq_plan": "fftwq_execute(%s);"},
+        "exclude": r"(wisdom|_print|sprint|export|import|cleanup|forget|"
+                   r"alignment_of|set_timelimit|thread|malloc|free)",
+        "includes": ["<fftw3.h>"],
     },
     "blis": {
         "header": "blis.h",
```

Notes on the entries, each of which was needed:

* `"__float128"` and `"__float128 *"` are the spellings clang's JSON AST
  actually uses for the `fftwq_*` parameters (**verified**: 63 occurrences of
  `__float128 *` and 32 of `fftwq_complex *` across the 72 declarations).
  No `fftwq_` function takes a bare `__float128` by value, so that entry is
  never exercised by FFTW itself — it is there because it is the one piece any
  other `__float128` candidate would need, and because `sym_scalar` covers it
  for free. `sym_scalar`/`sym_array` need no change — `klee_make_symbolic(&a, sizeof a)`
  on a 16-byte object is already right, and KLEE reserves
  `getTypeAllocSize()`.
* `"int"` keeps the `bound="8"` the double arm uses — the planner's `n` still
  reaches an allocator.
* `"unsigned"` still has to be pinned to `FFTW_ESTIMATE`, and `pin` still has
  to catch `flags`/`sign` by parameter name, for the reason the README gives:
  a bounded integer lands on `FFTW_EXHAUSTIVE`.
* The `after` hook keys on `"fftwq_plan"`, not `"fftw_plan"` — without it the
  27 planning drivers select an algorithm and compute nothing.
* `"double"` stays in the map because `fftwq_flops` takes `double *` and
  `fftwq_set_timelimit` takes `double`: FFTW's cost model is `double` at every
  precision.

### 3.2 `common/build-lib.sh` — a case arm and a generator flag

The tarball, SHA and `SRC` are the same as `fftw`'s; only the configure line,
the archive, the extra CPPFLAGS and the LDFLAGS differ.

```diff
@@ -37,6 +37,7 @@
 NATIVE_CC=${NATIVE_CC:-/usr/bin/clang}
 JOBS=${JOBS:-$(nproc)}
+# Extra preprocessor/linker flags a library needs in *both* builds.
+EXTRA_CPPFLAGS=""; EXTRA_LDFLAGS=""; GEN_CFLAGS=""
 
@@ (in the case statement, after the fftw) arm)
+  fftwq) VER=3.3.10; URL=https://www.fftw.org/fftw-3.3.10.tar.gz
+       SHA=56c932549852cddcfafdab3820b0200c7742675be92179e59e6215b340e26467
+       TAR=fftw-3.3.10.tar.gz; SRC=fftw-3.3.10
+       # FFTW's quad support is gated twice on the compiler *claiming* to be
+       # gcc >= 4.6: once in configure (AX_GCC_VERSION, configure.ac:562) and
+       # once in the public header (api/fftw3.h:471), which is where the
+       # fftwq_* declarations live. Clang says 4.2.1, so without this the
+       # library does not compile and the generator sees no quad API at all.
+       # CPPFLAGS rather than CFLAGS: autoconf's AC_EGREP_CPP runs
+       # "$CPP $CPPFLAGS" and never sees CFLAGS.
+       EXTRA_CPPFLAGS="-U__GNUC_MINOR__ -D__GNUC_MINOR__=6"
+       GEN_CFLAGS="$EXTRA_CPPFLAGS"
+       # There is no libquadmath.so on the default search path -- only the
+       # versioned SONAME -- and configure link-tests -lquadmath. GCC's own
+       # directory has the symlink.
+       EXTRA_LDFLAGS="-L$(dirname "$(gcc -print-file-name=libquadmath.so)")"
+       CONFIGURE_EXTRA=(--disable-fortran --enable-quad-precision)
+       ARCHIVES=(.libs/libfftw3q.a)
+       INCSUB=api ;;
```

and in the two build blocks, `CPPFLAGS="$EXTRA_CPPFLAGS"` and
`LDFLAGS="...$EXTRA_LDFLAGS"` added to the two `./configure` invocations, and
the generator call given the flags:

```diff
-"$HERE/gen-drivers.py" --library "$LIB" --include "$INC" \
-                       --out "$ROOT/$LIB/drivers"
+"$HERE/gen-drivers.py" --library "$LIB" --include "$INC" \
+                       --out "$ROOT/$LIB/drivers" --cflags "$GEN_CFLAGS"
```

`--cflags` already exists in `gen-drivers.py`'s argparse and is already split
on whitespace and appended to the clang AST-probe command line, so nothing
there changes.

`build-lib.sh` should also run `common/build-libquadmath.sh` for this arm (or
require it to have been run), because `build-drivers.sh` needs its output.
It is idempotent and takes about 30 seconds.

### 3.3 `common/build-drivers.sh` — an include arm, a link line, and the quad bitcode

Three changes.

```diff
@@ (case $LIB in)
+  fftwq)    INCBC=$WORK/fftwq-bc/api;             INCCOV=$WORK/fftwq-cov/api
+            LIBS="$WORK/fftwq-cov/.libs/libfftw3q.a"
+            # libquadmath is not in the module: build-libquadmath.sh compiles
+            # its math/ directory to bitcode and it is llvm-linked in below.
+            # libfftw3q's only libquadmath references are sinq and cosq.
+            EXTRA_BC="$WORK/libquadmath/libquadmath-math.bc"
+            EXTRA_CFLAGS="-U__GNUC_MINOR__ -D__GNUC_MINOR__=6"
+            EXTRA_LIBS="-L$(dirname "$(gcc -print-file-name=libquadmath.so)") -lquadmath" ;;
```

with `EXTRA_BC`, `EXTRA_CFLAGS`, `EXTRA_LIBS` defaulting to empty for every
other library, and `build_one` picking them up:

```diff
       "$LLVM_PREFIX/bin/clang" -emit-llvm -O0 -g -c \
-        -I"$INCBC" -I"$KLEE_SRC/include" -o "$bc.d" "$c" 2>>"$err" &&
-      "$LLVM_PREFIX/bin/llvm-link" "$bc.d" "$WORK/$LIB.bc" -o "$bc.l" 2>>"$err" &&
+        $EXTRA_CFLAGS -I"$INCBC" -I"$KLEE_SRC/include" -o "$bc.d" "$c" 2>>"$err" &&
+      "$LLVM_PREFIX/bin/llvm-link" "$bc.d" "$WORK/$LIB.bc" $EXTRA_BC \
+        -o "$bc.l" 2>>"$err" &&
       "$LLVM_PREFIX/bin/opt" -internalize-public-api-list=main \
         -passes='internalize,globaldce' "$bc.l" -o "$bc" 2>>"$err" && break
@@
     "$NATIVE_CC" -O0 -g -fprofile-instr-generate -fcoverage-mapping \
-      -I"$INCCOV" -I"$KLEE_SRC/include" -o "$exe" "$c" "$HERE/replay-flush.c" \
-      $LIBS_STR -L"$KLEE_BUILD/lib" -lkleeRuntest -lm 2>>"$err" ||
+      $EXTRA_CFLAGS -I"$INCCOV" -I"$KLEE_SRC/include" \
+      -o "$exe" "$c" "$HERE/replay-flush.c" \
+      $LIBS_STR -L"$KLEE_BUILD/lib" -lkleeRuntest $EXTRA_LIBS -lm 2>>"$err" ||
```

and the three new variables added to the `export` list next to `LIBS_STR`
(an array cannot be exported; these are plain strings with no spaces in the
paths, same as `LIBS_STR`).

Linking `libquadmath-math.bc` into the driver module rather than passing
`--link-llvm-lib` to KLEE is deliberate and matters: `opt -internalize
-globaldce` then prunes the 95 unused libquadmath translation units away
before KLEE ever loads the module, which is the same reason the library
bitcode is linked and pruned per driver today. **Verified**: after the prune,
`llvm-nm --undefined-only` on a driver module shows no libquadmath symbol at
all, and the module still resolves `sinq`/`cosq`.

### 3.4 `common/sweep-all.sh` and `common/summarise-all.py`

```diff
 LIBS=(  "gsl      10"
         "openlibm  3"
         "blis      4"
         "sundials  3"
         "gmp       2"
         "fftw      1"
+        "fftwq     1"
         "cxsparse  1" )
```

Stride 1, same as `fftw`, which takes the strided corpus from 255 to 282
drivers (286 if Cuba's four go in as well — see §3.7).
`summarise-all.py:28` also carries a hardcoded list and needs `fftwq` added:

```diff
-DEFAULT = "gsl openlibm blis sundials gmp fftw cxsparse".split()
+DEFAULT = "gsl openlibm blis sundials gmp fftw fftwq cxsparse".split()
```

`run-one.sh`, `matrix-sweep.sh`, `aggregate.py` and `coverage.py` need
**nothing**: they are all parameterised by `$LIB` and read
`<lib>/drivers/functions.tsv`. **Verified** for `coverage.py` — run by hand
against the quad replay binary and profile it produced the correct row.

`common/build-stp.sh` and the solver configuration are untouched.

### 3.5 And the KLEE

As §0: `KLEE_BUILD` must point at a KLEE containing `ec002431`. Adding
`fftwq` to `sweep-all.sh` against `3.2-buildtest` as it stands today would
produce 27 rows of `Unsupported FAdd operation`, one test each, and a
plausible-looking coverage number — the README's own failure mode. A cheap
guard would be for `build-drivers.sh`'s `fftwq` arm to run the five-line
`__float128` smoke test against `$KLEE` and refuse if it does not complete,
in the same spirit as `build-libquadmath.sh` refusing to write a partial
library.

### 3.6 README wording

If `fftwq` goes in, two paragraphs need adjusting rather than adding.
"Why it is not an eighth library here" is about *libquadmath* and stays true
as written; but the corpus table gains a row, and the "Quad precision" section
should say that the way binary128 is reached in the corpus is a library that
*uses* libquadmath concretely, not one that is driven symbolically — and that
FFTW-at-quad contributes coverage rather than solver load, because there is
not one `fcmp fp128` in it.

### 3.7 If Cuba goes in too

A `cuba` arm is a bigger change than `fftwq`, because it needs the two things
`fftwq` does not: a `preamble` and a source patch.

* **`build-lib.sh`**: a tarball arm like GSL's —
  `URL=http://www.feynarts.de/cuba/Cuba-4.2.2.tar.gz`,
  `CONFIGURE_EXTRA=(--with-real=16)`, plus
  `ac_cv_func_fork=no ac_cv_func_shmget=no` in the environment (KLEE cannot
  model `shmget`/`shmat`, and without this every driver dies at
  `src/cuhre/Rule.c:618`), `ARCHIVES=(libcubaq.a)`, and the header the
  generator reads is the **generated** `cubaq.h`, not `cuba.h`. `make lib`
  rather than `make`. A one-line patch to `src/common/Fork.c` is required
  before it compiles serially — move `extern coreinit cubafun_;` out of the
  `#ifdef HAVE_FORK` at `:19`.
* **`gen-drivers.py`**: `"cubareal"` → `sym_scalar`, `"cubareal *"` →
  `sym_array` (the same two-line `__float128` addition), a `CUBA_PREAMBLE`
  defining a `static int fpb_integrand(...)` and the file-static symbolic
  `cubareal` it multiplies in, and a `pin` table for
  `integrand`/`userdata`/`spin`/`statefile`/`peakfinder` and for
  `ndim`/`ncomp`/`nvec`/`flags`/`key*`/`maxeval`. That yields 12 drivers, of
  which the four to keep are `Vegas`, `Suave`, `Divonne`, `Cuhre` — a
  `select` of `^(Vegas|Suave|Divonne|Cuhre)$`, on CXSparse's reasoning that
  the `ll*` variants are the same algorithms with a wider counter.
* **`build-drivers.sh`**: the same `EXTRA_BC` link of
  `libquadmath-math.bc`, the same `-lquadmath` on the replay line, and
  `-I<quadmath>` on both compiles (Cuba's headers include `<quadmath.h>`).
  No `__GNUC_MINOR__` games — Cuba has no version gate.
* **`sweep-all.sh`**: `"cuba 1"`, four drivers, and they are the ones that
  will dominate the quad solver time.
