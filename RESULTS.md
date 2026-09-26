# Does STP decide these queries faster than Bitwuzla?

> **Measured 2026-08-26 to 2026-08-28, and not re-measured since.** Read this as
> a record of what those sweeps found, not as a current comparison. The STP in
> these numbers predates its floating-point abstraction, which has since been
> merged into `stp/stp` master and moves them substantially; the sections below
> say which build each finding came from, including the one taken against a
> tree 27 commits behind master. `common/configs/*.tsv` holds the
> configurations and `git log` the order they were run in.


Yes, on solver time, by about 23% per driver — and the qualifications matter
more than the number. The win is concentrated in a few of the seven libraries,
it does not convert into coverage, and two settings of STP itself are further
apart than STP and Bitwuzla are.

Swept twice. 255 drivers sampled from the corpus of 1118, four configurations,
1020 runs each time, 60s exploration budget, DFS, twelve at a time. The first
sweep used a 30-second query cap; the second used 5 seconds, after that was
measured to be worth 2.165x the exploration. **The 5-second numbers are the
ones to read** -- they describe the configuration this repository now
recommends -- and the older ones are kept beside them because a headline that
moves when a harness parameter changes should be visible as such.

## Solver time

On the drivers where no configuration was cut off — so all four answered the
same questions and their times can be put side by side:

| configuration | 5s cap (n=115) | 30s cap (n=117) |
| --- | ---: | ---: |
| **stp-tuned** | **0.773** | 0.851 |
| stp-shipped | 1.921 | 1.879 |
| z3 | 1.653 | 1.771 |

Per driver against Bitwuzla over each configuration's own comparable set, at
the 5-second cap: stp-tuned wins 71 and loses 32 of 129; stp-shipped wins 21
and loses 83; Z3 wins 35 and loses 53. Tightening the cap moved STP's advantage
from 0.851 to 0.773 and its record from 67-42 to 71-32, so the change made for
other reasons happens to favour it.

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

## On identical queries, outside KLEE

1,112 queries were dumped from twenty timeout-prone drivers and replayed
against both binaries directly -- same file to each, no cache, no budget, no
divergence. The two solvers **never disagreed** on any of the 1,082 they both
decided, which is the one unambiguous result in this section.

The speed result depends on how it is aggregated, and the spread is not small:

| the same 1,082 queries, aggregated | stp/bitwuzla |
| --- | ---: |
| per query, geometric mean | **0.602** |
| ratio of totals | 0.710 |
| per driver, geometric mean | **1.288** |

Per query STP wins by a distance; per driver it loses. Both are computed from
the same measurements and neither is wrong. They answer different questions,
and the reason they diverge is that this corpus is badly unbalanced:

| driver | queries | stp | bitwuzla | ratio |
| --- | ---: | ---: | ---: | ---: |
| mpf_set_q | 438 | 9.70s | 30.23s | **0.32** |
| mpq_get_d | 408 | 9.37s | 22.19s | **0.42** |
| SUNLinSolSetup_Dense | 26 | 0.83s | 0.95s | 0.87 |
| *the other 17 drivers* | 210 | | | 1.03 - 3.98 |

**Two GMP drivers are 78% of the corpus.** They are where a double is taken
apart and reassembled, STP is three times faster on them, and they carry the
per-query mean on their own. On seventeen of the twenty drivers STP is slower,
by as much as 4x on gsl_sf_fermi_dirac_mhalf_e.

So this replay does not establish a general per-query advantage for STP. What
it establishes is narrower and still useful: **STP is much faster on the
bit-manipulation queries GMP produces and slower on the special-function
queries GSL produces**, and no amount of aggregation makes that one number.

It also does not agree with the in-KLEE sweep, which had GSL at 0.791 in STP's
favour. The corpora are different -- twenty drivers chosen for timing out,
against 255 sampled by stride -- and the replayed queries were dumped from
Z3-driven runs, so they are Z3's distribution. The sweep is the better-founded
of the two.

### Asking for a model costs STP six times what it costs Bitwuzla

The replay above asks only `check-sat`. KLEE almost always needs a
counterexample. Adding `(get-model)` to all 1,082:

| | check-sat | with model | cost |
| --- | ---: | ---: | ---: |
| stp | 39.0s | 46.5s | **+19%** |
| bitwuzla | 63.4s | 65.5s | +3% |

That asymmetry is real and it is paid on nearly every query KLEE issues. It is
the one concrete lead this exercise produced for closing the distance between
STP's standalone behaviour and its behaviour inside KLEE.

### And a hypothesis that died

An earlier version of this file claimed STP's advantage was 21% on easy queries
and 6% on hard ones, and proposed work to close that gap. There is no such gap:
that came from comparing two KLEE runs that had explored different regions and
were therefore answering different questions. What replaced it -- that the
answer depends on whether you count per query or per driver -- is a caution
about method rather than a finding about solvers.

## Where STP loses, and why

STP is slower than Bitwuzla on exactly one class of query, and the correlation
across the 1,082 replayed queries is monotone:

| queries containing | n | stp/bitwuzla |
| --- | ---: | ---: |
| `fp.div` or `fp.sqrt` | 9 | **2.45x slower** |
| `fp.mul`, no div or sqrt | 34 | **1.49x slower** |
| none of those | 1,039 | **0.58x -- 1.7x faster** |

The multiplicative floating-point operations, and nothing else. The GMP drivers
STP dominates contain no `fp.div` and no `fp.sqrt` at all.

A 2,715-byte query with three `fp.div` and two `fp.sqrt` expands to 1.7 million
clauses. Taking that one query apart in both solvers:

| | STP | Bitwuzla | |
| --- | ---: | ---: | --- |
| CNF clauses | 1,703,895 | 1,374,204 | comparable |
| CNF variables | 364,585 | 454,458 | comparable |
| bit-blasting | 370ms | 158ms | 2.3x |
| **AIG to CNF encoding** | **1857ms** | **542ms** | **3.4x** |
| SAT solving | 137ms | 113ms | comparable |
| total | 2.63s | 1.37s | 1.9x |

