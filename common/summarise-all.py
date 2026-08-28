#!/usr/bin/env python3
"""One table across every library, for the question the repository asks.

    summarise-all.py [library ...]

aggregate.py reports a library at a time, which is what you want while working
on one. The question this suite exists to answer is not about one library, and
answering it by adding up totals across seven would be wrong twice over: a
faster solver does more work rather than finishing sooner, so its total solver
time is *larger*; and a total over a suite is settled by whichever handful of
drivers happen to be slowest, which here means whichever library contributed
them.

So this reports per driver, against a baseline, over the drivers where nothing
was cut off in either configuration -- no exploration budget reached and no
query timed out, so both answered the same questions. Wins and losses are a
count of drivers, and the geometric mean is over per-driver ratios, both of
which are indifferent to a library's size and to the tail.
"""
import collections
import importlib.util
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("FP_BENCH_WORK", "/mnt/baranem/fp_bench-work")
DEFAULT = ("gsl openlibm blis sundials sundials-f128 sundials-f16 gmp fftw cxsparse "
           "fftwq cuba f2clapack f2clapack-f64 f2clapack-f32 f2clapack-f16 "
           "cmsisdsp cmsisdsp-f32 hdf5 hdf5-f32").split()

_spec = importlib.util.spec_from_file_location("agg", os.path.join(HERE, "aggregate.py"))
agg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agg)


def main():
    libs = sys.argv[1:] or DEFAULT
    base = os.environ.get("BASELINE", "bitwuzla")
    rows, per_lib = {}, collections.defaultdict(dict)
    for lib in libs:
        out = os.path.join(WORK, lib, "runs-all")
        if not os.path.exists(os.path.join(out, "results.psv")):
            continue
        for name, byconf in agg.load(out).items():
            rows[(lib, name)] = byconf
            per_lib[lib][name] = byconf
    if not rows:
        sys.exit("no swept results found under %s/*/runs-all" % WORK)

    labels = sorted({c for v in rows.values() for c in v})
    if base not in labels:
        sys.exit("baseline %r not among %s" % (base, labels))

    def comparable(byconf, other):
        """Both ran, neither was cut off, and both spent measurable time."""
        for c in (base, other):
            r = byconf.get(c)
            if r is None or r["bounded"] or r["timeouts"]:
                return False
            if r.get("solver", 0) <= 0:
                return False
        return True

    print("corpus: %d drivers across %d libraries, %d configurations"
          % (len(rows), len(per_lib), len(labels)))
    print("baseline: %s   (per driver, where neither side was cut off)\n" % base)

    print("%-14s %7s %7s %7s %8s %9s %11s"
          % ("config", "n", "wins", "losses", "geomean", "median", "cut off"))
    for c in labels:
        if c == base:
            continue
        names = [k for k, v in rows.items() if comparable(v, c)]
        if not names:
            print("%-14s %7s" % (c, "-")); continue
        ratios = [rows[k][c]["solver"] / rows[k][base]["solver"] for k in names]
        assert all(r > 0 for r in ratios)
        lg = [math.log(r) for r in ratios]
        cut = sum(1 for v in rows.values()
                  if c in v and (v[c]["bounded"] or v[c]["timeouts"]))
        print("%-14s %7d %7d %7d %8.3f %9.3f %11d"
              % (c, len(names), sum(1 for r in ratios if r < 1),
                 sum(1 for r in ratios if r > 1),
                 math.exp(sum(lg) / len(lg)), sorted(ratios)[len(ratios) // 2],
                 cut))

    # Each configuration's comparable set is its own: a driver is in it only if
    # neither that configuration nor the baseline was cut off on it, and a
    # slower configuration is cut off more often. So the rows above are each
    # true, and they are not each other's like-for-like -- on GSL the two STP
    # rows happened to share all 18 drivers while Z3's 12 were a subset. This
    # block is the intersection, where every configuration can be compared to
    # every other.
    common = None
    for c in labels:
        if c == base:
            continue
        s = {k for k, v in rows.items() if comparable(v, c)}
        common = s if common is None else (common & s)
    if common:
        print("\non the %d drivers comparable under EVERY configuration" % len(common))
        print("%-14s %8s %7s %7s" % ("config", "geomean", "wins", "losses"))
        for c in labels:
            if c == base:
                continue
            rs = [rows[k][c]["solver"] / rows[k][base]["solver"] for k in common]
            print("%-14s %8.3f %7d %7d"
                  % (c, math.exp(sum(map(math.log, rs)) / len(rs)),
                     sum(1 for r in rs if r < 1), sum(1 for r in rs if r > 1)))

    # The comparable set is a minority here and always will be: a driver that
    # uses its whole budget is the normal case, not the exception. For those,
    # the question is not who finished sooner -- nobody finished -- but who got
    # further in the same time. Instructions and coverage are what "further"
    # means, and both are reported per driver against the baseline so that one
    # library's size does not decide the answer.
    print("\nsame budget, work done against %s (all drivers that ran under both)"
          % base)
    print("%-14s %7s %10s %9s %9s %11s"
          % ("config", "n", "instr x", "queries x", "cover +", "more/fewer"))
    for c in labels:
        if c == base:
            continue
        names = [k for k, v in rows.items()
                 if c in v and base in v
                 and v[c].get("instr", 0) > 0 and v[base].get("instr", 0) > 0]
        if not names:
            print("%-14s %7s" % (c, "-")); continue
        ins = [rows[k][c]["instr"] / rows[k][base]["instr"] for k in names]
        qs = [(rows[k][c]["queries"] / rows[k][base]["queries"]) for k in names
              if rows[k][base].get("queries", 0) > 0
              and rows[k][c].get("queries", 0) > 0]

        def cov(r):
            return (100.0 * r["fn_lines_cov"] / r["fn_lines"]) if r["fn_lines"] else None
        dc = [cov(rows[k][c]) - cov(rows[k][base]) for k in names
              if cov(rows[k][c]) is not None and cov(rows[k][base]) is not None]
        gm = lambda xs: math.exp(sum(map(math.log, xs)) / len(xs)) if xs else float("nan")
        print("%-14s %7d %10.3f %9.3f %+9.2f %11s"
              % (c, len(names), gm(ins), gm(qs),
                 (sum(dc) / len(dc)) if dc else float("nan"),
                 "%d/%d" % (sum(1 for r in ins if r > 1),
                            sum(1 for r in ins if r < 1))))

    print("\nper library, geomean of solver time against %s" % base)
    others = [c for c in labels if c != base]
    print("%-10s %6s  %s" % ("library", "n", "  ".join("%-9s" % c for c in others)))
    for lib in libs:
        if lib not in per_lib:
            continue
        cells, n_any = [], 0
        for c in others:
            names = [k for k, v in per_lib[lib].items() if comparable(v, c)]
            n_any = max(n_any, len(names))
            if not names:
                cells.append("%-9s" % "-"); continue
            lg = [math.log(per_lib[lib][k][c]["solver"]
                           / per_lib[lib][k][base]["solver"]) for k in names]
            cells.append("%-9.3f" % math.exp(sum(lg) / len(lg)))
        print("%-10s %6d  %s" % (lib, n_any, "  ".join(cells)))
    print("\nSolver time: <1 is faster than %s. Work done: >1 is further in the "
          "same budget.\nBoth are geometric means over per-driver ratios, so a "
          "library with three\ncomparable drivers counts as much per driver as "
          "one with sixty." % base)


if __name__ == "__main__":
    main()
