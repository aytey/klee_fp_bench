#!/usr/bin/env python3
"""Generate one symbolic driver per library function, from the library's header.

    gen-drivers.py --library gmp --include <dir> --out <dir> [--cflags ...]

Each driver makes every argument of one function symbolic and calls it, which
is what the APSEC floating-point-solver harness did to GSL by hand for 431
functions. Doing it from the header instead means following a new release is a
version bump, and it means the set is complete by construction rather than by
whoever was writing drivers that afternoon.

Signatures come from clang's AST rather than from reading the header as text,
so typedefs are already resolved: GMP declares `mpz_ptr` and `mpz_srcptr`, and
the AST says which is which -- that distinction is the whole of how an output
parameter is told from an input one.

A function is emitted only when every parameter can be made symbolic. The rest
are written to skipped.tsv with the type that stopped them, so what is missing
is a list somebody can read rather than a silence.
"""
import argparse
import json
import os
import re
import subprocess
import sys

from construct import Constructor, make as _make, normalise as cnorm

# --------------------------------------------------------------------------
# How a parameter of a given type becomes something symbolic.
#
# Each entry returns (declarations, argument, needs) for the n-th parameter.
# "needs" names a helper the driver must emit once, if any.
# --------------------------------------------------------------------------


# How many elements a pointer parameter is given. Small on purpose: every
# element is a separate symbolic object, and a length is what the surrounding
# code branches on rather than the values.
ARRAY_N = 8


def sym_array(ctype, name, n=ARRAY_N):
    """A caller-allocated buffer, symbolic in its entirety.

    Fixed length, because the generator cannot know which integer parameter is
    this array's length -- the association is a naming convention, not a type.
    Any integer parameter of a function that takes an array is bounded to the
    same length by build_driver, so a symbolic length cannot walk off the end.
    That is conservative: it costs the ability to explore a length larger than
    the buffer, which is a memory-safety question rather than a floating-point
    one, and this is a floating-point benchmark.
    """
    return (["  %s %s[%d];" % (ctype, name, n),
             '  klee_make_symbolic(%s, sizeof(%s), "%s");' % (name, name, name)],
            name)


def sym_scalar(ctype, name, bound=None):
    """A plain value KLEE can make symbolic directly."""
    lines = ["  %s %s;" % (ctype, name),
             '  klee_make_symbolic(&%s, sizeof(%s), "%s");' % (name, name, name)]
    if bound is not None:
        # A size or a precision reaches an allocator, and a symbolic allocation
        # size is concretised the moment it does. Bound it instead, so the
        # symbolic value is spent on the arithmetic rather than thrown away.
        lines.append("  klee_assume(%s <= %s);" % (name, bound))
        if not ctype.startswith("unsigned") and ctype not in ("size_t",):
            lines.append("  klee_assume(%s >= 0);" % name)
    return lines, name


GMP_TYPES = {
    # Outputs. GMP writes results through a _ptr and only reads a _srcptr, so
    # the convention names them apart and nothing has to be guessed.
    "mpz_ptr":    lambda n: (["  mpz_t %s;" % n, "  mpz_init(%s);" % n], n),
    "mpq_ptr":    lambda n: (["  mpq_t %s;" % n, "  mpq_init(%s);" % n], n),
    "mpf_ptr":    lambda n: (["  mpf_t %s;" % n, "  mpf_init(%s);" % n], n),

    # Inputs. The object itself is never made symbolic -- its _mp_d limb
    # pointer would become a symbolic pointer and KLEE would stop there. A
    # scalar is made symbolic and GMP's own conversion builds the object from
    # it, which is also the path worth exercising: mpf_set_d is the
    # double-to-multiprecision blast.
    "mpz_srcptr": lambda n: (sym_scalar("long", n + "_v")[0] +
                             ["  mpz_t %s;" % n,
                              "  mpz_init_set_si(%s, %s_v);" % (n, n)], n),
    "mpq_srcptr": lambda n: (sym_scalar("double", n + "_v")[0] +
                             ["  mpq_t %s;" % n, "  mpq_init(%s);" % n,
                              "  klee_assume(%s_v == %s_v);" % (n, n),
                              "  mpq_set_d(%s, %s_v);" % (n, n)], n),
    "mpf_srcptr": lambda n: (sym_scalar("double", n + "_v")[0] +
                             ["  mpf_t %s;" % n,
                              "  mpf_init_set_d(%s, %s_v);" % (n, n)], n),

    "double":        lambda n: sym_scalar("double", n),
    "long":          lambda n: sym_scalar("long", n),
    "unsigned long": lambda n: sym_scalar("unsigned long", n),
    "int":           lambda n: sym_scalar("int", n),
    "unsigned int":  lambda n: sym_scalar("unsigned int", n),
    # Sizes and precisions: bounded, for the reason in sym_scalar.
    "mp_bitcnt_t":   lambda n: sym_scalar("mp_bitcnt_t", n, bound="256"),
    "mp_size_t":     lambda n: sym_scalar("mp_size_t", n, bound="16"),
    "mp_exp_t":      lambda n: sym_scalar("mp_exp_t", n, bound="64"),
    "size_t":        lambda n: sym_scalar("size_t", n, bound="16"),
}

ARRAY_TYPES = {
    "double *":       lambda n: sym_array("double", n),
    "float *":        lambda n: sym_array("float", n),
    "int *":          lambda n: sym_array("int", n),
    "unsigned int *": lambda n: sym_array("unsigned int", n),
}

GMP_TYPES.update({})

GSL_TYPES = {
    "double":            lambda n: sym_scalar("double", n),
    "float":             lambda n: sym_scalar("float", n),
    "long double":       lambda n: sym_scalar("long double", n),
    "int":               lambda n: sym_scalar("int", n),
    "unsigned int":      lambda n: sym_scalar("unsigned int", n),
    "long":              lambda n: sym_scalar("long", n),
    "unsigned long":     lambda n: sym_scalar("unsigned long", n),
    "size_t":            lambda n: sym_scalar("size_t", n, bound="16"),
    # The result struct every gsl_sf_*_e function writes through.
    "gsl_sf_result *":   lambda n: (["  gsl_sf_result %s;" % n], "&" + n),
    "gsl_sf_result_e10 *":
                         lambda n: (["  gsl_sf_result_e10 %s;" % n], "&" + n),
    # A mode selects how hard the special-function code tries; symbolic is
    # meaningful and cheap.
    "gsl_mode_t":        lambda n: sym_scalar("gsl_mode_t", n, bound="2"),
}
GSL_TYPES.update(ARRAY_TYPES)

# The order of a SUNDIALS system. Square, so that a dense matrix and the
# vectors it acts on conform.
SUN_N = 8


def _sun_ctx(name):
    """A context. Everything SUNDIALS builds needs one."""
    return ["  SUNContext %s_c;" % name,
            "  SUNContext_Create(SUN_COMM_NULL, &%s_c);" % name]


def sun_context(name):
    return (["  SUNContext %s;" % name,
             "  SUNContext_Create(SUN_COMM_NULL, &%s);" % name], name)


def sun_vector(name):
    """A serial vector of SUN_N symbolic entries."""
    return (_sun_ctx(name) + [
        "  N_Vector %s = N_VNew_Serial(%d, %s_c);" % (name, SUN_N, name),
        '  klee_make_symbolic(N_VGetArrayPointer(%s), %d * sizeof(sunrealtype),'
        ' "%s_d");' % (name, SUN_N, name)], name)


def sun_matrix(name):
    """A dense SUN_N x SUN_N matrix, symbolic in every entry.

    Every entry matters here. The generic accessor mechanism fills an object
    with ARRAY_N elements, which for a square matrix is one row: the other 56
    stayed as SUNDenseMatrix left them, which is zero, and a matrix that is
    zero below its first row is singular. SUNLinSolSetup_Dense would take its
    first zero pivot and return, so the partial pivoting -- the reason this
    layer is worth measuring at all -- never ran.
    """
    return (_sun_ctx(name) + [
        "  SUNMatrix %s = SUNDenseMatrix(%d, %d, %s_c);" % (name, SUN_N, SUN_N, name),
        '  klee_make_symbolic(SUNDenseMatrix_Data(%s), %d * sizeof(sunrealtype),'
        ' "%s_d");' % (name, SUN_N * SUN_N, name)], name)