**Bitwuzla solves it the same way STP does.** Eager bit-blasting, a CNF of the
same order, and a SAT search that takes the same time. The whole difference is
that it converts the circuit to CNF 3.4x faster -- 2.53M clauses per second
against 917K. The stage that costs STP the query is the one doing no reasoning.

### Two things this rules out

**Abstraction.** STP's `--bv-term-abstraction` at significand widths -- the
"abstract at 53 and 64 bits" idea that had been sitting on the backlog -- makes
these queries **5 to 7 times slower**, monotonically worse the more it
abstracts: 10x at width 64, 15x at width 16, every answer still correct. The
refinement loop costs far more than the circuit it avoids. Bitwuzla, which has
an abstraction module on by default, is also *faster with it turned off* on
this set. It is not how either solver wins here.

**Tuning.** Every relevant STP option was measured on the 43 affected queries:
`--bb.div-v1/v2/v3`, `--bb.mult-variant`, `--bb.conjoin-constant`,
`--bb.simplify-during-bb`, `--aig-core-simplification`, `--bb.fp-native-arith`,
`--fp-domain-simplify`. All land between 1.8x and 2.2x. None helps, and none
gave a wrong answer.

The one measured lever is STP's AIG-to-CNF conversion.

## Does --stp-adapt-incremental earn its place?

On four drivers chosen because STP lost badly on them, dropping it looked like
a clear win: atan2 goes from 14.92s to 3.23s and atan2f from 3.05s to 0.53s, on
identical work. Swept over the whole corpus -- 255 drivers, three
configurations, 765 runs, 128 comparable under all three -- it is a wash, and
the two ways of summarising it disagree:

| | stp-adapt | stp-noadapt |
| --- | ---: | ---: |
| geometric mean per driver, against Bitwuzla | 0.854 | **0.810** |
| drivers won against Bitwuzla | 68 of 128 | 69 of 128 |
| **total solver time** | **108.3s** | 141.2s |

Dropping it is 5% better per driver and 30% worse in total, which is the
signature of a policy that wins big on a few expensive drivers and loses a
little on many cheap ones. Per library it splits four to two, with GSL neutral:

| library | no-adapt / adapt |
| --- | ---: |
| openlibm | 0.832 |
| blis | 0.859 |
| cxsparse | 0.917 |
| sundials | 0.925 |
| gsl | 1.015 |
| fftw | 1.078 |
| gmp | 1.152 |

**The flag stays.** atan2's 5.3x penalty is real and pathological, not typical,
and the sample that suggested otherwise was chosen for containing exactly such
cases. STP beats Bitwuzla either way -- 0.854 with the flag, 0.810 without --
so this is not what separates them.

## Why STP is slower inside KLEE than as a binary, and why that is still open

The same nominal queries cost KLEE's STP 5-6s and the STP binary 0.8s, an 8x
gap on atan2. Five explanations were measured and eliminated:

* **Formula construction.** Per-query phase timing over 90 queries: build and
  assert together, 0ms of 5783ms. All of it is solve.
* **Encoding asymmetry.** Both backends use native floating point --
  `vc_fpDivExpr` against `BITWUZLA_KIND_FP_DIV`. Neither is handed a
  pre-blasted bitvector problem.
* **Counterexample construction.** On atan2's queries, adding `(get-model)`
  costs STP -3% and Bitwuzla +0%.
* **The query timeout.** Capped at 5s and uncapped are indistinguishable:
  5.00s against 5.49s, and 17.98s against 16.99s without incremental.
* **Accumulating solver state.** KLEE keeps one validity checker for the whole
  run where the Bitwuzla backend builds and destroys one per query, which
  predicts later queries getting slower. The per-query ratio *falls* across the
  run, 13.6x to 3.7x. The opposite.

A profile says where the time goes: 53% `Minisat::Solver::propagate`, 13%
`pickBranchLit`, 6% `cancelUntil`. **Three quarters of it is the SAT search.**
The STP binary on the same query files barely enters MiniSat at all; its
profile is dominated by dynamic linking and parsing.

Which forces a correction to the method. "The same queries" was never true: the
SMT-LIB corpus was produced by `--debug-z3-dump-queries`, so it is what KLEE
hands *Z3*. Comparing it against what KLEE hands STP is not controlled, and the
conclusion drawn from it -- that STP and Bitwuzla are at parity outside KLEE,
so the gap must be harness overhead -- does not follow.

**Both ways of capturing what KLEE actually sends STP are broken.** KLEE cannot
print floating-point expressions as SMT-LIBv2 and says so. And
`--debug-dump-stp-queries` calls STP's presentation-language printer, which
aborts on any floating-point node: `PLPrinter.cpp:396`, "the presentation
language has no floating-point". KLEE's error handler turns that into
`abort()`, so the flag crashes KLEE eleven queries in.

That fix was made -- KLEE's dump now uses `vc_printSMTLIB2`, which emits a
self-contained problem -- and it turned up the reason the whole approach could
not have worked:

```
SMTLIB2: a float-to-IEEE-bits node (an API-only operation)
         has no SMT-LIB spelling
```

**Reinterpreting a float's bits as a bitvector has no portable spelling here.**
STP reaches it only through its C API and cannot print it. Z3 prints it as
`fp.to_ieee_bv`, which is an extension: STP's parser rejects the token and
Bitwuzla's calls it an undefined symbol. So a query containing one cannot be
exported by STP, and cannot be read back by either solver if Z3 exported it.

That is not a corner case on this corpus. It is what an elementary function
does: **45 of atan2's 94 queries contain it.** Which retracts the result this
section was built on -- "STP and Bitwuzla are at parity outside KLEE, 1.05s
against 1.01s over 94 queries" was measured over a set where 45 of those 94
were instant failures for both. The 8x is not explained, and on drivers like
this one it cannot be investigated by export at all; the in-KLEE sweep is the
only instrument that works.

