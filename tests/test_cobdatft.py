"""COBDATFT port vs. legacy/app/asm/COBDATFT.asm.

Each case was derived by hand from the assembler (statement numbers refer to
COBDATFT.asm).  The 80-byte record layout is maclib/COCDATFT.mac:
  type(1) in-date(20) out-type(1) out-date(20) error-msg(38)
"""
import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "runner" / "stubs" / "COBDATFT.c"

PRE_OUT = b"#" * 20          # sentinel: bytes the routine must not touch
PRE_MSG = b"~" * 38


def build(define_fill=None):
    out = Path(tempfile.mkdtemp(prefix="cobdatft-")) / "COBDATFT.so"
    cmd = ["gcc", "-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror",
           "-o", str(out), str(SRC)]
    if define_fill is not None:
        cmd.insert(1, f"-DCOBDATFT_BEYOND_CSECT={define_fill}")
    subprocess.run(cmd, check=True)
    lib = ctypes.CDLL(str(out))
    lib.COBDATFT.argtypes = [ctypes.c_char_p]
    lib.COBDATFT.restype = ctypes.c_int
    return lib


def rec(intype, indate, outtype, outdate=PRE_OUT, msg=PRE_MSG):
    r = intype + indate.ljust(20) + outtype + outdate + msg
    assert len(r) == 80, len(r)
    return r


def split(r):
    return {"type": r[0:1], "in": r[1:21], "outtype": r[21:22],
            "out": r[22:42], "msg": r[42:80]}


class CobdatftTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = build()

    def call(self, r):
        buf = ctypes.create_string_buffer(r, 80)
        rc = self.lib.COBDATFT(buf)
        return rc, buf.raw[:80]

    # --- VALIDIN2: YYYY-MM-DD -> YYYYMMDD (the only path CardDemo uses) -----
    def test_type2_to_yyyymmdd(self):
        rc, r = self.call(rec(b"2", b"2025-05-20", b"2"))
        f = split(r)
        self.assertEqual(rc, 0)
        self.assertEqual(f["out"], b"20250520" + PRE_OUT[8:])   # stmts 51-53, bytes 8-19 untouched
        self.assertEqual(f["msg"], PRE_MSG)
        self.assertEqual(f["in"], b"2025-05-20".ljust(20))       # input not modified

    def test_type2_outtype_blank_is_accepted(self):
        # stmt 49 only rejects out-type '1'
        rc, r = self.call(rec(b"2", b"2024-02-29", b" "))
        self.assertEqual(split(r)["out"][:8], b"20240229")
        self.assertEqual(split(r)["msg"], PRE_MSG)

    def test_type2_does_not_validate_separators(self):
        # stmts 47-48 are commented out: bytes 4 and 7 are skipped blindly
        rc, r = self.call(rec(b"2", b"20240315xx", b"2"))
        self.assertEqual(split(r)["out"][:8], b"202431xx")
        self.assertEqual(split(r)["msg"], PRE_MSG)

    def test_type2_outtype1_is_error(self):
        rc, r = self.call(rec(b"2", b"2025-05-20", b"1"))
        f = split(r)
        self.assertEqual(rc, 0)                                   # SR R15,R15 even on error
        self.assertEqual(f["out"], PRE_OUT)                       # nothing moved
        self.assertEqual(f["msg"], b"INVALID INPUT" + b"\x00" * 25)  # 38-byte MVC

    # --- VALIDIN1: YYYYMMDD -> YYYY-MM-DD ------------------------------------
    def test_type1_to_dashed(self):
        rc, r = self.call(rec(b"1", b"20240315", b"1"))
        self.assertEqual(split(r)["out"], b"2024-03-15" + PRE_OUT[10:])   # stmts 40-44
        self.assertEqual(split(r)["msg"], PRE_MSG)

    def test_type1_outtype2_is_error(self):
        rc, r = self.call(rec(b"1", b"20240315", b"2"))
        self.assertEqual(split(r)["out"], PRE_OUT)
        self.assertEqual(split(r)["msg"], b"INVALID INPUT" + b"\x00" * 25)

    def test_type1_dash_check_is_20_byte_clc(self):
        # stmt 36 compares 20 bytes, so a '-' at byte 4 alone is NOT rejected;
        # the date is reformatted around it.
        rc, r = self.call(rec(b"1", b"2024-03-15", b"1"))
        self.assertEqual(split(r)["out"][:10], b"2024--0-3-")
        self.assertEqual(split(r)["msg"], PRE_MSG)

    def test_type1_clc_equal_only_on_full_pool_match(self):
        # Equal only when COINPDT+4..COOUTDT+2 match "-INVALID INPUT" plus the
        # 6 bytes beyond the CSECT (modelled as X'00').  Out-type X'00' is not
        # '2', so only the CLC can send this to GOTOERR.
        indate = b"2024-INVALID INPUT\x00\x00"
        r0 = b"1" + indate + b"\x00" + b"\x00\x00\x00" + PRE_OUT[3:] + PRE_MSG
        rc, r = self.call(r0)
        self.assertEqual(split(r)["out"], b"\x00\x00\x00" + PRE_OUT[3:])
        self.assertEqual(split(r)["msg"], b"INVALID INPUT" + b"\x00" * 25)

    # --- type validation -----------------------------------------------------
    def test_unknown_type_is_error(self):
        for t in (b"3", b" ", b"\x00"):
            rc, r = self.call(rec(t, b"20240315", b"1"))
            self.assertEqual(rc, 0)
            self.assertEqual(split(r)["out"], PRE_OUT)
            self.assertEqual(split(r)["msg"], b"INVALID INPUT" + b"\x00" * 25)

    def test_beyond_csect_bytes_are_a_build_parameter(self):
        lib = build(define_fill="0x40")
        buf = ctypes.create_string_buffer(rec(b"9", b"", b"1"), 80)
        lib.COBDATFT(buf)
        self.assertEqual(buf.raw[42:80], b"INVALID INPUT" + b"\x40" * 25)


if __name__ == "__main__":
    unittest.main()
