#!/usr/bin/env python3
"""Split KLEE's Z3 query dump into standalone SMT-LIB2 files.

    split-queries.py <dump.smt2> <outdir> [prefix]

`--debug-z3-dump-queries` appends every query KLEE hands the solver to one
file, each bracketed by "; start Z3 query" and "; end Z3 query" and followed by
a (reset). Splitting them out gives a corpus that can be replayed against any
solver, which is the only way to compare two solvers on the *same* queries:
inside KLEE they diverge as soon as one of them times out a query the other
answered, and from then on they are not being asked the same things.

Two edits per query. The (reset) goes, because each file is its own session.
And a (set-logic) is prepended, because the dump has none and STP will not
enable its floating-point parser without one -- Z3 and Bitwuzla infer it, STP
says "FloatingPoint is a floating-point name; those are recognised only after a
floating-point (set-logic)".
"""
import os
import re
import sys

LOGIC = "QF_AUFBVFP"


def split(path):
    cur, out = [], []
    for line in open(path, errors="ignore"):
        if line.startswith("; start Z3 query"):
            cur = []
        elif line.startswith("; end Z3 query"):
            if cur:
                out.append("".join(cur))
            cur = []
        elif cur is not None:
            if line.strip() == "(reset)":
                continue
            cur.append(line)
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
            f.write("(set-logic %s)\n" % LOGIC)
            f.write(body)
            if "(exit)" not in body:
                f.write("(exit)\n")
        n += 1
    print("%s: %d queries" % (os.path.basename(dump), n))


if __name__ == "__main__":
    main()
