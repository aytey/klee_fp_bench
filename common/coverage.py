#!/usr/bin/env python3
"""Coverage of one GSL function, from the profiles a replay run produced.

    coverage.py <replay-binary> <profraw-dir> <target-function>

Prints one CSV line:

    fn_lines_covered,fn_lines,fn_branches_covered,fn_branches,
    all_lines_covered,all_lines,all_branches_covered,all_branches

The first four describe the driver's target function -- the GSL API entry point
the driver calls, which is what the benchmark is about. The last four are the
whole replayed binary, the analogue of the APSEC harness's whole-file totals.

The APSEC harness measured this with gcov, reading a function's line range out
of a checked-in JSON file and counting '#####' markers in the .gcov report.
Clang's source-based coverage reports per function directly, and gives branch
coverage as well, so that is what is used here; the numbers mean the same
thing, and each replay writes its own profile rather than accumulating into
shared .gcda files, which is what makes running the suite in parallel safe.
"""
import json
import os
import re
import subprocess
import sys

LLVM_COV = os.environ.get("LLVM_COV", "/usr/bin/llvm-cov")
LLVM_PROFDATA = os.environ.get("LLVM_PROFDATA", "/usr/bin/llvm-profdata")

# "name  regions miss cover lines miss cover branches miss cover"
ROW = re.compile(r"^(\S+)\s+"
                 r"(\d+)\s+(\d+)\s+\S+\s+"      # regions
                 r"(\d+)\s+(\d+)\s+\S+\s+"      # lines
                 r"(\d+)\s+(\d+)\s+\S+\s*$")    # branches


def main():
    binary, profdir, target = sys.argv[1:4]

    raw = sorted(os.path.join(profdir, f)
                 for f in os.listdir(profdir) if f.endswith(".profraw"))
    if not raw:
        print("0,0,0,0,0,0,0,0")
        return

    profdata = os.path.join(profdir, "merged.profdata")
    # A replay that crashed leaves a truncated profile; --failure-mode=any is
    # the default and would lose the whole run, so tolerate the bad ones.
    subprocess.run([LLVM_PROFDATA, "merge", "-sparse", "-failure-mode=all",
                    "-o", profdata] + raw,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(profdata):
        print("0,0,0,0,0,0,0,0")
        return

    common = [binary, "-instr-profile=" + profdata]

    # Whole-binary totals, and the source files that make it up.
    out = subprocess.run([LLVM_COV, "export"] + common + ["--summary-only"],
                         capture_output=True, text=True).stdout
    try:
        data = json.loads(out)["data"][0]
    except (ValueError, KeyError, IndexError):
        print("0,0,0,0,0,0,0,0")
        return
    tot = data["totals"]
    allv = (tot["lines"]["covered"], tot["lines"]["count"],
            tot["branches"]["covered"], tot["branches"]["count"])

    # The target function's own row. -show-functions needs the source files
    # named; take them from the summary rather than keeping a function-to-file
    # map in step with GSL.
    srcs = [f["filename"] for f in data["files"] if f["filename"].endswith(".c")]
    fn = (0, 0, 0, 0)
    if srcs:
        rep = subprocess.run([LLVM_COV, "report"] + common +
                             ["-show-functions"] + srcs,
                             capture_output=True, text=True).stdout
        for line in rep.splitlines():
            m = ROW.match(line)
            if m and m.group(1) == target:
                _, _, _, lines, lmiss, brs, bmiss = m.groups()
                fn = (int(lines) - int(lmiss), int(lines),
                      int(brs) - int(bmiss), int(brs))
                break

    print(",".join(str(x) for x in fn + allv))


if __name__ == "__main__":
    main()