def sun_linsol(name):
    """A dense linear solver, over a matrix and vector it conforms to.

    Built explicitly because the search will not: it takes the yielder with
    fewest arguments, which handed SUNLinSolSolve_Dense a band solver.
    """
    v, _ = sun_vector(name + "_y")
    m, _ = sun_matrix(name + "_a")
    return (v + m + _sun_ctx(name) + [
        "  SUNLinearSolver %s = SUNLinSol_Dense(%s_y, %s_a, %s_c);"
        % (name, name, name, name)], name)


SUNDIALS_TYPES = {
    "N_Vector":        sun_vector,
    "SUNMatrix":       sun_matrix,
    "SUNLinearSolver": sun_linsol,
    "SUNContext":      sun_context,
    "double":       lambda n: sym_scalar("double", n),
    "sunrealtype":  lambda n: sym_scalar("sunrealtype", n),
    "int":          lambda n: sym_scalar("int", n),
    "unsigned int": lambda n: sym_scalar("unsigned int", n),
    "long":         lambda n: sym_scalar("long", n),
    "sunindextype": lambda n: sym_scalar("sunindextype", n, bound="8"),
    "size_t":       lambda n: sym_scalar("size_t", n, bound="8"),
    "SUNComm":      lambda n: (["  SUNComm %s = SUN_COMM_NULL;" % n], n),
}
SUNDIALS_TYPES.update(ARRAY_TYPES)

FFTW_TYPES = {
    "double":       lambda n: sym_scalar("double", n),
    "int":          lambda n: sym_scalar("int", n, bound="8"),
    "unsigned int": lambda n: sym_scalar("unsigned int", n, bound="8"),
    # FFTW's planner is told to estimate rather than measure: measuring runs
    # timing experiments, which under symbolic execution is both meaningless
    # and unbounded.
    "unsigned":     lambda n: (["  unsigned %s = FFTW_ESTIMATE;" % n], n),
    # fftw_complex is double[2], so an array of them is an array of doubles
    # twice as long.
    "fftw_complex *": lambda n: (["  fftw_complex %s[%d];" % (n, ARRAY_N),
                                  '  klee_make_symbolic(%s, sizeof(%s), "%s");'
                                  % (n, n, n)], n),
}
FFTW_TYPES.update(ARRAY_TYPES)

# --------------------------------------------------------------------------
# FFTW at binary128: the same library through its fftwq_* API, with R =
# __float128. This map is the binary64 one with the element type changed, and
# that is the point -- the two arms differ in the format and in nothing else,
# so a difference between them is a difference the solver made.
# --------------------------------------------------------------------------
FFTWQ_TYPES = {
    "__float128":   lambda n: sym_scalar("__float128", n),
    # FFTW's cost model stays binary64 at every precision: fftwq_flops writes
    # through a double *, and fftwq_set_timelimit takes a double.
    "double":       lambda n: sym_scalar("double", n),
    "int":          lambda n: sym_scalar("int", n, bound="8"),
    "unsigned int": lambda n: sym_scalar("unsigned int", n, bound="8"),
    "unsigned":     lambda n: (["  unsigned %s = FFTW_ESTIMATE;" % n], n),
    "__float128 *": lambda n: sym_array("__float128", n),
    # fftwq_complex is __float128[2], the shape fftw_complex has at binary64.
    "fftwq_complex *": lambda n: (["  fftwq_complex %s[%d];" % (n, ARRAY_N),
                                   '  klee_make_symbolic(%s, sizeof(%s), "%s");'
                                   % (n, n, n)], n),
}
FFTWQ_TYPES.update(ARRAY_TYPES)

# The order of a BLIS matrix. Small, for the reason ARRAY_N is small.
BLIS_N = 4

# BLIS's object API hands every operand over as an opaque obj_t, and what shape
# that object has to be is a property of the *role* the parameter plays rather
# than of its type. Its argument checks are not advisory: bli_gemm requires
# alpha and beta to be 1x1 and a, b, c to be matrices of a floating type, and
# calls bli_abort() on anything else.
#
# So the constructor search cannot get this right, and its answer -- the
# constructor with fewest arguments, bli_obj_create_1x1 -- was right for the
# wrong reason. 1x1 is the one shape that is simultaneously a valid scalar, a
# valid vector and a valid matrix, so it passes every check and computes
# nothing: 484 drivers of dense linear algebra on single elements.
#
# What makes a real shape inferable is that BLIS names its parameters by role,
# consistently, across all 499 of them. alpha and beta are scalars, x and y are
# vectors, a and b and c are matrices. That is a fact about BLIS rather than
# about C, which is why it lives here and not in the search.
BLIS_ROLES = {
    "alpha": "s", "beta": "s", "alpha_conj": "s", "rho": "s", "chi": "s",
    "psi": "s", "kappa": "s", "norm": "s", "absq": "s", "index": "s",
    "alphax": "s", "alphay": "s", "a11": "s", "b11": "s", "c11": "s",
    "x": "v", "y": "v", "z": "v", "w": "v", "xt": "v",
    "a": "m", "b": "m", "c": "m", "p": "m", "ah": "m", "at": "m",
    "a1x": "m", "bx1": "m",
    # bli_amaxv writes an element *index*, and checks the object it writes it
    # into has an integer datatype rather than a floating one.
    "index": "i",
}
BLIS_SHAPES = {"s": (1, 1, "double", "BLIS_DOUBLE"),
               "v": (BLIS_N, 1, "double", "BLIS_DOUBLE"),
               "m": (BLIS_N, BLIS_N, "double", "BLIS_DOUBLE"),
               "i": (1, 1, "gint_t", "BLIS_INT")}


# Structure BLIS requires of an operand but does not carry in its type. A
# triangular solve wants a triangular object, and one built as general aborts
# in bli_trsm_check -- which is why twenty of BLIS's most interesting
# operations, the whole tr/he/sy family, ran nothing at all.
#
# Both halves are readable off the operation's own name. The prefix says what
# structure: tr triangular, he Hermitian, sy symmetric. And which operand
# carries it is positional in BLAS: a rank-k update accumulates *into* a
# structured C, and everything else reads a structured A.
BLIS_STRUC = {"tr": "BLIS_TRIANGULAR", "he": "BLIS_HERMITIAN",
              "sy": "BLIS_SYMMETRIC"}

# KLEE executes one thread and has no pthread implementation to call out to,
# so an external call to any of these fails the state outright. BLIS runs its
# initialisation through pthread_once and guards its runtime with a mutex, so
# without these four every BLIS driver died on its first call into the library
# -- and died as a failed external call, which is not a result about anything.
#
# Defining them in the driver puts them in the module KLEE is given, where they
# are ordinary code it can execute. The replay binary picks them up too, which
# is what keeps the two halves agreeing; both are single-threaded, so a once is
# a flag and a lock is nothing.
BLIS_PREAMBLE = [
    "int pthread_once(pthread_once_t *o, void (*fn)(void)) {",
    "  if (!*(int *)o) { *(int *)o = 1; fn(); }",
    "  return 0;",
    "}",
    "int pthread_mutex_init(pthread_mutex_t *m, const pthread_mutexattr_t *a)",
    "{ (void)m; (void)a; return 0; }",
    "int pthread_mutex_lock(pthread_mutex_t *m) { (void)m; return 0; }",
    "int pthread_mutex_unlock(pthread_mutex_t *m) { (void)m; return 0; }",
    "",
]


def blis_post(pub, pname):
    m = re.match(r"bli_(tr|he|sy)\w*$", pub)
    if not m:
        return []
    target = "c" if re.search(r"r2?k$", pub) else "a"
    if pname != target:
        return []
    return ["  bli_obj_set_struc(%s, &%%s);" % BLIS_STRUC[m.group(1)],
            "  bli_obj_set_uplo(BLIS_LOWER, &%s);"]


def blis_obj(name, pname=""):
    """A BLIS object of the shape its parameter's role requires.

    The buffer is attached rather than allocated, which is the other half of
    the defect: bli_obj_create mallocs storage and leaves it as it found it, so
    every driver built that way computed on whatever was already there. Here
    the storage is the driver's own array and it is symbolic, which is the only
    reason any of this reaches the solver.

    Column-major, so rs is 1 and cs is the row count -- a stride pair that has
    to agree with the dimensions, which is the other thing the search cannot
    know. An unrecognised role falls back to 1x1: no worse than before, and it
    still conforms.
    """
    m, n, ctype, dt = BLIS_SHAPES[BLIS_ROLES.get(pname, "s")]
    return (["  %s %s_b[%d];" % (ctype, name, m * n),
             '  klee_make_symbolic(%s_b, sizeof(%s_b), "%s_b");'
             % (name, name, name),
             "  obj_t %s;" % name,
             "  bli_obj_create_with_attached_buffer(%s, %d, %d, %s_b,"
             " 1, %d, &%s);" % (dt, m, n, name, m, name)],
            "&" + name)


