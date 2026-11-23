/*
 * CEE3ABD: stand-in for the z/OS Language Environment callable service
 * "terminate enclave with an abend".
 *
 *   CALL 'CEE3ABD' USING ABCODE, TIMING.
 *     ABCODE  PIC S9(9) BINARY   user abend code (U0000-U4095)
 *     TIMING  PIC S9(9) BINARY   0 = abend now, no LE condition handling
 *                                1 = abend after normal LE termination
 *
 * Programs are compiled with -std=ibm (binary-byteorder: big-endian), so both
 * fullwords arrive big-endian and are decoded here byte by byte.
 *
 * Emulation: report the abend, then end the process immediately with
 * LEGACY_ABEND_EXIT.  The runner treats that exit code as an abend: it skips
 * later steps and applies each DD's abnormal disposition.  When the runner
 * sets LEGACY_ABEND_FILE, the code is also written there ("U0999 TIMING=0"),
 * because a Unix exit status can't hold a 12-bit abend code.
 *
 * Both TIMING values end the process the same way.  _exit() bypasses libcob's
 * file close, so buffered output may be lost, much as on the mainframe, where
 * abnormal disposition usually deletes it anyway.  See docs/legacy-setup.md.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <unistd.h>

#define LEGACY_ABEND_EXIT 222

static int32_t be32(const unsigned char *p)
{
    return (int32_t)((uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 |
                     (uint32_t)p[2] << 8  | (uint32_t)p[3]);
}

int CEE3ABD(unsigned char *abcode, unsigned char *timing)
{
    int32_t code = abcode ? be32(abcode) : -1;
    int32_t when = timing ? be32(timing) : -1;
    const char *path = getenv("LEGACY_ABEND_FILE");

    fflush(stdout);
    fprintf(stderr, "CEE3ABD: USER ABEND U%04d TIMING=%d\n", (int)code, (int)when);
    fflush(stderr);
    if (path && *path) {
        FILE *f = fopen(path, "w");
        if (f) {
            fprintf(f, "U%04d TIMING=%d\n", (int)code, (int)when);
            fclose(f);
        }
    }
    _exit(LEGACY_ABEND_EXIT);
}
