#!/usr/bin/env python3
"""Give SUNDIALS a precision it does not ship: binary128 or binary16.

    sundials-precision.py <source-tree> <quad|half>

SUNDIALS is the one library here written entirely through a type indirection of
its own -- `sunrealtype`, which its own build already switches between float,
double and long double. That makes it the one library whose precision can be
changed from outside without touching a line of numerics, which is the whole
reason a precision axis over it is possible at all.

Two headers decide the width. `sundials_types.h` typedefs `sunrealtype` and
defines the literal suffix and the format limits; `sundials_math.h` maps
SUNRsqrt and its neighbours onto a libm. Each is a `#if defined(...)` chain, so
adding a precision means inserting a branch at the *front* of every chain --
in front, because SUNDIALS's generated config.h will still be defining
SUNDIALS_DOUBLE_PRECISION and the first matching branch wins.

Half computes through binary32 and rounds back. For the square root that is
exact rather than approximate: double rounding through a wider format is benign
when the wider one has at least 2p+2 bits, which for binary16 means 24, and
binary32 has exactly 24. For exp and pow it is an approximation, and is marked
as one here -- neither is on the paths this suite drives.
"""
import re
import sys

TYPES = {
    "quad": ("""#if defined(SUNDIALS_QUAD_PRECISION)

#include <quadmath.h>
typedef __float128 sunrealtype;
#define SUN_RCONST(x)     x##Q
#define SUN_BIG_REAL      FLT128_MAX
#define SUN_SMALL_REAL    FLT128_MIN
#define SUN_UNIT_ROUNDOFF FLT128_EPSILON
/* quadmath_snprintf is the one libquadmath entry point this suite cannot
   build, so a quad value cannot be printed. Nothing driven here prints. */
#define SUN_FORMAT_E      "% .18e"
#define SUN_FORMAT_G      "%.18g"
#define SUN_FORMAT_SG     "%+.18g"

#elif defined(SUNDIALS_SINGLE_PRECISION)"""),
    "half": ("""#if defined(SUNDIALS_HALF_PRECISION)

typedef _Float16 sunrealtype;
#define SUN_RCONST(x)     ((_Float16)(x))
#define SUN_BIG_REAL      ((_Float16)65504.0f)
#define SUN_SMALL_REAL    ((_Float16)6.103515625e-05f)
#define SUN_UNIT_ROUNDOFF ((_Float16)9.765625e-04f)
#define SUN_FORMAT_E      "% .4e"
#define SUN_FORMAT_G      "%.4g"
#define SUN_FORMAT_SG     "%+.4g"

#elif defined(SUNDIALS_SINGLE_PRECISION)"""),
}

MATH = {
    "quad": {
        "SUNRsqrt": "((x) <= SUN_RCONST(0.0) ? (SUN_RCONST(0.0)) : (sqrtq((x))))",
        "SUNRabs": "(fabsq((x)))",
        "SUNRexp": "(expq((x)))",
        "SUNRceil": "(ceilq((x)))",
        "SUNRcopysign": "(copysignq((x), (y)))",
        "SUNRpowerR": "(powq((base), (exponent)))",
        "SUNRround": "(roundq((x)))",
    },
    "half": {
        "SUNRsqrt": "((x) <= SUN_RCONST(0.0) ? (SUN_RCONST(0.0)) "
                    ": ((_Float16)sqrtf((float)(x))))",
        "SUNRabs": "((_Float16)fabsf((float)(x)))",
        "SUNRexp": "((_Float16)expf((float)(x)))",
        "SUNRceil": "((_Float16)ceilf((float)(x)))",
        "SUNRcopysign": "((_Float16)copysignf((float)(x), (float)(y)))",
        "SUNRpowerR": "((_Float16)powf((float)(base), (float)(exponent)))",
        "SUNRround": "((_Float16)roundf((float)(x)))",
    },
}

#: The argument names SUNDIALS gives each macro, so the replacement can use them.
ARGS = {"SUNRsqrt": "x", "SUNRabs": "x", "SUNRexp": "x", "SUNRceil": "x",
        "SUNRcopysign": "x, y", "SUNRpowerR": "base, exponent",
        "SUNRround": "x"}


def patch_types(path, prec):
    s = open(path).read()
    anchor = "#if defined(SUNDIALS_SINGLE_PRECISION)"
    if "SUNDIALS_%s_PRECISION" % prec.upper() in s:
        return 0
    assert s.count(anchor) == 1, "sundials_types.h no longer has one precision chain"
    open(path, "w").write(s.replace(anchor, TYPES[prec], 1))
    return 1


def patch_math(path, prec):
    s = open(path).read()
    n = 0
    for name, body in MATH[prec].items():
        blk = "#ifndef %s\n#if defined(SUNDIALS_DOUBLE_PRECISION)" % name
        if blk not in s:
            continue
        new = ("#ifndef %s\n#if defined(SUNDIALS_%s_PRECISION)\n"
               "#define %s(%s) %s\n"
               "#elif defined(SUNDIALS_DOUBLE_PRECISION)"
               % (name, prec.upper(), name, ARGS[name], body))
        s = s.replace(blk, new, 1)
        n += 1
    open(path, "w").write(s)
    return n


def main():
    tree, prec = sys.argv[1], sys.argv[2]
    if prec not in TYPES:
        sys.exit("precision must be quad or half")
    t = patch_types("%s/include/sundials/sundials_types.h" % tree, prec)
    m = patch_math("%s/include/sundials/sundials_math.h" % tree, prec)
    print("sundials %s: %d type branch, %d math macros" % (prec, t, m))


if __name__ == "__main__":
    main()