BLIS_TYPES = {
    "double":       lambda n: sym_scalar("double", n),
    "float":        lambda n: sym_scalar("float", n),
    "int":          lambda n: sym_scalar("int", n),
    # Dimensions and strides. Bounded, and they reach indexing arithmetic, so
    # a wrong one is an out-of-bounds rather than a path.
    "dim_t":        lambda n: sym_scalar("dim_t", n, bound="4"),
    "inc_t":        lambda n: sym_scalar("inc_t", n, bound="4"),
    "doff_t":       lambda n: sym_scalar("doff_t", n, bound="4"),
    "obj_t *":      blis_obj,
}
BLIS_TYPES.update(ARRAY_TYPES)

# OpenLibm is the shape this generator was waiting for: every function takes
# and returns machine floats, and nothing has to be constructed.
#
# There is deliberately no "long double" maker. On x86-64 that is the x87
# 80-bit format, which is not one of the IEEE widths the solvers reason about,
# and the functions that use it are skipped by the ordinary mechanism -- they
# appear in skipped.tsv with "long double" as the reason, rather than being
# filtered out by a name rule. A name rule would have been wrong anyway: the
# long-double functions end in 'l', and so do ceil and floor.
OPENLIBM_TYPES = {
    "double":       lambda n: sym_scalar("double", n),
    "float":        lambda n: sym_scalar("float", n),
    "int":          lambda n: sym_scalar("int", n),
    "long":         lambda n: sym_scalar("long", n),
    "long long":    lambda n: sym_scalar("long long", n),
}
OPENLIBM_TYPES.update(ARRAY_TYPES)

# The order of a CXSparse system. Small, because every entry is a separate
# symbolic value and an LU of a dense 4x4 already branches on sixteen of them.
CS_N = 4


def cs_matrix(name):
    """A dense CS_N x CS_N matrix in compressed-column form, symbolic in every
    value and concrete in every index.

    This is the whole reason a hand-written maker is needed. A compressed-column
    matrix is only valid if its column pointers are monotone, start at zero, end
    at nzmax, and its row indices are all below m -- relationships *between*
    arguments, which is the one class of precondition the search says it cannot
    meet. Nothing about the type says any of it.

    Concrete structure is also the right answer rather than a concession. A
    symbolic index reaches an allocation or an array subscript and gets
    concretised or reported as a memory error, spending the run on questions
    about integers; a symbolic value reaches cs_lu's pivot test, which is
    `if ((t = CS_ABS (x [i])) > a)` -- a comparison of two floating-point
    magnitudes, once per column, and the reason this library is here.

    Dense-in-sparse, rather than a band or a tridiagonal, because it leaves
    the pivot search a free choice in every column.
    """
    n, nz = CS_N, CS_N * CS_N
    # Through cs_di_spalloc rather than as three stack arrays. Several of these
    # functions resize the matrix they are given -- cs_dupl and cs_fkeep, and
    # so droptol and dropzeros through it, all call cs_sprealloc -- and
    # realloc() on a stack pointer is undefined. It took KLEE itself down with
    # a core dump rather than being reported as a memory error.
    # values=1 asks for the numerical array, triplet=0 for compressed-column.
    return (["  cs_di *%s = cs_di_spalloc(%d, %d, %d, 1, 0);"
             % (name, n, n, nz),
             "  for (int k_ = 0; k_ <= %d; k_++) %s->p[k_] = k_ * %d;"
             % (n, name, n),
             "  for (int k_ = 0; k_ < %d; k_++) %s->i[k_] = k_ %% %d;"
             % (nz, name, n),
             '  klee_make_symbolic(%s->x, %d * sizeof(double), "%s_x");'
             % (name, nz, name)], name)


def cs_analysis(name):
    """The symbolic analysis cs_lu wants.

    Left to the search this is cs_di_sfree -- one argument, returns a cs_dis*,
    and what it returns is NULL because it is the destructor. The recursion
    guard rejects it and the next candidate is cs_di_schol, which computes a
    *Cholesky* analysis: the elimination tree and column counts, not the column
    permutation and nonzero estimates cs_lu reads.
    """
    m, _ = cs_matrix(name + "_a")
    return (m + ["  cs_dis *%s = cs_di_sqr(0, %s_a, 0);" % (name, name)], name)


CXSPARSE_TYPES = {
    "cs_di *":  cs_matrix,
    "cs_dis *": cs_analysis,
    "double":   lambda n: sym_scalar("double", n),
    "int32_t":  lambda n: sym_scalar("int32_t", n, bound=str(CS_N)),
    "int":      lambda n: sym_scalar("int", n, bound=str(CS_N)),
    "double *": lambda n: sym_array("double", n, CS_N),
    "int32_t *": lambda n: sym_array("int32_t", n, CS_N),
}

# --------------------------------------------------------------------------
# CMSIS-DSP, at two widths.
#
# CMSIS-DSP writes the same kernel once per element type -- arm_add_f16,
# arm_add_f32 and arm_add_f64 are one algorithm written three times by the
# library's own authors -- so one type map with the element swapped gives a
# matched *pair* of corpora rather than two different benchmarks. That is what
# makes the binary16 arm measurable: the f32 twin is the control, and the
# format is the only thing that differs between them. 23 of the 30 branching
# drivers issue an identical query count at both widths.
#
# float16_t is _Float16 here rather than __fp16. They are the same format, but
# on x86-64 __fp16 is a storage type clang refuses as a parameter or a return
# type at all -- which 24 parameters and 17 return types of this API are.
# build-lib.sh redirects the typedef; see there.
# --------------------------------------------------------------------------
F16_N = 8
# The order of a CMSIS-DSP matrix, small for the reason ARRAY_N is small.
MAT_N = 4


def cmsis_matrix(suffix):
    """A dense MAT_N x MAT_N matrix, symbolic in every entry.

    arm_mat_init_* takes the dimensions and the buffer separately, so the
    buffer's length is a relationship between arguments -- the one class of
    precondition this generator says up front that it cannot meet. Square, so
    that every matrix a driver builds conforms with every other.
    """
    elem = {"f16": "float16_t", "f32": "float32_t"}[suffix]

    def make(name):
        return (["  %s %s_d[%d * %d];" % (elem, name, MAT_N, MAT_N),
                 '  klee_make_symbolic(%s_d, sizeof(%s_d), "%s_d");'
                 % (name, name, name),
                 "  arm_matrix_instance_%s %s;" % (suffix, name),
                 "  arm_mat_init_%s(&%s, %d, %d, %s_d);"
                 % (suffix, name, MAT_N, MAT_N, name)],
                "&" + name)
    return make


def _cmsis_types(elem):
    """The type map for one CMSIS-DSP precision."""
    return {
        elem:         lambda n: sym_scalar(elem, n),
        elem + " *":  lambda n: sym_array(elem, n, F16_N),
        "q15_t *":    lambda n: sym_array("q15_t", n, F16_N),
        "uint32_t *": lambda n: sym_array("uint32_t", n, F16_N),
        "int32_t *":  lambda n: sym_array("int32_t", n, F16_N),
        # CMSIS-DSP spells its lengths uint32_t, which build_driver's has_array
        # rule does not cover, so they are bounded here instead.
        "uint32_t":   lambda n: sym_scalar("uint32_t", n, bound=str(F16_N)),
        "uint16_t":   lambda n: sym_scalar("uint16_t", n, bound=str(F16_N)),
        "uint8_t":    lambda n: sym_scalar("uint8_t", n, bound="2"),
        "int32_t":    lambda n: sym_scalar("int32_t", n, bound=str(F16_N)),
        "int":        lambda n: sym_scalar("int", n, bound=str(F16_N)),
    }


CMSISDSP_TYPES = dict(_cmsis_types("float16_t"))
CMSISDSP_TYPES.update({
    "float32_t *": lambda n: sym_array("float32_t", n, F16_N),
    "float64_t *": lambda n: sym_array("float64_t", n, F16_N),
    "arm_matrix_instance_f16 *": cmsis_matrix("f16"),
})

CMSISDSP32_TYPES = dict(_cmsis_types("float32_t"))
CMSISDSP32_TYPES.update({
    "float16_t *": lambda n: sym_array("float16_t", n, F16_N),
    "float64_t *": lambda n: sym_array("float64_t", n, F16_N),
    "arm_matrix_instance_f32 *": cmsis_matrix("f32"),
})

