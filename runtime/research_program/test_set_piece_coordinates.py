"""Exercise the archived-evidence parser without importing research into the forecast path."""

from pathlib import Path
import subprocess
import sys
import unittest


class SetPieceEvidence(unittest.TestCase):
    def test_native_coordinates_and_page(self):
        study = (
            Path(__file__).resolve().parents[2]
            / "research"
            / "20261125-set-piece-evidence"
        )
        run = subprocess.run(
            [sys.executable, "-m", "unittest", "test_coordinates"],
            cwd=study,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
