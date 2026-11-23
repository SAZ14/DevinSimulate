/*
 * COBDATFT: C port of legacy/app/asm/COBDATFT.asm (HLASM date reformatter).
 *
 * Called from COBOL as  CALL 'COBDATFT' USING CODATECN-REC.
 * R1 -> parameter list; the first word points at the 80-byte record mapped
 * by legacy/app/maclib/COCDATFT.mac (same layout as cpy/CODATECN.cpy):
 *
 *   offset len  DSECT label  COBOL name
 *        0   1  COINTYPE     CODATECN-TYPE        '1'=YYYYMMDD in, '2'=YYYY-MM-DD in
 *        1  20  COINPDT      CODATECN-INP-DATE
 *       21   1  COOUTYPE     CODATECN-OUTTYPE     '1'=YYYY-MM-DD out, '2'=YYYYMMDD out
 *       22  20  COOUTDT      CODATECN-0UT-DATE
 *       42  38  COERMSG      CODATECN-ERROR-MSG
 *
 * This is a statement-by-statement port, including two behaviours that look
 * like bugs in the assembler but are what the load module actually does.
 * Both come from the rule that SS-format instructions (CLC, MVC) with an
 * implicit length take it from the FIRST operand's length attribute:
 *
 *  1. `CLC COINPDT+4,=C'-'`: the length attribute of COINPDT+4 is L'COINPDT=20,
 *     so this compares 20 bytes (COINPDT+4 .. COOUTDT+2) with 20 bytes of the
 *     literal pool starting at the '-' literal, not 1 byte.  In practice it
 *     is never equal, so a type-1 input containing '-' is NOT rejected.
 *  2. `MVC COERMSG,=C'INVALID INPUT'`: copies L'COERMSG = 38 bytes starting at
 *     the 13-byte literal, i.e. 25 bytes beyond it.
 *
 * Literal pool: there is no LTORG, so HLASM places the pool at the end of the
 * first (only) CSECT, after SAVE.  Both literals have odd lengths, so they
 * fall in the same alignment group and are laid out in order of appearance:
 *   =C'-' (line 36) then =C'INVALID INPUT' (line 56).
 * That puts the pool's last byte at the end of the CSECT.  The bytes after it
 * belong to whatever the binder put next in the load module and cannot be
 * derived from the source.  They are modelled as COBDATFT_BEYOND_CSECT
 * (default X'00').  See docs/legacy-setup.md, W6.  CardDemo only calls this
 * routine with type 2 -> 2, which never reaches either quirk.
 *
 * Exit: SR R15,R15, so RETURN-CODE is always 0, including on the error path.
 * Character literals are compared/stored in the run-time code page (ASCII here),
 * matching the EBCDIC->ASCII normalisation applied to all data.
 */
#include <string.h>

#ifndef COBDATFT_BEYOND_CSECT
#define COBDATFT_BEYOND_CSECT 0x00
#endif

enum {
    COINTYPE = 0,
    COINPDT  = 1,  L_COINPDT = 20,
    COOUTYPE = 21,
    COOUTDT  = 22,
    COERMSG  = 42, L_COERMSG = 38,
};

/* Literal pool image: '-' 'INVALID INPUT', then bytes beyond the CSECT.
 * Sized for the longest implicit-length read: MVC from pool+1 for 38 bytes. */
#define POOL_KNOWN 14
#define POOL_SIZE  (1 + L_COERMSG)
static unsigned char pool[POOL_SIZE];
static int pool_ready;

static void init_pool(void)
{
    memcpy(pool, "-INVALID INPUT", POOL_KNOWN);
    memset(pool + POOL_KNOWN, COBDATFT_BEYOND_CSECT, POOL_SIZE - POOL_KNOWN);
    pool_ready = 1;
}

/* MVC: byte-by-byte, left to right (matters only for destructive overlap). */
static void mvc(unsigned char *dst, const unsigned char *src, int len)
{
    for (int i = 0; i < len; i++)
        dst[i] = src[i];
}

/* CLC: unsigned byte compare; returns 0 when equal (condition code 0). */
static int clc(const unsigned char *a, const unsigned char *b, int len)
{
    for (int i = 0; i < len; i++)
        if (a[i] != b[i])
            return a[i] < b[i] ? -1 : 1;
    return 0;
}

int COBDATFT(unsigned char *r)
{
    if (!pool_ready)
        init_pool();

    if (r[COINTYPE] == '1')                         /* CLI COINTYPE,C'1' / BE  */
        goto validin1;
    if (r[COINTYPE] == '2')                         /* CLI COINTYPE,C'2' / BE  */
        goto validin2;
    goto gotoerr;                                   /* B GOTOERR               */

validin1:
    if (clc(r + COINPDT + 4, pool, L_COINPDT) == 0) /* CLC COINPDT+4,=C'-' (20) */
        goto gotoerr;
    if (r[COOUTYPE] == '2')                         /* CLI COOUTYPE,C'2'       */
        goto gotoerr;
    mvc(r + COOUTDT, r + COINPDT, 4);               /* MVC COOUTDT(4),COINPDT  */
    r[COOUTDT + 4] = '-';                           /* MVI COOUTDT+4,C'-'      */
    mvc(r + COOUTDT + 5, r + COINPDT + 4, 2);       /* MVC COOUTDT+5(2),COINPDT+4 */
    r[COOUTDT + 7] = '-';                           /* MVI COOUTDT+7,C'-'      */
    mvc(r + COOUTDT + 8, r + COINPDT + 6, 2);       /* MVC COOUTDT+8(2),COINPDT+6 */
    return 0;                                       /* B EXITL                 */

validin2:
    /* The separator check is commented out in the source (lines 47-48). */
    if (r[COOUTYPE] == '1')                         /* CLI COOUTYPE,C'1'       */
        goto gotoerr;
    mvc(r + COOUTDT, r + COINPDT, 4);               /* MVC COOUTDT(4),COINPDT  */
    mvc(r + COOUTDT + 4, r + COINPDT + 5, 2);       /* MVC COOUTDT+4(2),COINPDT+5 */
    mvc(r + COOUTDT + 6, r + COINPDT + 8, 2);       /* MVC COOUTDT+6(2),COINPDT+8 */
    return 0;                                       /* B EXITL                 */

gotoerr:
    mvc(r + COERMSG, pool + 1, L_COERMSG);          /* MVC COERMSG,=C'INVALID INPUT' (38) */
    return 0;                                       /* EXITL: SR R15,R15       */
}