# A length of zero underflows: arm_max_f16 computes blockSize - 1U and walks
# off the buffer. That is a precondition of the API rather than a bug, and
# nothing in the type says it.
CMSIS_MIN = {"blockSize": "1", "numRows": "1", "numCols": "1",
             "numSamples": "1", "nbVectors": "1", "vecDim": "1"}

# What is left out, and why: the _init_ functions are how the constructor
# search builds an arm_cfft_instance_f16 or an arm_fir_instance_f16 and are not
# drivers of their own; typecast is a bit reinterpretation and bitreversal is
# integer index arithmetic.
#
# arm_cfft_radix2_* goes for a different reason: CMSIS-DSP declares it in the
# header and compiles it into neither build -- arm_cfft_radix2_init_f16 and
# _f32 are both absent from the bitcode and from the coverage archive, so the
# driver links against nothing. It is the deprecated CFFT interface, and the
# radix-4 one beside it is present and is kept.
CMSIS_EXCLUDE = r"(_init_|_init_f(16|32)$|typecast|bitreversal|cfft_radix2)"

# --------------------------------------------------------------------------
# Cuba, at binary128.
#
# Cuba's REALSIZE=16 configuration makes its `cubareal` a __float128 and maps
# its mathematics onto libquadmath. Unlike FFTW's quad arm it *branches* on
# quad values -- Cuhre's adaptive convergence test is a quad fp.div with a
# symbolic denominator -- which is what makes it the binary128 solver load
# rather than a binary128 coverage row.
# --------------------------------------------------------------------------
CUBA_TYPES = {
    "cubareal":      lambda n: sym_scalar("cubareal", n),
    "cubareal *":    lambda n: sym_array("cubareal", n),
    "int":           lambda n: sym_scalar("int", n, bound="4"),
    "int *":         lambda n: sym_array("int", n),
}

# The integrand is a callback rather than a value, so the generator cannot make
# one symbolic: it has to be written. This is the smallest one that puts a
# symbolic quad *inside* the integration -- smooth in x, linear in a symbolic
# coefficient -- so the adaptive algorithms subdivide on a value the solver
# chose rather than on a constant.
CUBA_PREAMBLE = [
    "static cubareal fpb_coeff;",
    "",
    "static int fpb_integrand(const int *ndim, const cubareal x[],",
    "                         const int *ncomp, cubareal f[], void *userdata) {",
    "  (void)ndim; (void)ncomp; (void)userdata;",
    "  f[0] = fpb_coeff * x[0] * x[0];",
    "  return 0;",
    "}",
    "",
]
CUBA_PROLOGUE = [
    '  klee_make_symbolic(&fpb_coeff, sizeof(fpb_coeff), "coeff");',
]

# Callbacks, state files and the counters that decide how long the integration
# runs. epsrel and epsabs are deliberately *not* here: they are cubareal, they
# are what the convergence test divides by, and leaving them symbolic is the
# whole reason this library is in the corpus. maxeval bounds the run instead.
CUBA_PINS = {
    "integrand": "fpb_integrand", "userdata": "NULL", "spin": "NULL",
    "statefile": "NULL", "peakfinder": "NULL", "xgiven": "NULL",
    # ndim is 2 rather than 1 because Cuhre is a cubature rule and has none
    # for one dimension: at ndim=1 it returns immediately, and the driver runs
    # 225 instructions without entering the integration at all. Two is the
    # smallest that works for all four algorithms.
    "ndim": "2", "ncomp": "1", "nvec": "1", "flags": "0", "seed": "0",
    "mineval": "0", "maxeval": "1000",
    "nstart": "50", "nincrease": "50", "nbatch": "50", "gridno": "0",
    "key": "0", "key1": "0", "key2": "0", "key3": "0",
    "maxpass": "1", "nnew": "50", "nmin": "2",
    "border": "0.", "maxchisq": "10.", "mindeviation": ".25",
    "ngiven": "0", "ldxgiven": "0", "nextra": "0",
}

# --------------------------------------------------------------------------
# HDF5's datatype conversions.
#
# HDF5 has no _Float16 in any installed header -- all 34 of its half-precision
# conversion functions are private -- but every one of them is reachable
# through a single public entry point, H5Tconvert(src, dst, nelmts, buf,
# background, plist), whose types are named by hid_t handles rather than by C
# types. An hid_t is an opaque integer whose value must name a registered
# datatype, which the constructor search cannot fabricate, so these drivers are
# written from a table rather than read from a header. That is CXSparse's
# split -- structure by hand, values symbolic -- applied to a datatype instead
# of to a sparse matrix.
#
# What makes it worth the hand-writing is format purity: the queries are
# binary16 and nothing else. No accumulator, no libm, no wider intermediate
# anywhere on the path -- 1,742 binary16 terms and zero of any other sort. For
# a comparison *across* formats that is worth more than the driver count says.
# --------------------------------------------------------------------------
H5_NELMTS = 4

#: tag -> (C type, HDF5 native datatype). The tag is also what HDF5 spells into
#: the conversion function's own name, which is what functions.tsv needs.
H5_TYPES = {
    "_Float16": ("_Float16", "H5T_NATIVE_FLOAT16"),
    "float":    ("float", "H5T_NATIVE_FLOAT"),
    "double":   ("double", "H5T_NATIVE_DOUBLE"),
    "schar":    ("signed char", "H5T_NATIVE_SCHAR"),
    "uchar":    ("unsigned char", "H5T_NATIVE_UCHAR"),
    "short":    ("short", "H5T_NATIVE_SHORT"),
    "ushort":   ("unsigned short", "H5T_NATIVE_USHORT"),
    "int":      ("int", "H5T_NATIVE_INT"),
}

#: The conversions worth driving, with "F" standing for the format this arm is
#: about. The narrow integer destinations are the ones that keep their range
#: checks -- H5T_CONV_Fx_CORE's `*(S) > (ST)(D_MAX)` per element, which is
#: where the comparisons come from. Into int, binary16's whole range fits, the
#: checks fold away and the conversion is a bare fptosi; that pair is here as
#: the contrast rather than left out.
H5_PAIRS = [("F", "schar"), ("F", "uchar"), ("F", "short"), ("F", "ushort"),
            ("F", "int"), ("F", "double"),
            ("schar", "F"), ("uchar", "F"), ("short", "F"), ("ushort", "F"),
            ("double", "F")]


def h5_drivers(cfg):
    """One driver per conversion in H5_PAIRS, at this arm's format."""
    out = []
    for src, dst in H5_PAIRS:
        src = cfg["focus"] if src == "F" else src
        dst = cfg["focus"] if dst == "F" else dst
        if src == dst:
            continue
        sctype, snative = H5_TYPES[src]
        dctype, dnative = H5_TYPES[dst]
        body = [
            "int main(void) {",
            "  /* H5Tconvert converts in place, so the buffer has to be wide",
            "     enough for whichever of the two types is larger. */",
            "  union { %s s[%d]; %s d[%d]; } b;"
            % (sctype, H5_NELMTS, dctype, H5_NELMTS),
            '  klee_make_symbolic(&b, sizeof(b), "src");',
            # volatile, and not klee_warning: the drivers are replayed
            # natively against libkleeRuntest, which has klee_make_symbolic and
            # klee_assume but no klee_warning, so a driver that calls it
            # compiles and then fails to link.
            "  volatile herr_t r_ = H5Tconvert(%s, %s, %d, &b, NULL, H5P_DEFAULT);"
            % (snative, dnative, H5_NELMTS),
            "  return 0;",
            "}",
            "",
        ]
        out.append(("H5Tconvert_%s_%s" % (src.lstrip("_"), dst.lstrip("_")),
                    _source(cfg, body),
                    "H5T__conv_%s_%s" % (src, dst)))
    return out


# --------------------------------------------------------------------------
# f2cblaslapack, at binary128 and binary64.
#
# PETSc's f2c translation of BLAS and LAPACK, which ships a quadruple-precision
# set alongside the usual four. It is the binary128 library that *branches*:
# 5,396 `fcmp fp128` and 2,046 `fdiv fp128` against FFTW's 0 and 10, because
# partial pivoting is a magnitude comparison over the matrix in a loop --
# iqamax's `if (abs(dx[i]) > dmax)`, which qgetf2 calls once per column.
#
# It is also CXSparse's split, for CXSparse's reason. A BLAS call's integer
# arguments are the shape of the matrix -- lda is the distance between columns,
# and a symbolic one is concretised at the first subscript and spends the run
# on integers. So the shape is written and only the values are symbolic, and
# what is not measured is anything that depends on the shape.
#
# The roles below are a fact about LAPACK rather than about C, which is why
# they live here and not in the constructor search -- the same argument
# BLIS_ROLES makes. LAPACK names its arguments by role across all 3,851 of
# them, by a documented convention nobody deviates from.
# --------------------------------------------------------------------------

