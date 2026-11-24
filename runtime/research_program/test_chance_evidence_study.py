"""Include the isolated offline opportunity regressions in repository discovery."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


class OpportunityEvidence(unittest.TestCase):
    def test_isolated_study(self):
        root = Path(__file__).resolve().parents[2]
        suite = root / "research" / "20261124-chance-quality"
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-v"],
            cwd=suite,
            env=dict(os.environ, PYTHONPATH=str(root / "runtime")),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
