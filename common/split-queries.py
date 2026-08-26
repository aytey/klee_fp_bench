#!/usr/bin/env python3
"""Split KLEE's Z3 or Bitwuzla query dump into standalone SMT-LIB2 files.

    split-queries.py <dump.smt2> <outdir> [prefix]

`--debug-z3-dump-queries` appends every query KLEE hands the solver to one
file, each bracketed by "; start Z3 query" and "; end Z3 query" and followed by
a (reset). Splitting them out gives a corpus that can be replayed against any
solver, which is the only way to compare two solvers on the *same* queries:
inside KLEE they diverge as soon as one of them times out a query the other
answered, and from then on they are not being asked the same things.

Three edits per query. The (reset) goes, because each file is its own session.
A (set-logic) is prepended when the dump has none, because STP will not enable
its floating-point parser without one -- Z3 and Bitwuzla infer it, STP says
"FloatingPoint is a floating-point name; those are recognised only after a
floating-point (set-logic)". Bitwuzla's own printer emits one, so prepending
unconditionally would leave two and the second would narrow the logic.

And (define-const n T v) becomes (define-fun n () T v), which is the same
declaration in a spelling STP's parser accepts. Bitwuzla prints the short form;
STP answers it with a syntax error, and a corpus that half the comparison
cannot read is not a comparison.
"""
import os
import re
import sys

LOGIC = "QF_AUFBVFP"

# (define-const name TYPE ...) -> (define-fun name () TYPE ...)
DEFINE_CONST = re.compile(r"\(define-const\s+(\S+)\s+")


def split(path):
    cur, out = [], []
    for line in open(path, errors="ignore"):
        if line.startswith("; start Z3 query") or line.startswith("; start Bitwuzla query"):
            cur = []
        elif line.startswith("; end Z3 query") or line.startswith("; end Bitwuzla query"):
            if cur:
                out.append("".join(cur))
            cur = []
        elif cur is not None:
            if line.strip() == "(reset)":
                continue
            cur.append(DEFINE_CONST.sub(r"(define-fun \1 () ", line))
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    dump, outdir = sys.argv[1], sys.argv[2]
    prefix = sys.argv[3] if len(sys.argv) > 3 else "q"
    os.makedirs(outdir, exist_ok=True)
    n = 0
    for i, body in enumerate(split(dump)):
        if "(check-sat)" not in body:
            continue
        # A query asserting nothing but `true` or `(not false)` is KLEE probing
        # the solver, not asking it anything. Keeping them would put hundreds
        # of trivial files in a corpus meant to hold the expensive ones.
        stripped = re.sub(r";.*", "", body)
        if not re.search(r"\(assert (?!\(not false\)|true\))", stripped):
            continue
        with open(os.path.join(outdir, "%s%05d.smt2" % (prefix, i)), "w") as f:
            if "(set-logic" not in body:
                f.write("(set-logic %s)\n" % LOGIC)
            f.write(body)
            if "(exit)" not in body:
                f.write("(exit)\n")
        n += 1
    print("%s: %d queries" % (os.path.basename(dump), n))


if __name__ == "__main__":
    main()