There is a concrete improvement in the way: teach STP's SMT-LIB2 printer and
parser Z3's `fp.to_ieee_bv` spelling. It would make floating-point queries
round-trip, which is what any cross-solver comparison on this corpus needs.

## Why coverage saturates, and why it should not be the headline

Coverage barely moves for any solver or setting -- 86.55% at a 30s query cap,
87.50% with twice the exploration. That is not because the remaining lines are
hard to reach. Taking the union of every configuration's tests, per driver:

| best coverage any configuration reached | drivers |
| --- | ---: |
| 100% | 145 |
| 90-99% | 18 |
| 50-89% | 62 |
| under 50% | 4 |

145 of 229 are already complete, and the 83 that are not are mostly not
*reachable*. Two mechanisms account for it, and neither is about the solver:

**KLEE replaces the function under test.** It substitutes its own intrinsics
for `sqrt` (102 drivers), `fabs` (70), `fabsf` (10), `sqrtf` (9), `__finite`
(6) and `__isnanf` (1). Usually that is harmless -- the driver is testing
something else that happens to call sqrt. For two drivers it is not:
`openlibm/sqrtf` and `openlibm/__isnanf` have their *target* replaced, so they
measure KLEE's intrinsic and never enter the library at all. sqrtf tops out at
21% of its lines, and what is missing is the whole software implementation:
subnormal handling, exponent unbiasing, the Newton iteration.

**Inline assembly.** 47 drivers report module-level assembly that KLEE ignores.
`openlibm/fma` reaches 4.8%, and the lines it never runs are `__fnstcw` and the
mxcsr accessors -- reading and setting the x87 rounding mode, which is
assembly by definition.

So the ~13% residue is largely structural, and coverage cannot discriminate
between solvers on it. **Solver time is the measurement that works here;
coverage should be read as a sanity check that a driver ran, not as a score.**

## Where to set the query cap, and where to stop

The cap decides how long a doomed query burns before its state is discarded, so
tightening it converts wasted solver time into exploration. Over the corpus:

| cap | solver time | queries answered | timed out | wasted |
| --- | ---: | ---: | ---: | ---: |
| 30s | 7044s | 61,041 | 69 | **29.4%** |
| **5s** | **5235s** | **98,545** | 124 | **11.8%** |

More queries time out and less time is lost to them: 61% more answered in 26%
less solver time.

Tighter still keeps paying in exploration and stops paying in anything else.
Over 37 budget-bound drivers, against the 5-second cap:

| | 2s | 1s |
| --- | ---: | ---: |
| instructions | 1.546x | **2.389x** |
| wasted solver time | 9.4% | 5.9% |
| mean coverage | | **-0.12 points** |
| drivers covering more / less / same | | 3 / 3 / 31 |
| hard-killed runs | | 0 to 2 |

**5 seconds is where to stop.** One second explores 2.4x as far and covers
nothing more, with swings of -33 points on one driver and +30 on another and
two runs that stop being able to finish at all. That is the coverage ceiling
again: a suite already at 86% on lines that are mostly structural cannot reward
more depth, so past a point the extra exploration is only variance.

## What actually separates STP from Bitwuzla, end to end

With KLEE's query dump fixed to emit SMT-LIB2, the queries KLEE sends STP can
finally be replayed as themselves. `gsl_sf_multiply_e` -- STP 0.83s against
Bitwuzla 0.10s inside KLEE, and free of the float-to-bits operator that blocks
export elsewhere -- gives the controlled measurement:

| gsl_sf_multiply_e, 11 queries | stp / bitwuzla |
| --- | ---: |
| standalone, wall clock | 3.13x |
| **standalone, minus process startup** | **5.70x** |
| inside KLEE | 7.94x |

Process startup is 9.5ms for both and has to come out; over eleven invocations
it was hiding nearly half the difference. What remains says the gap is
**mostly the solver**, about 5.7x, with the harness contributing a further 1.4x
-- not the other way round, as an earlier section of this file concluded from a
comparison that turned out to be measuring failed parses.

Two of the eleven queries carry it, and they are the two containing `fp.div`.
STP's own breakdown on one of them:

```
 CNF Conversion:  142ms   <- 70%
 Bit Blasting:     24ms
 Sending to SAT:   19ms
 SAT Solving:      14ms   <-  7%
```

The same signature as every other query STP loses on: the cost is translating
the circuit, not searching it.

### Which is what the adaptive CNF effort fixes

STP's effort scale trades generation time for a smaller CNF, and its default
makes that trade on every query. Choosing per query from the AIG size
(`--cnf-generation-effort auto`, now the default on the `aytey_20260826_cnf_effort`
branch) halves the deficit on exactly these queries:

| on the queries KLEE sends STP | vs bitwuzla |
| --- | ---: |
| stp, shipped default | 5.61x |
| **stp, auto** | **2.88x** |

End to end in KLEE, over fourteen drivers spanning all seven libraries: solver
time **0.914** against stock STP, and on the drivers not pinned at their
exploration budget, `bli_gemv` 0.58, `atan2` 0.67, `gsl_cdf_rayleigh_P` 0.83.

An earlier measurement said this change did nothing for KLEE. That test forced
`very-low` for every query, which is not what the change does and is worse than
either fixed level -- `very-low` loses to `medium` below about 28k AIG nodes,
where most of KLEE's queries live. Testing an adaptive policy by pinning it to
one of its outcomes tests something else.

## Where STP's auto CNF threshold belongs for KLEE

