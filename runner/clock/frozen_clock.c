/*
 * frozen_clock: LD_PRELOAD shim that pins the wall clock for legacy runs.
 *
 * Why: GnuCOBOL 3.1.2's COB_CURRENT_DATE fixes the date and HH:MM:SS, but the
 * hundredths in FUNCTION CURRENT-DATE / ACCEPT FROM TIME still come from the
 * real clock (see docs/legacy-setup.md, W8).  POSTTRAN writes those hundredths
 * into TRAN-PROC-TS, so golden output would not be reproducible.
 *
 * libcob reads wall time through clock_gettime() and time() (checked with
 * `nm -D --undefined-only libcob.so.4`); gettimeofday() is covered too.
 * Only realtime clocks are frozen; monotonic/CPU clocks pass through, so
 * sleeps and timeouts still work.
 *
 * LEGACY_FROZEN_EPOCH = seconds since 1970-01-01T00:00:00Z (required).
 * Sub-second fields are always 0.  Combine with TZ=UTC.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <time.h>

static time_t frozen_epoch(void)
{
    static int ready;
    static time_t value;
    if (!ready) {
        const char *s = getenv("LEGACY_FROZEN_EPOCH");
        char *end = NULL;
        long long v = s ? strtoll(s, &end, 10) : 0;
        if (!s || !*s || *end) {
            fprintf(stderr, "frozen_clock: LEGACY_FROZEN_EPOCH missing or invalid\n");
            abort();
        }
        value = (time_t)v;
        ready = 1;
    }
    return value;
}

int clock_gettime(clockid_t id, struct timespec *ts)
{
    if (id == CLOCK_REALTIME || id == CLOCK_REALTIME_COARSE) {
        ts->tv_sec = frozen_epoch();
        ts->tv_nsec = 0;
        return 0;
    }
    static int (*real)(clockid_t, struct timespec *);
    if (!real)
        real = (int (*)(clockid_t, struct timespec *))dlsym(RTLD_NEXT, "clock_gettime");
    return real(id, ts);
}

time_t time(time_t *t)
{
    time_t v = frozen_epoch();
    if (t)
        *t = v;
    return v;
}

int gettimeofday(struct timeval *restrict tv, void *restrict tz)
{
    (void)tz;                 /* glibc declares tv nonnull */
    tv->tv_sec = frozen_epoch();
    tv->tv_usec = 0;
    return 0;
}
