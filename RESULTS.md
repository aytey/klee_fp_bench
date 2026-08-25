# Does STP decide these queries faster than Bitwuzla?

Yes, on solver time, by about 15% per driver — and the qualifications matter
more than the number. Half the drivers show no meaningful difference, the win
is concentrated in four of the seven libraries, and it converts into 0.32
points less coverage rather than more, which is too small a difference to call.

The larger finding is not about either solver. **Twenty-nine percent of all
solver time goes to sixty-nine queries, a tenth of a percent of them, that are
never answered** -- they hit the 30-second cap and their state is discarded.
Both solvers spend their time the same way. That tail, not the average query,
is where a benchmark on this corpus should be aimed.

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

## Where the time actually goes

96.1% of wall clock is inside the SMT solver, and 98.5% is inside the solver
stack including KLEE's caches. Whatever limits exploration here, it is the
solver: nothing else is big enough to matter.

Almost nothing reaches it. Of 34.3 million queries KLEE issued, 61,041 -- 0.18%
-- were passed to the solver; the counterexample cache absorbed the rest at a
98.7% hit rate. And of those 61,041, the cost is concentrated to a degree that
is hard to overstate:

| | queries | share of queries | solver time | share of time |
| --- | ---: | ---: | ---: | ---: |
| answered | 60,972 | 99.89% | 4,974s | 70.6% |
| **hit the 30s cap** | **69** | **0.11%** | **>=2,070s** | **29.4%** |

**Twenty-nine percent of all solver time is spent on sixty-nine queries that
are never answered.** Each burns the full cap and then its state is killed, so
the work is not merely expensive, it is discarded. Bitwuzla's profile is the
same shape: 61 queries, 28.6% of its time.

## A fixed budget does not settle it

| configuration | instructions | queries | coverage |
| --- | ---: | ---: | ---: |
| stp-tuned | 0.930x | 1.030x | -0.32 |
| stp-shipped | 0.962x | 1.039x | +0.06 |
| z3 | 0.660x | 0.608x | -0.44 |

An earlier draft of this file read the 0.930x as "the speed buys nothing". That
was wrong, and the control that catches it is worth stating: split the corpus
by whether the budget bound the run.

| | n | instructions | solver queries | ms/query |
| --- | ---: | ---: | ---: | ---: |
| neither cut off | 147 | 1.000x | 0.998x | **0.792x** |
| both hit the budget | 106 | 0.778x | 1.042x | 0.943x |

Where nothing was cut off, exploration is *identical* -- as it must be, since
DFS with the same feasible branches walks the same tree -- and STP is 21%
cheaper per query. Where the budget binds, a timed-out query kills a state, and
the two configurations time out different queries: 69 against 61, over 53 and
52 drivers, which is half the sample. From that point the runs are not
exploring the same tree, so "instructions executed" stops being a measure of
progress and the 0.778x cannot be attributed to solver speed.

What survives is coverage, which is an outcome rather than a path: -0.32 points
out of a hundred. On this corpus, a 21% cheaper query buys a difference in
coverage too small to call.

A cache hypothesis died the same way. On budget-bound drivers STP appeared to
miss KLEE's counterexample cache more (94.93% against 95.67%) and to need 39%
more solver queries per instruction. On the 154 drivers where exploration was
identical, the hit rates are 98.69% and 98.67% and STP issues slightly *fewer*
queries. The difference was an artefact of the two runs covering different
regions -- early DFS is query-dense, deep paths reuse the cache -- not a
property of either solver.

## The cap is worth more than the solver

The tail above is 69 queries that each burn the full 30-second cap and are then
discarded. Lowering the cap does not make them answerable; it makes them cheap
to give up on. Measured over the 45 budget-bound drivers, 30s against 5s:

| cap | instructions | coverage | drivers covering more |
| --- | ---: | ---: | ---: |
| 30s | 11,276,732 | 86.55% | |
| **5s** | **13,407,067** | **87.50%** | 2 of 45, none worse |

Per driver that is **2.165x the instructions** and **+0.95 coverage points**,
against the -0.32 that separates STP from Bitwuzla. A harness parameter is
worth about three times the choice of solver here, in the opposite direction.

Read the +0.95 carefully: 43 of the 45 drivers covered exactly the same lines,
two covered more, none covered less. It is a tail, not a broad shift.

And that points at something structural. Mean coverage is already 86.55% at the
30-second cap; doubling the exploration moves it to 87.50%. **Coverage on this
corpus is close to saturated** -- a driver reaches most of its target function
early and the residue is either unreachable or needs an input no amount of
extra depth is likely to find. Which is why neither the solver nor the cap
moves it much, and why solver time rather than coverage is where this benchmark
discriminates. Anyone tuning against the coverage number should know that
before they start.

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
