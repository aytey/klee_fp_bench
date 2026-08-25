/* Write the coverage profile even when a replay dies on a signal.
 *
 * the library's error handler calls abort() on a domain error, which is the
 * normal outcome for a good fraction of the inputs KLEE produces. abort() and
 * the other fatal signals skip atexit, so the profile for that run -- and with
 * it every line the run covered on the way to the error -- would be lost.
 *
 * The APSEC harness did the same thing for gcov with an LD_PRELOAD shim
 * (the APSEC harness's gcov_preload.c); this is linked into the replay binary
 * instead, so there is nothing to remember to set at replay time.
 */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>

static void flush_and_exit(int signo) { exit(128 + signo); }

__attribute__((constructor)) static void install_handlers(void) {
  static const int sigs[] = {SIGILL,  SIGFPE, SIGABRT, SIGBUS, SIGSEGV,
                             SIGHUP,  SIGINT, SIGQUIT, SIGTERM};
  struct sigaction sa;
  unsigned i;

  sa.sa_handler = flush_and_exit;
  sigemptyset(&sa.sa_mask);
  sa.sa_flags = SA_RESETHAND;
  for (i = 0; i < sizeof(sigs) / sizeof(sigs[0]); ++i)
    if (sigaction(sigs[i], &sa, NULL) == -1)
      perror("gsl-bench: could not install signal handler");
}
