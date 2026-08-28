# binary16 candidates for fp_bench

The question: **how do STP and Bitwuzla perform differently in different
floating-point modes** — binary16 against binary32, binary64 and binary128 —
and which open-source C library gives the sharpest signal for that comparison.
Assessed: the six primary and four qualified projects in
`native-fp16-float16-c-projects.md`, against the inclusion criteria in
`/home/avj/clones/fp_bench/README.md` and the binary16 constraints in
`/mnt/baranem/klee-float/3.2/FP_PORT_2026.md`.

Everything below that says "verified" was run. Everything that says "inferred"
was read. Work was done in `/mnt/baranem/fp_bench-work/fp16-probe/`; nothing
under `/home/avj/clones/fp_bench/` was modified, and the diffs are proposed
rather than applied.

---

## Verdict

**Add CMSIS-DSP `f16` as the eighth library, and add its `_f32` twins as a
matched control.** The measurement below is the reason: on the query class that
carries the whole STP-versus-Bitwuzla difference, **narrowing binary32 to
binary16 closes the gap** — STP is 1.75x slower than Bitwuzla on
`fp.div`/`fp.sqrt` queries at binary32 and 1.085x at binary16, because the
format is worth **2.06x** to STP there and only **1.27x** to Bitwuzla. That is
a result about the two solvers in two FP modes, it is exactly what this suite
exists to produce, and CMSIS-DSP is the only library of the ten that yields it.

