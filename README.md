# fp_bench

Does STP decide floating-point queries faster than Bitwuzla, on real numerical
code rather than on kernels written to be hard?

Two libraries, each driven the way the APSEC floating-point-solver paper drove
GSL: one driver per API function, every argument symbolic, and the measurement
is how much of that function KLEE's generated tests reach when replayed
natively. There is no bug oracle and none is wanted — coverage under a fixed
budget is the signal, because it is the thing a faster solver actually buys.

| | |
| --- | --- |
| `gsl/` | GSL 2.8 — special functions, CDFs, integration, roots |
| `gmp/` | GMP 6.3.0 — multi-precision integer, rational and float |
| `common/` | the generator, the harness and the reports, shared by both |

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
