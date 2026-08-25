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

SUNDIALS_TYPES = {
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

LIBRARIES = {
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
        "exclude": r"(GetArrayPointer|GetLength|GetCommunicator|GetVectorID|"
                   r"Space|Print|Destroy|Clone|NewEmpty|SetArrayPointer|"
                   r"GetLocalLength|GetSubvector|GetNumSubvectors|"
                   r"GetVecAtIndex|SetVecAtIndex|BufSize|BufPack|BufUnpack|"
                   r"Enable|Copy)",
        "includes": ["<sundials/sundials_context.h>", "<nvector/nvector_serial.h>",
                     "<sundials/sundials_math.h>"],
    },
}


def declarations(include_dir, cfg, extra_cflags):
    """Every function and enum clang can see through the library's header."""
    probe = os.path.join(os.environ.get("TMPDIR", "/tmp"), "fpbench_probe.c")
    with open(probe, "w") as f:
        for inc in cfg["includes"]:
            f.write("#include %s\n" % inc)
    cmd = ["clang", "-Xclang", "-ast-dump=json", "-fsyntax-only",
           "-I", include_dir] + extra_cflags + [probe]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    if not out.strip():
        sys.exit("clang produced no AST; check --include and --cflags")
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

    return "\n".join([
        "/* Generated by common/gen-drivers.py -- do not edit. */",
        "",
    ] + ["#include %s" % i for i in cfg["includes"]] + [
        '#include <klee/klee.h>',
        "",
    ] + cfg.get("preamble", []) + [
        "int main(void) {",
    ] + body + [
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
    emitted, skipped, symbols = 0, [], []
    for name, ret, params in decls:
        pub = public_name(name, cfg)
        if pub is None or pub in seen:
            continue
        seen.add(pub)
        if not args.all and not selected(pub, cfg):
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
