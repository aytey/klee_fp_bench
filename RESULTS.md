# Does STP decide these queries faster than Bitwuzla?

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
`--bitwuzla-abstraction`, which no measurement here has yet moved off its
default.

**The replayed corpus is not the hard tail.** It was dumped from Z3-driven
runs, so it carries Z3's query distribution, and only 15 of its 1,082 queries
take Bitwuzla longer than 0.2s. The 69 queries that exhaust a 30-second cap
inside KLEE are not in it. What the replay settles is the shape of the
advantage over ordinary queries, not the behaviour of the extreme tail.
