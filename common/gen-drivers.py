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

from construct import Constructor, normalise as cnorm

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

BLIS_TYPES = {
    "double":       lambda n: sym_scalar("double", n),
    "float":        lambda n: sym_scalar("float", n),
    "int":          lambda n: sym_scalar("int", n),
    # Dimensions and strides. Bounded, and they reach indexing arithmetic, so
    # a wrong one is an out-of-bounds rather than a path.
    "dim_t":        lambda n: sym_scalar("dim_t", n, bound="4"),
    "inc_t":        lambda n: sym_scalar("inc_t", n, bound="4"),
    "doff_t":       lambda n: sym_scalar("doff_t", n, bound="4"),
}
BLIS_TYPES.update(ARRAY_TYPES)

LIBRARIES = {
    "gmp": {
        "header": "gmp.h",
        # The declared names are the internal ones; the public spelling is a
        # macro over them.
        "internal": {"__gmpz_": "mpz_", "__gmpq_": "mpq_", "__gmpf_": "mpf_"},
        "types": GMP_TYPES,
        "includes": ["<gmp.h>"],
    },
    "gsl": {
        "header": "gsl/gsl_sf.h",
        "internal": {},
        "prefixes": ("gsl_sf_", "gsl_cdf_", "gsl_ran_"),
        "types": GSL_TYPES,
        "includes": ["<gsl/gsl_sf.h>", "<gsl/gsl_cdf.h>", "<gsl/gsl_math.h>"],
    },
    "blis": {
        "header": "blis.h",
        "internal": {},
        "prefixes": ("bli_",),
        "types": BLIS_TYPES,
        "includes": ["<blis.h>"],
    },
    "sundials": {
        "header": "sundials/sundials_nvector.h",
        "internal": {},
        "prefixes": ("N_V", "SUNMat", "SUNLinSol"),
        "types": SUNDIALS_TYPES,
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
            params = [p["type"]["qualType"]
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


def build_driver(pub, ret, params, cfg, ctor):
    """Driver source, or (None, reason) if some parameter cannot be made symbolic."""
    has_array = any(normalise(p) in ARRAY_TYPES for p in params)

    body, args = [], []
    for i, ptype in enumerate(params):
        key = normalise(ptype)
        if key == "void":
            continue
        maker = cfg["types"].get(key)
        if maker is not None:
            lines, arg = maker("a%d" % i)
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
        args.append(arg)

    call = "%s(%s);" % (pub, ", ".join(args))
    if normalise(ret).startswith(("void (", "void(")) is False and \
            not normalise(ret).startswith("void"):
        call = "volatile " + _return_type(ret) + " r_ = " + call
    return "\n".join([
        "/* Generated by common/gen-drivers.py -- do not edit. */",
        "",
    ] + ["#include %s" % i for i in cfg["includes"]] + [
        '#include <klee/klee.h>',
        "",
        "int main(void) {",
    ] + body + [
        "  " + call,
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
    args = ap.parse_args()

    cfg = LIBRARIES[args.library]
    os.makedirs(args.out, exist_ok=True)

    decls, enums = declarations(args.include, cfg, args.cflags.split())
    ctor = Constructor(decls, cfg["types"], enums,
                       lambda elem, name: sym_array(elem, name), ARRAY_N,
                       prefixes=cfg.get("prefixes", ())
                                or tuple(cfg.get("internal", {})))

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

    with open(os.path.join(args.out, "..", "functions.tsv"), "w") as f:
        f.write("driver\tcoverage symbol\n")
        for pub, sym in sorted(symbols):
            f.write("%s\t%s\n" % (pub, sym))

    with open(os.path.join(args.out, "..", "skipped.tsv"), "w") as f:
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