# The order of a matrix, small for the reason ARRAY_N is small.
F2C_N = 4

#: Integer arguments that are a dimension, and take the matrix order.
F2C_DIMS = {"m", "n", "k", "nrhs", "lda", "ldb", "ldc", "ldz", "ldu", "ldv",
            "ldvt", "ldq", "ldt", "ldx", "ldy", "ldab", "ldwork", "ihi", "kd"}
#: ...a stride or a starting index, and take 1.
F2C_ONES = {"incx", "incy", "inc", "ilo", "kl", "ku"}
#: ...a caller-allocated integer workspace the routine writes through.
F2C_IARR = {"ipiv", "jpvt", "iwork", "ipvt"}


def f2c_int(name, pname):
    """An integer argument, by the role its name gives it."""
    p = (pname or "").rstrip("_")
    if p in F2C_IARR:
        return (["  integer %s[%d] = {0};" % (name, F2C_N * F2C_N)], name)
    if p == "lwork":
        return (["  integer %s = %d;" % (name, F2C_N * F2C_N)], "&" + name)
    if p in F2C_ONES:
        return (["  integer %s = 1;" % name], "&" + name)
    # info is the status the routine writes back; everything else unrecognised
    # is far more often a dimension than not, and a wrong dimension is caught
    # by the routine's own argument check rather than by running off anything.
    v = 0 if p == "info" else F2C_N
    return (["  integer %s = %d;" % (name, v)], "&" + name)


def f2c_real(elem):
    """A floating-point argument: a matrix, a vector or a scalar, all of which
    f2c passes by pointer, so the widest of the three is the safe shape."""
    def make(name, pname):
        return (["  %s %s[%d];" % (elem, name, F2C_N * F2C_N),
                 '  klee_make_symbolic(%s, sizeof(%s), "%s");'
                 % (name, name, name)], name)
    return make


#: The character flags, by name. A symbolic one is not a value to explore but a
#: way to reach xerbla: LAPACK validates every one of them and returns without
#: computing anything if it does not recognise it.
F2C_FLAGS = {
    "uplo": "U", "trans": "N", "transa": "N", "transb": "N", "transr": "N",
    "side": "L", "diag": "N", "norm": "1", "job": "N", "jobz": "N",
    "jobu": "N", "jobvt": "N", "jobvl": "N", "jobvr": "N", "jobq": "N",
    "equed": "N", "fact": "N", "compq": "N", "compz": "N", "direct": "F",
    "storev": "C", "pivot": "V", "type": "G", "way": "N", "sense": "N",
    "balanc": "N", "howmny": "A", "eigsrc": "Q", "initv": "N", "vect": "Q",
    "cmach": "E", "range": "A", "order": "B", "dist": "S", "sym": "N",
    "pack": "N", "matrix": "G",
}


def f2c_char(name, pname):
    v = F2C_FLAGS.get((pname or "").rstrip("_"), "N")
    return (['  char %s[2] = "%s";' % (name, v)], name)


def _f2c_types(elem):
    return {
        elem + " *":   f2c_real(elem),
        "integer *":   f2c_int,
        "char *":      f2c_char,
        "logical *":   lambda n, p: (["  logical %s = 0;" % n], "&" + n),
        "ftnlen":      lambda n, p: (["  ftnlen %s = 1;" % n], n),
    }


F2CQ_TYPES = _f2c_types("quadreal")
F2CD_TYPES = _f2c_types("doublereal")
F2CS_TYPES = _f2c_types("real")
F2CH_TYPES = _f2c_types("halfreal")

#: Through the front door, and BLIS's reasoning for what that means. LAPACK is
#: two thirds auxiliary routines -- everything spelled `?la*` is internal by its
#: own naming convention -- and those are reached, and covered, through the
#: drivers that call them, exactly as CXSparse's cs_chol is measured through
#: cs_cholsol. What is kept is the BLAS levels 1 to 3 and the factorisations,
#: solves and condition estimates built on them.
F2C_ROOTS = (r"(gemm|gemv|ger|trsm|trmm|trsv|trmv|symm|syrk|syr2k|dot|nrm2|"
             r"asum|scal|axpy|copy|swap|rot|getrf|getf2|getrs|gesv|potrf|"
             r"potf2|potrs|posv|geqrf|geqr2|gels|trtrs|gecon|lange|lacpy|"
             r"laswp|lascl|lassq|lapy2)")