`CNF_EFFORT_AUTO` (STP PR #998) drops CNF generation to very low once the AIG
is big enough that building a good CNF costs more than solving a worse one. It
defaults the crossover to 200k AND-nodes, and deliberately conservatively: where
the crossover falls is a property of the query stream, and a general-purpose
solver cannot know its caller's. KLEE's stream is thousands of small queries
with a tail of enormous ones, decided by an incremental solver holding state
across them, which is not the distribution the default was measured against.

Sweeping the threshold over the corpus -- 255 drivers, seven libraries, six
settings each, 1,530 runs -- puts the crossover at the bottom of the range:

| threshold | geomean vs medium | faster | slower | >10% slower | total solver |
| --- | ---: | ---: | ---: | ---: | ---: |
| **0** | **0.811** | 77 | 28 | 22 | 106.5s |
| 32k | 0.912 | 60 | 38 | 28 | 117.8s |
| 10k | 0.957 | 58 | 45 | 32 | 113.7s |
| 100k | 0.961 | 61 | 46 | 36 | 132.5s |
| 200k (STP default) | 0.970 | 52 | 44 | 33 | 141.7s |

over the 128 drivers where all six settings explored *identically* and nothing
was cut off. That restriction matters: exploration diverged on about a third of
the corpus, and on those drivers a faster configuration explores further and
meets harder queries, so its raw solver total and timeout count go *up* for a
reason that has nothing to do with the setting. Read whole-corpus timeout counts
here and threshold 0 looks like a regression; it is an artefact.

Only the threshold-0 row is far enough from the others to read confidently.
The settings at and above 10k cluster around 0.95 without a clean monotone
ordering, and 200k lands exactly where it should -- indistinguishable from
leaving the effort at medium, which is what a threshold that rarely fires means.

Per library, threshold 0 is the best or equal-best setting in six of seven:

| gsl | blis | openlibm | cxsparse | fftw | sundials | gmp |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.653 | 0.646 | 0.796 | 0.887 | 0.905 | 0.907 | 0.967 |

The win is an average, not a floor. 22 of 128 drivers are more than 10% slower
and the worst is 2.8x, with a p10/p90 spread of 0.449 to 1.333. What it buys is
25% of KLEE's solver time.

**A threshold of 0 is not the same as asking for very-low outright.** The effort
stays `AUTO`, so `BVExactEncoder`'s `allowAuto=false` path still pins itself to
medium; `--stp-cnf-effort=0` would drop that too and cost the incremental
refinement its clause count. Only the threshold expresses "very low everywhere
except where refinement needs better". This is the case for the knob existing:
200k stays the right default for STP, and the caller that has measured its own
workload moves it.

Not yet wired into `backends.tsv` -- the flag only exists in the PR #998 build,
and the corpus baseline should not depend on an unmerged branch.

### What threshold 0 is worth against Bitwuzla

The corpus comparison that put STP at 0.773 against Bitwuzla was run with STP
at its stock CNF effort. Re-running all three interleaved in one sweep -- so
the comparison is like-for-like rather than a ratio between two sweeps:

| | vs Bitwuzla | STP faster | slower | solver time |
| --- | ---: | ---: | ---: | --- |
| **stp-thr0** | **0.682** | 84 | 18 | 94.0s vs 127.8s |
| stp-medium | 0.833 | 71 | 34 | 124.6s vs 127.8s |

129 drivers comparable under all three. Threshold 0 wins in all seven
libraries: GSL 0.510, GMP 0.526, BLIS 0.617, OpenLibm 0.634, CXSparse 0.834,
SUNDIALS 0.917, FFTW 0.991. The tail is still there -- p90 is 1.333 and the
worst driver is 10.5x -- so this is a distribution that STP wins on balance,
not one it dominates.

Two checks make the number readable rather than merely favourable. `thr0`
against `medium` lands at 0.818 here, against 0.811 in the threshold sweep and
0.816 in a third run: three independent measurements within 0.7%. And
`stp-medium` against Bitwuzla reproduces the earlier baseline at 0.785 on the
115 drivers the two sweeps share, against the 0.773 published from the earlier
one.

That second check only passed on the second attempt, and the first attempt is
worth recording. The same sweep run while this repo's own solver was being
compiled on the same machine put `stp-medium` at 0.841 -- both solvers slower
than their earlier selves, Bitwuzla by 17% and STP by 27%, which understated
STP's own margin at 0.716 rather than flattering it. Nothing about the
comparison is safe on a shared machine, in either direction.

## What Bitwuzla was owed, and what it is worth

Every STP figure above comes from a configuration chosen by measuring it.
Bitwuzla's came from no options at all, and from a backend that called
`bitwuzla_new` and `bitwuzla_delete` inside the per-query path -- so every
query met a cold solver while STP kept a session across the whole run. That is
not a knob Bitwuzla was set wrongly; it is a courtesy one solver had and the
other did not.

`--bitwuzla-incremental` gives Bitwuzla the same session, one instance with
each query's assertions confined to a push/pop scope. Over 127 drivers where
all four configurations explored *identically* and nothing was cut off:

| | per-driver geomean | faster | slower | total solver |
| --- | ---: | ---: | ---: | ---: |
| `bwz-default` | — | — | — | 103.4s |
| `bwz-noabs` | 1.061 | 63 | 32 | 128.9s |
| **`bwz-incr`** | **0.760** | 74 | 23 | 147.8s |
| `stp-thr0` | 0.711 | 78 | 22 | 82.9s |

**The abstraction is not the knob.** Turning it off costs 6% overall and more
on the floating-point-heavy libraries -- GSL 1.304, OpenLibm 1.179 -- which is
the opposite of what its targeting `bvmul` and `bvudiv` suggested. Bitwuzla's
own default was already right here.

**The session is worth having, and it changes the answer.**

| STP against | per-driver geomean | drivers |
| --- | ---: | --- |
| Bitwuzla as KLEE ships it | 0.711 | 79 faster, 23 slower |
| **Bitwuzla with a session** | **0.936** | **50 faster, 48 slower** |

Most of what was reported as STP deciding these queries faster was STP being
handed an advantage the harness never gave Bitwuzla. Given the same one, the
two solvers split the corpus almost exactly in half.

**The two statistics disagree, and both are true.** Per driver the session wins
(0.760); by total solver time it loses badly (147.8s against 103.4s), because
it has a tail. p90 is 1.500 and the worst driver is 6.3x, with `mpf_get_d_2exp`
at 3.3x, `gsl_cdf_rayleigh_P` at 2.8x and `mpf_get_d` at 2.6x; those three
alone exceed the whole net regression, the rest of the corpus offsetting them.
Read per driver, Bitwuzla-with-a-session is close to STP. Read by totals, STP
is still well clear. Which matters depends on whether the typical query or the
whole run is the thing being paid for.

**And the comparison is still not symmetric.** A retained clause database is
not always worth keeping -- which is exactly why STP has
`--stp-incremental-engage-at` and `--stp-adapt-incremental`, both tuned here,
and why its own incrementality is conditional rather than unconditional.
Bitwuzla now has the naive version of what STP has the measured version of. An
engage threshold or a periodic reset is the obvious next step, and until it
exists the 0.936 above still flatters STP.

### Bitwuzla's own settings, once KLEE could reach them

Anchored on `bwz-incr`, so each knob is asked about on top of the session
rather than on top of a handicap. 127 drivers, identically explored under all
five configurations:

| | per-driver geomean | faster | slower | total solver |
| --- | ---: | ---: | ---: | ---: |
| `bwz-incr` | — | — | — | 152.1s |
| `bwz-incr-bv17` | 0.996 | 41 | 34 | 153.1s |
| `bwz-incr-bv24` | 1.009 | 37 | 39 | 153.8s |
| **`bwz-incr-rwl1`** | **0.925** | 66 | 27 | 137.5s |
| `stp-thr0` | 0.898 | 58 | 42 | 86.3s |

**The abstraction width does nothing.** Both settings have a median of exactly
1.000 and split the corpus like a coin. The reasoning that put them in the
table -- this corpus is floating point, an `fp.mul` on doubles blasts to a
53-bit multiply, the default line sits at 33 -- was sound and the prediction
from it was simply wrong. Where Bitwuzla draws that line is not what decides
these queries.

**The rewriter is, and in the direction this table did not expect.** `rwl1` --
cheap term rewrites, without the full pass and its preprocessing -- is worth
7.5%, on 66 drivers against 27, and helps in six libraries of seven (GSL 0.800,
GMP 0.866, BLIS 0.898, CXSparse 0.939, OpenLibm 0.950, FFTW 0.987; only
SUNDIALS at 1.041 goes the other way). The row was put in the table with the
note that it could only lose, there being no level above the default. It won.

That is the same shape as the result this whole line of work started from. STP
spends more time building a good CNF than it saves solving one, and dropping
the effort is worth 19%; Bitwuzla spends more time rewriting than it saves, and
dropping it is worth 7.5%. Both defaults are tuned for queries harder than the
ones KLEE actually asks.

### Where that leaves the comparison

| STP against | per-driver geomean | drivers |
| --- | ---: | --- |
| Bitwuzla as KLEE shipped it | 0.711 | 79 faster, 23 slower |
| with a session | 0.898-0.936 | 50-59 faster, 42-48 slower |
| **with a session and a cheap rewriter** | **0.971** | **56 faster, 44 slower** |

The middle row is quoted as a range on purpose: the same pair of
configurations measured 0.936 in one sweep and 0.898 in the next, on comparable
sets of the same size but different membership. Differences of that order are
not results here, which is worth remembering before reading much into 0.971
either. What survives is the shape: **tuned against tuned, on this corpus, the
two solvers are level**, and the 0.682 first reported was mostly measuring a
harness that gave one of them a session and the other nothing.

### What to actually pass Bitwuzla

The session and the rewriter had only ever been measured together. Apart, they
say different things. All four corners, 127 drivers identically explored:

| config | geomean | faster/slower | total | p90 | worst | >2x slower |
| --- | ---: | :---: | ---: | ---: | ---: | ---: |
| `bwz-default` | — | — | 115.8s | — | — | — |
| **`--bitwuzla-rewrite-level=1`** | **0.897** | 65 / 31 | **95.4s** | 1.33 | **2.00** | **0** |
| `--bitwuzla-incremental` | 0.766 | 74 / 30 | 170.9s | 1.50 | 5.38 | 9 |
| both | 0.740 | 68 / 27 | 155.9s | 1.33 | 4.50 | 5 |

**The rewriter is the recommendation, on its own.** It is the only row where
the per-driver figure and the total agree -- 10% off the typical driver and 18%
off the whole bill -- and the only one with no tail: the worst driver is 2.00x
and nothing exceeds it. Six libraries of seven gain, FFTW alone losing at
1.030. The win is also *larger* alone (0.897) than on top of the session
(0.966); the two overlap, the session already recovering some of what the full
rewriter was spending.

**The session is a trade, and not one to take yet.** It is the best per-driver
number here, and it makes the run cost more: 170.9s against 115.8s, with nine
drivers more than twice as slow and a worst case of 5.38x. Under a time budget
those are exactly the drivers that stop exploring, which is the outcome this
suite exists to notice. A retained clause database is not always worth keeping
-- STP has `--stp-incremental-engage-at` and `--stp-adapt-incremental` for that
reason and both were tuned here. Bitwuzla has no equivalent yet.

So, for KLEE today: **`--bitwuzla-rewrite-level=1`**, the abstraction left
alone, and incrementality left off until it can be made conditional.

Against that Bitwuzla, STP is at **0.758** (74 faster, 23 slower) -- further
ahead than the 0.918 it manages against a Bitwuzla with the session, because
the session is the part that closes the gap and the part not yet safe to
recommend. The closest Bitwuzla can currently get is with a setting it cannot
be trusted with; the best it can be trusted with leaves STP ahead by about a
quarter.

### The knobs neither solver had been tuned on

Everything above moved one factor at a time and stopped when something worked.
Of the nine settings KLEE exposes for STP the tuned configuration set four, and
on Bitwuzla's side rewrite level 1 had beaten the default 2 without anyone
asking about 0. 106 drivers, identically explored under all seven:

| STP, against its tuned baseline | geomean | faster/slower | total | worst |
| --- | ---: | :---: | ---: | ---: |
| `stp-best` | — | — | 45.4s | — |
| `+--stp-incremental-piece-rewriting` | 1.012 | 42 / 37 | 44.3s | 6.00 |
| `+--stp-incremental-scoped-preprocessing` | 1.011 | 32 / 41 | 45.8s | 1.67 |
| `+--stp-bv-abstraction-width=53` | 1.186 | 29 / 47 | 84.3s | 5.82 |
| `+--stp-bv-abstraction-width=24` | 1.267 | 18 / 58 | 100.9s | 4.80 |

**Nothing here improves STP, and the abstraction actively harms it** -- 19% at
the binary64 significand and 27% at binary32, nearly doubling the bill. That is
the outcome `STPSolver.cpp` predicted in the comment above the option: "GSL is
almost entirely double, so this suite sits on the losing side of that split;
measuring it is the point." It was, and it is. The two incremental
simplification flags are coin flips, 42 against 37 and 32 against 41.

So the configuration these results have used throughout **is** STP's best of
what KLEE can reach. It was not under-tuned, which was the live possibility
worth ruling out before comparing anything.

**Bitwuzla's rewrite level has an interior optimum.** Level 0 is worse than 1
by 5.5% -- 36 drivers faster against 48 slower, 60.5s against 51.1s -- having
already established that the default 2 is worse than 1 by 7.5%. Cheap rewriting
beats both full rewriting and none, which is a real minimum rather than a
monotone trend, and worth more than either endpoint would have suggested.

| both solvers at their best available | geomean | drivers |
| --- | ---: | --- |
| `stp-best` vs `bwz-rwl1` | 0.775 | 64 faster, 17 slower |

### What is left of the gap is one mechanism, and it is division

OpenLibm is the library where the two summary statistics disagree about who
won: STP takes 24% less time on the typical driver and 59% more in total. One
driver explains it. `atan2f` costs STP 2.48s against Bitwuzla's 0.96s, which is
1.52s of a 1.20s net gap -- the rest of the library offsetting it. Removed,
OpenLibm reads 0.706 in STP's favour.

`atan2(y, x)` is `atan(y/x)`, and division is where this work started. The
`atan2` driver is STP's worst case anywhere in the corpus: 12.2s against 1.7s.

Bitwuzla's abstraction module replaces wide `bvmul`, `bvudiv` and `bvurem` with
fresh variables and refines them by CEGAR, rather than blasting them. Turning
it off says how much of the gap that is:

| `atan2`, double | solver |
| --- | ---: |
| Bitwuzla, abstraction on (its default) | **0.862s** |
| Bitwuzla, abstraction off | 9.418s |
| STP, best configuration | 9.653s |

**Without the abstraction Bitwuzla lands on top of STP** -- 9.418s against
9.653s, a 2% difference where there had been an 11x one. `atan2f` repeats it at
smaller scale: 1.341s against STP's 1.172s. Division-free drivers move the
other way, `log1p` at 0.91 and `expf` at 0.60, so the abstraction is a trade
rather than a free win, and that is why disabling it costs only 6% across the
corpus while costing 991% here.

So the residual difference between these two solvers, after both are tuned, is
not spread across the corpus. It is lazy abstraction of wide multiplies and
divides: Bitwuzla has one that works, and on the drivers where it fires it is
worth an order of magnitude.

STP has the same idea behind `--stp-bv-abstraction-width`, and measured above
it costs 19% at the binary64 significand and 27% at binary32 -- on `atan2f`
specifically, 2.448s against 2.482s, which is no change at all. The mechanism
that decides these queries is one both solvers implement and only one of them
implements usefully. That, rather than any aggregate on this page, is where
STP's remaining work is.

### Taking atan2 apart

Dumped out of KLEE and replayed against both solvers on identical files, with
STP configured as KLEE configures it, `atan2`'s 89 queries divide cleanly:

| | STP | Bitwuzla | ratio |
| --- | ---: | ---: | ---: |
| all 89 | 7.80s | 2.70s | 2.89 |
| **41 with floating point** | **6.97s** | **1.86s** | **3.74** |
| 48 pure bit-vector | 0.84s | 0.84s | **1.00** |

The bit-vector half is at parity to two decimal places. Everything that
separates these solvers is in the floating-point queries, and net of the ~9.5ms
each process spends starting, that half is about 4.5x.

Measured with STP's own defaults instead of KLEE's the same corpus reads 6.29x,
because the standalone binary keeps the conservative 200k CNF threshold that
this suite moved to 0 for KLEE. Quoting that would have overstated the gap by
more than half.

**Where the time goes.** One query, 2,664 bytes, two `fp.div` on doubles. Every
AST phase before bit-blasting takes 0-1ms and leaves 642 nodes. That blasts to
**159,606 AIG nodes**, and MiniSat spends **5.6 million propagations** over
3,780 conflicts. CNF generation is no longer the bottleneck -- `auto` picks
`very-low` and it is cheap -- so what is left is search over a circuit that
should not be that large.

It is that large because of how `fp.div` is lowered. SymFPU's
`fixedPointDivide` widens both operands to about twice the significand and then
issues **two** full-width operations on them:

    ubv div(ex / ey);   // a ~107-bit BVDIV
    ubv rem(ex % ey);   // and a ~107-bit BVMOD, on the same operands

carrying the author's note that it is "not the best way of doing this but
pretty universal". Only the low 54 bits of the quotient survive, and the
remainder is used only to ask whether it is zero.

**What the other solver does about it.** Stripped of the floating point, a
107-bit `bvudiv` alone says it plainly:

| 107-bit `bvudiv`, low bits constrained | |
| --- | ---: |
| Bitwuzla, abstraction on (its default) | **0.025s** |
| Bitwuzla, `--abstraction=false` | 0.655s |
| STP, tuned | 0.151s |
| STP with `--bv-term-abstraction` at width 53 | 0.475s |

Lazy abstraction of that one operation is worth **26x** to Bitwuzla. The same
feature costs STP 3x.

**And it is not a matter of choosing the width.** Sweeping
`--bv-abstraction-width` across the 41 floating-point queries: off 8.69s, 53
19.89s, 64 17.35s, 80 22.28s, 100 24.31s, 106 26.81s. Every setting is worse
than not abstracting, and the *higher* the width the worse it gets -- so the
configuration that abstracts only the wide divisions, and nothing else, is the
worst of the six. That points at what refinement does with a division rather
than at which terms are picked.

`reproducers/` holds both queries. They are small and they are seconds to run,
which is a better place to work than a 255-driver sweep.

### Trying to close it, and a stale binary

Two things were worth attempting: STP's refinement of an abstracted division,
and SymFPU's lowering that produces the division in the first place.

**The first was already done, and this suite had been measuring a binary that
predated it.** The STP KLEE was linked against here was built from the CNF
commit, 27 commits behind master, and what landed in between includes PR #989 --
"Bound an abstracted division by its dividend and its divisor", "Refine an
abstracted division by the divisor the candidate chose", "Refine an abstracted
division by inequalities over its quotient". On the isolated wide division that
changes the answer completely:

| `reproducers/wide-bvudiv.smt2` | abstraction off | on |
| --- | ---: | ---: |
| the build measured above (pre-merge) | 0.231s | 0.670s |
| **current master** | 0.165s | **0.016s** |
| Bitwuzla | 0.655s | 0.025s |

Abstraction goes from costing 2.9x to saving 10x, and at 0.016s **STP beats
Bitwuzla on that query**. Every statement above about STP's abstraction not
working was measured before that landed and should be read as being about the
older binary.

**It does not carry to the corpus, and that is the more useful result.** On the
41 floating-point queries, on current master, abstraction is still a net loss --
12.22s against 25.99s standalone, and inside KLEE 6.25s against 18.21s, the
same either side of incrementality so it is not an interaction with that.

It is a loss made of large wins and larger losses, which the totals hide:
q00047 0.637s to 0.070s, q00050 0.604s to 0.078s, and q00011 0.215s to
**3.002s**. It wins on 21 of 41 queries. The obvious response is the one the
CNF effort already got, a policy that engages it only where it pays -- but
there is no signal to build one on. Abstraction wins on 4 of the 10 most
expensive queries and 5 of the 10 cheapest, so cost does not predict it, and an
oracle that always chose correctly would reach **8.30s against Bitwuzla's
1.86s**. Perfect selection is not enough; the gap is not in the choosing.

**The second was tried and does not pay.** `BBDivMod` subtracts at full operand
width when the loop invariant says the remainder cannot exceed the divisor, so
where the divisor's top bits are zero -- exactly SymFPU's shape -- the subtract
can be narrowed. Implemented on `aytey_20260826_narrow_divmod`, it shrinks the
circuit by 22% on a division of that shape and 13% on a real query, and runs a
query that is all construction three times faster. Over the 41 queries it is
14% *slower*. On one query MiniSat goes from 3,780 conflicts to 1,159; on
another from **18** to 5,733, a free query becoming the most expensive in the
set. 165 tests pass and 400 randomised division queries agree with master.

Which answers the SymFPU question without needing the patch. A lowering that
emitted a smaller division would be another way of making the circuit smaller,
and making the circuit smaller is measurably not what these queries want: 13%
of it can go with the search getting harder. The remaining distance to a solver
that abstracts the division is not a distance in circuit size.

### A correction: the abstraction width used above was badly chosen

Every measurement on this page that enables STP's bit-vector abstraction passes
`--stp-bv-abstraction-width=53`, on the reasoning that a binary64 significand is
53 bits. STP's own default is 64, and 64 is the better setting here. The
stage-1 sweep above had already hinted at it -- 64 read 17.35s against 53's
19.89s -- and it was not followed up.

At width 64 on a 298-query floating-point corpus drawn from eleven drivers,
abstraction costs about 9% against leaving it off (40.0s against 36.6s) rather
than the 19-27% the width-53 rows report. The direction of every conclusion
above survives: abstraction still loses on this workload, STP's own version
still does not pay, and the mechanism is still what separates the two solvers
on division. The magnitudes on the width-53 rows are overstated.

What surfaced it is worth recording too. A later STP branch documented its own
measurements, in a header comment, against this very corpus. Its figures for
one profile would not reproduce here -- and the reason was that this suite had
been holding the width at 53 while the branch measured at 64.

## What the corpus actually asks the solver, by width

The suite now spans binary16 to binary128, so the first question about it is
what fraction of its queries are floating-point at all, and at which widths.
`common/dump-queries.sh` over every library at `sweep-all.sh`'s own strides --
438 drivers, a 10-second budget, split into one file per query -- and
`common/query-sorts.py` over the result:

| | queries containing it |
| --- | ---: |
| binary16 | 989 |
| binary32 | 1,272 |
| binary64 | 16,737 |
| x87 fp80 | 0 |
| binary128 | 680 |
| **any floating-point sort** | **18,442 of 41,456 (44.5%)** |

The columns overlap, and where they overlap is informative: 956 queries carry
binary32 *and* binary64 together, which is OpenLibm's float functions
evaluating in double, and 137 carry binary16, binary32 and binary64 at once,
which is klee-uclibc's `logf` and `expf` inside CMSIS-DSP's half-precision
kernels. 15,644 are binary64 alone. x87 fp80 is zero throughout, as it should
be: `long double` is skipped everywhere.

**Match `to_fp` and not just `FloatingPoint`.** The first pass of this census
matched the sort name alone and reported 7.4%. That is wrong by a factor of
six, because the sort name appears only where something is *declared* of that
sort, and KLEE's ordinary output is a bitvector reinterpreted as a float:
`((_ to_fp 11 53) @def0)` is a binary64 query that never writes the word. The
error was visible as GMP reporting zero floating-point queries when the whole
reason `mpf` is in the corpus is its double conversions -- it has 11,750.

Two rows are artefacts of the short budget rather than facts about the
libraries. HDF5 reports 44 queries and almost no binary16, because `H5open()`
runs 75M instructions before the first conversion and a 10-second budget never
reaches the arithmetic; measured properly its queries are binary16 and nothing
else. OpenLibm is 18 of its 47 sampled drivers, its transcendentals being slow
to drain. FFTW's zero, on the other hand, is real at both widths -- see below.

### Instructions are not queries, and here they run backwards

`getrf`, one routine from f2cblaslapack, at the four widths the same source
compiles to, 60-second budget each:

| | instructions | queries | asserts | asserts/query |
| --- | ---: | ---: | ---: | ---: |
| binary16 | 67,293 | 195 | 4,928 | 25.3 |
| binary32 | 74,582 | 88 | 2,079 | 23.6 |
| binary64 | 381,476 | 58 | 1,283 | 22.1 |
| binary128 | 5,607,102 | 21 | 313 | 14.9 |

Instructions rise 83x while queries fall by a factor of nine. They are not
merely uncorrelated, they are inverted, and the reason is worth knowing before
anyone quotes an instruction count as solver load. `common/queries-by-function.py`
attributes both to the subprogram that caused them, out of KLEE's own
`run.istats`: **`qlamc4_` is 96.5% of the instructions at binary128 and issues
no queries at all.** It is LAPACK determining machine epsilon by iterated
halving, a loop whose length scales with the exponent range -- 5.4M
instructions at binary128 against 352k at binary64, all of it concrete. The
queries that do reach the solver come from about 1,600 instructions in
`qgetrf2_`, `qtrsm_` and `qlaswp_`.

Note also that a query is not a constraint. Each carries the whole path
condition, so the narrow formats show *more* asserts per query: they explore
deeper before the budget runs out.

### The optimiser decides which comparisons become queries

The single largest threat to reading any of this as a property of the solvers.
A comparison that if-converts to a `select` never becomes a path condition and
never reaches the solver, and at `-O2` that is the common case for the
reductions this corpus is full of:

* `iqamax_`, the pivot search `qgetf2` calls once per column, carries 14
  `fcmp fp128` and 18 `select`. Its driver runs 117 instructions and issues
  **zero** queries.
* CMSIS-NN's `arm_maximum_f16` carries 52 `fcmp half`, all consumed by
  `select`; five of six hand-written drivers dumped nothing.
* SUNDIALS' `N_VMaxNorm` if-converts at binary16 and binary64 -- 1 test each --
  and **branches** at binary128, forking to 256. Same source, same flags.

The last is the one to worry about: part of what a width comparison measures on
this corpus is at which widths LLVM declined to if-convert. It is not visible
without reading the IR, so a driver's `fcmp` count should be checked against
its query count before its width behaviour is believed.

What does branch, in f2cblaslapack, is the zero and exception tests rather than
the pivot search -- `qgetf2` on whether the pivot is zero (2 of 3),
`qpotf2` on positive-definiteness (2 of 2), `qtrsm` on `alpha` (13 of 13),
`qgemm` on `alpha` and `beta` (10 of 10).

### Where the non-floating-point queries come from

Of the 23,014 queries carrying no floating-point sort, the largest single
source is a harness decision rather than a library: the generator makes every
integer parameter symbolic and bounds it, and a *shape* left symbolic
manufactures integer queries and no floating-point ones. FFTW is the clean
case. Its drivers leave the transform length `n` symbolic in [0,8], and every
query it issues is the planner factorising it -- `bvsrem @def0 5`,
`bvslt @def0 1` -- while the symbolic data is never branched on because a
transform is branch-free. Pinning `n` to 8 takes `fftw_plan_dft_1d` from 56
queries, none floating-point, to **none at all**, with 1.6M instructions of
transform still executed.

The three arms whose integer arguments are pinned concrete -- `f2clapack`,
`f2clapack-f64`, `cuba` -- are at 100% floating-point density. `fftw` and
`fftwq` are at 0%, and `sundials` at 3%. That is the whole spread, and it
tracks one design choice.

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
about a query, and on the 1,082 replayed queries STP and Bitwuzla agreed
without exception; this measures speed only.

**STP is tuned here and Bitwuzla is not.** Every STP figure comes from a
configuration chosen by measurement on this corpus -- its SAT solver, when
incrementality engages, whether that adapts, and now the CNF-effort threshold.
Bitwuzla has run throughout with no options at all, which is to say at KLEE's
defaults and its own. The comparison is therefore *tuned STP against Bitwuzla
as KLEE ships it*, which is a fair description of what a KLEE user gets today
and is **not** the same claim as one solver being faster than the other.

Two asymmetries are worth naming because they are not small. KLEE holds one STP
session across queries and pushes and pops around each -- incrementality that
`--stp-incremental-engage-at` exists to tune and that was worth 8% -- while
`BitwuzlaSolverImpl` calls `bitwuzla_new` and `bitwuzla_delete` inside the
per-query path and never calls `bitwuzla_push`. Every query meets a cold
solver. And of Bitwuzla's own options -- SAT backend, rewrite level,
abstraction width and the preprocessing passes -- KLEE exposes exactly one,
`--bitwuzla-abstraction`. Both of those are answered in "What Bitwuzla was owed"
below -- the abstraction default turns out to be right, the missing session
did not -- but the SAT backend, rewrite level and abstraction width remain
unreachable from KLEE and unmeasured.

**The width census is a shape, not a census.** Its 41,456 queries come from a
10-second budget, which is a tenth of what a sweep gives a driver -- enough to
say which sorts a library's queries carry and not enough to say how many it
would eventually issue. Two of its rows are known artefacts of that, named
above. It was also taken on a machine running other work, which does not matter
for counting sorts and would matter for anything timed.

**The four-width axis is one routine.** `getrf` at binary16 through binary128
is identical code with one variable changed, which is the right experiment, but
it is a single routine and `?lamc4` dominates its instruction count. It says
what width costs that routine; it is not a corpus-wide result.

**The replayed corpus is not the hard tail.** It was dumped from Z3-driven
runs, so it carries Z3's query distribution, and only 15 of its 1,082 queries
take Bitwuzla longer than 0.2s. The 69 queries that exhaust a 30-second cap
inside KLEE are not in it. What the replay settles is the shape of the
advantage over ordinary queries, not the behaviour of the extreme tail.