| | corpus | verdict |
| --- | --- | --- |
| 1 | **CMSIS-DSP `f16`, plus the `_f32` twins of the same functions** | 84 f16 drivers, 30 of which have an `_f32` twin that walks the identical path tree. 830 matched query pairs already dumped and replayed; the result is the table below. One-line typedef patch, 13 generator type-map entries, one hand-written maker. |
| 2 | **HDF5 `H5Tconvert`, as ~12 hand-written drivers** | Verified reachable, and it **reproduces the headline independently**: 1.006 at binary16 against 1.089 at binary32, on a corpus with **no other floating-point sort in it at all** — 1,742 `(_ to_fp 5 11)` terms across 150 queries, `fp.gt`/`fp.lt` only. The highest format purity in the survey. Costs ~75M instructions of `H5open()` per driver and needs `--libc=uclibc --posix-runtime`. |
| 3 | **CMSIS-NN `f16`** | Builds on x86-64 with no patch and genuine `half` arithmetic. Demoted on measured yield: at `-O2`, **every one** of `arm_maximum_f16`'s 52 `fcmp half` is consumed by a `select`, and five of six hand-written drivers dumped **zero** queries. Worth adding for coverage of a second binary16 implementation, and worth building at `-O1` if the object is queries. |
| — | **Reject: CORE-MATH, XNNPACK, SIMDe, KleidiAI, NumKong, PULP-TrainLib, OpenBLAS** | On substance, not on integration cost. Five of them emit **no binary16 arithmetic at all** in the IR an x86-64 build produces (XNNPACK, SIMDe, NumKong, OpenBLAS's live kernel, PULP-TrainLib does not compile). KleidiAI does not build for x86-64. CORE-MATH is rejected on substance: **2 of its 43 binary16 functions contain any binary16 arithmetic** — the rest are binary32/binary64 table reconstruction behind a `_Float16` signature — so a CORE-MATH corpus would be a binary64 solver benchmark mislabelled. (Its `#pragma STDC FENV_ACCESS ON` abort is a separate, and fixable, KLEE gap; it is not the reason.) |

**Four KLEE gaps stand between this and a sweep**, all found by running:

1. **`/mnt/baranem/klee-float/3.2-buildtest/bin/klee` does not support
   binary16.** A build from Aug 27 09:12; the binary16 commits (`4f6a9d26`,
   `c5ecc2fc`, `7745325b`) are Aug 28. It fails `fadd half` with
   `Unsupported FAdd operation`. Everything below used
   `/mnt/baranem/quad-2026/klee-build/bin/klee` (Aug 28 10:12), which supports
   it. `build-lib.sh` and `run-one.sh` default `KLEE_BUILD` to the stale one.
2. **`llvm.copysign.f16` and `llvm.floor.f16` are mis-lowered into a
   type-incorrect module.** LLVM 16's
   `IntrinsicLowering::ReplaceFPIntrinsicWithCall`
   (`/mnt/baranem/llvm16/src/llvm/lib/CodeGen/IntrinsicLowering.cpp:202-222`)
   has cases for float, double, x87 and fp128 and `default: llvm_unreachable`
   — no `HalfTyID` — and KLEE's `IntrinsicCleaner` sends `copysign`, `floor`,
   `ceil`, `trunc`, `round`, `roundeven`, `sin`, `cos`, `exp`, `log`, `pow`
   and `sqrt` through it (`lib/Module/IntrinsicCleaner.cpp:376-437`). At `f16`
   the result is a call to the *binary32* `copysignf`/`floorf` with `half`
   arguments. Costs 3 of the 84 drivers.
3. **`llvm.rint.f16`, `llvm.minnum.f16` and `llvm.maxnum.f16` are
   "unimplemented intrinsic"**, and `llvm.experimental.constrained.*` at any
   width **aborts** at `Executor.cpp:1289` rather than erroring.
4. **`IndependentSolver`'s `assertCreatedPointEvaluatesToTrue` fires on this
   corpus** — 2 of 28 CMSIS-DSP drivers and the HDF5 driver. It is the
   unsoundness FP_PORT_2026.md records against `--z3-array-ackermannize`,
   reached with that flag off, backend-dependent (Bitwuzla aborts where STP
   does not) and absent under `--use-independent-solver=false`. Not a binary16
   bug, but a binary16 corpus is where it shows.

---

## The measurement: STP against Bitwuzla, binary16 against binary32

This is `common/replay-queries.sh` on a matched pair of corpora: **the same 23
CMSIS-DSP functions, at f16 and at f32, driven identically**, so the two
corpora differ in the floating-point format and in nothing else.

**How the corpus was built** (all commands in
`/mnt/baranem/fp_bench-work/fp16-probe/`, scripts `dump.sh` and `analyse.py`):

* 30 branchy `_f16` drivers were generated by `gen-drivers.py`, and their
  `_f32` twins produced by retyping the same generated driver — every one of
  the 30 has an `_f32` twin symbol in the same build.
* Each was run under KLEE with `--debug-bitwuzla-dump-queries`, a 30s budget
  and a 5s cap, and the dumps split with `common/split-queries.py`.
* **23 of the 30 produced an identical query count at both formats**, which is
  the matched set: 830 queries per format, 1,660 files. The seven that did not
  are excluded rather than compared: `arm_clip` (1,561 against 1,281 — the only
  budget-bound pair), `arm_atan2` (0 against 10 — the f16 side aborts on the
  `copysign.f16` bug), `arm_mat_inverse` (38 / 21 — the f32 side aborts on the
  `IndependentSolver` assertion), `arm_svm_rbf_predict` (113 / 34),
  `arm_svm_sigmoid_predict` (81 / 41), `arm_householder` (196 / 264) and
  `arm_mat_cholesky` (8 / 7). Where a pair diverges the two runs stopped asking
  the same questions, which is precisely the condition `RESULTS.md` says makes
  a comparison meaningless.
* The two formats were interleaved in one directory so the replay processes an
  f16 query and its f32 counterpart adjacently — the same trick
  `replay-queries.sh` already uses for the two solvers, applied to the second
  axis.

**The corpora differ where they should and nowhere else.** Sort census over
the 830 files per format:

| sort | in the f16 corpus | in the f32 corpus |
| --- | ---: | ---: |
| `(_ FloatingPoint 5 11)` | 1,313 | 0 |
| `(_ FloatingPoint 8 24)` | 0 | 1,313 |
| `(_ FloatingPoint 11 53)` | 945 | 945 |

The 945 binary64 terms are identical in both and come from klee-uclibc's `logf`
and `expf`, which are implemented at double precision — so `arm_logsumexp` and
`arm_jensenshannon_distance` carry a binary64 component that the format switch
does not touch. That is a dilution, and it is visible per function below.

**The matched-pair claim holds inside KLEE too, independently.** Before the
query dump, seven drivers were run end to end at f16, f32 and f64 (`axis/mk.sh`
and `axis/run.sh` in the working area). `arm_canberra_distance` and `arm_max`
walk *byte-for-byte the same* exploration at f16 and f32 — same instructions,
same paths, same query count — with only solver time differing:

| kernel | width | solver | instrs | paths | queries | solver time |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `arm_canberra_distance` | f16 | bitwuzla | 14,569 | 511 | 2,308 | 0.113s |
| `arm_canberra_distance` | f32 | bitwuzla | 14,569 | 511 | 2,308 | 0.150s |
| `arm_canberra_distance` | f16 | stp | 14,569 | 511 | 2,308 | 0.338s |
| `arm_canberra_distance` | f32 | stp | 14,569 | 511 | 2,308 | 0.361s |
| `arm_max` | f16 / f32 | either | 282 | 8 | 57 | 0.024–0.064s |
| `arm_clip` (budget-bound) | f16 | bitwuzla | 51,076 | 4,494 | 18,050 | 60.03s |
| `arm_clip` (budget-bound) | f32 | bitwuzla | 42,718 | 3,758 | 15,094 | 60.07s |

That is `RESULTS.md`'s "neither cut off" condition satisfied by construction:
the two runs cannot diverge, because the format does not change which branches
are feasible. The budget-bound `arm_clip` rows are the exception, and they are
one of the seven pairs excluded from the replay corpus for exactly that reason.

**The verdict column, first, because the script's own comment warns about it.**
STP answers an unsupported operator with `(error …)` in ten milliseconds, which
reads as a very fast solve. It did not happen here:

| | f16 | f32 |
| --- | ---: | ---: |
| both `sat` | 475 | 475 |
| both `unsat` | 348 | 348 |
| both `timeout` (10s) | 7 | 7 |
| `error` or `other` | **0** | **0** |
| sat/unsat disagreements | **0** | **0** |

The seven timeouts are the same seven queries in both formats and for both
solvers. 823 pairs remain in which all four runs answered and agreed, and every
number below is over those.

### The headline

| | n | STP total | Bitwuzla total | gmean(stp/btw) | STP faster / slower |
| --- | ---: | ---: | ---: | ---: | ---: |
| **binary16** | 823 | 37.97s | 31.70s | **1.008** | 346 / 362 |
| **binary32** | 823 | 57.94s | 45.35s | **1.075** | 334 / 388 |

and the same data read along the other axis — the same query, one format
narrower:

| solver | binary16 total | binary32 total | gmean(f16/f32) |
| --- | ---: | ---: | ---: |
| STP | 37.97s | 57.94s | **0.866** |
| Bitwuzla | 31.70s | 45.35s | **0.923** |

**Narrowing the format helps STP roughly twice as much as it helps Bitwuzla**
(13.4% against 7.7%), and that is what moves the head-to-head from 1.075 to
1.008.

### Where it comes from

Split the 823 pairs by which floating-point operators the query contains —
`RESULTS.md`'s own table shape, now with a second format beside it:

| query class | n | stp/btw at **binary32** | stp/btw at **binary16** | STP f16/f32 | Bitwuzla f16/f32 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fp.div` or `fp.sqrt` | 131 | **1.753** | **1.085** | **0.486** | 0.785 |
| `fp.mul`/`fp.fma`, no div or sqrt | 294 | 0.969 | 0.949 | 0.922 | 0.942 |
| `fp.add`/`fp.cmp` only | 58 | 0.932 | 1.075 | 0.883 | 0.765 |
| no `fp.` at all | 340 | 0.997 | 1.022 | 1.021 | 0.997 |

Four things this says.

**One. `RESULTS.md`'s finding reproduces on a different library at a different
width.** STP is 1.75x slower than Bitwuzla on division and square root at
binary32, against the 2.45x that repository measures at binary64 — the same
class, the same direction, a smaller magnitude at a smaller width.

**Two. At binary16 the gap is essentially gone**: 1.085, on 131 queries. The
class that costs STP the corpus stops costing it anything once the significand
is 11 bits.

**Three. The mechanism is width-dependent and STP-specific.** Narrowing
binary32 to binary16 is worth 2.06x to STP on those queries and 1.27x to
Bitwuzla. `reproducers/README.md` names why: SymFPU's `fixedPointDivide` lowers
one `fp.div` to a `bvudiv` and a `bvurem` on operands of about `2w` bits, which
is 47 bits at binary32 and 21 at binary16. Bitwuzla builds the same circuit but
does not pay STP's AIG-to-CNF cost on it, so it has less to gain from the
circuit shrinking.

**Four. The queries with no floating point in them are the control, and they
move by 2%.** 340 of the 830 are pure bitvector — the bounded `blockSize`, the
array reads — and the format switch leaves them at 1.021 and 0.997. Whatever
the other rows are measuring, it is the format and not the harness.

### Per function

| function | n | stp/btw f16 | stp/btw f32 | STP f16/f32 | Bwz f16/f32 | what it computes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `arm_jensenshannon_distance` | 102 | 1.099 | **1.892** | **0.521** | 0.897 | `log`, division |
| `arm_cosine_distance` | 32 | 1.131 | 1.228 | 0.705 | 0.765 | `sqrt`, division |
| `arm_correlation_distance` | 32 | 1.062 | 1.200 | 0.710 | 0.802 | `sqrt`, division |
| `arm_cmplx_mag` | 16 | 0.982 | 1.162 | 0.813 | 0.963 | `sqrt` |
| `arm_max` | 11 | 1.046 | 1.120 | 0.935 | 1.000 | comparison |
| `arm_rms` | 32 | 1.007 | 1.092 | 0.770 | 0.835 | `sqrt` |
| `arm_euclidean_distance` | 34 | 1.037 | 1.089 | 0.805 | 0.845 | `sqrt` |
| `arm_min` | 11 | 1.015 | 1.087 | 0.959 | 1.028 | comparison |
| `arm_min_no_idx` | 18 | 1.084 | 1.069 | 1.002 | 0.988 | comparison |
| `arm_absmin` | 11 | 1.029 | 1.062 | 0.948 | 0.979 | comparison |
| `arm_canberra_distance` | 33 | 0.973 | 0.895 | 1.042 | 0.958 | division |
| `arm_std` | 30 | 0.947 | 0.981 | 0.787 | 0.815 | `sqrt` |
| `arm_svm_polynomial_predict` | 269 | 0.954 | 0.936 | 1.006 | 0.988 | `fma` chain |
| `arm_svm_linear_predict` | 63 | 0.971 | 0.955 | 0.996 | 0.979 | `fma` chain |
| `arm_logsumexp` | 51 | 1.047 | 0.948 | 0.892 | 0.807 | `exp`/`log` at binary64 |
| `arm_chebyshev_distance` | 17 | 1.099 | 0.904 | 1.123 | 0.924 | comparison |
| `arm_absmax` | 11 | 0.959 | 1.253 | 0.943 | 1.233 | comparison |
| `arm_mfcc` | 2 | 0.957 | 0.799 | 0.877 | 0.732 | mixed |
| `arm_mat_solve_upper_triangular` | 4 | 0.962 | 0.903 | 0.847 | 0.795 | division |
| `arm_mat_solve_lower_triangular` | 4 | 1.049 | 0.915 | 0.909 | 0.793 | division |
| `arm_absmax_no_idx` | 11 | 1.058 | 0.942 | 1.025 | 0.912 | comparison |
| `arm_absmin_no_idx` | 11 | 1.098 | 0.904 | 1.082 | 0.891 | comparison |
| `arm_max_no_idx` | 18 | 0.988 | 0.983 | 1.001 | 0.996 | comparison |

**`arm_jensenshannon_distance` is the driver to keep.** 102 queries, STP 1.892x
slower than Bitwuzla at binary32 and 1.099x at binary16, and the format is worth
0.521 to STP against 0.897 to Bitwuzla. It is the sharpest single instance of
the effect in the corpus, and it is a real DSP kernel, not a synthetic.

**The comparison-only functions are the null result and they behave like one.**
`arm_max_no_idx`, `arm_min_no_idx`, `arm_svm_linear_predict` and
`arm_svm_polynomial_predict` sit within a few percent of 1.0 at both formats and
show almost no format effect (0.996-1.006). An `fp.lt` on binary16 and an
`fp.lt` on binary32 are the same query to both solvers, and the data says so.

### A second corpus, independently, with no other format in it

The CMSIS-DSP corpus carries 945 binary64 terms in both formats (klee-uclibc's
`expf`/`logf`), which dilutes the format axis. HDF5's `H5Tconvert` has no such
component, so it is worth asking the same question there. Two drivers, six
lines each, differing only in the source datatype:

```c
H5Tconvert(H5T_NATIVE_FLOAT16, H5T_NATIVE_SCHAR, 4, &b, NULL, H5P_DEFAULT);
H5Tconvert(H5T_NATIVE_FLOAT,   H5T_NATIVE_SCHAR, 4, &b, NULL, H5P_DEFAULT);
```

Both explore **81 completed paths** — the same branch tree, since the range
check is per element and there are four elements either way — and dump corpora
that are single-format throughout: 1,742 `(_ to_fp 5 11)` terms and no other
floating-point sort in the first, 920 `(_ to_fp 8 24)` and nothing else in the
second. Every query is `fp.gt`/`fp.lt` only; no division, no square root, no
libm. Replayed the same way:

| | n | mean bytes | STP | Bitwuzla | gmean(stp/btw) | STP faster / slower |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **binary16** | 150 | 860 | 2.843s | 2.840s | **1.006** | 73 / 68 |
| **binary32** | 80 | 1,279 | 1.778s | 1.648s | **1.089** | 29 / 48 |

All 230 `sat`, both solvers agreeing on every one, no errors.

**1.006 against 1.089 reproduces 1.008 against 1.075** — the same effect, the
same direction, the same size, on a different library whose queries contain no
arithmetic at all and no second format. The caveat is that this pair is
*path*-matched rather than query-matched: 150 queries against 80, and the f32
queries are larger on average because a `float` buffer is 16 bytes where a
`_Float16` buffer is 8, so this is a corpus-level comparison and not a
per-query one. It is a confirmation, not a second independent measurement of
the same precision.

### The queries the two solvers disagree about most

At binary16, ranked by |log(stp/btw)|, every one of the top seven is
`arm_logsumexp`:

| driver | query | bytes | STP | Bitwuzla | ratio | result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `arm_logsumexp` | 0051 | 8,090 | 0.156 | 1.148 | **0.14** | unsat |
| `arm_logsumexp` | 0052 | 8,091 | 0.216 | 1.230 | 0.18 | sat |
| `arm_logsumexp` | 0037 | 6,633 | 3.814 | 1.141 | **3.34** | unsat |
| `arm_logsumexp` | 0035 | 6,109 | 0.277 | 0.090 | 3.08 | sat |
| `arm_logsumexp` | 0039 | 6,659 | 2.609 | 1.034 | 2.52 | unsat |
| `arm_logsumexp` | 0045 | 6,803 | 0.300 | 0.128 | 2.34 | sat |
| `arm_jensenshannon_distance` | 0096 | 22,496 | 0.471 | 1.066 | 0.44 | sat |

`arm_logsumexp` holds both the largest STP win (7.4x) and the largest STP loss
(3.3x) in the corpus, on queries of almost the same size. Those are the
mixed-format queries — binary16 arithmetic wrapped around klee-uclibc's
binary64 `expf`/`logf` — and the two solvers evidently take very different
routes through them. That is a lead worth pulling on and it is the one place in
this corpus where the two are far apart rather than a few percent apart.

---

## What binary16 does to the solvers, structurally

The corpus says where the difference lives; this says why, per operator and per
width. Six queries, each isolating one floating-point operator as an **unsat**
algebraic identity so that no model can be guessed and the circuit has to be
built or the identity recognised, instantiated at all four IEEE widths and run
against `/mnt/baranem/klee-float/deps/install-stp-term2/bin/stp` and
`/home/avj/clones/bitwuzla/main/build/src/main/bitwuzla` — the two binaries
`replay-queries.sh` defaults to. Sources in
`…/scratchpad/ops/{cmp,add,mul,fma,sqrt,div}_{16,32,64,128}.smt2`. Best of
three, all twenty-four `unsat`, both solvers agreeing on every one.

| operator | | b16 | b32 | b64 | b128 |
| --- | --- | ---: | ---: | ---: | ---: |
| `fp.div` (`x/x = 1`) | STP | 0.035 | 0.093 | 0.448 | **5.602** |
| | Bitwuzla | 0.025 | 0.058 | 0.471 | 2.515 |
| | stp/btw | 1.40 | 1.60 | 0.95 | **2.23** |
| `fp.lt` transitivity | STP | 0.022 | 0.024 | 0.025 | 0.036 |
| | Bitwuzla | 0.019 | 0.030 | 0.044 | **0.264** |
| | stp/btw | 1.15 | 0.79 | 0.57 | **0.14** |
| `fp.add`, `fp.mul`, `fp.fma` commutativity; `fp.sqrt` sign | both | ~0.015 | ~0.015 | ~0.015 | ~0.015 |

Three findings.

**Division is the only operator whose cost tracks the width**, and it tracks it
steeply: 0.035 → 0.093 → 0.448 → 5.602 for STP, roughly 4-12x per doubling of
the significand. That is `fixedPointDivide`'s `2w`-bit `bvudiv`, and it is why
the corpus split above puts the entire effect in the div/sqrt row.

**The commutativity identities are dispatched by rewriting, not by
bit-blasting.** `fp.add`, `fp.mul`, `fp.fma` and the `fp.sqrt` sign property all
answer in about 15 ms in both solvers at *every* width, binary128 included.
Width only costs anything where a circuit is actually built — which is a real
statement about these solvers and a caution about synthetic FP benchmarks:
an identity-shaped query measures the rewriter.

**The comparison row runs the other way, and only at binary128.** STP is flat
(0.022 → 0.036) where Bitwuzla degrades 14x (0.019 → 0.264), so on pure
comparison STP is 7x *faster* at binary128 and slightly slower at binary16. It
is a small absolute number, but it is the one place in this report where the
ordering between the solvers reverses with width rather than merely narrowing.

Taken with the corpus measurement: **the ordering between the two solvers is
not constant across FP modes.** It depends on the width and on which operator
dominates the query, and at binary16 nearly every difference the suite has
measured at binary64 shrinks toward parity.

---

## What binary16 does to the arithmetic, before any candidate

Four facts established on `/mnt/baranem/llvm16/install/bin/clang` (16.0.6,
x86-64), all verified, all of which decide what a candidate is worth.

**`__fp16` cannot appear in a signature on x86-64.** Not a soft restriction:

```
error: function return value cannot have __fp16 type; did you forget * ?
error: parameters cannot have __fp16 type; did you forget * ?
```

Any library whose half typedef resolves to `__fp16` therefore cannot be
compiled for this host at all without redirecting the typedef to `_Float16`.
That is CMSIS-DSP exactly (below), and it is why "it works on Arm" says nothing
about whether it works here.

**`-fexcess-precision=16` is not optional, and it is the difference between a
binary16 benchmark and a binary32 one wearing its name.** FP_PORT_2026.md says
a compound statement keeps a binary32 intermediate at every optimisation level.
The shape that matters is the one every f16 kernel in every candidate is
written in — `s += x[i]*y[i]` — and it is worse than "keeps an intermediate":

| source | `-O2` (default) | `-O2 -fexcess-precision=16` |
| --- | --- | --- |
| `a + b` | `fadd half` | `fadd half` |
| `(a + b) * c` | `fpext`, `fadd float`, `fmul float`, `fptrunc` | `fadd half`, `fmul half` |
| `s += x[i]*y[i]` | `llvm.fmuladd.f32` + `fpext`/`fptrunc` per iteration | `llvm.fmuladd.f16` |
| the same with `-ffp-contract=off` | `fmul float`, `fadd float`, `fptrunc` per iteration | `fmul half`, `fadd half` |

So a dot product built the way `build-lib.sh` builds every library today —
`-O2 -g -fno-vectorize -fno-slp-vectorize` — issues **binary32** multiplies and
adds on binary16 storage, from end to end, with no binary16 arithmetic anywhere
in the module. That is the single most important flag in this report.

**At `-O0`, `-fexcess-precision=16` also gives per-operation `half` rounding** —
verified on both clang 16 and the `/usr/bin/clang` 21 that does the coverage
build. So the two builds `build-lib.sh` makes agree, which they would not
without the flag: `-O0` alone is fpext/op/fptrunc.

**And the `-O` level that makes binary16 real is the one that flattens the
branches.** Verified on `arm_max_f16.c`:

| | `-O0` | `-O1` | `-O2` |
| --- | ---: | ---: | ---: |
| `fcmp … half` | 1 | 1 | 5 |
| `select i1 …, half` | 0 | 1 | 5 |

`if (x > max) max = x;` becomes a `select` from `-O1`, and KLEE does not fork on
a `select`. This is not new with binary16 — the existing corpus is built at
`-O2` and pays the same — but it caps what the min/max/clamp family can
contribute, and it is why `arm_max_f16` finishes in 0.1s with 8 paths where its
IR has five half comparisons.

---

## 1. CMSIS-DSP — recommended, and it needs a one-line patch

Clone: `/mnt/baranem/fp_bench-work/fp16-probe/cmsis-dsp` at
`c0c8640de1a6e190c35535d7069594ceb5855161` (2026-08-12). Patched copy at
`.../cmsis-dsp-x86`.

### The blocker, and the one-line fix

`Include/arm_math_types_f16.h:52-65` gates the entire f16 half of the library:

```c
#if defined(__ARM_FEATURE_MVE) && (__ARM_FEATURE_MVE & 2)
  #define ARM_FLOAT16_SUPPORTED
#else
  #if !defined(DISABLEFLOAT16)
    #if defined(__ARM_FP16_FORMAT_IEEE) || defined(__ARM_FP16_FORMAT_ALTERNATIVE)
      typedef __fp16 float16_t;
      #define ARM_FLOAT16_SUPPORTED
    #endif
  #endif
#endif
```

x86-64 clang defines neither `__ARM_FP16_FORMAT_IEEE` nor
`__ARM_FEATURE_MVE`, so `ARM_FLOAT16_SUPPORTED` is undefined, every f16 source
is `#if defined(ARM_FLOAT16_SUPPORTED)`-guarded, and a host build produces
**zero** f16 symbols. Forcing `-D__ARM_FP16_FORMAT_IEEE` does not help: it
selects `typedef __fp16 float16_t`, and 24 f16 parameters and 17 f16 return
types then fail to compile for the reason above.

The fix that was verified is to add one branch:

```c
    #if defined(FP_BENCH_X86_FLOAT16)
      typedef _Float16 float16_t;
      #define ARM_FLOAT16_SUPPORTED
    #elif defined(__ARM_FP16_FORMAT_IEEE) || defined(__ARM_FP16_FORMAT_ALTERNATIVE)
      typedef __fp16 float16_t;
      #define ARM_FLOAT16_SUPPORTED
    #endif
```

This is a change to the library, not to a build flag, and it should be recorded
the way `--disable-assembly` and `amd64_SRCS=fenv.c` are: it is the same format
under a different spelling, and CMSIS-DSP's own scalar fallbacks already cast
to `_Float16` explicitly, e.g. `arm_add_f16.c:135`:

```c
*pDst++ = (_Float16)(*pSrcA++) + (_Float16)(*pSrcB++);
```

so nothing in the sources depends on which spelling the typedef gets.

### x86-64 build — verified

`cmake -S Source -B build -DHOST=ON` sets `__GNUC_PYTHON__`
(`Source/configDsp.cmake:3-5`), which is the switch that drops the CMSIS-Core
dependency; it is what the Python wrapper uses and it works. With
`CC=wllvm` and `CFLAGS="-O2 -g -fexcess-precision=16 -fno-vectorize
-fno-slp-vectorize -DFP_BENCH_X86_FLOAT16"`:

* all **106** `Source/**/*_f16.c` compile, 0 failures;
* `libCMSISDSP.a` has 772 defined `T` symbols, **131** ending in `_f16`;
* `cmake --install` lays out `inst/include/CMSIS-DSP/arm_math_f16.h` and
  `inst/lib64/libCMSISDSP.a`, which is the shape `build-lib.sh` already looks
  for;
* the extracted bitcode (`/mnt/baranem/fp_bench-work/fp16-probe/cmsisdsp.bc`,
  4.5 MB) contains **no `<N x T>` vector types and no `@llvm.x86.*`** — the
  HOST build is entirely scalar, so none of the Helium/Neon problem reaches
  KLEE.

### API surface and parameter shapes — verified via clang AST

`arm_math_f16.h` declares **127** distinct `*_f16` functions. Parameter
histogram (top of `/mnt/baranem/fp_bench-work/fp16-probe/…`, reproduced by an
AST probe):

| count | type |
| ---: | --- |
| 193 | `float16_t *` |
| 89 | `uint32_t` |
| 33 | `arm_matrix_instance_f16 *` |
| 24 | `float16_t` |
| 22 | `uint32_t *` |
| 11 | `arm_cfft_instance_f16 *` |
| 10 | `arm_rfft_fast_instance_f16 *`, 10 `arm_mfcc_instance_f16 *` |
| 8 | `int32_t *` |
| ~20 | one filter/SVM/interp instance struct each |

Scalars and caller-allocated arrays: `gen-drivers.py` handles both directly.
The instance structs are handled by the existing constructor search, because
every one of them has an `arm_*_init_f16` that writes through a `T *` —
`arm_svm_rbf_init_f16`, `arm_cfft_init_1024_f16`, `arm_fir_init_f16`. The one
exception is `arm_matrix_instance_f16`, where `arm_mat_init_f16(S, nRows,
nCols, pData)` makes the buffer length a relationship between arguments; that
needs a hand-written maker, exactly as CXSparse's matrix does.

**Generated: 84 drivers, 3 skipped**, using an unmodified copy of
`gen-drivers.py` plus the `cmsisdsp` entry in the diff at the end of this
report. The three skips are `arm_linear_interp_f16`,
`arm_bilinear_interp_f16` and `arm_gaussian_naive_bayes_predict_f16`, each on
its own instance struct. **84 of 84 built to bitcode**, 0 failures.

### Is the arithmetic really binary16? — verified

All 106 f16 sources compiled at `-O2 -fexcess-precision=16` and the `.ll` read.
No `fpext half` and no `fptrunc float to half` survives in the kernels; the
arithmetic is `fadd half`/`fmul half`/`llvm.fmuladd.f16`. Intrinsic census over
the 84 driver modules:

| count | intrinsic | KLEE |
| ---: | --- | --- |
| 908 | `llvm.fmuladd.f16` | fine — `Expr::FMA`, one rounding |
| 45 | `llvm.fabs.f16` | fine |
| 4 | `llvm.floor.f16` | **broken** (`arm_mfcc_f16`, `arm_vlog_f16`) |
| 2 | `llvm.copysign.f16` | **broken** (`arm_atan2_f16`) |

### Would the drivers branch? — verified

30 of the 84 drivers have at least one `fcmp … half` in their `-O2` IR. The
branchy families are the ones the README would want: `absmax`/`absmin`/`max`/
`min` and their `no_idx` variants (8), the distance functions
(`canberra`, `chebyshev`, `correlation`, `cosine`, `euclidean`,
`jensenshannon`, 6), `clip`, `householder`, `logsumexp`, `cmplx_mag`, `rms`,
`std`, the four SVM predictors, `mat_cholesky`, `mat_inverse`, the two
triangular solves, `mfcc`, `atan2`. The other 54 are the block-arithmetic
surface the README already declined: `add`, `sub`, `mult`, `scale`, `offset`,
`negate`, `copy`, `fill`, `dot_prod`, the matrix multiplies, the transforms.

So the f16 half of CMSIS-DSP is *less* branch-free than the f32 half the README
rejected — the distance and classifier families have no f32-corpus equivalent
in this suite — but it is the same library and the same 2:1 ratio of block
arithmetic to decisions.

### Smoke test — feasibility signal only, not a measurement

Another agent was running KLEE on this machine throughout; these wall clocks
are evidence that the drivers *run*, not evidence of how long anything takes.
`/mnt/baranem/quad-2026/klee-build/bin/klee`, Bitwuzla, 60s budget, 5s query
cap, 2000 MB, DFS, klee-uclibc's `libm.a` linked.

| driver | rc | wall | instrs | paths | tests | queries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `arm_absmax_f16` | 0 | 0.1s | 381 | 8 | 9 | 61 |
| `arm_add_f16` | 0 | 0.1s | 303 | 9 | 9 | 75 |
| `arm_biquad_cascade_df1_f16` | 0 | 0.1s | 2,545 | 9 | 10 | 522 |
| `arm_braycurtis_distance_f16` | 0 | 0.1s | 511 | 9 | 9 | 71 |
| `arm_cfft_f16` | 0 | 0.1s | 199 | 0 | 2 | 47 |
| `arm_chebyshev_distance_f16` | 0 | 0.1s | 455 | 8 | 9 | 67 |
| `arm_dot_prod_f16` | 0 | 0.1s | 344 | 9 | 9 | 80 |
| `arm_f64_to_f16` | 0 | 0.1s | 382 | 9 | 9 | 81 |
| `arm_levinson_durbin_f16` | 0 | 0.1s | 1,238 | 7 | 8 | 239 |
| `arm_mat_mult_f16` | 0 | 0.1s | 1,399 | 1 | 1 | 252 |
| `arm_mat_solve_upper_triangular_f16` | 0 | 0.1s | 1,046 | 5 | 5 | 216 |
| `arm_max_f16` | 0 | 0.1s | 325 | 8 | 9 | 61 |
| `arm_mse_f16` | 0 | 0.1s | 370 | 9 | 9 | 72 |
| `arm_sqrt_f16` | 0 | 0.1s | 69 | 2 | 2 | 38 |
| `arm_canberra_distance_f16` | 0 | 0.2s | 14,574 | 511 | 511 | 2,310 |
| `arm_vlog_f16` | 0 | 0.4s | 1,107 | 9 | 9 | 310 |
| `arm_rms_f16` | 0 | 0.7s | 514 | 17 | 17 | 101 |
| `arm_householder_f16` | 0 | 1.4s | 3,633 | 24 | 25 | 1,068 |
| `arm_std_f16` | 0 | 1.4s | 1,024 | 15 | 15 | 182 |
| `arm_cosine_distance_f16` | 0 | 1.5s | 1,966 | 17 | 17 | 351 |
| `arm_mat_cholesky_f16` | 0 | 10.5s | 779 | 5 | 5 | 165 |
| `arm_fir_f16` | 0 | 10.9s | 33,070 | 284 | 295 | 6,937 |
| `arm_svm_rbf_predict_f16` | 0 | 60.4s | 8,066 | 69 | 82 | 2,618 |
| `arm_mat_inverse_f16` | 0 | 60.6s | 10,023 | 9 | 15 | 2,135 |
| `arm_clip_f16` | 0 | 60.7s | 54,109 | 4,780 | 4,780 | 19,116 |
| `arm_jensenshannon_distance_f16` | 0 | 68.2s | 3,591 | 1 | 80 | 1,543 |
| `arm_entropy_f16` | **134** | 0.4s | — | — | — | — |
| `arm_minkowski_distance_f16` | **134** | 63.4s | 4,242 | — | — | 2,246 |

28 of the 84 drivers, chosen to cover both halves of the library. Every one
reaches the library — no `bli_init_once`, no `SUNContext_Create`, nothing
missing from the module, and `arm_fir_f16`, `arm_cfft_f16`,
`arm_biquad_cascade_df1_f16` and the four SVM predictors show that the
constructor search built their instance structs and the calls went through.
Four things stand out.

**The corpus is query-dense and the queries are individually cheap.**
`arm_clip_f16`: 19,116 queries in 59.87s of solver time, 3.1 ms each.
`arm_canberra_distance_f16`: 2,310 queries in 0.12s, 0.05 ms each.
`arm_fir_f16`: 6,937 queries in 10.9s wall. Compare `RESULTS.md`, where 69
queries burn 29% of all solver time at a 30s cap. There is no comparable tail
here — which is why the solver comparison for this library has to be made on
the replayed corpus rather than on in-KLEE wall clock: with no query
dominating, per-driver time is dominated by everything except the solver, and
the replay is where the 1.008-against-1.075 difference is visible at all.

**Two of the 28 abort KLEE, both on the same assertion, and it is not a
binary16 bug.** `arm_entropy_f16` and `arm_minkowski_distance_f16` die on
`IndependentSolver.cpp:535`'s `assertCreatedPointEvaluatesToTrue` — the
unsoundness FP_PORT_2026.md records against `--z3-array-ackermannize`, reached
here with that flag off. Isolated on `arm_entropy_f16`:

| solver | flags | outcome |
| --- | --- | --- |
| Bitwuzla | default | **abort (rc 134)** |
| STP | default | 2,414,406 instructions, 51,959 paths |
| Bitwuzla | `--use-independent-solver=false` | 25,901 instructions, 530 paths |
| STP | `--use-independent-solver=false` | 16,673 instructions, 331 paths |

so it needs the independent solver and it is backend-dependent, exactly as the
port document describes. Two in twenty-eight is a high enough rate that it
would be the first thing a real sweep of this library ran into.

**`arm_minkowski_distance_f16`'s hardest query is binary64.**
`arm_minkowski_distance_f16.c:124` is
`sum += (_Float16)powf(fabsf((float32_t)((_Float16)pA[i] - (_Float16)pB[i])), order);`
— it promotes to float and calls `powf`, which klee-uclibc implements through
`libm/e_pow.c` at *double* precision. The timing-out query comes from there.
Labelling a corpus "binary16" does not make its queries binary16.

**Two drivers measure nothing.** `arm_cfft_f16` and `arm_mat_mult_f16` are the
argument-relationship problem: the constructor search built an
`arm_cfft_instance_f16` for a length the 8-element buffer cannot hold
(`memory error: out of bound pointer` at `arm_cfft_f16.c:707`), and
`arm_mat_mult_f16` completes one path because 4x4 times 4x4 is branch-free.
The 21 transform drivers would need pinning or excluding the way FFTW's
planner flags are pinned.

### Two smaller things a real integration would hit

* **`blockSize` must be at least 1.** `arm_max_f16.c:197` computes
  `blkCnt = (blockSize - 1U)` and a symbolic `blockSize == 0` underflows to
  `0xFFFFFFFF`; KLEE reports `memory error: out of bound pointer`. This is a
  precondition of the API, not a bug, and nothing in the type says it. The
  `"min"` key in the generator diff below fixes it — verified: with
  `klee_assume(a1 >= 1)` the same driver runs clean, 287 instructions, 8
  completed paths, no error.
* **`llvm.copysign.f16` fails two different ways.** In `arm_atan2_f16` the
  mis-typed value reaches a `phi half`, KLEE's module verifier says
  `PHI node operands are not the same type as the result!` and the run aborts
  before it starts. In a module where it does not reach a phi, and with
  klee-uclibc's `libm.a` linked so that `copysignf` resolves, KLEE reports
  `memory error: out of bound pointer` — a wrong error from a compiler
  lowering bug. That second mode is the dangerous one: it looks like a finding.

---

## 2. CMSIS-NN — recommended third: builds cleanly, but the branches are `select`s

Clone: `/mnt/baranem/fp_bench-work/fp16-probe/cmsis-nn` at
`13c97dbb6f781d4aab38ed34e6e441f42b79aff4` (2026-08-20).

**No patch needed — verified.** `Include/arm_nn_math_types_flt.h:80-86` has the
branch CMSIS-DSP lacks:

```c
#if defined(__ARM_FP16_FORMAT_IEEE) || defined(__ARM_FP16_FORMAT_ALTERNATIVE)
typedef __fp16 float16_t;
#elif defined(__FLT16_MAX__)
typedef _Float16 float16_t;
#endif
```

x86-64 clang 16 defines `__FLT16_MAX__`, so `float16_t` is `_Float16` out of
the box. `cmake -DARM_NN_ENABLE_F16=ON -DARM_NN_ENABLE_F32=ON` with
`CC=wllvm` builds `libcmsis-nn.a` with **48** `_f16` symbols, no failures. One
trap: `CMSIS_OPTIMIZATION_LEVEL` defaults to **`-Ofast`**
(`CMakeLists.txt:20-22`), which is `-ffast-math` — it must be overridden to
`-O2`, for the same reason `build-lib.sh` strips BLIS's
`-funsafe-math-optimizations`.

**The arithmetic is genuinely binary16 — verified.** 36 f16 sources compiled at
`-O2 -fexcess-precision=16`; `fadd half`/`fmul half`/`llvm.fmuladd.f16` counts
and `fcmp … half` counts per file, with **no external libm dependency at all**
(the only undefined symbols are CMSIS-NN's own inner kernels). Highlights:
`arm_nn_depthwise_conv3x3_f16` 55 half ops, `arm_nn_lstm_step_f16` 30,
`arm_batch_matmul_f16` 16, `arm_svdf_f16` 16; `arm_maximum_f16` and
`arm_minimum_f16` 52 `fcmp half` each, `arm_nn_activation_f16` 9,
`arm_svdf_f16` 8. Four sources still accumulate in float
(`arm_nn_conv1d_k3_f16`, `arm_nn_conv1d_k5_f16`,
`arm_nn_depthwise_conv1d_k3_f16`, `arm_nn_depthwise_conv2x5_f16`) — worth
noting, not disqualifying.

### What it would actually yield — measured, and it is the thing that demotes it

Six drivers were hand-written against three makers (`nndrv/_pre.h` and
`nndrv/*.c` in the working area): a `cmsis_nn_dims` fixed at 1x2x2x2, a
`cmsis_nn_context` with a concrete scratch buffer, and a symbolic
`cmsis_nn_activation_f16`. Every f16 buffer symbolic, 60s budget, 5s cap,
Bitwuzla, `--debug-bitwuzla-dump-queries`:

| driver | instrs | paths | queries dumped |
| --- | ---: | ---: | ---: |
| `arm_elementwise_add_f16` | 131 | 1 | **0** |
| `arm_elementwise_mul_f16` | 131 | 1 | **0** |
| `arm_maximum_f16` | 943 | 1 | **0** |
| `arm_minimum_f16` | 943 | 1 | **0** |
| `arm_softmax_f16` | 484 | 0 | 9 |
| `arm_max_pool_f16` | — | — | driver error (see below) |

Five of six produce **no solver query at all**, and the IR says why. At `-O2`,
*every* `fcmp half` in these kernels is consumed by a `select`:

| source | `fcmp half` | `select i1 …, half` | `br i1` |
| --- | ---: | ---: | ---: |
| `arm_maximum_f16` | 52 | **52** | 115 |
| `arm_minimum_f16` | 52 | **52** | 115 |
| `arm_elementwise_add_f16` | 6 | **6** | 8 |
| `arm_nn_activation_f16` | 9 | 8 | 4 |
| `arm_max_pool_f16` | 7 | 5 | 18 |
| `arm_softmax_f16` | 6 | 5 | 15 |

KLEE does not fork on a `select`, and the `br i1`s that remain are on the
dimensions and loop counters — which the makers have to hold *concrete*,
because the buffer length is `n*h*w*c`. So the branch structure a driver can
reach is integer and already decided. The 52 half comparisons in
`arm_maximum_f16` are real binary16 arithmetic and they are exactly the thing
that never reaches the solver.

`arm_softmax_f16` is the exception and the one to keep: 9 queries, and one of
them hit `Query timed out (fork)` at `arm_softmax_f16.c:127`. But its queries
are mixed — 109 `(_ to_fp 5 11)` terms against 130 `(_ to_fp 8 24)` — because
softmax's exponential approximation runs in `float`. A mode comparison wants
the format to be the variable, and here it is not.

One driver bug worth recording because a real integration would hit it:
`klee_make_symbolic(&pp.activation, …)` on a *sub-object* of a
`cmsis_nn_pool_params_f16` is `memory error: invalid pointer: make_symbolic`.
KLEE wants whole objects, so the maker has to declare the activation struct
separately and assign it in.

**Why it is third and not first.** `arm_nnfunctions_flt.h` declares **30**
`*_f16` functions. Their parameter histogram is:

| count | type |
| ---: | --- |
| 101 | `float16_t *` |
| 87 | `cmsis_nn_dims *` |
| 27 | `cmsis_nn_context *` |
| 8 | `arm_nn_tensor_layout` |
| 22 | one `cmsis_nn_*_params_f16 *` or another |
| 6 | `float16_t` |

`cmsis_nn_dims` is `{n, h, w, c}` and the buffer it describes must be exactly
`n*h*w*c` elements; `cmsis_nn_context` is a scratch buffer whose size comes
from a separate `arm_*_get_buffer_size_f16` call. Neither has a constructor
the search can find, and both are relationships between arguments. Three
hand-written makers — the CXSparse treatment, which this suite has paid for
before and which is not by itself a reason to decline.

The reason to place it second is the measured yield above: the makers cost
about what CXSparse's did, and what they buy is a corpus whose floating-point
decisions are compiled into `select` before KLEE sees them. It is worth adding
for coverage of a second, independent binary16 implementation — and worth
adding at `-O1` rather than `-O2` if the object is queries, since `-O1` already
gives real `half` arithmetic on single-operation statements and leaves more of
the comparisons as branches. That is a change to how the suite builds one
library and should be argued separately.

---

## 3. CORE-MATH — reject on substance: 2 of 43 functions do binary16 arithmetic

Clone: `/mnt/baranem/fp_bench-work/fp16-probe/core-math` at
`9afd7177cbdfd211e2d919c27792638f791ae2b3` (2026-08-28).

**43 binary16 functions**, `src/binary16/*/[a-z]*f16.c`, all `cr_*f16` with
`_Float16` scalar parameters and returns — on shape alone this is the OpenLibm
case, the one `gen-drivers.py` was built for, needing one type-map entry. It is
also the one candidate with no library and no header: 43 loose sources, one
per-function `Makefile` including `../support/Makefile.univariate`, and a test
harness that needs MPFR. A header and an archive would have to be generated.

That is the small problem, and the constrained-intrinsic abort below is a
fixable KLEE gap rather than a property of the library. **The reason to reject
CORE-MATH is the second point: 2 of its 43 binary16 functions contain any
binary16 arithmetic at all.** A CORE-MATH corpus would put binary16 in the
signature and binary32/binary64 in every query — a binary64 solver benchmark
under a binary16 label, which is the one thing a comparison across FP modes
cannot tolerate. The other two points are why it also does not run today.

**One. 41 of the 43 sources carry `#pragma STDC FENV_ACCESS ON`, and KLEE
aborts on what it produces.** Every FP operation becomes
`llvm.experimental.constrained.*`. KLEE does not implement them:

```
KLEE: WARNING ONCE: unsupported intrinsic llvm.experimental.constrained.fmul.f32
KLEE: WARNING ONCE: unsupported intrinsic llvm.experimental.constrained.fptrunc.f16.f32
klee: lib/Core/Executor.cpp:1289: Assertion `vnumber != -1 && "Invalid operand
      to eval(), not a value or constant!"' failed.
```

`cr_expf16`, `cr_sinf16` and `cr_sqrtf16` all abort with `rc=134` on the first
call. `-ffp-exception-behavior=ignore` does **not** suppress an in-source
pragma (verified: still 7 constrained intrinsics in `expf16.ll`). The only
escape is to strip the pragma from the sources, which changes what the library
is — CORE-MATH's whole claim is correct rounding, and the pragma is how it
tells the compiler not to reassociate its way out of it.

A fourth, smaller thing: one of the 43, `powf16.c:196`, does not compile with
clang 16 at all (`__builtin_roundeven` is not available), and five more need
`-std=gnu2x -D_GNU_SOURCE` for `M_PI` and `signgam`.

**Two. There is almost no binary16 arithmetic in it.** All 42 compilable
sources at `-O2 -fexcess-precision=16`, counting half arithmetic and half
comparisons:

| | half arith | half fcmp |
| --- | ---: | ---: |
| `tgammaf16` | 3 | 4 |
| `lgammaf16` | 1 | 7 |
| **the other 40** | **0** | **0** |

`cr_expf16`, with the pragma stripped, is the whole library in twelve
instructions:

```llvm
define half @cr_expf16(half %0) {
  %2 = bitcast half %0 to i16
  ...
  %8  = getelementptr [2048 x %union.b32u32_u], ptr @T1, i64 0, i64 %7
  %9  = load float, ptr %8
  %11 = getelementptr [2048 x %union.b32u32_u], ptr @T2, i64 0, i64 %10
  %12 = load float, ptr %11
  %13 = fmul float %9, %12
  %14 = fptrunc float %13 to half
  ret half %14
}
```

Two 8 KiB binary32 tables indexed by the argument's own bit pattern, one
binary32 multiply, one rounding. The survey's caveat — "not necessarily a
corpus in which every intermediate operation is carried out at binary16
precision" — understates it: it is a corpus in which **no** intermediate
operation is carried out at binary16 precision, in 40 of 43 functions. Add
that the branchy ones branch on `double` (`compoundf16` has 83 branches and 14
`double` arithmetic ops, `atan2f16` 21 and 7) and CORE-MATH is a binary64
benchmark with a binary16 signature.

**Three. What is left computes nothing to decide.** With the pragma stripped,
`cr_expf16` under KLEE: **28 instructions, 1 completed path, 1 test, 0
branches**, on both STP and Bitwuzla, plus
`Symbolic memory read will send the following array of 8192 bytes to the
constraint solver`. Branch-free, and it hands the solver an 8 KiB array read
to get there. That is `sinq`'s failure mode from the README's quad section,
without even the compensation of being hard.

`cr_tgammaf16` — the one function with real half arithmetic — does run:
7,469 instructions, 90 paths, 8.2s. One function is not a library.

---

## 4. XNNPACK — reject

Sparse clone at `/mnt/baranem/fp_bench-work/fp16-probe/xnnpack`.

* **Zero** functions in `include/xnnpack.h` take or return a half type. All f16
  buffers are `const void *` / `void *`, and `construct.py:161` refuses
  `void`/`void *`/`void **` outright. Verified by running the real generator
  against `xnnpack.h`: 177 drivers, 231 skipped, of which 99 skipped on
  `void *`. **12 of the 177 are f16-named, and all 12 are `xnn_create_*` or a
  setup that returns `xnn_status_invalid_parameter` before touching data.**
* The f16 API is a create → reshape → setup → `xnn_run_operator` protocol.
  `gen-drivers.py` emits one call per driver.
* On x86-64 without `-mf16c`, `src/xnnpack/math.h:528-567` makes `xnn_float16`
  a `struct { uint16_t value; }` and `src/xnnpack/simd/f16-scalar.h:36-54`
  does the arithmetic in float. `include/xnnpack.h:54` says so in a warning
  comment.
* The kernel an x86-64 build actually selects,
  `src/f16-dwconv/gen/f16-f32acc-dwconv-3p1c-minmax-scalar-acc2.c`, at
  `-O2 -fexcess-precision=16`: **0** half arithmetic ops, 9 `fadd float`, 9
  `fmul float`. The `f32acc` in the filename is the whole story. The true-half
  variant exists but is referenced by no config on any target.
* `xnn_initialize` goes through `pthread_mutex_t` and `cpuinfo_initialize()`,
  which is the `bli_init_once` failure mode with a second layer: if cpuinfo
  fails, every f16 config is NULL and every create returns
  `xnn_status_unsupported_hardware` — a driver that runs and measures nothing.

## 5. SIMDe — reject

Clone at `/mnt/baranem/fp_bench-work/fp16-probe/simde`.

* The type is right and the arithmetic is not. On x86-64/clang-16
  `simde-f16.h` selects `SIMDE_FLOAT16_API_FLOAT16` and
  `typedef _Float16 simde_float16` (verified by probe: `sizeof == 2`,
  `SIMDE_FLOAT16_IS_SCALAR == 1`). But `simde-f16.h:290` reads
  `#if defined(SIMDE_FLOAT16_FLOAT16) || defined(SIMDE_FLOAT16_FP16)`, and
  **neither macro is defined anywhere in the tree** — the real names carry an
  `_API_` infix. So `simde_float16_to_float32` always takes the portable
  integer path, on every target. Non-inline wrappers around eight f16
  intrinsics at `-O2 -fexcess-precision=16`: **0** half arithmetic ops, 19
  `fadd float`, 8 `fptrunc float→half`. `simde_vcgth_f16` compiles to
  `fcmp ogt float`.
* Header-only. `simde/CMakeLists.txt:16` is `add_library(simde INTERFACE)`;
  `SIMDE_FUNCTION_ATTRIBUTES` is `static` under every configuration. An object
  including `arm/neon.h` and `x86/avx512.h` at `-O2` is 744 bytes with **0
  symbols** — `extract-bc` would harvest nothing and there is no installed
  header whose declarations are a shipped surface.
* 433 f16-typed entry points (423 Neon, 10 AVX-512), of which **343 are ruled
  out by parameter type alone**: the vector types are a
  `union { simde_float16_t values[8]; … }` for which the generator has no
  maker. The ~90 scalar survivors compute in binary32 per the point above.

## 6. KleidiAI — reject

Clone at `/mnt/baranem/fp_bench-work/fp16-probe/kleidiai`.

* **It does not build for x86-64.** `CMakeLists.txt:617-638` sets per-source
  `COMPILE_OPTIONS -march=armv8-a` / `-march=armv8.2-a+fp16` unconditionally,
  with no host branch, including on the set named `KLEIDIAI_FILES_SCALAR`.
  Verified: configure succeeds, build dies with
  `error: unknown target CPU 'armv8-a'` on every translation unit.
* **61 of its 62 f16 `.c` files `#error` on non-AArch64.** There are zero
  portable-C f16 kernels; 16 include `arm_neon.h` and ~46 are offset
  calculators delegating to a sibling `.S`. Forcing the guard on one of the
  latter yields 10 defines, **0 occurrences of `half`**, and an unresolved
  `declare void @kai_kernel_matmul_clamp_f16_…`.
* **No half type appears in any public signature.** 490 f16-header prototypes,
  of which 435 are `kai_get_*` integer accessors and 55 are `kai_run_*` taking
  `const void*`/`void*` plus ~900 `size_t`. All 55 skip on `void *`, and the
  packed operand must be the output of a specific `kai_run_rhs_pack_*`.

## 7. OpenBLAS — reject: three functions and no binary16 arithmetic

Built and run at `/mnt/baranem/fp_bench-work/fp16-probe/openblas*`, 0.3.34.dev
(`47048dd6`).

* **Which format.** OpenBLAS implements both, and only one is in scope.
  `bfloat16` (`typedef uint16_t bfloat16`, `common.h:285`) is the `sb*`/`bgemm`
  family. IEEE binary16 is the `sh` prefix and `hfloat16`, behind
  `BUILD_HFLOAT16` (`Makefile.rule:312`), which `common.h:289-295` makes
  `_Float16`.
* **Three public functions.** `cblas_shgemm` (`cblas.h:526`), `shgemv_` and
  `shgemm_` (`f77blas.h:267,494`). `cblas_shgemv` is compiled and exported but
  not declared in `cblas.h`, so the generator would not see it. Three drivers
  is not a library.
* **The installed header disagrees with the built library under clang.**
  `openblas_config.h:75` guards the typedef on
  `#if defined(__GNUC__) && (__GNUC__ > 12)`; clang 16 reports `__GNUC__ == 4`,
  so the header a driver includes resolves `hfloat16` to `uint16_t` while the
  archive was compiled with `_Float16`. Harmless in practice (two bytes, passed
  by pointer) but the generator needs `-Dhfloat16=_Float16`.
* **x86-64 builds, and there is no x86-64 SHGEMM kernel** — `kernel/Makefile.L3:226`
  falls back to `kernel/generic/gemmkernel_2x2.c`, pure C, which is the good
  news. The bad news is what it computes:
  `conversion_macros.h:66` is `TO_F32(x) ((float)(x))` and the inner loop is
  `res0 = res0 + TO_F32(load0)*TO_F32(load1)` with `res0` a `float`. At
  `-O2 -fexcess-precision=16` that file has **0** half arithmetic ops, 26
  `fpext half → float`, 6 `fadd float`. Over the whole `openblas.bc`: 0
  `fadd/fsub/fdiv half`, 24 `fmul half`, 62 `fpext half`, and all 24 of the
  `fmul half` are in `gemm_small_matrix_kernel_*.c`, which
  `gemm_small_matrix_permit.c` disables with an unconditional `return 0;`.
* **Every driver dies at `openblas_cancel_begin`** —
  `driver/others/openblas_cancel.c:87` is
  `static __thread size_t openblas_cancel_slot`, and KLEE reports
  `unimplemented intrinsic (llvm.threadlocal.address.p0)`. This is BLIS's
  `bli_init_once`, exactly. Removing the `__thread` (correct under KLEE's
  single thread) fixes it: verified, 21,538 instructions, 7 completed paths,
  8 tests inside a 60s budget, with symbolic output reaching a post-call
  comparison.
* **But the forks are binary32.** `level3.c:281` (`alpha[0] == ZERO`),
  `level3.c:260` (`beta[0] != ONE`), `gemm_beta.c:83` — `alpha` and `beta` are
  `float` in SHGEMM. The binary16 half is branch-free block arithmetic.

Verdict: three drivers, half as a storage format, two patches, and no binary16
decisions. It is a coverage target, not a binary16 solver benchmark.

## 8. HDF5 — recommended second: the purest binary16 queries in the survey

* **Zero `_Float16` functions in any installed header.** All 34 live in
  `H5Tconv_float.h` (19), `H5Tconv_integer.h` (10), `H5Tconv_complex.h` (3)
  and `H5Tpkg.h` (2), every one of which is listed under `H5T_PRIVATE_HDRS`
  at `src/CMakeLists.txt:865-879`. The only public exposure is
  `H5T_NATIVE_FLOAT16`, an `hid_t`.
* The public route is `H5Tconvert(hid_t, hid_t, size_t, void *, void *, hid_t)`.
  Across `H5Tpublic.h`'s 76 `H5_DLL` declarations the parameter census is 104
  `hid_t`, 19 `const char *`, 14 `size_t`, 4 `void *`. `gen-drivers.py` has a
  maker for none of it, and `hid_t` is an opaque integer handle whose value
  must name a registered datatype — not something the constructor search can
  fabricate.
* It does build (CMake + clang 16,
  `-DHDF5_ENABLE_NONSTANDARD_FEATURE_FLOAT16=ON`, which is the default), and
  what is inside is the most interesting binary16 code found anywhere in this
  survey: at `-O2 -fexcess-precision=16`, `src/H5Tconv_float.c` has **0** half
  arithmetic ops but **104 `fcmp half`**, 240 `fpext half`, 30
  `fptrunc → half`, and 332 `fptosi`/`fptoui`/`sitofp`/`uitofp` on `half`.
  The comparisons come from `H5T_CONV_Fx_CORE`
  (`src/H5Tconv_macros.h:788,804`), which is the range check
  `if (*(S) > (ST)(D_MAX) || …) … else if (*(S) < (ST)(D_MIN))`.

### Reached, and it is the purest binary16 query source in the survey

`gen-drivers.py` cannot fabricate an `hid_t`, but that is the CXSparse split
again: structure by hand, values symbolic. The whole driver is six lines.

```c
#include "hdf5.h"
#include <klee/klee.h>
int main(void) {
  union { _Float16 h[4]; signed char c[8]; } b;
  klee_make_symbolic(&b, sizeof(b), "src");
  herr_t r = H5Tconvert(H5T_NATIVE_FLOAT16, H5T_NATIVE_SCHAR, 4, &b, NULL, H5P_DEFAULT);
  if (r < 0) klee_warning("CONVERT-FAILED"); else klee_warning("CONVERT-OK");
  return 0;
}
```

Built against a wllvm bitcode HDF5 (3,375 `T` symbols, 70 `Float16` symbols),
`--libc=uclibc --posix-runtime`, `--use-independent-solver=false`, Bitwuzla,
120s budget. **Verified, and it works**: `CONVERT-OK`, and

| driver | wall | instrs | paths | tests | queries |
| --- | ---: | ---: | ---: | ---: | ---: |
| `_Float16` → `signed char` | 125.8s | 75,152,390 | **81** | 81 | **150** |
| `float` → `signed char` (the control) | 125.5s | 75,152,567 | **81** | 81 | 80 |
| `_Float16` → `int` | 35.2s | 9,959,128 | 2 | 2 | 2 |

and the 150 queries are the point:

| | count |
| --- | ---: |
| `(_ to_fp 5 11)` terms | **1,742** |
| any other floating-point sort | **0** |
| `fp.gt` | 530 |
| `fp.lt` | 341 |

**Nothing but binary16.** No binary32, no binary64, no `float` accumulator, no
libm — the highest format purity of any candidate in this survey, and by a
distance. The `float` control is the mirror image: 920 `(_ to_fp 8 24)` terms
and nothing else. The queries are the `H5T_CONV_Fx_CORE` range test
(`src/H5Tconv_macros.h:788,804`), `*(S) > (ST)(D_MAX)` and
`*(S) < (ST)(D_MIN)` per element, with four symbolic halves each.

The destination type is what decides whether there is anything to ask. Into
`int`, binary16's whole range fits, so clang folds the range checks away and
`H5T__conv__Float16_int` compiles to 0 `fcmp half` and a bare `fptosi half to
i32` — 2 queries. Into `signed char` the checks survive:
`H5T__conv__Float16_schar` has **32 `fcmp half`** and 88 `br i1`. The narrow
integer destinations (`schar`, `uchar`, `short`, `ushort`) are the ones worth
driving; there are 34 `_Float16` conversion functions in `H5Tconv_float.h`,
`H5Tconv_integer.h` and `H5Tconv_complex.h`, and the narrow half of them is
maybe a dozen drivers.

**The cost is `H5open()`.** 75M instructions and about 30 seconds of the wall
clock go into HDF5's library initialisation before the conversion runs, and
`H5.c:237` calls `atexit`, which is a failed external call without
`--libc=uclibc --posix-runtime`. That is the OSQP shape — except that here the
driver *does* reach the arithmetic and *does* return, which is what OSQP could
not do. It also means the exploration budget has to be raised well above 60s
for this library or the whole budget is spent on init.

Verdict: **recommended second**, as a dozen hand-written drivers rather than a
generated corpus. It is the one candidate whose queries are binary16 and
nothing else, which for a comparison across FP modes is worth more than its
driver count suggests — and it reproduced the CMSIS-DSP headline (1.006 against
1.089) with no arithmetic in its queries at all, which says the effect is not
an artefact of one library's kernels.

## 9. NumKong — reject

* On x86-64 without `-mavx512fp16`, `include/numkong/types.h:1044-1063` makes
  `nk_f16_t` an `unsigned short` and `NK_NATIVE_F16` 0. Verified by
  preprocessing. With `-mavx512fp16` the type is `_Float16` and the
  translation unit does not compile (3 errors), and would in any case need
  AVX512-FP16 intrinsics KLEE cannot execute.
* IR check: `c/dispatch_f16.c` at `-O2 -fexcess-precision=16`, 17,283 lines of
  `.ll`, and **the token `half` appears 0 times**. The conversion in
  `include/numkong/cast/serial.h` is a hand-written sign/exponent/mantissa
  unpack through a `union {float; uint32_t}` — the exact pattern the survey
  document says disqualifies a project.
* Accumulation is at f32 by construction:
  `include/numkong/dot/serial.h:161` instantiates
  `nk_define_dot_(f16, f32, f32, nk_f16_to_f32_serial)`. Branch-free.
* `NK_PUBLIC` is `inline static` (`types.h:106-113`), so 2,088 header kernels
  have no external symbols.

## 10. PULP-TrainLib — reject

* `lib/include/pulp_train_defines.h:32` is `typedef float16alt fp16;`.
  `float16alt` is a PULP-GCC RISC-V type, clang 16 says
  `error: unknown type name 'float16alt'`, and the name says it is the
  *alternative* format, not binary16.
* All 21 `lib/sources/*fp16*.c` fail on `#include "pmsis.h"`, which is not in
  the repository. 193 `pi_cl_team_fork` calls, 94 `pi_core_id`, 41 cluster-DMA
  calls.
* 101 of 111 public declarations take `void *args` — the cluster-fork calling
  convention, a pointer to an undescribed struct. It is not a library with an
  API.

---

## Is a precision axis enough on its own?

An earlier draft of this report argued that binary16 belonged in
`reproducers/` and in a `quad_example.c`-shaped precision axis rather than as
an eighth library, on the ground that in-KLEE exploration barely changes with
the format. That was the wrong criterion and the wrong conclusion, and the
measurement above is what corrects it: the corpus is where the solver
difference shows, and a synthetic reproducer is not a substitute for it. Two
reasons, both from data in this report.

**A reproducer measures the rewriter as often as it measures the circuit.**
Four of the six isolated-operator queries above answer in 15 ms at every width
in both solvers, because `fp.add x y = fp.add y x` is a rewrite rule. Only
`fp.div` scales. A synthetic corpus therefore risks measuring nothing at all
unless every query is hand-checked, whereas 131 of the 823 real CMSIS-DSP query
pairs land in the div/sqrt class without anyone choosing them.

**And the synthetic query does not agree with the corpus about the answer.**
Put the two side by side on the same operator class:

| | b16 | b32 | b64 |
| --- | ---: | ---: | ---: |
| isolated `fp.div` query, stp/btw | 1.40 | 1.60 | **0.95** |
| div/sqrt queries from a real corpus, stp/btw | 1.085 | 1.753 | **2.45** (`RESULTS.md`) |

At binary64 the synthetic says the two solvers are level and the corpus says
STP is 2.45x slower. A KLEE div/sqrt query is not a bare `fp.div`: it carries a
path condition, byte-level array reads and a chain of `fp.fma`s around the
division, and that context is most of what the solver spends its time on.
`RESULTS.md` calls the replayed-query comparison the only one with nothing else
in it — but "nothing else in it" has to mean the same *KLEE* queries, not a
query someone wrote.

So both, and in this order: **the corpus is the measurement, the reproducers
are the explanation.** The corpus says the effect is in div/sqrt and is worth
2.06x to STP; the width matrix says why, and how far it goes at binary64 and
binary128 where CMSIS-DSP cannot reach.

Two things a precision axis still buys, and they are worth taking:

**(a) `reproducers/`: the width row.** `div_{16,32,64,128}.smt2` and
`cmp_{16,32,64,128}.smt2` from `…/scratchpad/ops/` extend
`atan2-fp-div.smt2`'s single binary64 data point into a curve, and they are the
only thing here that reaches binary128 — where STP is 2.23x slower on division
and 7x *faster* on comparison. Six files and one table.

**(b) The `_f32` twins as a first-class part of the `cmsisdsp` corpus, not a
separate directory.** 97 of CMSIS-DSP's 106 f16 sources have an `_f32` twin and
56 have an `_f64` twin, written once per width by the library's own authors, so
a driver at each width is a retype of one generated file. 23 of the 30 branchy
pairs produced *identical* query counts, which makes the format the only
variable — `RESULTS.md`'s "neither cut off" split obtained by construction
instead of by luck. That is the control the headline table rests on, and it
costs a second `"select"` regex.

A binary128 leg is not available from CMSIS-DSP, and `quadmath/quad_example.c`
already has one. A `_Float16` leg could be added there for one `#elif`, but it
would walk into this report's opening trap: its `poly()` is
`acc = acc * x + c[i]`, and at `REAL = _Float16` that is `llvm.fmuladd.f32`
plus `fpext`/`fptrunc` at `-O0` *and* at `-O2`, becoming `llvm.fmuladd.f16`
only under `-fexcess-precision=16`. The same applies to
`test/Floats/fp128_dual_precision_kernel.c`, whose RUN lines build at `-O0`.

---

## What fp_bench would need

Nothing under `/home/avj/clones/fp_bench/` was modified. The `gen-drivers.py`
diff *was* exercised, in a copy at
`/mnt/baranem/fp_bench-work/fp16-probe/gen/`, and it is what produced the 84
drivers and the `arm_max_f16` lower bound. The `build-lib.sh` diff was
exercised as its constituent commands (the patch, the two CMake
configure/build/install runs, `extract-bc`, `llvm-link`) but not as the script;
the `build-drivers.sh` and `sweep-all.sh` diffs are proposed, not run.

### `common/gen-drivers.py`

Inside the `LIBRARIES` dict, and the two definitions it needs above it:

```python
# ---------------------------------------------------------------------------
# CMSIS-DSP, half precision.
#
# float16_t is _Float16 here rather than __fp16: the two are the same format,
# and on x86-64 __fp16 is a storage type clang refuses as a parameter or a
# return type at all. build-lib.sh redirects the typedef; see there.
# ---------------------------------------------------------------------------
F16_N = 8
MAT_N = 4


def cmsis_matrix(name):
    """A dense MAT_N x MAT_N f16 matrix, symbolic in every entry.

    arm_mat_init_f16 takes the dimensions and the buffer separately, so the
    buffer's length is a relationship between arguments -- the one class of
    precondition the search says it cannot meet. Square, so that every matrix
    a driver builds conforms with every other.
    """
    return (["  float16_t %s_d[%d * %d];" % (name, MAT_N, MAT_N),
             '  klee_make_symbolic(%s_d, sizeof(%s_d), "%s_d");' % (name, name, name),
             "  arm_matrix_instance_f16 %s;" % name,
             "  arm_mat_init_f16(&%s, %d, %d, %s_d);" % (name, MAT_N, MAT_N, name)],
            "&" + name)


CMSISDSP_TYPES = {
    "float16_t":     lambda n: sym_scalar("float16_t", n),
    "float16_t *":   lambda n: sym_array("float16_t", n, F16_N),
    "float32_t *":   lambda n: sym_array("float32_t", n, F16_N),
    "float64_t *":   lambda n: sym_array("float64_t", n, F16_N),
    "q15_t *":       lambda n: sym_array("q15_t", n, F16_N),
    "uint32_t *":    lambda n: sym_array("uint32_t", n, F16_N),
    "int32_t *":     lambda n: sym_array("int32_t", n, F16_N),
    # CMSIS-DSP spells its lengths uint32_t, which build_driver's has_array
    # rule does not cover, so they are bounded here instead.
    "uint32_t":      lambda n: sym_scalar("uint32_t", n, bound=str(F16_N)),
    "uint16_t":      lambda n: sym_scalar("uint16_t", n, bound=str(F16_N)),
    "uint8_t":       lambda n: sym_scalar("uint8_t", n, bound="2"),
    "int32_t":       lambda n: sym_scalar("int32_t", n, bound=str(F16_N)),
    "int":           lambda n: sym_scalar("int", n, bound=str(F16_N)),
    "arm_matrix_instance_f16 *": cmsis_matrix,
}
```

```python
    "cmsisdsp": {
        "header": "arm_math_f16.h",
        "internal": {},
        "prefixes": ("arm_",),
        "types": CMSISDSP_TYPES,
        # The library is q7/q15/q31/f32/f64/f16 of nearly everything; this is
        # the half-precision benchmark, so say so. The f32 and f64 twins are
        # what the precision axis in fp16/ uses instead.
        "select": r"_f16$",
        # The instance initialisers are how the constructor search builds an
        # arm_cfft_instance_f16 or an arm_fir_instance_f16; they are not
        # drivers of their own. typecast is a bit reinterpretation and
        # bitreversal is integer index arithmetic.
        "exclude": r"(_init_|_init_f16$|typecast|bitreversal)",
        # A length of zero underflows: arm_max_f16 computes blockSize - 1U and
        # walks off the buffer. That is a precondition of the API rather than
        # a bug, and nothing in the type says it.
        "min": {"blockSize": "1", "numRows": "1", "numCols": "1",
                "numSamples": "1", "nbVectors": "1", "vecDim": "1"},
        # HOST=ON in the CMake build; FP_BENCH_X86_FLOAT16 is the typedef
        # redirection. Both have to be in the driver too, because the driver
        # includes the same header.
        "defines": {"__GNUC_PYTHON__": "1", "FP_BENCH_X86_FLOAT16": "1"},
        "includes": ["<arm_math_f16.h>"],
    },
```

and, in `build_driver`, four lines beside the existing `pin` handling — the
`"min"` key is the only new mechanism this needs:

```python
        lo = cfg.get("min", {}).get(pname)
        if lo is not None:
            lines.append("  klee_assume(a%d >= %s);" % (i, lo))
```

Result, verified against `arm_math_f16.h`: **84 drivers, 3 skipped**
(`arm_linear_interp_f16`, `arm_bilinear_interp_f16`,
`arm_gaussian_naive_bayes_predict_f16`, each on its own instance struct).

### `common/build-lib.sh`

A `case` arm:

```bash
  cmsisdsp) VER=git; REPO=https://github.com/ARM-software/CMSIS-DSP ;;
```

a builder, beside `build_blis` and `build_openlibm`:

```bash
  build_cmsisdsp() {  # <destination> <cc> <cflags>
    local dest=$1 cc=$2 cflags=$3
    rm -rf "$dest"
    cp -a "$WORK/$LIB-src" "$dest"
    # CMSIS-DSP's float16_t is __fp16, and it exists at all only when the
    # compiler defines __ARM_FP16_FORMAT_IEEE. x86-64 clang does not, so
    # every f16 source compiles to nothing -- and forcing the macro is worse,
    # because __fp16 there is a storage type clang refuses as a parameter or
    # a return type, which 24 parameters and 17 return types of this API are.
    # _Float16 is the same format and is a first-class arithmetic type. The
    # library's own scalar fallbacks already cast to _Float16 explicitly, so
    # nothing in the sources depends on which spelling the typedef gets.
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
    # __GNUC_PYTHON__, which drops the CMSIS-Core dependency. The result is
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
```

and a line in the archive/header table:

```bash
    cmsisdsp) BUILD=build_cmsisdsp; ARCS=(libCMSISDSP.a); HDR=arm_math_f16.h ;;
```

**The flag that matters is in the two `$BUILD` invocations, not in the case
arm.** Both need `-fexcess-precision=16` added:

```bash
    "$BUILD" "$WORK/$LIB-bc" wllvm \
      "-O2 -g -fexcess-precision=16 -fno-vectorize -fno-slp-vectorize \
       -fno-openmp-simd -Wno-implicit-function-declaration"
...
    "$BUILD" "$WORK/$LIB-cov" "$NATIVE_CC" \
      "-O0 -g -fexcess-precision=16 -fprofile-instr-generate -fcoverage-mapping \
       -fno-openmp-simd -Wno-implicit-function-declaration"
```

Without it, `s += x[i]*y[i]` compiles to `llvm.fmuladd.f32` and the corpus
measures binary32 while claiming binary16. With it, both builds round per
operation and agree — verified on clang 16 at `-O2` and on `/usr/bin/clang` 21
at `-O0`, which are the two compilers `build-lib.sh` actually uses. It is
inert for every existing library, since none of them has a `_Float16`.

`-ffp-contract=off` is **not** wanted: `llvm.fmuladd.f16` is an FMA with a
single rounding, KLEE implements it as `Expr::FMA`, and all three solvers have
the operation natively. Leaving contraction on is the more interesting query.

### `common/build-drivers.sh`

```bash
  cmsisdsp) INCBC=$WORK/cmsisdsp-bc/inst/include/CMSIS-DSP
            INCCOV=$WORK/cmsisdsp-cov/inst/include/CMSIS-DSP
            LIBS="$WORK/cmsisdsp-cov/inst/lib64/libCMSISDSP.a" ;;
```

The generated drivers carry `#define FP_BENCH_X86_FLOAT16 1` and
`#define __GNUC_PYTHON__ 1` themselves (that is what the `"defines"` key in the
generator config is for), so no flag change is needed here. The `-O0` driver
compile is fine as it stands: the drivers contain no arithmetic.

### `common/sweep-all.sh`

```bash
LIBS=(  "gsl          10"
        "openlibm      3"
        "blis          4"
        "sundials      3"
        "gmp           2"
        "fftw          1"
        "cxsparse      1"
        "cmsisdsp      3"
        "cmsisdsp-f32  3" )
```

A stride of 3 takes 84 drivers to 28, which is the contribution the other small
libraries make. **The strides for the two formats must be equal**, and for the
same reason the two corpora were interleaved in the replay: a sweep that
samples the two formats differently is not a comparison of formats.

### The second library entry: the `_f32` twins

The measurement in this report rests on the matched pair, so the `_f32` twins
are not an optional extra — they are the control. They cost one more
`LIBRARIES` entry over the same build, because CMSIS-DSP writes the same kernel
once per width:

```python
def _cmsis_types(elem):
    """The type map for one CMSIS-DSP precision. float16_t, float32_t and
    float64_t name the same kernels at three widths, so the map is the same
    map with the element type swapped -- which is what makes the f16 and f32
    corpora a controlled pair rather than two different benchmarks."""
    return {
        elem:            lambda n: sym_scalar(elem, n),
        elem + " *":     lambda n: sym_array(elem, n, F16_N),
        "q15_t *":       lambda n: sym_array("q15_t", n, F16_N),
        "uint32_t *":    lambda n: sym_array("uint32_t", n, F16_N),
        "int32_t *":     lambda n: sym_array("int32_t", n, F16_N),
        "uint32_t":      lambda n: sym_scalar("uint32_t", n, bound=str(F16_N)),
        "uint16_t":      lambda n: sym_scalar("uint16_t", n, bound=str(F16_N)),
        "uint8_t":       lambda n: sym_scalar("uint8_t", n, bound="2"),
        "int32_t":       lambda n: sym_scalar("int32_t", n, bound=str(F16_N)),
        "int":           lambda n: sym_scalar("int", n, bound=str(F16_N)),
    }

CMSISDSP_TYPES   = dict(_cmsis_types("float16_t"),
                        **{"float32_t *": lambda n: sym_array("float32_t", n, F16_N),
                           "float64_t *": lambda n: sym_array("float64_t", n, F16_N),
                           "arm_matrix_instance_f16 *": cmsis_matrix("f16")})
CMSISDSP32_TYPES = dict(_cmsis_types("float32_t"),
                        **{"float16_t *": lambda n: sym_array("float16_t", n, F16_N),
                           "float64_t *": lambda n: sym_array("float64_t", n, F16_N),
                           "arm_matrix_instance_f32 *": cmsis_matrix("f32")})
```

with `cmsis_matrix` taking the suffix, and a second entry differing from the
first in three lines:

```python
    "cmsisdsp-f32": {
        "header": "arm_math.h",
        "internal": {},
        "prefixes": ("arm_",),
        "types": CMSISDSP32_TYPES,
        "select": r"_f32$",
        "exclude": r"(_init_|_init_f32$|typecast|bitreversal)",
        "min": {...same...},
        "defines": {"__GNUC_PYTHON__": "1", "FP_BENCH_X86_FLOAT16": "1"},
        "includes": ["<arm_math.h>", "<arm_math_f16.h>"],
    },
```

`build-drivers.sh` needs `cmsisdsp-f32` pointed at the same `cmsisdsp-bc` /
`cmsisdsp-cov` trees; nothing else changes, since both formats come out of one
build of one library.

### One script that does not exist yet: dumping a query corpus

`split-queries.py` and `replay-queries.sh` are both in `common/`, but nothing
in the pipeline *produces* the dump they consume — `RESULTS.md`'s 1,112 queries
were evidently dumped by hand. The measurement in this report needed about
fifteen lines, and they are worth keeping as `common/dump-queries.sh`:

```bash
# dump-queries.sh <lib> <out-dir> -- one .smt2 per driver, then split
for bc in "$WORK/$LIB/obj"/*.bc; do
  n=$(basename "$bc" .bc)
  "$KLEE" --output-dir=/tmp/dq-$n --solver-backend=bitwuzla --search=dfs \
    --max-time="${BUDGET}s" --max-solver-time="${MAX_SOLVER_TIME}s" \
    --debug-bitwuzla-dump-queries="$OUT/$n.smt2" $LINK_LIBM "$bc" > /dev/null 2>&1
  "$HERE/split-queries.py" "$OUT/$n.smt2" "$OUT/split/$n" q
done
```

and one thing the analysis needs that `replay-queries.sh` does not do: when two
corpora are being compared, **interleave them in a single directory** so each
query and its counterpart are timed adjacently. `find | sort` does the rest, as
long as the format is the *last* component of the filename.

### And in KLEE, before any of it means anything

* Rebuild `/mnt/baranem/klee-float/3.2-buildtest` — the binary `build-lib.sh`
  and `run-one.sh` default to predates binary16 support and fails
  `fadd half` outright.
* Teach `IntrinsicCleaner` not to hand `f16` to
  `IntrinsicLowering::LowerIntrinsicCall`. LLVM 16's
  `ReplaceFPIntrinsicWithCall` has no `HalfTyID` case; the effect is a call to
  a binary32 libm function with `half` arguments, which either fails KLEE's
  own module verifier (`arm_atan2_f16`) or, once klee-uclibc's `libm.a`
  resolves the name, produces a spurious `memory error: out of bound pointer`.
  The clean fix is the one `sqrtf16`/`fabsf16` already got: handle
  `llvm.copysign`, `llvm.floor`, `llvm.ceil`, `llvm.trunc`, `llvm.round`,
  `llvm.rint`, `llvm.minnum` and `llvm.maxnum` at `f16` in the Executor
  rather than lowering them. Three of the 84 CMSIS-DSP drivers are blocked on
  this today (`arm_atan2_f16`, `arm_mfcc_f16`, `arm_vlog_f16`); a wider
  corpus would hit more.
* `llvm.experimental.constrained.*` should error rather than assert. It is
  what `#pragma STDC FENV_ACCESS ON` produces, which is what every
  correctly-rounded libm is written with, and today it aborts the process at
  `Executor.cpp:1289`.
* `IndependentSolver`'s `assertCreatedPointEvaluatesToTrue` needs the
  attention FP_PORT_2026.md already flags. It killed 2 of 28 drivers here with
  `--z3-array-ackermannize` off, on Bitwuzla but not STP, and
  `--use-independent-solver=false` makes it go away — which is not a fix, it
  is a much slower solver stack.
* There is no host libm at binary16 and nothing supplies one — verified:
  `expf16`, `logf16`, `powf16`, `sinf16` and `fmaf16` all reach KLEE as
  unresolved external calls, and klee-uclibc has none of them. `sqrtf16` and
  `fabsf16` work, through KLEE's own substitution. Any candidate that *calls*
  a binary16 elementary function rather than implementing one has no path.
  CMSIS-DSP does not: its `arm_vlog_f16` and `arm_vexp_f16` promote to float
  and call `logf`/`expf`, which klee-uclibc supplies — and note the price of
  that, which `arm_minkowski_distance_f16` pays: the query that times out in
  a binary16 driver comes out of a binary64 `pow`.

---

## What was verified, and what was not

**Verified by running**, in `/mnt/baranem/fp_bench-work/fp16-probe/`:

* the headline measurement — 30 f16 drivers and their 30 retyped f32 twins
  built and run under KLEE with `--debug-bitwuzla-dump-queries`, split with
  `common/split-queries.py`, and 1,660 files replayed through
  `common/replay-queries.sh` against the STP and Bitwuzla binaries that script
  defaults to (`analyse.py` and `opsplit.py` in the working area do the
  arithmetic);
* the verdict-column check — 0 errors, 0 `other`, 0 sat/unsat disagreements
  over 1,646 answered queries, so the `(error …)`-reads-as-a-fast-solve trap
  did not fire;
* the twenty-four isolated-operator queries at four widths, all `unsat`, both
  solvers agreeing;
* every clang IR claim (compile, `llvm-dis`, grep) for all ten candidates;
* the CMSIS-DSP, CMSIS-NN, HDF5 and OpenBLAS x86-64 builds, and the CORE-MATH
  per-source compile;
* driver generation (84 CMSIS-DSP drivers from a copy of the real
  `gen-drivers.py` with the `cmsisdsp` entry and the `min` key), and 84/84
  driver bitcode builds;
* 28 CMSIS-DSP KLEE runs, 6 CMSIS-NN runs, 3 HDF5 runs;
* the KLEE binary16 intrinsic census (fourteen `__builtin_*f16` probes) and
  the stale-versus-current KLEE binary comparison.

**Inferred from reading**, not run: XNNPACK's behaviour under KLEE (no driver
reaches f16 arithmetic to run); KleidiAI beyond the two kernels that could be
forced to compile; and whether the `build-lib.sh`, `build-drivers.sh` and
`sweep-all.sh` diffs work end to end inside the repository — nothing under
`/home/avj/clones/fp_bench/` was touched, and the builds were exercised as
their constituent commands in the working area.

**Which numbers can carry weight.** The replayed-query numbers can: both
solvers see the same file, they run back to back per query, and the two formats
were interleaved so a slow patch of machine falls on both. The isolated-operator
timings are best-of-three and are order-of-magnitude. **The in-KLEE wall clocks
are feasibility signals only** — the fp128 agent was running KLEE on this
machine throughout, and `README.md` is explicit that wall-clock sweeps are not
robust to that. No sweep was run.
