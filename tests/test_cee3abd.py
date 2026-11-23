"""CEE3ABD stub, called from real GnuCOBOL code compiled with the runner's flags."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from runner import build

PROGRAM = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. ABENDT.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  ABCODE                  PIC S9(9) BINARY.
       01  TIMING                  PIC S9(9) BINARY.
       PROCEDURE DIVISION.
           DISPLAY 'BEFORE ABEND'
           MOVE 0 TO TIMING
           MOVE 999 TO ABCODE
           CALL 'CEE3ABD' USING ABCODE, TIMING.
           DISPLAY 'NOT REACHED'
           GOBACK.
"""


class Cee3abdTest(unittest.TestCase):
    def test_abend_from_cobol(self):
        build.build_stubs()
        tmp = Path(tempfile.mkdtemp(prefix="cee3abd-"))
        src = tmp / "ABENDT.cbl"
        src.write_text(PROGRAM)
        exe = tmp / "ABENDT"
        subprocess.run(["cobc", *build.COBC_FLAGS, "-o", str(exe), str(src)],
                       check=True, capture_output=True)
        abend_file = tmp / "abend"
        proc = subprocess.run([str(exe)], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "COB_LIBRARY_PATH": str(build.LIB),
                                   "LEGACY_ABEND_FILE": str(abend_file)})
        self.assertEqual(proc.returncode, build.ABEND_EXIT)
        self.assertEqual(proc.stdout, "BEFORE ABEND\n")          # nothing after the CALL runs
        self.assertIn("CEE3ABD: USER ABEND U0999 TIMING=0", proc.stderr)
        # 999 only decodes correctly if the stub reads the fullword big-endian
        self.assertEqual(abend_file.read_text(), "U0999 TIMING=0\n")


if __name__ == "__main__":
    unittest.main()
