/* quad_example.c -- one kernel, two precisions.
 *
 *   clang -DFLOAT128 ...   REAL is __float128, the math comes from libquadmath
 *   clang           ...    REAL is double,     the math comes from libm
 *
 * The point of the macro block is that the body below is written once and
 * says nothing about which precision it is at.  That is how real numerical
 * code reaches for quads, and it is what has to survive the toolchain.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>

#ifdef FLOAT128
#include <quadmath.h>
#define REAL __float128
#define sin   sinq
#define cos   cosq
#define tan   tanq
#define asin  asinq
#define acos  acosq
#define atan  atanq

#define sinh  sinhq
#define cosh  coshq
#define tanh  tanhq
#define asinh asinhq
#define acosh acoshq
#define atanh atanhq

#define exp   expq
#define log   logq
#define pow   powq
#define sqrt  sqrtq
#define ceil  ceilq
#define fabs  fabsq
#define floor floorq
#define fmod  fmodq
#define modf  modfq

#define atof(a) strtoflt128((a), NULL)
#else
#include <math.h>
#define REAL double
#endif

#include "klee/klee.h"

/* ---------------------------------------------------------------- stage 1
 * Arithmetic and a comparison.  No library call of any kind: at the IR level
 * this is fadd/fsub/fmul/fdiv/fcmp on the REAL type and nothing else.
 */
REAL arith(REAL x, REAL y) {
  REAL s = x + y;
  REAL d = x - y;
  REAL p = x * y;
  REAL q = (y != 0) ? x / y : x;
  return s + d * p - q;
}

int compare(REAL x, REAL y) {
  if (x < y)  return -1;
  if (x > y)  return  1;
  if (x == y) return  0;
  return 2; /* unordered: at least one is NaN */
}

/* ---------------------------------------------------------------- stage 2
 * Conversions.  int <-> REAL, and double <-> REAL, which for __float128 are
 * fpext/fptrunc between two different float widths in the same function.
 */
REAL widen(int i, double d) {
  return (REAL)i + (REAL)d;
}

double narrow(REAL x) {
  return (double)x;
}

/* ---------------------------------------------------------------- stage 3
 * Horner evaluation -- a loop over REAL, the shape most numerical kernels
 * actually have.
 */
REAL poly(const REAL *c, int n, REAL x) {
  REAL acc = 0;
  for (int i = 0; i < n; i++)
    acc = acc * x + c[i];
  return acc;
}

/* ---------------------------------------------------------------- stage 4
 * The renamed library calls.  Under -DFLOAT128 every one of these is a call
 * into libquadmath rather than into libm.
 */
REAL transcendental(REAL x) {
  REAL a = sin(x) * cos(x) + tan(x);
  REAL b = exp(x) - log(fabs(x) + 1);
  REAL c = sqrt(fabs(x)) + pow(x, 2);
  return a + b + c;
}

REAL rounding(REAL x) {
  REAL ip;
  REAL fp = modf(x, &ip);
  return floor(x) + ceil(x) + fmod(x, 2) + ip + fp;
}

/* ---------------------------------------------------------------- stage 5
 * Parsing, which is where atof/strtoflt128 comes in.
 */
REAL parse(const char *s) {
  return atof(s);
}

/* ---------------------------------------------------------------- driver */

int main(void) {
  REAL x, y;
  klee_make_symbolic(&x, sizeof(x), "x");
  klee_make_symbolic(&y, sizeof(y), "y");

  /* Keep the reasoning in range so the interesting branch is reachable. */
  klee_assume(x > 0);
  klee_assume(x < 100);
  klee_assume(y > 0);
  klee_assume(y < 100);

  REAL a = arith(x, y);
  if (a > 1000)
    klee_assert(a > 0);

  int c = compare(x, y);
  if (c == 0)
    klee_assert(!(x < y));

  REAL w = widen(3, 0.5);
  double n = narrow(w);
  if (n > 3.4 && n < 3.6)
    klee_assert(1);

  REAL coeffs[3] = {1, 2, 3};
  REAL p = poly(coeffs, 3, x);
  if (p > 300)
    klee_assert(p > x);

  REAL t = transcendental(x);
  REAL r = rounding(x);
  REAL v = parse("3.14159265358979323846264338327950288");

  if (t + r + v > 0)
    return 1;
  return 0;
}
