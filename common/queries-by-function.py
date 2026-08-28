#!/usr/bin/env python3
"""Instructions and solver queries per subprogram, from KLEE's run.istats.

    queries-by-function.py <klee-output-dir>...

KLEE already attributes both to the function whose instruction caused them; no
patch and no extra flag are needed. run.istats is callgrind format, so `fn=`
records name the subprogram and the cost lines under them carry the events
listed by the `events:` line.

This is the difference between "how much program ran" and "how much the solver
was asked", and on this corpus those are not the same question and sometimes
not even the same direction. Driving f2cblaslapack's blocked LU at binary128
executes 5.6M instructions, of which 96.5% are qlamc4_ -- LAPACK determining
machine epsilon by iterated halving, a loop whose length scales with the
exponent range and which issues *no queries at all*. The 37 queries that do
reach the solver come from about 1,600 instructions elsewhere.

Two traps, both of which give plausible wrong answers:

* **A cost line following `calls=` is the inclusive cost of that call.** Summing
  it with the self costs counts every callee once per caller; it read 6,043,685
  queries for a run that issued 29.
* **`Q` is the pre-cache counter.** It counts what was asked of the solver
  chain, most of which the independent solver and the counterexample cache
  answer without the SMT solver seeing it -- 864,304 on a run whose info file
  says 39. `Qv + Qiv` is what was actually solved, and is the number that
  matches what dump-queries.sh writes out.
"""
import collections
import os
import sys


def read(path):
    """(instructions, solved queries, solver time in us) per subprogram."""
    ins, q, t = collections.Counter(), collections.Counter(), collections.Counter()
    ev, fn, skip = None, "?", False
    for line in open(path, errors="ignore"):
        if line.startswith("events:"):
            ev = line.split()[1:]
        elif line.startswith("fn="):
            fn, skip = line[3:].strip(), False
        elif line.startswith("calls="):
            skip = True                      # the next line is inclusive
        elif ev and line[:1].isdigit():
            if skip:
                skip = False
                continue
            v = line.split()[2:]
            if len(v) < len(ev):
                continue
            ins[fn] += int(v[ev.index("I")])
            q[fn] += int(v[ev.index("Qv")]) + int(v[ev.index("Qiv")])
            t[fn] += int(v[ev.index("Qtime")])
    return ins, q, t


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ins, q, t = collections.Counter(), collections.Counter(), collections.Counter()
    seen = 0
    for root in sys.argv[1:]:
        for dirpath, _, files in os.walk(root):
            if "run.istats" in files:
                a, b, c = read(os.path.join(dirpath, "run.istats"))
                ins.update(a); q.update(b); t.update(c); seen += 1
    if not seen:
        sys.exit("no run.istats under: " + " ".join(sys.argv[1:]))

    ti, tq = sum(ins.values()), sum(q.values())
    print("%-28s %12s %7s %8s %11s" %
          ("subprogram", "instrs", "share", "queries", "solver ms"))
    print("-" * 70)
    for fn, c in ins.most_common(15):
        if not c and not q[fn]:
            continue
        print("%-28s %12d %6.1f%% %8d %11.1f"
              % (fn, c, 100.0 * c / ti if ti else 0, q[fn], t[fn] / 1000.0))
    print("-" * 70)
    print("%-28s %12d %7s %8d %11.1f"
          % ("TOTAL (%d run%s)" % (seen, "" if seen == 1 else "s"),
             ti, "", tq, sum(t.values()) / 1000.0))


if __name__ == "__main__":
    main()