LIBRARIES = {
    "cxsparse": {
        "header": "cs.h",
        "internal": {},
        # The double/int32 flavour. CXSparse also ships cs_dl (long), cs_ci and
        # cs_cl (complex), which are the same algorithms recompiled -- four
        # copies of every measurement, and the complex ones are not the
        # arithmetic this benchmark is about.
        "prefixes": ("cs_di_",),
        "types": CXSPARSE_TYPES,
        # order selects a fill-reducing permutation. 1 to 3 run cs_amd, which
        # is graph code over integers: a large share of the library's lines and
        # none of its floating point. 0 is the natural ordering, which keeps
        # the run in the numerics. qr=0 asks sqr for an LU analysis.
        "pin": {"order": "0", "qr": "0"},
        # Allocation, I/O, and the integer half: the elimination tree, the
        # orderings, the permutations, the depth-first searches. Real code, no
        # floating point.
        #
        # cs_di_chol and cs_di_qr go too, for a different reason: each needs an
        # analysis of its own shape -- cs_chol reads S->cp and S->parent, which
        # cs_sqr does not compute at all -- and a maker cannot know which of
        # its callers it is building for. Their numerics are still measured,
        # through cs_di_cholsol and cs_di_qrsol, which analyse, factorise and
        # solve in one call and need no cs_dis from outside.
        "exclude": r"^cs_di_(malloc|calloc|realloc|free|spalloc|spfree|"
                   r"sprealloc|dalloc|dfree|nfree|sfree|done|idone|ndone|ddone|"
                   r"load|print|entry|compress|amd|counts|cumsum|dfs|etree|"
                   r"post|tdfs|leaf|pinv|randperm|scc|dmperm|maxtrans|reach|"
                   r"ereach|fkeep|permute|symperm|ipvec|pvec|chol|qr)$",
        "includes": ["<cs.h>"],
    },
    "openlibm": {
        "header": "openlibm_math.h",
        "internal": {},
        # sin, cos, exp: the C library's own names, with no prefix to select
        # on. An empty prefix matches every declaration the header makes, and
        # the exclusions below do the narrowing.
        "prefixes": ("",),
        # Without this the header hides everything behind
        # "#if __BSD_VISIBLE || __XSI_VISIBLE", which is the whole Bessel
        # family -- j0, j1, jn, y0, y1, yn -- and they are compiled into the
        # library either way. OpenLibm's own build defines it, so a driver
        # that does not is reading a smaller library than the one it links.
        "defines": {"__BSD_VISIBLE": "1"},
        "types": OPENLIBM_TYPES,
        # isopenlibm reports the build; the rest are the classification macros
        # spelled as functions, which branch on the bits rather than compute.
        "exclude": r"^(isopenlibm)$",
        "includes": ["<openlibm_math.h>"],
    },
    "gmp": {
        "header": "gmp.h",
        # The declared names are the internal ones; the public spelling is a
        # macro over them.
        "internal": {"__gmpz_": "mpz_", "__gmpq_": "mpq_", "__gmpf_": "mpf_"},
        "types": GMP_TYPES,
        # mpz is multi-precision *integer* arithmetic: it blasts to bitvectors
        # and never reaches the floating-point theory. The floating-point
        # surface is mpf, plus the conversions to and from double, which are
        # where a double is taken apart and put back together.
        "select": r"^(mpf_|mp[zq]_(set|get|init_set)_d$)",
        "exclude": r"_(init|clear|swap|realloc|sizeinbase|size|out_str|inp_str|"
                   r"random|urandom|rrandom|set_str|get_str|set_default_prec|"
                   r"get_default_prec|set_prec|get_prec|set_prec_raw)$",
        "includes": ["<gmp.h>"],
    },
    "gsl": {
        "header": "gsl/gsl_sf.h",
        "internal": {},
        "prefixes": ("gsl_sf_", "gsl_cdf_", "gsl_ran_"),
        "types": GSL_TYPES,
        # GSL needs almost nothing: 640 of its 648 are already mathematics.
        "exclude": r"_(alloc|free|reset|service)$",
        "includes": ["<gsl/gsl_sf.h>", "<gsl/gsl_cdf.h>", "<gsl/gsl_math.h>"],
    },
    "fftw": {
        "header": "fftw3.h",
        "internal": {},
        "prefixes": ("fftw_",),
        "types": FFTW_TYPES,
        "pin": {"flags": "FFTW_ESTIMATE", "sign": "FFTW_FORWARD"},
        # Planning selects an algorithm; the transform happens in execute. A
        # plan-returning driver has to run the plan it built or it computes
        # nothing.
        "after": {"fftw_plan": "fftw_execute(%s);"},
        "exclude": r"(wisdom|_print|sprint|export|import|cleanup|forget|"
                   r"alignment_of|set_timelimit|thread|malloc|free)",
        "includes": ["<fftw3.h>"],
    },
    "blis": {
        "header": "blis.h",
        "internal": {},
        "prefixes": ("bli_",),
        "types": BLIS_TYPES,
        # A datatype is a shape question, not a value one: bli_gemm checks its
        # operands are of a floating type, and a symbolic num_t ranges over
        # BLIS_INT and BLIS_CONSTANT too, which abort. This is a
        # double-precision benchmark, so say so.
        "pin": {"dt": "BLIS_DOUBLE", "datatype": "BLIS_DOUBLE"},
        "post": blis_post,
        "preamble": BLIS_PREAMBLE,
        # BLIS is two thirds infrastructure -- object accessors, runtime
        # settings, memory pools, thread info, argument validators. Keep the
        # BLAS-like operations.
        "select": r"^bli_(gemm|trsm|trmm|hemm|symm|herk|syrk|her2k|syr2k|gemv|"
                  r"hemv|symv|trmv|trsv|ger|her|her2|syr|syr2|axpy|dot|scal|"
                  r"copy|add|sub|swap|norm|amax|invert|set[vm]|xpby|packm|"
                  r"unpackm)",
        # ...but only through the front door. BLIS exports its blocked
        # variants, kernel variants, control-tree builders and expert
        # interfaces too, and those take a cntx_t, a cntl_t or a thrinfo_t
        # that the generator can only fabricate: of 351 of them, 250 abort,
        # crash or fail to compile, and the 101 that survive are running
        # BLIS's plumbing rather than its arithmetic.
        #
        # Four more go for reasons of their own: packm_alloc and packm_init
        # are memory-pool plumbing, and dotaxpyv and dotxaxpyf require one
        # operand to be an *alias* of another, which is a relationship between
        # arguments rather than a property of either -- the one class of
        # precondition this generator says up front that it cannot meet.
        "exclude": r"(_check$|_ex$|_int$|_ukernel$|_cntl|sup|"
                   r"_(blk|ker|unb|unf)_var|"
                   r"^bli_(packm_(alloc|init)|dotaxpyv|dotxaxpyf)$)",
        "includes": ["<blis.h>"],
    },
    "sundials": {
        "header": "sundials/sundials_nvector.h",
        "internal": {},
        "prefixes": ("N_V", "SUNMat", "SUNLinSol"),
        # Which functions may *build* something is a wider question than which
        # get drivers: an N_Vector needs a SUNContext, and nothing named
        # SUNContext_Create is ever going to be a driver of its own.
        "ctor_prefixes": ("N_V", "SUN"),
        "types": SUNDIALS_TYPES,
        # What SUNDIALS calls a length.
        "sizes": {"sunindextype"},
        # _Band wants a banded matrix where these makers build a dense one,
        # and a band matrix adds no floating-point decision a dense one does
        # not already make.
        "exclude": r"(_Band$|GetID|GetType|LastFlag|NumIters|Resid|ResNorm|"
                   r"SetZeroGuess|SetScalingVectors|Initialize$|Free|"
                   r"GetArrayPointer|GetLength|GetCommunicator|GetVectorID|"
                   r"Space|Print|Destroy|Clone|NewEmpty|SetArrayPointer|"
                   r"GetLocalLength|GetSubvector|GetNumSubvectors|"
                   r"GetVecAtIndex|SetVecAtIndex|BufSize|BufPack|BufUnpack|"
                   r"Enable|Copy)",
        # The N_Vector layer alone is mostly branch-free kernels. The dense
        # matrix and linear-solver layers are where SUNDIALS makes decisions
        # about floating-point values: SUNLinSolSolve_Dense factorises with
        # partial pivoting, which is a comparison of magnitudes per column.
        "includes": ["<sundials/sundials_context.h>", "<nvector/nvector_serial.h>",
                     "<sundials/sundials_math.h>",
                     "<sunmatrix/sunmatrix_dense.h>", "<sunmatrix/sunmatrix_band.h>",
                     "<sunlinsol/sunlinsol_dense.h>", "<sunlinsol/sunlinsol_band.h>"],
    },
    # SUNDIALS at binary128 and binary16. The library is written through
    # sunrealtype throughout, so the type map above needs no change at all --
    # only the macro that decides what sunrealtype is, which the driver has to
    # define too because it includes the same headers.
    "sundials-f128": {
        "header": "sundials/sundials_nvector.h",
        "internal": {},
        "defines": {"SUNDIALS_QUAD_PRECISION": "1"},
        "prefixes": ("N_V", "SUNMat", "SUNLinSol"),
        # Which functions may *build* something is a wider question than which
        # get drivers: an N_Vector needs a SUNContext, and nothing named
        # SUNContext_Create is ever going to be a driver of its own.
        "ctor_prefixes": ("N_V", "SUN"),
        "types": SUNDIALS_TYPES,
        # What SUNDIALS calls a length.
        "sizes": {"sunindextype"},
        # _Band wants a banded matrix where these makers build a dense one,
        # and a band matrix adds no floating-point decision a dense one does
        # not already make.
        "exclude": r"(_Band$|GetID|GetType|LastFlag|NumIters|Resid|ResNorm|"
                   r"SetZeroGuess|SetScalingVectors|Initialize$|Free|"
                   r"GetArrayPointer|GetLength|GetCommunicator|GetVectorID|"
                   r"Space|Print|Destroy|Clone|NewEmpty|SetArrayPointer|"
                   r"GetLocalLength|GetSubvector|GetNumSubvectors|"
                   r"GetVecAtIndex|SetVecAtIndex|BufSize|BufPack|BufUnpack|"
                   r"Enable|Copy)",
        # The N_Vector layer alone is mostly branch-free kernels. The dense
        # matrix and linear-solver layers are where SUNDIALS makes decisions
        # about floating-point values: SUNLinSolSolve_Dense factorises with
        # partial pivoting, which is a comparison of magnitudes per column.
        "includes": ["<sundials/sundials_context.h>", "<nvector/nvector_serial.h>",
                     "<sundials/sundials_math.h>",
                     "<sunmatrix/sunmatrix_dense.h>", "<sunmatrix/sunmatrix_band.h>",
                     "<sunlinsol/sunlinsol_dense.h>", "<sunlinsol/sunlinsol_band.h>"],
    },
    "sundials-f16": {
        "header": "sundials/sundials_nvector.h",
        "internal": {},
        "defines": {"SUNDIALS_HALF_PRECISION": "1"},
        "prefixes": ("N_V", "SUNMat", "SUNLinSol"),
        # Which functions may *build* something is a wider question than which
        # get drivers: an N_Vector needs a SUNContext, and nothing named
        # SUNContext_Create is ever going to be a driver of its own.
        "ctor_prefixes": ("N_V", "SUN"),
        "types": SUNDIALS_TYPES,
        # What SUNDIALS calls a length.
        "sizes": {"sunindextype"},
        # _Band wants a banded matrix where these makers build a dense one,
        # and a band matrix adds no floating-point decision a dense one does
        # not already make.
        "exclude": r"(_Band$|GetID|GetType|LastFlag|NumIters|Resid|ResNorm|"
                   r"SetZeroGuess|SetScalingVectors|Initialize$|Free|"
                   r"GetArrayPointer|GetLength|GetCommunicator|GetVectorID|"
                   r"Space|Print|Destroy|Clone|NewEmpty|SetArrayPointer|"
                   r"GetLocalLength|GetSubvector|GetNumSubvectors|"
                   r"GetVecAtIndex|SetVecAtIndex|BufSize|BufPack|BufUnpack|"
                   r"Enable|Copy)",
        # The N_Vector layer alone is mostly branch-free kernels. The dense
        # matrix and linear-solver layers are where SUNDIALS makes decisions
        # about floating-point values: SUNLinSolSolve_Dense factorises with
        # partial pivoting, which is a comparison of magnitudes per column.
        "includes": ["<sundials/sundials_context.h>", "<nvector/nvector_serial.h>",
                     "<sundials/sundials_math.h>",
                     "<sunmatrix/sunmatrix_dense.h>", "<sunmatrix/sunmatrix_band.h>",
                     "<sunlinsol/sunlinsol_dense.h>", "<sunlinsol/sunlinsol_band.h>"],
    },
    "cmsisdsp": {
        "header": "arm_math_f16.h",
        "internal": {},
        "prefixes": ("arm_",),
        "types": CMSISDSP_TYPES,
        # The library is q7/q15/q31/f32/f64/f16 of nearly everything. This is
        # the binary16 arm, so say so; cmsisdsp-f32 below is the same corpus at
        # binary32 and is the control rather than a second benchmark.
        "select": r"_f16$",
        # Both arms are cut to the kernels that exist at *both* widths, not
        # just the control. Four f16 functions have no f32 twin, and were the
        # f16 arm to keep them the two driver lists would differ in length --
        # so a sweep's stride would select different kernels from each, and
        # the pair would stop being a pair. arm_math.h is included below so
        # the AST knows which names exist at binary32.
        "twin": ("_f16", "_f32"),
        "exclude": CMSIS_EXCLUDE,
        "min": CMSIS_MIN,
        # HOST=ON is what CMSIS-DSP's own build defines; FP_BENCH_X86_FLOAT16
        # is the typedef redirection build-lib.sh patches in. Both have to be
        # in the driver too, because the driver includes the same header.
        "defines": {"__GNUC_PYTHON__": "1", "FP_BENCH_X86_FLOAT16": "1"},
        "includes": ["<arm_math_f16.h>", "<arm_math.h>"],
    },
    "cmsisdsp-f32": {
        "header": "arm_math.h",
        "internal": {},
        "prefixes": ("arm_",),
        "types": CMSISDSP32_TYPES,
        "select": r"_f32$",
        # The control: exactly the binary32 twins of the binary16 corpus, and
        # nothing else. arm_math_f16.h is included above so that the AST knows
        # which those are.
        "twin": ("_f32", "_f16"),
        "exclude": CMSIS_EXCLUDE,
        "min": CMSIS_MIN,
        "defines": {"__GNUC_PYTHON__": "1", "FP_BENCH_X86_FLOAT16": "1"},
        "includes": ["<arm_math.h>", "<arm_math_f16.h>"],
    },
    "fftwq": {
        "header": "fftw3.h",
        "internal": {},
        "prefixes": ("fftwq_",),
        "types": FFTWQ_TYPES,
        "pin": {"flags": "FFTW_ESTIMATE", "sign": "FFTW_FORWARD"},
        # Keyed on fftwq_plan, not fftw_plan: without it the 27 planning
        # drivers select an algorithm and compute nothing.
        "after": {"fftwq_plan": "fftwq_execute(%s);"},
        "exclude": r"(wisdom|_print|sprint|export|import|cleanup|forget|"
                   r"alignment_of|set_timelimit|thread|malloc|free)",
        "includes": ["<fftw3.h>"],
    },
    "cuba": {
        # The quad build generates its own header, with cubareal a __float128.
        # cuba.h is the binary64 one and declares a different ABI.
        "header": "cubaq.h",
        "internal": {},
        # Vegas, Suave, Divonne, Cuhre: no prefix to select on, so the select
        # below does all the narrowing.
        "prefixes": ("",),
        "types": CUBA_TYPES,
        "pin": CUBA_PINS,
        "preamble": CUBA_PREAMBLE,
        "prologue": CUBA_PROLOGUE,
        # The four algorithms, through the front door. Cuba also exports ll*
        # variants, which are the same algorithms with a wider evaluation
        # counter, and the Fortran-facing lowercase spellings -- CXSparse's
        # reasoning, that recompiling one algorithm is not a second
        # measurement of it.
        "select": r"^(Vegas|Suave|Divonne|Cuhre)$",
        "includes": ["<cubaq.h>"],
    },
    "f2clapack": {
        "header": "f2clapack.h",
        "internal": {},
        # The routines are named by their leading letter, so there is no
        # prefix to select on; the select below does the narrowing.
        "prefixes": ("",),
        "types": F2CQ_TYPES,
        "select": r"^(q" + F2C_ROOTS + r"|iqamax)_$",
        "includes": ["<f2clapack.h>"],
    },
    "f2clapack-f64": {
        "header": "f2clapack.h",
        "internal": {},
        "prefixes": ("",),
        "types": F2CD_TYPES,
        "select": r"^(d" + F2C_ROOTS + r"|idamax)_$",
        "includes": ["<f2clapack.h>"],
    },
    # f2cblaslapack ships the same routines at four widths -- half, single,
    # double and quad, with a makefile target for each -- so this is the one
    # place in the corpus where binary16, binary32, binary64 and binary128 can
    # be compared over *identical* code rather than over four libraries that
    # happen to be similar.
    "f2clapack-f32": {
        "header": "f2clapack.h",
        "internal": {},
        "prefixes": ("",),
        "types": F2CS_TYPES,
        "select": r"^(s" + F2C_ROOTS + r"|isamax)_$",
        "includes": ["<f2clapack.h>"],
    },
    "f2clapack-f16": {
        "header": "f2clapack.h",
        "internal": {},
        "prefixes": ("",),
        "types": F2CH_TYPES,
        "select": r"^(h" + F2C_ROOTS + r"|ihamax)_$",
        "includes": ["<f2clapack.h>"],
    },
    "hdf5": {
        # Written from a table rather than read from a header; see h5_drivers.
        "header": "hdf5.h",
        "internal": {},
        "types": {},
        "synthetic": h5_drivers,
        "focus": "_Float16",
        "includes": ["<hdf5.h>"],
    },
    "hdf5-f32": {
        "header": "hdf5.h",
        "internal": {},
        "types": {},
        "synthetic": h5_drivers,
        "focus": "float",
        "includes": ["<hdf5.h>"],
    },
}


