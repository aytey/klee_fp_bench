#!/usr/bin/env python3
"""Which floating-point sorts does each query in a corpus contain?

    query-sorts.py <dir>...

Each directory is one produced by dump-queries.sh -- either its `split/`
subdirectory directly, or a parent holding several libraries' worth. Every
`.smt2` under it is classified by which IEEE formats appear in it, and the
counts are reported per library and in total.

**Match `to_fp` as well as `FloatingPoint`, or the answer is wrong by a factor
of six.** A query carries a width in one of two spellings, and the sort name is
the rarer one: it appears only where something is *declared* of that sort.
KLEE's usual output is a bitvector reinterpreted as a float,

    (assert (not (fp.eq ((_ to_fp 11 53) @def0) ((_ to_fp 11 53) (_ bv0 64)))))

which is a binary64 query that never writes the word FloatingPoint. Matching
the sort alone read 7.4% of one corpus as floating-point where the true figure
was 44.5%, and reported GMP -- whose whole floating-point surface is the
double conversions -- as containing none at all.

A query is counted once per sort it contains, so the columns overlap: a query
carrying both binary16 and binary64 appears in both. The combination table at
the end is the non-overlapping view, and is where a corpus admits that its
binary16 kernels are calling a binary64 libm.
"""
import collections
import os
import re
import sys

#: (name, "<exponent> <significand>") as SMT-LIB spells the format.
SORTS = [("binary16", "5 11"), ("binary32", "8 24"), ("binary64", "11 53"),
         ("x87 fp80", "15 64"), ("binary128", "15 113")]
BY = {bits: name for name, bits in SORTS}
PAT = re.compile(r"(?:FloatingPoint|to_fp) (5 11|8 24|11 53|15 64|15 113)")
ORDER = [name for name, _ in SORTS]


def classify(path):
    with open(path, errors="ignore") as fh:
        return {BY[m] for m in PAT.findall(fh.read())}


def corpora(roots):
    """Every (label, directory of .smt2) under the given roots."""
    for root in roots:
        if os.path.basename(root.rstrip("/")) == "split":
            yield os.path.basename(os.path.dirname(root.rstrip("/"))), root
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name, "split")
            if os.path.isdir(d):
                yield name, d
            elif name.endswith(".smt2"):
                yield os.path.basename(root.rstrip("/")), root
                break


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    per, total, combos, n_all = {}, collections.Counter(), collections.Counter(), 0
    for label, d in corpora(sys.argv[1:]):
        c, n = collections.Counter(), 0
        for f in os.listdir(d):
            if not f.endswith(".smt2"):
                continue
            n += 1
            found = classify(os.path.join(d, f))
            for s in found:
                c[s] += 1
                total[s] += 1
            combos[tuple(sorted(found, key=ORDER.index))] += 1
        if n:
            per[label] = (n, c)
            n_all += n

    w = max([len(k) for k in per] + [8])
    head = "%-*s %9s " % (w, "library", "queries") + " ".join("%10s" % s for s in ORDER)
    print(head)
    print("-" * len(head))
    for label in sorted(per):
        n, c = per[label]
        print("%-*s %9d " % (w, label, n) + " ".join("%10d" % c[s] for s in ORDER))
    print("-" * len(head))
    print("%-*s %9d " % (w, "TOTAL", n_all) + " ".join("%10d" % total[s] for s in ORDER))
    any_fp = n_all - combos[()]
    print("\n%d of %d queries carry a floating-point sort (%.1f%%)"
          % (any_fp, n_all, 100.0 * any_fp / n_all if n_all else 0))
    print("\nby exact combination present in one query:")
    for k, v in combos.most_common(12):
        print("  %7d  %s" % (v, " + ".join(k) if k else "(none)"))


if __name__ == "__main__":
    main()
