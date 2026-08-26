# Two queries that hold STP's remaining gap to Bitwuzla

Both are small, both are seconds to run, and between them they contain the
whole of what separates the two solvers on this corpus. See "What is left of
the gap is one mechanism" in ../RESULTS.md.

## `atan2-fp-div.smt2`

One query as KLEE asked it, dumped from `atan2`. 2,664 bytes, two `fp.div` on
doubles. STP 0.53s, Bitwuzla 0.087s.

STP blasts it to **159,606 AIG nodes** and MiniSat then does **5.6 million
propagations** over 3,780 conflicts. Every AST-level phase before that takes
0-1ms on 642 nodes, and CNF generation is no longer the cost -- with
`--cnf-auto-threshold 0` it picks `very-low` and the time is all search.

The size comes from SymFPU's `fixedPointDivide` (`core/operations.h`), which
lowers one `fp.div` as

    ubv ex(x.append(ubv::zero(w - 1)));   // ~2w bits
    ubv ey(y.extend(w - 1));              // ~2w bits
    ubv div(ex / ey);                     // a ~107-bit BVDIV
    ubv rem(ex % ey);                     // and a ~107-bit BVMOD, same operands
    return resultWithRemainderBit<t>(div.extract(w - 1, 0), !(rem.isAllZeros()));

with the author's own note that it is "not the best way of doing this but
pretty universal". Only the low 54 bits of the quotient are used and the
remainder is only tested against zero, so most of what is built is discarded.

## `wide-bvudiv.smt2`

The same shape with the floating point taken away: a 107-bit `bvudiv` whose low
54 bits are constrained. It is the smaller thing to iterate on.

| | |
| --- | ---: |
| Bitwuzla, abstraction on (default) | **0.025s** |
| Bitwuzla, `--abstraction=false` | 0.655s |
| STP, tuned | 0.151s |
| STP, `--bv-term-abstraction 1 --bv-abstraction-width 53` | 0.475s |

Bitwuzla's abstraction is worth **26x** to it here. STP's costs it 3x.

## What has been ruled out

Sweeping `--bv-abstraction-width` over 53, 64, 80, 100 and 106 on the 41
floating-point queries from `atan2`: **every** width is worse than off, and
monotonically so -- 8.69s off, 19.89s at 53, 26.81s at 106. Abstracting *only*
the wide divisions is the worst case, which points at refinement on a division
rather than at the choice of what to abstract.

`cegar-next-codex` and `cegar-next-claude` were both built, linked and measured
here; neither changes `atan2`, with abstraction still roughly doubling it.
