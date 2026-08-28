# Why these libraries, and not the other sixteen

The binary16 and binary128 arms were chosen by surveying twenty candidate
projects and measuring them, not by reading their documentation. This records
what was measured, because most of the rejections are non-obvious and several
of the projects that look strongest on paper produce nothing.

The full workings are in `selection/fp16-candidates.md` and
`selection/fp128-candidates.md`.

## The test each candidate had to pass

The suite's own criteria, from `README.md`: the API has to be generatable by
`common/gen-drivers.py`, the library has to build to bitcode with the clang
KLEE was built against, its drivers have to reach the arithmetic and finish
inside the budget, and they have to *ask the solver something*.

For a width arm there is one more, and it is the one that does the work:

    llvm-dis lib.bc | grep -c 'fcmp .*<the format>'

A library can be full of floating-point arithmetic and never branch on a value,
in which case it contributes coverage and no solver load. That single count
separated every candidate below, and it takes about an hour per library.

## Accepted

| | why |
| --- | --- |
| **CMSIS-DSP** (binary16, +f32 control) | The only surveyed project whose half-precision API is broad, native and buildable for x86-64. 79 kernels exist at both widths, written once per width by the library's own authors, so the matched pair costs nothing to construct. |
| **HDF5** (binary16, +f32 control) | The purest binary16 queries anywhere in the survey: its conversion range checks are `fcmp half` and nothing else -- 1,742 binary16 terms and zero of any other sort in one driver's queries. |
| **f2cblaslapack** (binary128, +f64/f32/f16) | 11,058 `fcmp fp128` and 5,899 `fdiv fp128`, against FFTW-quad's 0 and 10. Ships the same routines at four widths, which makes it the corpus's only four-width axis over identical code. |
| **Cuba** (binary128) | Cuhre's adaptive convergence test is a quad `fp.div` with a symbolic denominator -- the ~227-bit division that is the reason binary128 is worth reaching. |

**FFTW at binary128** is in the corpus too, and is the instructive case: it
builds, it runs, its 27 drivers mirror the binary64 arm exactly, and it has
**zero** `fcmp fp128` in 38,000 quad operations. A transform never branches on
a value it is transforming. It contributes coverage, not solver load, and it is
in `sweep-all.sh` on that understanding.

## Rejected, with the measurement that settled it

**Emit no arithmetic in the target format on x86-64** -- the token for the
format does not appear in the live kernel's IR at all: **XNNPACK** (the live
kernel is `f32acc`), **SIMDe** (a misspelled macro forces the portable f32 path),
**NumKong** (`nk_f16_t` is an `unsigned short`), **OpenBLAS** (its live SHGEMM
kernel).

**Do not build for this target:** **KleidiAI** -- 61 of its 62 f16 sources
`#error` on non-AArch64 and there is no portable-C kernel; **PULP-TrainLib** --
its `fp16` is a PULP-GCC RISC-V type clang rejects, and it needs a `pmsis.h`
that is not in the repository.

**CORE-MATH**, which the survey ranked third, has two problems and only one is
fixable: 41 of its 43 binary16 sources abort KLEE on
`llvm.experimental.constrained.*`, which is a KLEE gap; and **2 of the 43
contain any binary16 arithmetic**, the rest being a `_Float16` API over
binary32 and binary64 evaluation, which is not.

**CMSIS-NN** builds cleanly with no patch and has genuine half arithmetic, and
was still dropped on measurement: at `-O2` all 52 `fcmp half` in
`arm_maximum_f16` are consumed by `select`, and five of six hand-written
drivers dumped zero queries.

**libquadmath** as a library of its own: its transcendentals do not finish --
`sinq` reduces its argument through a 5,312-byte table indexed by a symbolic
value -- and the two parts worth driving, `strtoflt128` and the printf path,
are exactly the two this suite cannot build with clang. See "Quad precision" in
`README.md`.

**MPFR** with `--enable-float128`: two functions and about twenty `fcmp fp128`
between them, roughly half integer limb work, and it will not configure with
clang at all (`GMP_NUMB_BITS and sizeof(mp_limb_t) are not consistent`).

**PETSc, Surface Evolver, galculator, Perl 5** -- each rejected for a reason of
its own, in `selection/fp128-candidates.md`. PETSc's is worth singling out
because `README.md` had it wrong: MPI is *not* the barrier, since `--with-mpi=0`
and `--with-precision=__float128` build clean under clang 16. The barrier is
that `construct.py` models a constructor as one call where PETSc needs four, so
1,579 drivers run, return, and measure nothing.

## The one that cannot be done with a flag

The obvious shortcut -- compile the whole corpus with `-Dfloat=_Float16` or
`-Ddouble=__float128` and skip the survey -- does not work, and fails silently.
`README.md` records the demonstration. A precision axis needs a library written
through a numeric type of its own; it cannot be imposed from outside.