def declarations(include_dir, cfg, extra_cflags):
    """Every function and enum clang can see through the library's header."""
    probe = os.path.join(os.environ.get("TMPDIR", "/tmp"), "fpbench_probe.c")
    with open(probe, "w") as f:
        for d, v in cfg.get("defines", {}).items():
            f.write("#define %s %s\n" % (d, v))
        for inc in cfg["includes"]:
            f.write("#include %s\n" % inc)
    cmd = ["clang", "-Xclang", "-ast-dump=json", "-fsyntax-only",
           "-I", include_dir] + extra_cflags + [probe]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = r.stdout
    if not out.strip():
        sys.exit("clang produced no AST; check --include and --cflags")
    # A missing type or header does not stop clang emitting an AST -- it emits
    # a *partial* one and reports the error on stderr. Generating from that
    # silently produces drivers in a type that does not exist, which then fail
    # to compile one at a time rather than failing here, once, with the reason.
    errs = [l for l in r.stderr.splitlines() if ": error:" in l]
    if errs:
        sys.exit("clang could not parse the header cleanly:\n  " +
                 "\n  ".join(errs[:5]))
    fns, enums = [], {}
    for node in json.loads(out).get("inner", []):
        kind = node.get("kind")
        if kind == "EnumDecl":
            # clang knows what values the type actually has, so a tag parameter
            # can be symbolic over exactly those rather than over any int.
            vals = [e["name"] for e in node.get("inner", [])
                    if e.get("kind") == "EnumConstantDecl"]
            for spelling in filter(None, [node.get("name")]):
                enums[spelling] = vals
        elif kind == "TypedefDecl":
            inner = node.get("inner", [])
            for i in inner:
                if i.get("kind") == "ElaboratedType" or i.get("ownedTagDecl"):
                    owned = i.get("ownedTagDecl", {}).get("name")
                    if owned and owned in enums and node.get("name"):
                        enums[node["name"]] = enums[owned]
        elif kind == "FunctionDecl":
            params = [(p["type"]["qualType"], p.get("name", ""))
                      for p in node.get("inner", [])
                      if p.get("kind") == "ParmVarDecl"]
            fns.append((node.get("name", ""), node["type"]["qualType"], params))
    return fns, enums


def public_name(name, cfg):
    for internal, public in cfg.get("internal", {}).items():
        if name.startswith(internal):
            return public + name[len(internal):]
    if "prefixes" in cfg and name.startswith(cfg["prefixes"]):
        return name
    return None


