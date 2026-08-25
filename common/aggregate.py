#!/usr/bin/env python3
"""Summarise a sweep.

    aggregate.py <library>

The question this benchmark exists to answer is whether one solver decides
floating-point queries faster than another, and the trap in answering it is
that a fixed time budget does not measure that. **A faster solver does not
finish sooner, it does more work** -- it explores further, issues different
queries, and ends up with a *larger* total of solver time than the slow one. So
neither the totals nor a whole-set ms/query is a like-for-like comparison.

What is comparable is the drivers where nothing was cut off: no configuration
hit the exploration budget and none had a query time out. There the
configurations answered the same questions, and their solver times can be put
side by side. That set is usually a minority of the suite and it is the one to
read; the whole-set figures are printed too, so the size of the distortion is
visible rather than hidden.

Coverage of the driver's target function is the outcome metric, and it is
reported separately for a reason: it is what a faster solver is supposed to
buy, and it does not always follow per-query cost.
"""
import collections
import math
import os
import sqlite3
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("FP_BENCH_WORK", "/mnt/baranem/fp_bench-work")

FIELDS = ("label driver rc wall ntests nerr nreplayed "
          "fn_lines_cov fn_lines fn_br_cov fn_br "
          "all_lines_cov all_lines all_br_cov all_br").split()


def run_stats(out, label, name):
    path = os.path.join(out, label, name, "run.stats")
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        cols = [r[1] for r in con.execute("PRAGMA table_info(stats)")]
        row = con.execute(
            "SELECT * FROM stats ORDER BY rowid DESC LIMIT 1").fetchone()
        con.close()
    except Exception:
        return {}
    if row is None:
        return {}
    d = dict(zip(cols, row))
    return {"solver": d.get("SolverTime", 0) / 1e6,
            "queries": d.get("SolverQueries", 0),
            "instr": d.get("Instructions", 0)}


def log_facts(out, label, name):
    """Whether the budget bound the run, and how many queries timed out.

    Both end a state, so both make one configuration's exploration diverge from
    another's -- which is what disqualifies a driver from the comparable set.
    """
    path = os.path.join(out, label, name + ".log")
    bounded, timeouts = False, 0
    try:
        for line in open(path, errors="ignore"):
            if "halting execution" in line:
                bounded = True
            elif line.startswith("KLEE: ERROR: ") and "Query timed out" in line:
                timeouts += 1
    except OSError:
        pass
    return bounded, timeouts


def load(out):
    rows = collections.defaultdict(dict)
    path = os.path.join(out, "results.psv")
    if not os.path.exists(path):
        sys.exit("no results at %s -- run matrix-sweep.sh first" % path)
    for line in open(path):
        parts = line.strip().split("|")
        if len(parts) != 8:
            continue
        head, cov = parts[:7], parts[7].split(",")
        if len(cov) != 8:
            continue
        r = dict(zip(FIELDS, head + cov))
        for k in FIELDS[2:]:
            r[k] = float(r[k]) if k == "wall" else int(r[k])
        r.update(run_stats(out, r["label"], r["driver"]))
        r["bounded"], r["timeouts"] = log_facts(out, r["label"], r["driver"])
        r["bounded"] = r["bounded"] or r["rc"] != 0
        rows[r["driver"]][r["label"]] = r
    return rows


def pct(cov, tot):
    return 100.0 * cov / tot if tot else 0.0


def main():
    lib = sys.argv[1] if len(sys.argv) > 1 else "gsl"
    out = os.environ.get("FP_BENCH_OUT", os.path.join(WORK, lib, "runs"))
    rows = load(out)
    labels = sorted({c for v in rows.values() for c in v})
    complete = sorted(n for n in rows if len(rows[n]) == len(labels))
    print("%s: %d drivers, %d configurations, %d ran under all"
          % (lib, len(rows), len(labels), len(complete)))
    if not complete:
        return

    def tot(label, field, names):
        return sum(rows[n][label].get(field, 0) for n in names)

    def table(title, names):
        if not names:
            return
        print("\n=== %s (%d drivers) ===" % (title, len(names)))
        print("%-22s %10s %9s %10s %11s %12s %8s %7s"
              % ("config", "solverT", "ratio", "queries", "ms/query", "instr",
                 "timeouts", "killed"))
        base = min(tot(c, "solver", names) for c in labels) or 1.0
        for c in labels:
            st, q = tot(c, "solver", names), tot(c, "queries", names)
            print("%-22s %10.1f %8.2fx %10d %11.1f %12d %8d %7d"
                  % (c, st, st / base, q, (1000 * st / q) if q else 0,
                     tot(c, "instr", names), tot(c, "timeouts", names),
                     sum(1 for n in names if rows[n][c]["rc"] != 0)))

    clean = [n for n in complete
             if not any(rows[n][c]["bounded"] or rows[n][c]["timeouts"]
                        for c in labels)]
    table("whole set -- a faster solver does MORE work, not less", complete)
    table("nothing cut off: the comparable set", clean)
    if clean:
        instr = {c: tot(c, "instr", clean) for c in labels}
        q = {c: tot(c, "queries", clean) for c in labels}
        print("work done: %s" % ("IDENTICAL under every configuration"
                                 if len(set(instr.values())) == 1
                                 and len(set(q.values())) == 1
                                 else "differs -- "
                                 + "/".join(str(instr[c]) for c in labels)))

    print("\n=== coverage of the driver's target function ===")
    print("%-22s %9s %9s %9s %8s %8s %9s"
          % ("config", "line%", "branch%", "medLine%", "full", "none", "tests"))
    for c in labels:
        rs = [rows[n][c] for n in complete]
        lines = [pct(r["fn_lines_cov"], r["fn_lines"]) for r in rs]
        brs = [pct(r["fn_br_cov"], r["fn_br"]) for r in rs]
        print("%-22s %9.2f %9.2f %9.2f %8d %8d %9d"
              % (c, statistics.mean(lines), statistics.mean(brs),
                 statistics.median(lines),
                 sum(1 for r in rs if r["fn_lines"]
                     and r["fn_lines_cov"] == r["fn_lines"]),
                 sum(1 for r in rs if r["fn_lines_cov"] == 0),
                 sum(r["ntests"] for r in rs)))

    # Per driver against a baseline, which is steadier than a total: a total
    # over a suite like this is settled by a handful of drivers in the tail.
    base = os.environ.get("BASELINE", labels[0])
    if base in labels and len(labels) > 1:
        print("\n=== against %s, per driver, where neither was cut off ===" % base)
        print("%-22s %6s %10s %10s %7s %7s %9s"
              % ("config", "n", "solverT", "baseT", "wins", "losses", "geomean"))
        for c in labels:
            if c == base:
                continue
            names = [n for n in complete
                     if not (rows[n][c]["bounded"] or rows[n][c]["timeouts"]
                             or rows[n][base]["bounded"]
                             or rows[n][base]["timeouts"])]
            if not names:
                continue
            lg = [math.log(rows[n][c]["solver"] / rows[n][base]["solver"])
                  for n in names
                  if rows[n][c].get("solver", 0) > 0
                  and rows[n][base].get("solver", 0) > 0]
            print("%-22s %6d %10.1f %10.1f %7d %7d %9.3f"
                  % (c, len(names), tot(c, "solver", names),
                     tot(base, "solver", names),
                     sum(1 for n in names
                         if rows[n][c]["solver"] < rows[n][base]["solver"]),
                     sum(1 for n in names
                         if rows[n][c]["solver"] > rows[n][base]["solver"]),
                     math.exp(sum(lg) / len(lg)) if lg else float("nan")))


if __name__ == "__main__":
    main()
