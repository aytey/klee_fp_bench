# Does STP decide these queries faster than Bitwuzla?

Yes, on solver time, by about 15% per driver — and the qualification matters
more than the number. Half the drivers show no meaningful difference, the win
is concentrated in four of the seven libraries, and the extra speed does not
turn into more exploration or more coverage under a fixed budget.

Swept 2026-08-25: 255 drivers sampled from the corpus of 1118, four
configurations each, 1020 runs. 60s exploration budget, 30s query cap, DFS,
twelve at a time.

## Solver time

On the 117 drivers where no configuration was cut off — so all four answered
the same questions and their times can be put side by side:

| configuration | geomean vs bitwuzla | wins | losses |
| --- | ---: | ---: | ---: |
| **stp-tuned** | **0.851** | **62** | 33 |
| stp-shipped | 1.879 | 15 | 72 |
| z3 | 1.771 | 34 | 57 |

`stp-tuned` is `--stp-sat-solver=minisat --stp-incremental-engage-at=8
--stp-adapt-incremental --use-forked-solver=false`. `stp-shipped` is MiniSat
and nothing else.

**The configuration is worth more than the solver.** Two settings of the same
STP are 2.2x apart, which is larger than the gap to Bitwuzla in either
direction. Measured stock, STP loses to Bitwuzla clearly; measured tuned, it
wins. Any comparison that does not say which was run is not saying much.

**The median is 0.988.** The geometric mean is carried by a tail of large wins,
not by a broad shift: on half the comparable drivers the two solvers are within
a percent or two of each other.

## The same win is not spread evenly

| library | n | stp-tuned | stp-shipped | z3 |
| --- | ---: | ---: | ---: | ---: |
| gmp | 19 | **0.619** | 2.167 | 1.193 |
| gsl | 18 | **0.791** | 2.850 | 7.573 |
| blis | 18 | **0.893** | 2.318 | 1.593 |
| openlibm | 28 | **0.904** | 2.532 | 3.075 |
| fftw | 13 | 1.042 | 1.159 | 1.202 |
| cxsparse | 17 | 1.044 | 1.224 | 1.185 |
| sundials | 19 | 1.088 | 1.114 | 0.990 |

STP wins on four libraries and loses slightly on three. The four it wins are
the ones whose queries come from deep expression trees over a few symbolic
doubles — special functions, elementary functions, multi-precision conversion.
The three it loses are the ones whose queries are shallower and more numerous.

## Under a fixed budget, the speed buys nothing

A faster solver in a fixed budget does not finish sooner, it does more. Across
all 248 drivers that ran under both, per driver against Bitwuzla:

| configuration | instructions | queries | coverage |
| --- | ---: | ---: | ---: |
| stp-tuned | 0.930x | 1.030x | -0.32 |
| stp-shipped | 0.962x | 1.039x | +0.06 |
| z3 | 0.660x | 0.608x | -0.44 |

**This is the result that complicates the headline.** `stp-tuned` decides its
queries measurably faster and then executes *fewer* instructions and covers
*slightly less* than Bitwuzla in the same sixty seconds. Whatever is limiting
exploration on most of this corpus, it is not how quickly the solver answers.

OpenLibm is the exception and shows what the other case looks like: there
`stp-tuned` runs 1.082x the instructions and 1.086x the queries. Its drivers
are almost pure solver -- `sin` spends 99.5% of its budget there -- so
throughput is the binding constraint and a faster decision procedure converts
straight into path. On GSL, which is not solver-bound, the same speed advantage
produced 0.948x the instructions.

## What this does not establish

**One run, one machine, no repetition.** There is no variance estimate here.
Twelve KLEE processes share a box, so a driver's time depends a little on what
else was running; configurations are interleaved per driver rather than run as
blocks, which controls for drift but not for noise. A 4% difference in this
table is not a result. A 2.2x one is.

**255 of 1118 drivers**, sampled by a per-library stride so that GSL's 646 do
not decide the answer on their own.

**108 of 255 drivers were cut off** under the STP configurations and 121 under
Z3 -- exhausted the budget or timed out a query. Those are excluded from the
solver-time comparison and included in the work-done one, which is the only
honest way to use them, but it does mean the two tables are answering slightly
different questions over different sets.

**Nothing here is about correctness.** No configuration disagreed with another
about a query; this measures speed only.