def normalise(t):
    """Drop qualifiers the mapping does not care about."""
    t = re.sub(r"\bconst\b", "", t).strip()
    return re.sub(r"\s+", " ", t)


def selected(pub, cfg):
    """Is this function worth a driver for a floating-point comparison?

    Every library carries a surface that computes nothing -- object accessors,
    runtime settings, memory pools, argument validators. They are legitimate
    coverage targets but they exercise no arithmetic, so they dilute a solver
    comparison with paths that never reach the solver. --all keeps them.
    """
    keep, drop = cfg.get("select"), cfg.get("exclude")
    if keep and not re.search(keep, pub):
        return False
    if drop and re.search(drop, pub):
        return False
    return True


def _source(cfg, lines):
    """The file a driver is: the library's headers, KLEE's, whatever preamble
    the library needs, and then `lines`, which begin at `int main`.

    Shared with the libraries whose drivers are written from a table rather
    than read from a header, so that both kinds of driver are the same file
    with a different middle.
    """
    return "\n".join([
        "/* Generated by common/gen-drivers.py -- do not edit. */",
        "",
    ] + ["#define %s %s" % (d, v)
         for d, v in sorted(cfg.get("defines", {}).items())] + [
        "",
    ] + ["#include %s" % i for i in cfg["includes"]] + [
        '#include <klee/klee.h>',
        "",
    ] + cfg.get("preamble", []) + lines)


def build_driver(pub, ret, params, cfg, ctor):
    """Driver source, or (None, reason) if some parameter cannot be made symbolic."""
    has_array = any(normalise(p) in ARRAY_TYPES for p, _ in params)

    body, args = [], []
    for i, (ptype, pname) in enumerate(params):
        key = normalise(ptype)
        # Some arguments are not values to explore but switches that have to
        # hold a particular one. FFTW's planner flags are the case that
        # matters: left to a bounded integer this lands on FFTW_EXHAUSTIVE,
        # which makes the planner time every algorithm it knows.
        pinned = cfg.get("pin", {}).get(pname)
        if pinned is not None:
            body.append("  %s a%d = %s;" % (key, i, pinned))
            args.append("a%d" % i)
            continue
        if key == "void":
            continue
        maker = cfg["types"].get(key)
        if maker is not None:
            lines, arg = _make(maker, "a%d" % i, pname)
        else:
            # Not a type we know how to make directly -- ask whether anything
            # in the library yields one.
            lines, arg = ctor.build(ptype, "a%d" % i)
            if lines is None:
                return None, arg
        if has_array and key in ("int", "unsigned int", "long", "unsigned long",
                                 "size_t"):
            # Some integer of a function taking an array is that array's
            # length, and which one is a convention rather than a type. Bound
            # them all rather than guess.
            lines.append("  klee_assume(a%d <= %d);" % (i, ARRAY_N))
            if not key.startswith("unsigned") and key != "size_t":
                lines.append("  klee_assume(a%d >= 0);" % i)
        # Some parameters have a lower bound the type does not say either. A
        # CMSIS-DSP blockSize of zero underflows: arm_max_f16 computes
        # blockSize - 1U and walks off the buffer. That is a precondition of
        # the API rather than a bug, and only the library knows it.
        lo = cfg.get("min", {}).get(pname)
        if lo is not None:
            lines.append("  klee_assume(a%d >= %s);" % (i, lo))
        body += lines
        # Some libraries need a word said about an operand after it exists --
        # see blis_post, where it is the structure BLIS checks for but cannot
        # infer from a type.
        for extra in cfg.get("post", lambda *_: [])(pub, pname):
            body.append(extra % ("a%d" % i))
        args.append(arg)

    call = "%s(%s);" % (pub, ", ".join(args))
    after = []
    if normalise(ret).startswith(("void (", "void(")) is False and \
            not normalise(ret).startswith("void"):
        call = "volatile " + _return_type(ret) + " r_ = " + call
    # Some functions hand back something that has to be used before anything
    # is computed. An fftw_plan is the case that matters: a driver that builds
    # one and returns has selected an algorithm and run no arithmetic at all,
    # which turned FFTW's seventeen planning entry points into seventeen
    # drivers that measured nothing.
    hook = cfg.get("after", {}).get(normalise(_return_type(ret)))
    if hook:
        after.append("  " + hook % "r_")

    return _source(cfg, [
        "int main(void) {",
    ] + cfg.get("prologue", []) + body + [
        "  " + call,
    ] + after + [
        "  return 0;",
        "}",
        "",
    ]), None


def _return_type(ret):
    return normalise(ret).split("(")[0].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True, choices=sorted(LIBRARIES))
    ap.add_argument("--include", required=True, help="the library's include dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cflags", default="", help="extra flags for the AST probe")
    ap.add_argument("--all", action="store_true",
                    help="emit every function, not only the computational ones")
    args = ap.parse_args()

    cfg = LIBRARIES[args.library]
    os.makedirs(args.out, exist_ok=True)
    # Anything this tool wrote before. Regenerating with a narrower selector
    # has to drop what it no longer selects, or the directory silently keeps
    # drivers the configuration has excluded.
    for stale in os.listdir(args.out):
        path = os.path.join(args.out, stale)
        if stale.endswith((".c", ".tsv")) and os.path.isfile(path):
            with open(path, errors="ignore") as f:
                if stale.endswith(".tsv") or "Generated by" in f.readline():
                    os.remove(path)

    if "synthetic" in cfg:
        # A library whose drivers cannot come from its header: the
        # signatures are private, or the API takes handles rather than
        # types. What the table cannot express, it does not emit, so
        # there is nothing to skip.
        symbols, skipped = [], []
        for name, src, sym in cfg["synthetic"](cfg):
            with open(os.path.join(args.out, name + ".c"), "w") as f:
                f.write(src)
            symbols.append((name, sym))
        emitted = len(symbols)
    else:
        decls, enums = declarations(args.include, cfg, args.cflags.split())
        ctor = Constructor(decls, cfg["types"], enums,
                           lambda elem, name: sym_array(elem, name), ARRAY_N,
                           prefixes=cfg.get("ctor_prefixes")
                                    or cfg.get("prefixes", ())
                                    or tuple(cfg.get("internal", {})),
                           pins=cfg.get("pin", {}),
                           sizes=cfg.get("sizes", ()))

        # A header can declare the same function twice -- GMP does, for the ones it
        # also offers as __GMP_EXTERN_INLINE -- and writing the driver twice would
        # make the count larger than the set.
        seen = set()
        # Every name the header declares, for the twin filter below.
        declared = {name for name, _, _ in decls}
        emitted, skipped, symbols = 0, [], []
        for name, ret, params in decls:
            pub = public_name(name, cfg)
            if pub is None or pub in seen:
                continue
            seen.add(pub)
            if not args.all and not selected(pub, cfg):
                continue
            # A control arm emits only the drivers whose counterpart at the
            # other width exists, so that the two corpora are one set of
            # kernels at two formats rather than two different benchmarks.
            # CMSIS-DSP's f32 API is half again the size of its f16 one, and
            # a comparison across formats over a set that differs between them
            # is not a comparison of formats.
            twin = cfg.get("twin")
            if twin and pub.endswith(twin[0]) and \
                    pub[:-len(twin[0])] + twin[1] not in declared:
                continue
            src, reason = build_driver(pub, ret, params, cfg, ctor)
            if src is None:
                skipped.append((pub, reason))
                continue
            with open(os.path.join(args.out, pub + ".c"), "w") as f:
                f.write(src)
            # The public spelling is not always the symbol. GMP's mpf_add is a
            # macro over __gmpf_add, and the coverage report knows only the latter,
            # so the mapping has to travel with the drivers.
            symbols.append((pub, name))
            emitted += 1

    with open(os.path.join(args.out, "functions.tsv"), "w") as f:
        f.write("driver\tcoverage symbol\n")
        for pub, sym in sorted(symbols):
            f.write("%s\t%s\n" % (pub, sym))

    with open(os.path.join(args.out, "skipped.tsv"), "w") as f:
        f.write("function\tunsupported parameter type\n")
        for pub, reason in sorted(skipped):
            f.write("%s\t%s\n" % (pub, reason))

    print("%s: %d drivers, %d skipped" % (args.library, emitted, len(skipped)))
    by_reason = {}
    for _, reason in skipped:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1])[:8]:
        print("  %4d  %s" % (n, reason))


if __name__ == "__main__":
    main()
